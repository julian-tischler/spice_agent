"""
`python -m spice_agent.circuit` - what this layer does, in two parts.

First the parasitic model: predicted self-resonance, DCR and loss resistance
per profile. The SRF column is the one to check against a real inductor's
datasheet, since it is a single unambiguous number that validates the
winding-capacitance guess on its own.

Then a compiled netlist, ideal and as-built side by side, which is the
quickest way to see what the expansion actually emits.
"""

from spice_agent.circuit import PROFILES, Circuit
from spice_agent.circuit.parasitics import BREADBOARD, SMD_PCB

SIZES = ((100e-6, "100u"), (1e-3, "1m"), (10e-3, "10m"), (100e-3, "100m"))

print("Predicted inductor self-resonance by profile")
print("(compare against any real inductor's datasheet SRF)\n")
header = f"{'L':>10} " + "".join(f"{p.name:>16}" for p in PROFILES.values())
print(header)
print("-" * len(header))
for henries, label in SIZES:
    row = f"{label:>10} "
    for profile in PROFILES.values():
        srf = profile.inductor_srf_hz(henries)
        row += f"{'-' if srf is None else f'{srf / 1e3:,.0f} kHz':>16}"
    print(row)

print("\nDCR (same law for both profiles) and parallel loss resistance Rp,")
print("which is also the height of the self-resonant impedance peak:\n")
print(f"{'L':>10} {'DCR':>11} {'Rp smd (Q=%g)' % SMD_PCB.inductor_q_at_srf:>16}"
      f" {'Rp bread (Q=%g)' % BREADBOARD.inductor_q_at_srf:>17}")
print("-" * 57)
for henries, label in SIZES:
    print(f"{label:>10} {SMD_PCB.inductor_dcr(henries):8.2f} ohm "
          f"{SMD_PCB.inductor_rp(henries) / 1e3:14.0f}k "
          f"{BREADBOARD.inductor_rp(henries) / 1e3:16.0f}k")

print("\nProfile descriptions as the design prompt would see them:\n")
for profile in PROFILES.values():
    print(f"[{profile.name}] {profile.describe()}\n")

EXAMPLE = {
    "title": "RC Low-Pass Filter",
    "components": [
        {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
        {"id": "L1", "type": "inductor", "nodes": ["in", "out"], "value": "10m"},
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
    ],
    "analysis": {"type": "ac", "sweep": "dec", "points": 100,
                 "fstart": "1", "fstop": "100meg", "output_node": "out"},
}

circuit = Circuit.from_dict(EXAMPLE)
for technology in ("ideal", "breadboard"):
    print(f"===== netlist: {technology} =====")
    print(circuit.to_netlist(output_path="output.txt", technology=technology))
