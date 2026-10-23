"""Real process reservation/settlement probes, without requests or credentials."""

from __future__ import annotations

import asyncio
from pathlib import Path
import signal
import subprocess
import sys
import time

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.outputs import LLMResult, ChatGeneration

from novel_agent.studio.db import Database
from novel_agent.studio.llm import BudgetExceeded, Meter


class BudgetSettings:
    def values(self):
        return {"budget": 0.05}


async def worker(folder, label, action):
    db = Database(folder / "budget.sqlite")
    db.execute(
        "INSERT OR IGNORE INTO runs(id,kind,status) VALUES(?,?,?)",
        (label, "evaluation", "evaluating"),
    )
    meter = Meter(db, BudgetSettings(), label, "offline-budget-probe", 4000, False)
    try:
        await meter.on_chat_model_start(
            None, [[HumanMessage("无网络预算夹具")]], run_id=label
        )
    except BudgetExceeded:
        (folder / f"{label}-denied").write_text("quota rejected")
        return
    if action == "settle":
        response = LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="离线夹具",
                            usage_metadata={
                                "input_tokens": 1000,
                                "output_tokens": 50,
                                "total_tokens": 1050,
                            },
                            response_metadata={"model_name": "offline-budget-fixture"},
                        )
                    )
                ]
            ]
        )
        await meter.on_llm_end(response, run_id=label)
    (folder / f"{label}-ready").write_text(action)
    while True:
        await asyncio.sleep(0.05)


def child(folder, label, action):
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(folder), label, action],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def await_marker(folder, label, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for state in ("ready", "denied"):
            if (folder / f"{label}-{state}").exists():
                return state
        if process.poll() is not None:
            _, err = process.communicate()
            raise AssertionError(err.decode())
        time.sleep(0.01)
    raise TimeoutError("budget child did not reach reservation boundary")


def finish(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def test_two_processes_cannot_reserve_more_than_shared_budget_and_kill_keeps_unknown_cost(
    tmp_path,
):
    Database(tmp_path / "budget.sqlite")
    processes = [(label, child(tmp_path, label, "reserve")) for label in ("a", "b")]
    try:
        states = {label: await_marker(tmp_path, label, p) for label, p in processes}
        assert sorted(states.values()) == ["denied", "ready"]
        winner = next(p for label, p in processes if states[label] == "ready")
        assert winner.poll() is None
        winner.kill()
        winner.wait(timeout=5)
        assert winner.returncode == -signal.SIGKILL
        reopened = Database(tmp_path / "budget.sqlite")
        rows = reopened.all("SELECT state,cost,reserved FROM usage")
        assert len(rows) == 1 and rows[0]["state"] == "reserved"
        assert rows[0]["cost"] == 0 and 0.032 <= rows[0]["reserved"] <= 0.05
        # A restarted controller cannot refund an unacknowledged provider call.
        probe = child(tmp_path, "restart", "reserve")
        try:
            assert await_marker(tmp_path, "restart", probe) == "denied"
        finally:
            finish(probe)
        assert reopened.all("SELECT state,cost,reserved FROM usage") == rows
    finally:
        for _, process in processes:
            finish(process)


def test_kill_after_settlement_does_not_restore_reservation_or_lose_charge(tmp_path):
    Database(tmp_path / "budget.sqlite")
    process = child(tmp_path, "settled", "settle")
    try:
        assert await_marker(tmp_path, "settled", process) == "ready"
        process.kill()
        process.wait(timeout=5)
        assert process.returncode == -signal.SIGKILL
        reopened = Database(tmp_path / "budget.sqlite")
        rows = reopened.all(
            "SELECT state,input_tokens,output_tokens,cost,reserved FROM usage"
        )
        assert rows == [
            {
                "state": "settled_estimate",
                "input_tokens": 1000,
                "output_tokens": 50,
                "cost": 0.0024,
                "reserved": 0.0,
            }
        ]
    finally:
        finish(process)


if __name__ == "__main__":
    import socket

    def forbid_network(*args, **kwargs):
        raise AssertionError("offline probe cannot connect to a network")

    socket.socket.connect = forbid_network
    folder = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parents[1]
    if not folder.is_relative_to(root / ".cache"):
        raise SystemExit("budget probe workspace must remain in project .cache")
    asyncio.run(worker(folder, sys.argv[2], sys.argv[3]))
