"""
Turning an ideal component into its parasitic equivalent circuit.

Called only from Circuit.to_netlist(), at the last possible moment, so
that everything upstream - validation, the JSON schema the model fills
in, the exporter's layout logic - keeps working on the ideal circuit the
designer actually chose.
"""

from spice_agent.circuit.model import GROUND
from spice_agent.units import parse_spice_value


def expand_with_parasitics(components, profile, input_node: str,
                           output_node: str) -> list[str]:
    """
    Turn a list of ideal Components into SPICE lines including parasitics.

    Each component becomes a small subnetwork. Internal nodes are named
    "<id>_<letter>" and extra elements "<prefix>_<id>_<what>", so they can
    never collide with the node names the designer chose.

    Equivalent circuits used:

      inductor    four elements: the series DCR, then the ideal L in
                  parallel with its winding capacitance Cp and a loss
                  resistance Rp. Cp sets the self-resonant frequency, above
                  which the part stops behaving as an inductor at all; Rp
                  sets how sharp that resonance is, and without it DCR is
                  the only damping and the peak is unphysically high.
      capacitor   ideal C in series with ESL and ESR.
      resistor    ideal R in series with its lead inductance, with a small
                  shunt capacitance across it.
      sources     left ideal — a source's own parasitics are not part of
                  the circuit being designed.

    Then, per technology:

      every terminal   series lead/trace inductance and contact resistance,
                       the lumped stand-in for a layout we do not have
      every real node  stray capacitance to ground (meaningful where there
                       is a ground plane)
      input -> output  direct coupling capacitance. With no ground plane
                       this is a path AROUND the filter and it bottoms out
                       the stopband regardless of filter order.

    Stated simplification: terminals connected to ground get no lead
    inductance or contact resistance. Modelling those would give every
    component its own local ground and so model ground bounce, which is
    finer-grained than the rest of this model justifies. The effect is
    that ground returns are treated as perfect.
    """
    lines: list[str] = []
    real_nodes: set[str] = set()

    def terminal(comp_id: str, node: str, tag: str) -> str:
        """Insert this terminal's lead inductance and contact resistance,
        returning the node the component itself should connect to."""
        real_nodes.add(node)
        if node == GROUND:
            return node          # ground is the reference; do not float it
        inner = node
        if profile.interconnect_l_per_terminal:
            nxt = f"{comp_id}_{tag}l"
            lines.append(f"L_{comp_id}_{tag} {inner} {nxt} "
                         f"{profile.interconnect_l_per_terminal:.6e}")
            inner = nxt
        if profile.contact_resistance:
            nxt = f"{comp_id}_{tag}c"
            lines.append(f"R_{comp_id}_{tag} {inner} {nxt} "
                         f"{profile.contact_resistance:.6e}")
            inner = nxt
        return inner

    for comp in components:
        n1, n2 = comp.nodes
        value = comp.value

        if comp.type in ("voltage_source", "current_source"):
            real_nodes.update(comp.nodes)
            lines.append(comp.to_spice_line())
            continue

        a = terminal(comp.id, n1, "a")
        b = terminal(comp.id, n2, "b")
        v = parse_spice_value(value)

        if comp.type == "inductor":
            dcr = profile.inductor_dcr(v)
            rp = profile.inductor_rp(v)
            # R_dcr sits OUTSIDE the parallel block - the wire resistance is
            # in series with the whole winding:
            #
            #     a --[R_dcr]-- <id>_i --+--( L )--+-- b
            #                            +-- C_p --+
            #                            +-- R_p --+
            #
            # Putting R_dcr inside instead (C_p across L+R_dcr) is also a
            # standard drawing and measures identically to four significant
            # figures up to 10 MHz; the two only diverge above the frequency
            # where 1/(omega*Cp) drops below R_dcr, by which point a real
            # winding is distributed and neither lumped form means much.
            tank = f"{comp.id}_i" if dcr else a
            if dcr:
                lines.append(f"R_{comp.id}_dcr {a} {tank} {dcr:.6e}")
            lines.append(f"{comp.id} {tank} {b} {value}")
            if profile.inductor_winding_c:
                lines.append(f"C_{comp.id}_wnd {tank} {b} "
                             f"{profile.inductor_winding_c:.6e}")
            if rp:
                lines.append(f"R_{comp.id}_par {tank} {b} {rp:.6e}")

        elif comp.type == "capacitor":
            node = a
            if profile.capacitor_esl:
                nxt = f"{comp.id}_i"
                lines.append(f"{comp.id} {node} {nxt} {value}")
                node = nxt
                tail = f"{comp.id}_j" if profile.capacitor_esr else b
                lines.append(f"L_{comp.id}_esl {node} {tail} "
                             f"{profile.capacitor_esl:.6e}")
                node = tail
            else:
                tail = f"{comp.id}_j" if profile.capacitor_esr else b
                lines.append(f"{comp.id} {node} {tail} {value}")
                node = tail
            if profile.capacitor_esr:
                lines.append(f"R_{comp.id}_esr {node} {b} "
                             f"{profile.capacitor_esr:.6e}")

        elif comp.type == "resistor":
            mid = f"{comp.id}_i" if profile.resistor_series_l else b
            lines.append(f"{comp.id} {a} {mid} {value}")
            if profile.resistor_series_l:
                lines.append(f"L_{comp.id}_ser {mid} {b} "
                             f"{profile.resistor_series_l:.6e}")
            if profile.resistor_shunt_c:
                lines.append(f"C_{comp.id}_shn {a} {b} "
                             f"{profile.resistor_shunt_c:.6e}")

        else:  # pragma: no cover - COMPONENT_PREFIXES is the gate
            raise ValueError(f"No parasitic model for component type {comp.type!r}")

    # Stray capacitance from each real node to ground.
    if profile.interconnect_node_c:
        for i, node in enumerate(sorted(real_nodes - {GROUND})):
            lines.append(f"C_stray{i}_{node} {node} {GROUND} "
                         f"{profile.interconnect_node_c:.6e}")

    # The path around the filter. Last, because it is the one element that
    # can defeat an otherwise correct design.
    if profile.io_coupling_c and input_node != output_node:
        lines.append(f"C_io_coupling {input_node} {output_node} "
                     f"{profile.io_coupling_c:.6e}")

    return lines
