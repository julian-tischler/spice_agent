"""
The circuit model: components, the analysis to run, and the circuit
that holds them.

This is the deterministic compiler layer. The LLM produces a JSON
description; Circuit.from_dict() validates it and to_netlist() turns it
into ngspice text. No LLM logic and no simulator knowledge belongs here,
which is what keeps this layer testable on its own.
"""

import os
from dataclasses import dataclass, field
from typing import Literal


GROUND = "0"

# The world around the filter, inserted by the harness when the spec asks
# for a source / load resistance (see DesignSpec.source_ohm / load_ohm).
# Reserved so a designed circuit can never use - or quietly cancel - them.
SOURCE_ID = "R_SOURCE"
LOAD_ID = "R_LOAD"
SOURCE_NODE = "src_ext"
# Supply rails, likewise owned by the harness (DesignSpec.supply_pos_v /
# supply_neg_v). The designed circuit connects to the rail NODES; it may
# not drive them.
SUPPLY_POS_ID = "V_SUPPLY_POS"
SUPPLY_NEG_ID = "V_SUPPLY_NEG"
VCC = "vcc"
VEE = "vee"
EXTERNAL_IDS = frozenset({SOURCE_ID, LOAD_ID, SUPPLY_POS_ID, SUPPLY_NEG_ID})

COMPONENT_PREFIXES = {
    "resistor": "R",
    "capacitor": "C",
    "inductor": "L",
    "voltage_source": "V",
    "current_source": "I",
    "opamp": "X",
}

# Parts with more than two terminals: named pins instead of a node pair,
# and a `part` from the model library instead of a value. The id may use
# the schematic convention 'U1'; it is emitted as the SPICE instance 'XU1'.
MULTI_PIN = {"opamp"}
ID_PREFIXES = {"opamp": ("X", "U")}


@dataclass
class Component:
    id: str                   # e.g. "R1" — must start with the correct prefix
    type: str                 # one of COMPONENT_PREFIXES keys
    nodes: list[str] = field(default_factory=list)  # ["in", "out"]; "0" is ground
    value: str = ""           # e.g. "1k", "100n", "AC 1"
    # Multi-pin parts only: the library part, and {pin name: node}. `nodes`
    # is then derived from `pins` in the model's pin order, so everything
    # that walks nodes keeps working.
    part: str | None = None
    pins: dict | None = None

    def __post_init__(self):
        if self.type not in COMPONENT_PREFIXES:
            raise ValueError(
                f"Unsupported component type '{self.type}'. "
                f"Supported: {list(COMPONENT_PREFIXES)}"
            )
        prefixes = ID_PREFIXES.get(self.type, (COMPONENT_PREFIXES[self.type],))
        if not self.id.upper().startswith(prefixes):
            raise ValueError(
                f"Component id '{self.id}' should start with "
                f"{' or '.join(repr(p) for p in prefixes)} for type '{self.type}'"
            )
        if self.type in MULTI_PIN:
            self._init_pins()
            return
        if self.pins or self.part:
            raise ValueError(f"Component '{self.id}' ({self.type}) takes 'nodes' and "
                             f"'value', not 'pins'/'part'")
        if len(self.nodes) != 2:
            raise ValueError(
                f"Component '{self.id}' must have exactly 2 nodes, "
                f"got {self.nodes}"
            )

    def _init_pins(self) -> None:
        from spice_agent.models import OPAMP_PINS, get_opamp

        if not self.part:
            raise ValueError(f"Component '{self.id}' ({self.type}) needs a 'part' "
                             f"from the model library")
        get_opamp(self.part)                         # raises on an unknown part
        pins = self.pins or {}
        missing = [p for p in OPAMP_PINS if p not in pins]
        extra = [p for p in pins if p not in OPAMP_PINS]
        if missing or extra:
            raise ValueError(
                f"Component '{self.id}' ({self.type}) needs exactly the pins "
                f"{list(OPAMP_PINS)}"
                + (f"; missing {missing}" if missing else "")
                + (f"; unknown {extra}" if extra else ""))
        self.nodes = [str(pins[p]) for p in OPAMP_PINS]
        self.value = self.part

    @property
    def spice_name(self) -> str:
        if self.type in MULTI_PIN and not self.id.upper().startswith("X"):
            return "X" + self.id
        return self.id

    def to_spice_line(self) -> str:
        if self.type in MULTI_PIN:
            return f"{self.spice_name} {' '.join(self.nodes)} {self.part}"
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

    def to_spice_lines(self, output_path: str, op_path: str | None = None) -> list[str]:
        """The .control block. With `op_path`, the DC operating point is
        solved first and written there as an ASCII raw file - needed for
        active parts, whose AC result means nothing if they are not
        biased correctly."""
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
        op = (["set filetype=ascii", "op", f"write {op_path} all"] if op_path else [])
        return [
            ".control",
            *op,
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
                    f"resistors and supply rails the harness adds; use other ids.")
            for c in self.components:
                if (c.type in ("voltage_source", "current_source")
                        and {VCC, VEE} & set(c.nodes)):
                    raise ValueError(
                        f"Source '{c.id}' drives a supply rail ({VCC}/{VEE}); the rails "
                        f"are provided by the harness - connect parts to the nodes "
                        f"'{VCC}' and '{VEE}' instead of adding a supply.")
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

    @property
    def active_parts(self) -> list[Component]:
        return [c for c in self.components if c.type in MULTI_PIN]

    def terminated(self, source_ohm: float = 0.0, load_ohm: float | None = None,
                   supply_pos: float | None = None,
                   supply_neg: float | None = None) -> "Circuit":
        """
        This circuit placed in the world the spec describes: between its
        source and load resistances, and with its supply rails. Returns self
        unchanged when there are none, so an unterminated, unpowered spec
        simulates exactly the circuit the model wrote.

        V1's input terminal moves to SOURCE_NODE and R_SOURCE joins it to the
        original input node; R_LOAD runs from the output node to ground;
        V_SUPPLY_POS / V_SUPPLY_NEG hold the nodes vcc / vee at the rails.
        """
        powered = supply_pos is not None
        if source_ohm <= 0 and load_ohm is None and not powered:
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
        if powered:
            components.append(Component(SUPPLY_POS_ID, "voltage_source", [VCC, GROUND],
                                        f"DC {supply_pos:g}"))
            components.append(Component(SUPPLY_NEG_ID, "voltage_source", [VEE, GROUND],
                                        f"DC {supply_neg:g}"))
        return Circuit(title=self.title, components=components, analysis=self.analysis,
                       terminations_applied=True)

    def to_netlist(self, output_path: str, technology=None, *, source_ohm: float = 0.0,
                   load_ohm: float | None = None, supply_pos: float | None = None,
                   supply_neg: float | None = None, ideal_ids=()) -> str:
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

        from spice_agent.models import IDEAL_OPAMP, get_opamp

        self.validate()
        profile = get_profile(technology)
        if self.active_parts and supply_pos is None:
            raise ValueError(
                f"{', '.join(c.id for c in self.active_parts)}: active parts need supply "
                f"rails, and this spec provides none - design with passive parts only.")
        # An op-amp's chosen grade plays the role of a passive's value; the
        # technology decides its physics. Ideal technology simulates every
        # op-amp as the ideal one (as every inductor is then perfect); the
        # diagnosis does the same for single parts via ideal_ids.
        swapped = [Component(c.id, c.type, part=IDEAL_OPAMP.name, pins=dict(c.pins))
                   if c.type in MULTI_PIN and (profile.is_ideal or c.id in ideal_ids) else c
                   for c in self.components]
        base = Circuit(self.title, swapped, self.analysis, self.terminations_applied)
        circuit = base.terminated(source_ohm, load_ohm, supply_pos, supply_neg)

        # The title is free text from the model ("fc ≈ 9.95 kHz"). SPICE
        # netlists are ASCII and the file is written in the platform's
        # default encoding, so non-ASCII would crash the write on Windows.
        lines = [self.title.encode("ascii", "replace").decode("ascii")]
        if source_ohm > 0 or load_ohm is not None:
            lines.append(f"* harness terminations (ideal, not part of the design): "
                         f"source {source_ohm:g} ohm, load "
                         f"{'none' if load_ohm is None else f'{load_ohm:g} ohm'}")
        if supply_pos is not None:
            lines.append(f"* harness supply rails (ideal): {VCC} = {supply_pos:g} V, "
                         f"{VEE} = {supply_neg:g} V")
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
        for part in dict.fromkeys(c.part for c in circuit.components if c.type in MULTI_PIN):
            lines.append(get_opamp(part).subckt())
        op_path = op_path_for(output_path) if circuit.active_parts else None
        lines += self.analysis.to_spice_lines(output_path, op_path)
        lines.append(".end")
        return "\n".join(lines) + "\n"


def op_path_for(output_path: str) -> str:
    """Where the operating point of a run writing `output_path` goes."""
    stem, _ = os.path.splitext(output_path)
    return stem + "_op.raw"
