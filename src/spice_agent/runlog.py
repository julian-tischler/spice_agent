"""
Per-run logging: one self-contained folder per agent run, filed by mode.

    out/runs/
        live/               runs that called a real model (gemini, claude):
                            the data worth keeping
        dry/                everything else (analytic baselines, scripted
                            fakes): safe to delete at any time
    Each mode folder has its own index.jsonl, one summary line per run.

    out/runs/live/2026-11-10T14-32-07_lowpass-10000Hz_claude-haiku/
        run.json            manifest: request, spec, backend/model, environment,
                            per-iteration timing/usage/verdict, status, outputs
        request.txt         the verbatim command line
        spec.json           the DesignSpec that was built from it
        system_prompt.txt   the exact brief sent to the model
        iter_01/
            prompt.txt          what was sent this turn (claude backend)
            command.json        how the model was called
            model_response.json the raw, untouched reply
            circuit.json        the circuit the model proposed
            circuit_full.json   ...with the analysis block the harness attached
            circuit.cir         the exact netlist handed to ngspice
            circuit_ideal.cir   same circuit, ideal parts (non-ideal technology only)
            ngspice.log         ngspice stdout/stderr
            sweep.csv           the measured AC sweep
            result.json         the verification verdict
        final/              written when the spec is met (circuit, netlist, plot...)

Everything the recorder is given is written as-is; it never summarises.
A recorder built with `RunRecorder.disabled()` (or root=None) accepts the
same calls and does nothing, so call sites need no `if recorder:` checks.
The folder is created before the first model call and run.json is rewritten
at the end - including on errors and Ctrl+C - so a crashed run still leaves
a readable record with status "error" / "aborted".
"""

import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

RUNS_DIR_ENV = "SPICE_AGENT_RUNS_DIR"
# Backends that call a real model. A run on any other backend is "dry".
LIVE_BACKENDS = frozenset({"gemini", "claude"})
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)


def default_runs_dir() -> Path:
    return Path(os.environ.get(RUNS_DIR_ENV) or Path.cwd() / "out" / "runs")


def _secrets() -> list[str]:
    return [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v) >= 8]


def _redact(text: str) -> str:
    for s in _secrets():
        text = text.replace(s, "[REDACTED]")
    return text


def _json_default(o):
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json", exclude_none=True)
    return str(o)


def _dumps(obj) -> str:
    return _redact(json.dumps(obj, indent=2, default=_json_default, ensure_ascii=False))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-")


def _cmd_out(cmd: list[str], cwd=None, timeout=10) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=cwd)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def collect_environment() -> dict:
    """What a result depends on besides the inputs: code, libraries, simulator."""
    here = Path(__file__).resolve().parent
    versions = {}
    for pkg in ("numpy", "pandas", "matplotlib", "google-genai", "pytest"):
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": versions,
        "git_commit": _cmd_out(["git", "rev-parse", "HEAD"], cwd=here),
        "git_dirty": None,
        "ngspice": None,
    }
    status = _cmd_out(["git", "status", "--porcelain"], cwd=here)
    if status is not None:
        env["git_dirty"] = bool(status)
    try:
        from spice_agent.paths import require_ngspice
        out = _cmd_out([require_ngspice(), "-v"], timeout=15) or ""
        m = re.search(r"ngspice-[\w.]+", out)
        env["ngspice"] = m.group(0) if m else (out.splitlines()[0] if out else None)
    except Exception:
        pass
    return env


class RunRecorder:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else None
        self.manifest: dict = {}
        self._t0 = time.monotonic()
        self._iters: dict[int, dict] = {}
        self._finished = False

    # -- construction ----------------------------------------------------
    @classmethod
    def disabled(cls) -> "RunRecorder":
        return cls(None)

    @property
    def enabled(self) -> bool:
        return self.root is not None

    @classmethod
    def start(cls, *, spec, backend: str, model: str, argv=None, settings=None,
              runs_dir=None, live: bool | None = None) -> "RunRecorder":
        """
        Create the run folder and write the request, spec and a first manifest.
        The folder goes under <runs_dir>/live/ or <runs_dir>/dry/; `live`
        defaults to whether `backend` calls a real model (LIVE_BACKENDS).
        """
        if live is None:
            live = backend in LIVE_BACKENDS
        mode = "live" if live else "dry"
        runs_dir = (Path(runs_dir) if runs_dir else default_runs_dir()) / mode
        started = datetime.now().astimezone()
        base = (f"{started:%Y-%m-%dT%H-%M-%S}_{spec.filter_type}-{spec.target_hz:g}Hz_"
                f"{_slug(backend)}-{_slug(model)}")
        root, n = runs_dir / base, 1
        while root.exists():
            n += 1
            root = runs_dir / f"{base}-{n}"
        root.mkdir(parents=True)

        argv = list(sys.argv[1:] if argv is None else argv)
        rec = cls(root)
        rec.manifest = {
            "run_id": root.name,
            "status": "running",
            "mode": mode,
            "started_at": started.isoformat(timespec="seconds"),
            "request": {"argv": argv, "command_line": shlex.join(argv)},
            "spec": spec.to_dict(),
            "spec_text": spec.describe(),
            "backend": backend,
            "model": model,
            "settings": settings or {},
            "environment": collect_environment(),
            "parasitics_profile": _profile_dict(spec.technology),
        }
        rec._write_text("request.txt", shlex.join(argv) + "\n")
        rec._write_json("spec.json", {"spec": spec.to_dict(), "text": spec.describe()})
        rec._flush()
        return rec

    # -- context manager: guarantees a final manifest --------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and self.enabled:
            self.manifest["status"] = "aborted" if issubclass(exc_type, KeyboardInterrupt) else "error"
            self.manifest["error"] = _redact("".join(traceback.format_exception(exc_type, exc, tb)))
        self._finalize()
        return False

    # -- recording calls (all no-ops when disabled) ----------------------
    def set_system_prompt(self, text: str) -> None:
        self._write_text("system_prompt.txt", text)

    def iteration_dir(self, n: int) -> Path | None:
        if not self.enabled:
            return None
        d = self.root / f"iter_{n:02d}"
        d.mkdir(exist_ok=True)
        return d

    def record_model_call(self, n: int, *, raw, command=None, prompt: str | None = None,
                          usage: dict | None = None, duration_s: float | None = None) -> None:
        if not self.enabled:
            return
        self._write_json(f"iter_{n:02d}/model_response.json", raw)
        if command is not None:
            self._write_json(f"iter_{n:02d}/command.json", command)
        if prompt is not None:
            self._write_text(f"iter_{n:02d}/prompt.txt", prompt)
        it = self._iters.setdefault(n, {"iteration": n})
        it["model_duration_s"] = None if duration_s is None else round(duration_s, 3)
        if usage:
            it["usage"] = usage
        self._flush()

    def record_circuit(self, n: int, circuit: dict, call: int = 1) -> None:
        self._write_json(f"iter_{n:02d}/{_stem(call)}circuit.json", circuit)

    def record_simulation(self, n: int, capture: dict | None, result: dict, call: int = 1) -> None:
        """Write the netlist, ngspice output, sweep and verdict for one simulation."""
        if not self.enabled:
            return
        capture = capture or {}
        d, s = f"iter_{n:02d}", _stem(call)
        if capture.get("circuit_json") is not None:
            self._write_json(f"{d}/{s}circuit_full.json", capture["circuit_json"])
        if capture.get("netlist") is not None:
            self._write_text(f"{d}/{s}circuit.cir", capture["netlist"])
        if capture.get("netlist_ideal") is not None:
            self._write_text(f"{d}/{s}circuit_ideal.cir", capture["netlist_ideal"])
        if "ngspice_stdout" in capture:
            self._write_text(f"{d}/{s}ngspice.log",
                             f"returncode: {capture.get('ngspice_returncode')}\n"
                             f"--- stdout ---\n{capture['ngspice_stdout']}\n"
                             f"--- stderr ---\n{capture['ngspice_stderr']}\n")
        if capture.get("op") is not None:
            self._write_json(f"{d}/{s}operating_point.json", capture["op"])
        sweep = capture.get("sweep")
        if sweep is not None and self.enabled:
            (self.root / d).mkdir(exist_ok=True)
            sweep.to_csv(self.root / d / f"{s}sweep.csv", index=False)
        self._write_json(f"{d}/{s}result.json", result)
        it = self._iters.setdefault(n, {"iteration": n})
        it.setdefault("simulations", []).append({
            "passed": bool(result.get("passed")),
            "simulation_failed": bool(result.get("simulation_failed")),
            "message": result.get("message"),
        })
        self._flush()

    def final_dir(self) -> Path | None:
        if not self.enabled:
            return None
        d = self.root / "final"
        d.mkdir(exist_ok=True)
        return d

    def note_external_output(self, path) -> None:
        if self.enabled:
            self.manifest.setdefault("external_outputs", []).append(str(path))

    def note(self, key: str, value) -> None:
        if self.enabled:
            self.manifest.setdefault("notes", {})[key] = value

    def finish(self, run) -> None:
        """Record the outcome of an AgentRunResult."""
        if not self.enabled:
            return
        if run.passed:
            status = "passed"
        elif getattr(run, "stalled", False):
            status = "stalled"      # the same design resubmitted repeatedly
        elif run.final_text:
            status = "stopped"      # model gave up, or a usage limit ended the run
        else:
            status = "failed"       # iteration budget exhausted
        self.manifest.update({
            "status": status,
            "passed": run.passed,
            "iterations_used": run.iterations,
            "final_result": run.final_result,
            "final_text": run.final_text,
        })
        # Written now, not only at exit: anything reading run.json before
        # the run folder closes (the history plot) must see the verdict.
        self._flush()

    # -- internals -------------------------------------------------------
    def _write_text(self, rel: str, text: str) -> None:
        if not self.enabled:
            return
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_redact(text), encoding="utf-8")

    def _write_json(self, rel: str, obj) -> None:
        self._write_text(rel, _dumps(obj))

    def _flush(self) -> None:
        if self.enabled:
            self.manifest["iterations"] = [self._iters[k] for k in sorted(self._iters)]
            self._write_text("run.json", _dumps(self.manifest))

    def _finalize(self) -> None:
        if not self.enabled or self._finished:
            return
        self._finished = True
        if self.manifest.get("status") == "running":
            self.manifest["status"] = "error"
            self.manifest["error"] = "run ended without a result"
        self.manifest["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.manifest["duration_s"] = round(time.monotonic() - self._t0, 3)
        totals: dict = {"model_calls": 0}
        for it in self._iters.values():
            if "model_duration_s" in it:
                totals["model_calls"] += 1
            for k, v in (it.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    totals[k] = round(totals.get(k, 0) + v, 6)
        self.manifest["usage_total"] = totals
        self.manifest["files"] = sorted(
            p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self._flush()
        index = self.root.parent / "index.jsonl"
        m = self.manifest
        line = {k: m.get(k) for k in ("run_id", "started_at", "status", "mode", "backend", "model",
                                       "spec_text", "iterations_used", "duration_s")}
        line["path"] = self.root.name
        with open(index, "a", encoding="utf-8") as f:
            f.write(_redact(json.dumps(line, ensure_ascii=False)) + "\n")


def _stem(call: int) -> str:
    return "" if call == 1 else f"call{call}_"


def _profile_dict(name: str) -> dict | None:
    try:
        from spice_agent.circuit import get_profile
        return asdict(get_profile(name))
    except Exception:
        return None
