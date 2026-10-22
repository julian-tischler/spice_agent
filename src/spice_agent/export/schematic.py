"""
Drawing a circuit as an LTspice schematic (.asc).

Symbol geometry, placement and the file writer are together because they
are inseparable: layout needs pin offsets, and the writer needs layout.

Two strategies. A ladder - a chain of series elements from source to
output with shunt legs to ground, which is every filter the agent
designs - is drawn left to right with real wires. Anything else falls
back to net labels: components in a row, each pin tagged with its node
name. Uglier, always correct, and simulates identically.

.asc geometry (all on a 16-unit grid):
  SYMBOL <name> X Y R<rot>   symbol origin and rotation
  a pin at symbol-local (px, py) lands on the sheet at
      R0:   (X+px, Y+py)      R90:  (X-py, Y+px)
      R180: (X-px, Y-py)      R270: (X+py, Y-px)
  WIRE x1 y1 x2 y2, FLAG x y <net>  ('0' = ground),
  TEXT x y Left 2 !<directive>      (';' instead of '!' = comment)
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from spice_agent.circuit import GROUND, Circuit, Component
from spice_agent.export.netlist import (
    analysis_directive,
    as_built_circuit,
    to_ltspice_netlist,
)


@dataclass(frozen=True)
class Symbol:
    name: str
    pin_a: tuple[int, int]
    pin_b: tuple[int, int]
    # WINDOW lines for horizontal placement so labels sit above/below the
    # body instead of being drawn rotated through it (cosmetic only).
    window_r90: tuple[str, ...] = ()
    window_r270: tuple[str, ...] = ()


SYMBOLS: dict[str, Symbol] = {
    "resistor": Symbol("res", (16, 16), (16, 96),
                       ("WINDOW 0 0 56 VBottom 2", "WINDOW 3 32 56 VTop 2"),
                       ("WINDOW 0 32 56 VTop 2", "WINDOW 3 0 56 VBottom 2")),
    "inductor": Symbol("ind", (16, 16), (16, 96),
                       ("WINDOW 0 5 56 VBottom 2", "WINDOW 3 32 56 VTop 2"),
                       ("WINDOW 0 32 56 VTop 2", "WINDOW 3 5 56 VBottom 2")),
    "capacitor": Symbol("cap", (16, 0), (16, 64),
                        ("WINDOW 0 0 32 VBottom 2", "WINDOW 3 32 32 VTop 2"),
                        ("WINDOW 0 32 32 VTop 2", "WINDOW 3 0 32 VBottom 2")),
    "voltage_source": Symbol("voltage", (0, 16), (0, 96)),
    "current_source": Symbol("current", (0, 0), (0, 80)),
}

GROUND = "0"


def _rotate(px: int, py: int, rot: int) -> tuple[int, int]:
    if rot == 0:
        return px, py
    if rot == 90:
        return -py, px
    if rot == 180:
        return -px, -py
    if rot == 270:
        return py, -px
    raise ValueError(rot)


def pin_positions(sym: Symbol, x: int, y: int, rot: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """Sheet coordinates of (pin A, pin B) for a symbol placed at (x, y)."""
    ax, ay = _rotate(*sym.pin_a, rot)
    bx, by = _rotate(*sym.pin_b, rot)
    return (x + ax, y + ay), (x + bx, y + by)


# ---------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------

@dataclass
class Placed:
    comp: Component
    sym: Symbol
    x: int
    y: int
    rot: int

    @property
    def pins(self):
        return pin_positions(self.sym, self.x, self.y, self.rot)


@dataclass
class Layout:
    strategy: str                                   # "ladder" | "netlabel"
    placed: list[Placed] = field(default_factory=list)
    wires: list[tuple[int, int, int, int]] = field(default_factory=list)
    flags: list[tuple[int, int, str]] = field(default_factory=list)

    def wire(self, x1, y1, x2, y2):
        if (x1, y1) != (x2, y2):
            self.wires.append((x1, y1, x2, y2))

    def flag(self, x, y, net):
        if (x, y, net) not in self.flags:
            self.flags.append((x, y, net))


COL_PITCH = 128     # horizontal distance between ladder nodes
RAIL_Y = 96         # y of the signal rail
LEFT_X = 96         # x of the source column
STUB = 16           # wire stub length in netlabel mode


def _source(circuit: Circuit) -> Component:
    srcs = [c for c in circuit.components if c.type in ("voltage_source", "current_source")]
    if len(srcs) != 1:
        raise ValueError("LTspice export needs exactly one source")
    return srcs[0]


def _find_ladder(circuit: Circuit):
    """
    Try to interpret the circuit as: source between `in` and ground, a
    series path in -> ... -> out, and from each path node an optional
    vertical chain of components ending at ground.

    Returns (path_nodes, series_comps, shunt_chains) or None.
    series_comps[i] joins path_nodes[i] and path_nodes[i+1];
    shunt_chains[node] is the ordered list of components from that node
    down to ground.
    """
    src = _source(circuit)
    if GROUND not in src.nodes:
        return None
    n_in = src.nodes[0] if src.nodes[1] == GROUND else src.nodes[1]
    n_out = circuit.analysis.output_node
    passives = [c for c in circuit.components if c is not src]

    adj: dict[str, list[Component]] = defaultdict(list)
    for c in passives:
        for n in c.nodes:
            adj[n].append(c)

    # BFS for the shortest path in -> out that never passes through ground
    prev: dict[str, tuple[str, Component] | None] = {n_in: None}
    queue = [n_in]
    while queue and n_out not in prev:
        n = queue.pop(0)
        for c in adj[n]:
            other = c.nodes[1] if c.nodes[0] == n else c.nodes[0]
            if other == GROUND or other in prev:
                continue
            prev[other] = (n, c)
            queue.append(other)
    if n_out not in prev:
        return None

    path_nodes, series = [n_out], []
    while prev[path_nodes[-1]] is not None:
        n, c = prev[path_nodes[-1]]
        series.append(c)
        path_nodes.append(n)
    path_nodes.reverse()
    series.reverse()

    # Everything else must form linear chains from a path node to ground
    used = set(id(c) for c in series)
    chains: dict[str, list[Component]] = {}
    for n in path_nodes:
        chain = []
        cur = n
        while True:
            nxt = [c for c in adj[cur] if id(c) not in used]
            if not nxt:
                break
            if cur != n and len(nxt) != 1:
                return None            # branch inside a stub -> not a ladder
            if cur == n and len(nxt) > 1:
                return None            # two stubs on one node -> not a ladder
            c = nxt[0]
            used.add(id(c))
            chain.append(c)
            cur = c.nodes[1] if c.nodes[0] == cur else c.nodes[0]
            if cur == GROUND:
                break
            if cur in path_nodes:
                return None            # loops back onto the ladder
        if chain and cur != GROUND:
            return None                # dangling stub
        if chain:
            chains[n] = chain
    if len(used) != len(passives):
        return None
    return path_nodes, series, chains


def _place_horizontal(layout: Layout, comp: Component, x_left: int, x_right: int, y: int,
                      left_node: str):
    """Series element between two rail points, centred in the span."""
    sym = SYMBOLS[comp.type]
    # R270 puts pin A on the left, R90 puts pin A on the right.
    rot = 270 if comp.nodes[0] == left_node else 90
    (ax, _), (bx, _) = pin_positions(sym, 0, 0, rot)
    span = abs(ax - bx)
    x_lo = (x_left + x_right - span) // 2
    x_lo -= x_lo % 16
    # origin such that the leftmost pin lands on x_lo and pins are on y
    (ax, ay), (bx, by) = pin_positions(sym, 0, 0, rot)
    x0 = x_lo - min(ax, bx)
    y0 = y - ay
    p = Placed(comp, sym, x0, y0, rot)
    layout.placed.append(p)
    (ax, ay), (bx, by) = p.pins
    left_pin, right_pin = ((ax, ay), (bx, by)) if ax < bx else ((bx, by), (ax, ay))
    layout.wire(x_left, y, *left_pin)
    layout.wire(*right_pin, x_right, y)


def _place_vertical(layout: Layout, comp: Component, x: int, y_top: int, top_node: str) -> int:
    """Element hanging below a rail point. Returns the y of its bottom pin."""
    sym = SYMBOLS[comp.type]
    rot = 0 if comp.nodes[0] == top_node else 180
    (ax, ay), (bx, by) = pin_positions(sym, 0, 0, rot)
    top_off, bot_off = ((ax, ay), (bx, by)) if ay < by else ((bx, by), (ax, ay))
    x0 = x - top_off[0]
    y0 = y_top + 16 - top_off[1]          # leave a 16-unit stub above
    p = Placed(comp, sym, x0, y0, rot)
    layout.placed.append(p)
    pa, pb = p.pins
    top_pin, bot_pin = (pa, pb) if pa[1] < pb[1] else (pb, pa)
    layout.wire(x, y_top, *top_pin)
    return bot_pin[1]


def _layout_ladder(circuit: Circuit, ladder) -> Layout:
    path_nodes, series, chains = ladder
    src = _source(circuit)
    layout = Layout("ladder")

    col_x = {n: LEFT_X + COL_PITCH * (i + 1) for i, n in enumerate(path_nodes)}

    # Source on the far left, + pin on the rail, - pin to ground
    n_in = path_nodes[0]
    y_bot = _place_vertical(layout, src, LEFT_X, RAIL_Y, n_in)
    layout.flag(LEFT_X, y_bot, GROUND)
    layout.wire(LEFT_X, RAIL_Y, col_x[n_in], RAIL_Y)

    # Series elements along the rail
    for i, comp in enumerate(series):
        _place_horizontal(layout, comp, col_x[path_nodes[i]], col_x[path_nodes[i + 1]],
                          RAIL_Y, path_nodes[i])

    # Shunt chains straight down from each node
    for n in path_nodes:
        x = col_x[n]
        y = RAIL_Y
        cur = n
        for comp in chains.get(n, []):
            y = _place_vertical(layout, comp, x, y, cur)
            cur = comp.nodes[1] if comp.nodes[0] == cur else comp.nodes[0]
            if cur != GROUND:
                layout.flag(x, y, cur)        # label the intermediate node
                y += 16
                layout.wire(x, y - 16, x, y)
        if chains.get(n):
            layout.flag(x, y, GROUND)

    # Net labels on every rail node so LTspice keeps our node names
    for n in path_nodes:
        layout.flag(col_x[n], RAIL_Y, n)
    return layout


def _layout_netlabel(circuit: Circuit) -> Layout:
    """Fallback: components in a row, every pin flagged with its net."""
    layout = Layout("netlabel")
    for i, comp in enumerate(circuit.components):
        sym = SYMBOLS[comp.type]
        x = LEFT_X + COL_PITCH * i
        # place vertically with pin A at (x, RAIL_Y)
        ax, ay = sym.pin_a
        p = Placed(comp, sym, x - ax, RAIL_Y - ay, 0)
        layout.placed.append(p)
        (pax, pay), (pbx, pby) = p.pins
        layout.wire(pax, pay, pax, pay - STUB)
        layout.flag(pax, pay - STUB, comp.nodes[0])
        layout.wire(pbx, pby, pbx, pby + STUB)
        layout.flag(pbx, pby + STUB, comp.nodes[1])
    return layout


def layout_circuit(circuit: Circuit) -> Layout:
    circuit.validate()
    ladder = _find_ladder(circuit)
    return _layout_ladder(circuit, ladder) if ladder else _layout_netlabel(circuit)


# ---------------------------------------------------------------------
# .asc writer
# ---------------------------------------------------------------------

def _value_attrs(comp: Component) -> list[str]:
    v = comp.value.strip()
    if comp.type in ("voltage_source", "current_source") and v.upper().startswith("AC"):
        # LTspice keeps the AC spec in Value2 with an empty DC Value
        return ['SYMATTR Value ""', f"SYMATTR Value2 {v}"]
    return [f"SYMATTR Value {v}"]


def to_asc(circuit: Circuit) -> tuple[str, Layout]:
    """Render the circuit as LTspice .asc text. Returns (text, layout)."""
    layout = layout_circuit(circuit)

    xs = ([x for w in layout.wires for x in (w[0], w[2])] + [p.x for p in layout.placed]
          + [f[0] for f in layout.flags])
    ys = ([y for w in layout.wires for y in (w[1], w[3])] + [p.y for p in layout.placed]
          + [f[1] for f in layout.flags])
    width = max(xs + [400]) + 200
    height = max(ys + [300]) + 200

    lines = ["Version 4", f"SHEET 1 {width} {height}"]
    lines += [f"WIRE {x1} {y1} {x2} {y2}" for x1, y1, x2, y2 in layout.wires]
    lines += [f"FLAG {x} {y} {net}" for x, y, net in layout.flags]
    for p in layout.placed:
        lines.append(f"SYMBOL {p.sym.name} {p.x} {p.y} R{p.rot}")
        if p.rot == 90:
            lines += p.sym.window_r90
        elif p.rot == 270:
            lines += p.sym.window_r270
        lines.append(f"SYMATTR InstName {p.comp.id}")
        lines += _value_attrs(p.comp)
    text_y = max(ys) + 64 if ys else 300
    lines.append(f"TEXT {LEFT_X - 64} {text_y} Left 2 !{analysis_directive(circuit)}")
    lines.append(f"TEXT {LEFT_X - 64} {text_y + 32} Left 2 ;{circuit.title}")
    return "\n".join(lines) + "\n", layout


def export(circuit_json: dict, asc_path: str | os.PathLike | None = None,
           cir_path: str | os.PathLike | None = None, technology=None) -> dict:
    """
    Convenience wrapper for the agent CLI: write whichever of .asc/.cir
    were requested from a circuit JSON dict. Returns a summary dict.

    With `technology` set to a non-ideal profile both files describe the
    AS-BUILT circuit, parasitics and all, so what you open is what was
    simulated. The expanded circuit is no longer a simple ladder, so the
    schematic falls back to the net-label layout (reported as usual in
    the returned "layout" key).
    """
    circuit = as_built_circuit(Circuit.from_dict(circuit_json), technology)
    out: dict = {}
    if asc_path is not None:
        text, layout = to_asc(circuit)
        Path(asc_path).write_text(text, encoding="ascii")
        out["asc"] = str(asc_path)
        out["layout"] = layout.strategy
    if cir_path is not None:
        Path(cir_path).write_text(to_ltspice_netlist(circuit), encoding="ascii")
        out["cir"] = str(cir_path)
    return out
