"""
Hand-written reference netlists with known, analytically checkable
behaviour.

These are NOT produced by the agent. They exist to prove the ngspice
pipeline works and to serve as ground truth for what a correct design
looks like: every template carries the formula for its cutoff or centre
frequency in a comment, and tests/circuit/test_templates.py simulates
each one and checks it against that formula.

Each template is a format string with one {output_path} placeholder.
"""

NETLIST_TEMPLATE_1 = """RC Low-Pass Filter Testx
V1 in 0 AC 1
R1 in out 1k
C1 out 0 100n

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# ---------------------------------------------------------------------
# Higher-order low-pass filters
# ---------------------------------------------------------------------

# 2nd-order series RLC low-pass. Output taken across the capacitor.
# fc = 1 / (2*pi*sqrt(L*C)) = 1 / (2*pi*sqrt(10m * 100n)) ~= 5.03 kHz
# Q  = (1/R) * sqrt(L/C) = (1/470) * sqrt(10m / 100n) ~= 0.67
# R is chosen close to the Butterworth value (Q = 0.707) so the passband
# is flat with no resonant peak, and the rolloff is -40 dB/decade.
NETLIST_TEMPLATE_2 = """RLC 2nd-Order Low-Pass Filter Test
V1 in 0 AC 1
R1 in n1 470
L1 n1 out 10m
C1 out 0 100n

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# 3rd-order Butterworth LC ladder low-pass (pi topology: C-L-C), doubly
# terminated in 50 ohm. Normalized Butterworth g-values: g1=1, g2=2, g3=1.
# Designed for fc = 10 kHz:
#   C = g / (2*pi*fc*R0) = 1 / (2*pi*10k*50)  ~= 318 nF
#   L = g * R0 / (2*pi*fc) = 2*50 / (2*pi*10k) ~= 1.59 mH
# Note: the 50/50 source/load termination halves the output voltage, so
# the passband sits at -6 dB rather than 0 dB. Rolloff is -60 dB/decade.
NETLIST_TEMPLATE_3 = """LC 3rd-Order Butterworth Low-Pass Filter Test
V1 in 0 AC 1
RS in n1 50
C1 n1 0 318n
L1 n1 out 1.59m
C2 out 0 318n
RL out 0 50

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# ---------------------------------------------------------------------
# Other easy-to-test passive circuits
# ---------------------------------------------------------------------

# 1st-order RC high-pass. Same R and C as TEMPLATE_1 but swapped, so the
# corner frequency is identical: fc = 1 / (2*pi*R*C) ~= 1.59 kHz.
# Gain rises at +20 dB/decade below fc and is flat (0 dB) above it.
NETLIST_TEMPLATE_4 = """RC High-Pass Filter Test
V1 in 0 AC 1
C1 in out 100n
R1 out 0 1k

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# 2nd-order RLC band-pass. Series L and C, output taken across R.
# f0 = 1 / (2*pi*sqrt(L*C)) = 1 / (2*pi*sqrt(10m * 100n)) ~= 5.03 kHz
# Q  = (1/R) * sqrt(L/C) = (1/100) * sqrt(10m / 100n) ~= 3.16
# Bandwidth (-3 dB) = f0 / Q ~= 1.59 kHz. Peak gain at f0 is 0 dB.
NETLIST_TEMPLATE_5 = """RLC Band-Pass Filter Test
V1 in 0 AC 1
L1 in n1 10m
C1 n1 out 100n
R1 out 0 100

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# 2nd-order RLC band-stop (notch). Series R, output taken across the
# series LC tank, which is a short at resonance.
# f0 = 1 / (2*pi*sqrt(L*C)) ~= 5.03 kHz  (same L and C as TEMPLATE_5)
# Q  = (1/R) * sqrt(L/C) ~= 3.16, so the notch is fairly narrow.
# Gain is 0 dB far from f0 and drops to a deep null at f0.
NETLIST_TEMPLATE_6 = """RLC Band-Stop (Notch) Filter Test
V1 in 0 AC 1
R1 in out 100
L1 out n1 10m
C1 n1 0 100n

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# Twin-T notch filter (RC only, no inductor). Classic passive notch:
# f0 = 1 / (2*pi*R*C) with R = 1k, C = 100n  ~= 1.59 kHz
# The T-arms use R/2 = 500 and 2C = 200n. Unloaded passive twin-T gives
# a theoretically infinite-depth null at f0, with gain returning to 0 dB
# on both sides. Useful as an RC-only alternative to TEMPLATE_6.
NETLIST_TEMPLATE_7 = """Twin-T Notch Filter Test
V1 in 0 AC 1
* R-C-R arm (low-pass T)
R1 in n1 1k
R2 n1 out 1k
C3 n1 0 200n
* C-R-C arm (high-pass T)
C1 in n2 100n
C2 n2 out 100n
R3 n2 0 500

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# Resistive voltage divider. Frequency-independent, so the AC sweep must
# return a perfectly flat line at v(out) = 0.5 (-6.02 dB) with 0 degrees
# phase at every frequency. Handy as a sanity check that the ngspice
# pipeline, parser and gain calculation are all wired up correctly.
NETLIST_TEMPLATE_8 = """Resistive Voltage Divider Test
V1 in 0 AC 1
R1 in out 1k
R2 out 0 1k

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""

# 3-stage cascaded RC low-pass (3rd order, RC only). Each stage is the
# same 1k/100n pair as TEMPLATE_1. Because the stages load each other,
# the overall -3 dB point is well below the single-stage 1.59 kHz
# (roughly 300-400 Hz), but the asymptotic rolloff is still -60 dB/decade.
# Included as an RC-only 3rd-order contrast to the LC ladder in TEMPLATE_3.
NETLIST_TEMPLATE_9 = """Cascaded RC 3rd-Order Low-Pass Filter Test
V1 in 0 AC 1
R1 in n1 1k
C1 n1 0 100n
R2 n1 n2 1k
C2 n2 0 100n
R3 n2 out 1k
C3 out 0 100n

.control
ac dec 100 1 1meg
wrdata {output_path} v(out) v(in)
.endc

.end
"""


# Lookup table so tests/scripts can iterate over every template by name.
NETLIST_TEMPLATES = {
    "rc_lowpass_1st": NETLIST_TEMPLATE_1,
    "rlc_lowpass_2nd": NETLIST_TEMPLATE_2,
    "lc_butterworth_lowpass_3rd": NETLIST_TEMPLATE_3,
    "rc_highpass_1st": NETLIST_TEMPLATE_4,
    "rlc_bandpass": NETLIST_TEMPLATE_5,
    "rlc_notch": NETLIST_TEMPLATE_6,
    "twin_t_notch": NETLIST_TEMPLATE_7,
    "resistive_divider": NETLIST_TEMPLATE_8,
    "cascaded_rc_lowpass_3rd": NETLIST_TEMPLATE_9,
}
