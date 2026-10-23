"""Actual subprocess crashes against isolated production SQLite and graph code."""

from pathlib import Path
from uuid import uuid4

import pytest

from scripts.run_recovery_probe import (
    ROOT,
    SCENARIOS,
    STATE_OPERATIONS,
    run_case,
    safe_root,
)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_sigkill_chapter_commit_recovers_without_half_commit_or_old_sources(scenario):
    # pytest's OS temp directory is deliberately not used: artifacts stay in workspace.
    root = ROOT / ".cache/process-recovery-tests" / uuid4().hex
    result = run_case(root, scenario)
    assert result["passed"] is True
    assert result["signal"] == "SIGKILL"
    assert result["child_returncode"] == -9
    assert result["source_consistent"]
    state = result["state_checks"]
    assert (state["all_rows"], state["active_rows"], state["stale_rows"]) == (14, 7, 7)
    assert state["active_version"] == 2
    assert state["sequences"] == list(range(1, 8))
    assert state["operations"] == list(STATE_OPERATIONS)
    assert state["obsolete_sources_excluded"] == 7
    assert state["knowledge_actors"] == ["actor:gu"]
    assert state["resource_totals"]["item:pill"]["consumed"] == 1
    assert state["hook_status"] == "resolved"
    assert state["repeated_confirmation_unchanged"]
    assert state["time_cutoff_checked"]


def test_probe_rejects_real_data_and_outside_workspace():
    for path in (ROOT / "data", Path("/tmp/novel-recovery-probe")):
        with pytest.raises(ValueError):
            safe_root(path)
