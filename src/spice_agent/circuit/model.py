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

# The world around the filter, inserted by the harness when the spec asks
# for a source / load resistance (see DesignSpec.source_ohm / load_ohm).
# Reserved so a designed circuit can never use - or quietly cancel - them.
SOURCE_ID = "R_SOURCE"
LOAD_ID = "R_LOAD"
SOURCE_NODE = "src_ext"
EXTERNAL_IDS = frozenset({SOURCE_ID, LOAD_ID})

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
        # in radians - converted to degrees when parsed). Unlike v(node), which is complex during AC analysis,
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
    # True once terminated() has added the harness's source/load resistors;
    # only then may the reserved ids and node appear.
    terminations_applied: bool = False

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

        if not self.terminations_applied:
            reserved = sorted({i for i in ids if i.upper() in EXTERNAL_IDS})
            if reserved:
                raise ValueError(
                    f"Component id(s) {reserved} are reserved for the source/load "
                    f"resistors the harness adds; use other ids.")
            if any(SOURCE_NODE in c.nodes for c in self.components):
                raise ValueError(
                    f"Node '{SOURCE_NODE}' is reserved for the harness's source "
                    f"resistance; use another node name.")

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
        """The non-ground terminal of the source — the circuit's input.
        With a source resistance applied, the board's input is the far side
        of R_SOURCE, not the source terminal itself."""
        for c in self.components:
            if c.id == SOURCE_ID:
                return next(n for n in c.nodes if n != SOURCE_NODE)
        for c in self.components:
            if c.type in ("voltage_source", "current_source"):
                non_ground = [n for n in c.nodes if n != GROUND]
                if non_ground:
                    return non_ground[0]
        raise ValueError("Circuit has no source with a non-ground terminal.")

    def terminated(self, source_ohm: float = 0.0, load_ohm: float | None = None) -> "Circuit":
        """
        This circuit placed between the source and load resistances the spec
        asks for. Returns self unchanged when there are none, so an
        unterminated spec simulates exactly the circuit the model wrote.

        V1's input terminal moves to SOURCE_NODE and R_SOURCE joins it to the
        original input node; R_LOAD runs from the output node to ground.
        """
        if source_ohm <= 0 and load_ohm is None:
            return self
        self.validate()
        board_in = self.input_node()
        components = []
        for c in self.components:
            if source_ohm > 0 and c.type in ("voltage_source", "current_source"):
                c = Component(c.id, c.type,
                              [SOURCE_NODE if n == board_in else n for n in c.nodes], c.value)
            components.append(c)
        if source_ohm > 0:
            components.append(Component(SOURCE_ID, "resistor", [SOURCE_NODE, board_in],
                                        f"{source_ohm:g}"))
        if load_ohm is not None:
            components.append(Component(LOAD_ID, "resistor",
                                        [self.analysis.output_node, GROUND], f"{load_ohm:g}"))
        return Circuit(title=self.title, components=components, analysis=self.analysis,
                       terminations_applied=True)

    def to_netlist(self, output_path: str, technology=None, *, source_ohm: float = 0.0,
                   load_ohm: float | None = None, ideal_ids=()) -> str:
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
        circuit = self.terminated(source_ohm, load_ohm)

        # The title is free text from the model ("fc ≈ 9.95 kHz"). SPICE
        # netlists are ASCII and the file is written in the platform's
        # default encoding, so non-ASCII would crash the write on Windows.
        lines = [self.title.encode("ascii", "replace").decode("ascii")]
        if circuit is not self:
            lines.append(f"* harness terminations (ideal, not part of the design): "
                         f"source {source_ohm:g} ohm, load "
                         f"{'none' if load_ohm is None else f'{load_ohm:g} ohm'}")
        if profile.is_ideal:
            lines += [c.to_spice_line() for c in circuit.components]
        else:
            lines.append(f"* parasitics: {profile.name} - {profile.description}")
            if ideal_ids:
                lines.append(f"* kept ideal for diagnosis: {', '.join(sorted(ideal_ids))}")
            lines += expand_with_parasitics(
                circuit.components, profile,
                input_node=circuit.input_node(),
                output_node=self.analysis.output_node,
                ideal_ids=set(ideal_ids) | EXTERNAL_IDS,
            )
        lines += self.analysis.to_spice_lines(output_path)
        lines.append(".end")
        return "\n".join(lines) + "\n"
