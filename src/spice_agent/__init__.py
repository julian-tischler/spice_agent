"""
LLM-driven analog filter design, verified by real SPICE simulation.

    circuit/      the design, and how it becomes SPICE text
    simulation/   running ngspice and judging the result
    agent/        the LLM loop that proposes and revises designs
    export/       LTspice schematics and netlists
    plotting      annotated Bode plots
    templates     hand-written reference netlists
    paths         locating ngspice and LTspice
    units         SPICE value parsing

See ARCHITECTURE.md for the pipeline and the reasoning behind the layout.
"""
