"""Offline budget/isolation checks for the paid task runner, never model calls."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from novel_agent.studio.db import Database, uid
from novel_agent.studio.llm import BudgetExceeded, Meter
from scripts.run_mcp_live_eval import ScopedSettings, grade, public_message


class SettingsStub:
    def __init__(self, budget):
        self.budget = budget

    def values(self):
        return {"budget": self.budget}


def usage(db, run, cost):
    db.execute(
        "INSERT INTO usage(id,run_id,role,cost,state) VALUES(?,?,?,?,?)",
        (uid(), run, "offline-budget-fixture", cost, "settled_estimate"),
    )


@pytest.mark.asyncio
async def test_local_budget_reservation_uses_existing_meter_without_raising_global_cap(
    tmp_path,
):
    db = Database(tmp_path / "budget.sqlite")
    usage(db, "other", 250)
    usage(db, "this", 1)
    base = SettingsStub(300)
    scoped = ScopedSettings(base, db, "this", limit=3)
    too_large = Meter(db, scoped, "this", "offline-budget-fixture", 300000, False)
    with pytest.raises(BudgetExceeded):
        await too_large.on_chat_model_start(
            None, [[HumanMessage("离线预算边界")]], run_id="large"
        )
    assert db.usage_summary()["reserved"] == 0
    small = Meter(db, scoped, "this", "offline-budget-fixture", 1000, False)
    await small.on_chat_model_start(
        None, [[HumanMessage("离线预算边界")]], run_id="small"
    )
    assert 0 < db.usage_summary()["reserved"] < 0.02
    assert base.values()["budget"] == 300


@pytest.mark.asyncio
async def test_global_cap_still_rejects_even_with_spare_local_budget(tmp_path):
    db = Database(tmp_path / "global.sqlite")
    usage(db, "other", 9.9)
    scoped = ScopedSettings(SettingsStub(10), db, "this", limit=3)
    meter = Meter(db, scoped, "this", "offline-budget-fixture", 20000, False)
    with pytest.raises(BudgetExceeded):
        await meter.on_chat_model_start(
            None, [[HumanMessage("离线预算边界")]], run_id="call"
        )
    assert db.usage_summary()["reserved"] == 0


def test_trace_serializer_excludes_private_reasoning_and_provider_extras():
    message = AIMessage(
        content="公开结果",
        additional_kwargs={
            "reasoning_content": "PRIVATE_REASONING",
            "authorization": "PRIVATE_AUTH",
        },
    )
    encoded = json.dumps(public_message(message), ensure_ascii=False)
    assert "公开结果" in encoded and "PRIVATE" not in encoded


def test_grader_does_not_call_missing_fault_feedback_or_formal_mutation_a_pass():
    before = {"chapters": [{"canonical_revision": 0}], "events": [], "versions": []}
    after = {"chapters": [{"canonical_revision": 1}], "events": [], "versions": []}
    result = grade(
        {"proposal": {}},
        {"status": "ready", "snapshot": {}},
        [],
        before,
        after,
        {"spent": 0, "reserved": 0},
    )
    assert not result["passed"]
    assert not result["checks"]["requested_fault_observed"]
    assert not result["checks"]["formal_state_unchanged"]
