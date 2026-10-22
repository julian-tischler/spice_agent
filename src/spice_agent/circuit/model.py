"""
The circuit model: components, the analysis to run, and the circuit
that holds them.

This is the deterministic compiler layer. The LLM produces a JSON
description; Circuit.from_dict() validates it and to_netlist() turns it
into ngspice text. No LLM logic and no simulator knowledge belongs here,
which is what keeps this layer testable on its own.
"""

from dataclasses import dataclass, field
from typing import Literal


GROUND = "0"

COMPONENT_PREFIXES = {
    "resistor": "R",
    "capacitor": "C",
    "inductor": "L",
    "voltage_source": "V",
    "current_source": "I",
}


@dataclass
class Component:
    id: str                   # e.g. "R1" — must start with the correct prefix
    type: str                 # one of COMPONENT_PREFIXES keys
    nodes: list[str]          # e.g. ["in", "out"] — node "0" is always ground
    value: str                # e.g. "1k", "100n", "AC 1"

    def __post_init__(self):
        if self.type not in COMPONENT_PREFIXES:
            raise ValueError(
                f"Unsupported component type '{self.type}'. "
                f"Supported: {list(COMPONENT_PREFIXES)}"
            )
        expected_prefix = COMPONENT_PREFIXES[self.type]
        if not self.id.upper().startswith(expected_prefix):
            raise ValueError(
                f"Component id '{self.id}' should start with "
                f"'{expected_prefix}' for type '{self.type}'"
            )
        if len(self.nodes) != 2:
            raise ValueError(
                f"Component '{self.id}' must have exactly 2 nodes, "
                f"got {self.nodes}"
            )

    def to_spice_line(self) -> str:
        n1, n2 = self.nodes
        return f"{self.id} {n1} {n2} {self.value}"


@dataclass
class AnalysisSpec:
    type: Literal["ac", "dc", "tran"] = "ac"
    # AC sweep params
    sweep: str = "dec"          # dec | oct | lin
    points: int = 100
    fstart: str = "1"
    fstop: str = "1meg"
    # Output node to probe. Gain is computed as vdb(output_node), which
    # is only directly the transfer-function gain if the AC source has
    # unity magnitude (e.g. "AC 1") — Circuit.validate() enforces this.
    output_node: str = "out"

    def to_spice_lines(self, output_path: str) -> list[str]:
        if self.type != "ac":
            raise NotImplementedError(
                f"Analysis type '{self.type}' not yet supported by the "
                "generator — only 'ac' is implemented in v1."
            )
        # vdb()/vp() are real-valued pseudo-vectors (dB magnitude, phase
        # in degrees). Unlike v(node), which is complex during AC analysis,
        # these don't trigger wrdata's "repeat the x-column per vector"
        # behavior for complex data — so the output file has a single
        # shared frequency column, which is much simpler and more robust
        # to parse.
        return [
            ".control",
            f"ac {self.sweep} {self.points} {self.fstart} {self.fstop}",
            f"wrdata {output_path} vdb({self.output_node}) vp({self.output_node})",
            ".endc",
        ]


@dataclass
class Circuit:
    title: str
    components: list[Component]
    analysis: AnalysisSpec

    @classmethod
    def from_dict(cls, data: dict) -> "Circuit":
        """Build a Circuit from the JSON schema the LLM produces."""
        components = [Component(**c) for c in data["components"]]
        analysis = AnalysisSpec(**data.get("analysis", {}))
        title = data.get("title", "LLM-generated circuit")
        return cls(title=title, components=components, analysis=analysis)

    def validate(self) -> None:
        """Basic sanity checks before compiling. Raises ValueError on failure."""
        if not self.components:
            raise ValueError("Circuit has no components.")

        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate component ids found: {ids}")

        all_nodes = {n for c in self.components for n in c.nodes}
        if "0" not in all_nodes:
            raise ValueError(
                "Circuit has no ground node ('0'). SPICE requires every "
                "circuit to reference node 0 as ground."
            )

        sources = [c for c in self.components if c.type in ("voltage_source", "current_source")]
        if not sources:
            raise ValueError(
                "Circuit has no independent source — AC analysis needs at "
                "least one source with an AC magnitude (e.g. 'AC 1')."
            )

        if self.analysis.type == "ac":
            ac_sources = [s for s in sources if "AC" in s.value.upper()]
            if not ac_sources:
                raise ValueError(
                    "AC analysis requires a source with an AC magnitude, "
                    "e.g. value='AC 1'. None of the sources declare one."
                )
            # gain is read directly as vdb(output_node), which is only
            # correct if the AC source magnitude is 1 (unity) — anything
            # else needs to be subtracted out, which we don't do (yet).
            for s in ac_sources:
                magnitude_str = s.value.upper().split("AC", 1)[1].strip().split()[0]
                try:
                    magnitude = float(magnitude_str)
                except ValueError:
                    raise ValueError(
                        f"Could not parse AC magnitude from source "
                        f"'{s.id}' value '{s.value}'."
                    )
                if abs(magnitude - 1.0) > 1e-9:
                    raise ValueError(
                        f"Source '{s.id}' has AC magnitude {magnitude}, but "
                        "gain is computed as vdb(output_node) which assumes "
                        "unity (AC 1) source magnitude. Use 'AC 1' or update "
                        "the gain calculation to normalize by source magnitude."
                    )

            output_nodes = {n for c in self.components for n in c.nodes}
            if self.analysis.output_node not in output_nodes:
                raise ValueError(
                    f"analysis.output_node '{self.analysis.output_node}' "
                    f"is not a node used by any component. Available nodes: "
                    f"{sorted(output_nodes)}"
                )

    def input_node(self) -> str:
        """The non-ground terminal of the source — the circuit's input."""
        for c in self.components:
            if c.type in ("voltage_source", "current_source"):
                non_ground = [n for n in c.nodes if n != GROUND]
                if non_ground:
                    return non_ground[0]
        raise ValueError("Circuit has no source with a non-ground terminal.")

    def to_netlist(self, output_path: str, technology=None) -> str:
        """
        Compile this circuit into a full ngspice netlist string.

        `technology` is a parasitics.TechnologyProfile or its name. The
        default (None / "ideal") emits the components exactly as given.
        Any other profile expands each ideal component into its parasitic
        equivalent circuit - see expansion.expand_with_parasitics().

        Expansion deliberately happens ONLY here, at the last possible
        moment. Everything upstream (validation, the JSON schema the model
        fills in, the LTspice exporter's layout logic) keeps working on the
        ideal circuit, which is what the designer actually chose.
        """
        # Imported here rather than at module scope: expansion needs GROUND
        # from this module, so a top-level import would be circular. This is
        # the only direction the cycle can be broken without moving GROUND
        # somewhere less obvious.
        from spice_agent.circuit.expansion import expand_with_parasitics
        from spice_agent.circuit.parasitics import get_profile

        self.validate()
        profile = get_profile(technology)

        lines = [self.title]
        if profile.is_ideal:
            lines += [c.to_spice_line() for c in self.components]
        else:
            lines.append(f"* parasitics: {profile.name} - {profile.description}")
            lines += expand_with_parasitics(
                self.components, profile,
                input_node=self.input_node(),
                output_node=self.analysis.output_node,
            )
        lines += self.analysis.to_spice_lines(output_path)
        lines.append(".end")
        return "\n".join(lines) + "\n"
