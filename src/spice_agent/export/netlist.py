"""
Writing a circuit as a SPICE netlist both simulators will run.

LTspice needs only the `.ac` directive and plots interactively; ngspice's
batch mode refuses a netlist with no `.print`/`.plot`/`.fourier` line. A
`.print ac` satisfies ngspice and is ignored by LTspice, so one file
serves both.

as_built_circuit() is the counterpart to the parasitic expansion: it
rebuilds the EXPANDED circuit as real Components, so the exporter can
show what was actually simulated rather than the idealised design.
"""

from __future__ import annotations

from spice_agent.circuit import (
    COMPONENT_PREFIXES,
    EXTERNAL_IDS,
    Circuit,
    Component,
    expand_with_parasitics,
    get_profile,
)


def analysis_directive(circuit: Circuit) -> str:
    a = circuit.analysis
    if a.type != "ac":
        raise NotImplementedError("Only AC analysis is supported for LTspice export.")
    return f".ac {a.sweep} {a.points} {a.fstart} {a.fstop}"


def as_built_circuit(circuit: Circuit, technology) -> Circuit:
    """
    The circuit as it was actually SIMULATED, parasitics included.

    Returns a new Circuit whose components are the expanded ones, so the
    whole exporter (netlist and schematic alike) can render what ngspice
    really saw rather than the idealised design. Reconstructed by parsing
    the expansion's own output lines back into Components, which is safe
    because every element it emits is a two-terminal R, L, C or source.

    An ideal profile returns the circuit unchanged.
    """
    profile = get_profile(technology)
    if profile.is_ideal:
        return circuit

    circuit.validate()
    lines = expand_with_parasitics(
        circuit.components, profile,
        input_node=circuit.input_node(),
        output_node=circuit.analysis.output_node,
        ideal_ids=EXTERNAL_IDS,
    )
    by_prefix = {v: k for k, v in COMPONENT_PREFIXES.items()}

    components = []
    for line in lines:
        if line.startswith("*"):
            continue
        ident, n1, n2, *value = line.split()
        components.append(Component(
            id=ident,
            type=by_prefix[ident[0].upper()],
            nodes=[n1, n2],
            value=" ".join(value),
        ))
    return Circuit(title=f"{circuit.title} (as built: {profile.name})",
                   components=components, analysis=circuit.analysis,
                   terminations_applied=circuit.terminations_applied)


def to_ltspice_netlist(circuit: Circuit, include_print: bool = True,
                       technology=None) -> str:
    """
    Portable .cir text, runnable in BOTH simulators. Validates first.

    LTspice needs only the `.ac` directive and plots interactively, but
    ngspice's batch mode refuses a netlist with no `.print`, `.plot` or
    `.fourier` line ("incomplete or empty netlist ... no simulations
    run!"). Emitting a `.print ac` line satisfies ngspice and is ignored
    harmlessly by LTspice, so one file serves both. Pass
    include_print=False for a minimal LTspice-only netlist.

    `technology` controls which circuit is written: the default None/ideal
    writes the design as chosen, any other profile writes the as-built
    version including parasitics - i.e. exactly what got simulated.
    """
    circuit = as_built_circuit(circuit, technology)
    circuit.validate()
    lines = [circuit.title]
    lines += [c.to_spice_line() for c in circuit.components]
    lines.append(analysis_directive(circuit))
    if include_print:
        node = circuit.analysis.output_node
        lines.append(f".print ac vdb({node}) vp({node})")
    lines.append(".end")
    return "\n".join(lines) + "\n"
