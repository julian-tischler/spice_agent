"""
Running simulations and judging the results.

    ngspice.py    drives the simulator, parses its output
    metrics.py    measures a sweep (cutoff, peak, notch, bandwidth)
    spec.py       DesignSpec - the requirement model
    verify.py     check_filter_spec - the verdict and its feedback
    diagnosis.py  why a non-ideal design failed: ideal comparison,
                  per-parasitic ablation, part self-resonances
    pipeline.py   simulate_and_verify - the whole thing in one call

Import from the package, not its modules; the split above is an
implementation detail.
"""

from spice_agent.simulation.ngspice import (
    SimulationError,
    compute_ac_metrics,
    run_ngspice_ac,
)
from spice_agent.simulation.diagnosis import diagnose
from spice_agent.simulation.pipeline import (
    simulate_and_verify,
    simulate_get_full_sweep,
)
from spice_agent.simulation.spec import DesignSpec, FilterType
from spice_agent.simulation.verify import (
    MIN_STOPBAND_ATTENUATION_DB,
    check_filter_spec,
)

__all__ = [
    "DesignSpec",
    "FilterType",
    "MIN_STOPBAND_ATTENUATION_DB",
    "SimulationError",
    "check_filter_spec",
    "compute_ac_metrics",
    "diagnose",
    "run_ngspice_ac",
    "simulate_and_verify",
    "simulate_get_full_sweep",
]
