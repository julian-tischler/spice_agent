"""
The hint catalogue (spice_agent.hints): every piece of advice or
explanation the harness gives the model, documented in one place.

These tests hold the catalogue to the rule in ARCHITECTURE.md - feedback
conveys hard facts only, rules of thumb are always labelled - and make sure
nothing model-facing bypasses it.
"""

import ast
import re
from pathlib import Path

import pytest

from spice_agent.hints import (
    HINTS,
    KINDS,
    RULE_OF_THUMB,
    RULE_OF_THUMB_PREFIX,
    markdown,
    render,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "spice_agent"
SOURCES = {p: p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")
           if p.name != "hints.py"}

# Phrases that claim things about designs nobody simulated. The rule:
# report what was measured, or what the catalogue / spec says - never a
# verdict on the design space.
BANNED = ("cannot help", "will not help", "no choice of", "is at its limit",
          "cannot reach", "impossible", "the cause is the layout")


@pytest.mark.parametrize("hint", HINTS.values(), ids=lambda h: h.id)
def test_every_hint_is_documented(hint):
    assert hint.kind in KINDS
    for name in ("text", "applies_when", "valid_for", "why", "source"):
        assert getattr(hint, name).strip(), f"{hint.id}: empty {name}"


@pytest.mark.parametrize("hint", HINTS.values(), ids=lambda h: h.id)
def test_every_hint_renders_with_its_example(hint):
    """No placeholder is left unfilled (JSON examples in a hint use other braces)."""
    text = hint.render(**hint.example)
    assert text.strip()
    assert not re.search(r"\{[a-z_][a-z0-9_]*\}", text), text


@pytest.mark.parametrize("hint", HINTS.values(), ids=lambda h: h.id)
def test_no_hint_claims_anything_about_untested_designs(hint):
    text = hint.text.lower()
    for phrase in BANNED:
        assert phrase not in text, f"{hint.id}: '{phrase}'"


def test_rules_of_thumb_are_always_labelled():
    rules = [h for h in HINTS.values() if h.kind == RULE_OF_THUMB]
    assert rules
    for h in rules:
        assert h.render(**h.example).startswith(RULE_OF_THUMB_PREFIX)
        assert h.render(capital=True, **h.example).startswith("Rule of thumb: ")
    others = [h for h in HINTS.values() if h.kind != RULE_OF_THUMB]
    for h in others:
        assert not h.render(**h.example).lower().startswith("rule of thumb")


@pytest.mark.parametrize("hint", HINTS.values(), ids=lambda h: h.id)
def test_every_hint_is_used(hint):
    """Referenced by id somewhere in the package (the parasitic groups go
    through diagnosis._advice(key))."""
    used = any(f'"{hint.id}"' in s for s in SOURCES.values())
    if not used and hint.id.startswith("parasitic."):
        key = hint.id.split(".")[1]
        used = any(f'_advice("{key}")' in s for s in SOURCES.values())
    assert used, f"{hint.id} is never rendered"


def test_no_rule_of_thumb_text_outside_the_catalogue():
    """A string literal saying 'rule of thumb' anywhere else means advice that
    bypassed the catalogue (comments and docstrings are fine)."""
    for path, text in SOURCES.items():
        tree = ast.parse(text)
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                assert "rule of thumb" not in node.value.lower(), \
                    f"{path.name}:{node.lineno}: {node.value[:60]!r}"


def test_hints_md_is_up_to_date():
    """HINTS.md is generated - regenerate with `python -m spice_agent.hints`."""
    assert (ROOT / "HINTS.md").read_text(encoding="utf-8") == markdown()


def test_the_notch_hint_says_what_r_really_does():
    """It used to promise 'a smaller R gives a narrower, deeper notch'; with
    lossy parts a smaller R makes it shallower, and a live run that followed
    the old hint lost its notch (2026-11-19)."""
    text = render("prompt.passive.series_rlc_notch")
    assert "narrower, deeper" not in text
    assert "a smaller R narrows it" in text and "shallower" in text


def test_scale_factor_states_where_it_is_exact():
    text = render("verdict.scale_factor", what="cutoff", k="0.5")
    assert "exactly for ideal R, L and C" in text
    assert "without changing its shape" not in text
