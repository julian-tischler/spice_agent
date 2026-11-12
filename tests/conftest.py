"""
Suite-wide fixtures.

Every CLI run writes a run folder by default. Tests drive the CLI with
scripted fakes, so without this they would file fake "gemini" runs into
the real out/runs/live/ next to genuine data. Point the run log at a
throwaway directory instead; tests that inspect run folders set their
own SPICE_AGENT_RUNS_DIR on top of this.
"""

import pytest

from spice_agent.runlog import RUNS_DIR_ENV


@pytest.fixture(scope="session")
def _test_runs_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("runs")


@pytest.fixture(autouse=True)
def _isolate_run_logs(_test_runs_dir, monkeypatch):
    monkeypatch.setenv(RUNS_DIR_ENV, str(_test_runs_dir))
