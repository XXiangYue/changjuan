"""SIGKILL probes of the real chapter graph and SQLite commit boundary.

No API credentials, network, model effectiveness, or production database are used.
Injection exists only in this probe subprocess, never in application code.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# Snapshot files before importing their implementation into this subprocess.
# Preparation and recovery store their own manifests instead of assuming sameness.
SOURCE_SHA256 = {
    str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in [
        ROOT / "scripts/run_recovery_probe.py",
        *[
            ROOT / "src/novel_agent/studio" / name
            for name in (
                "db.py",
                "repository.py",
                "engine.py",
                "models.py",
                "retrieval.py",
                "context_budget.py",
                "story_state.py",
                "llm.py",
                "length_control.py",
                "api.py",
            )
        ],
    ]
}

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from novel_agent.studio.db import Database, encode
from novel_agent.studio.engine import Engine
from novel_agent.studio.models import (
    MEMORY_NAMES,
    Approve,
    Blueprint,
    ChapterPlan,
    MemoryProposal,
    ProjectCreate,
    ReviewReport,
    RunCreate,
    digest,
)
from novel_agent.studio.repository import Conflict, Repository
from novel_agent.studio.retrieval import Retrieval
from novel_agent.studio.story_state import replay

SCENARIOS = (
    "before_commit",
    "inside_transaction",
    "committed_before_index",
    "response_lost",
)
RECEIPT_FILES = ("ready.json", "case.json", "verified.json", "crash.log", "recover.log")
STATE_OPERATIONS = (
    "resource.establish",
    "resource.establish",
    "resource.transfer",
    "resource.consume",
    "knowledge.learn",
    "hook.plant",
    "hook.resolve",
)
STATE_EVENT_COUNT = len(STATE_OPERATIONS)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def safe_root(path):
    path = Path(path).resolve()
    if not any(
        path.is_relative_to(ROOT / part) for part in (".cache", "artifacts/resume")
    ):
        raise ValueError(
            "Probe data must stay inside workspace .cache/ or artifacts/resume/"
        )
    return path


def copy_receipts(source, destination):
    """Retain portable original bytes; large isolated SQLite files stay in cache."""
    source, destination = safe_root(source), safe_root(destination)
    if not destination.is_relative_to(ROOT / "artifacts/resume"):
        raise ValueError("Portable receipts must be inside artifacts/resume/")
    destination.mkdir(parents=True, exist_ok=True)
    mapping = []
    for name in RECEIPT_FILES:
        origin, target = source / name, destination / name
        data_hash = hashlib.sha256(origin.read_bytes()).hexdigest()
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != data_hash:
                raise ValueError(f"Refusing to overwrite different evidence: {target}")
        else:
            shutil.copyfile(origin, target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == data_hash
        mapping.append(
            {
                "source": str(origin.relative_to(ROOT)),
                "copy": str(target.relative_to(ROOT)),
                "sha256": data_hash,
            }
        )
    return mapping


def archive_report(report_path):
    """Copy a historical report's receipts without changing measured outcomes."""
    report_path = safe_root(report_path)
    if not report_path.is_relative_to(ROOT / "artifacts/resume"):
        raise ValueError("Report must be inside artifacts/resume/")
    original_bytes = report_path.read_bytes()
    report = json.loads(original_bytes)
    if report.get("evidence_copy"):
        return report
    mappings = []
    for case in report["cases"]:
        original = safe_root(ROOT / case["receipt"])
        assert (
            hashlib.sha256(original.read_bytes()).hexdigest() == case["receipt_sha256"]
        )
        destination = report_path.parent / "receipts" / original.parent.name
        copies = copy_receipts(original.parent, destination)
        mappings.extend(copies)
        case["receipt"] = str((destination / "verified.json").relative_to(ROOT))
        # The hash and every measured field remain exactly as originally reported.
    report["evidence_copy"] = {
        "reason": "Retain original raw evidence in delivery-scanned artifacts; isolated DB files remain in .cache",
        "original_report_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "measured_outcomes_changed": False,
        "source_provenance": "Core source hashes were not captured by the original run; do not retrospectively attribute current sources to it",
        "files": mappings,
    }
    write_json(report_path, report)
    return report


class OfflineSettings:
    """A settings contract that never reads .env, private settings, or environment keys."""

    def __init__(self, root):
        self.root = safe_root(root)
        self.data = self.root / "data"
        self.cache = self.root / ".cache"
        self.data.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)

    def within(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("outside isolated probe workspace")
        return path

    def values(self):
        return {"api_key": "", "vector_enabled": False, "budget": 0}


class FixtureModel:
    """Only establishes a reviewable draft; no quality claim follows from its scores."""

    is_test = True

    def __init__(self, root, owner="顾舟", forbid=False):
        self.root, self.owner, self.forbid = root, owner, forbid

    def record(self, role):
        if self.forbid:
            raise AssertionError(
                "Recovery must reuse the reviewed draft, not call a model"
            )
        with (self.root / "fixture-calls.jsonl").open("a", encoding="utf-8") as out:
            out.write(encode({"role": role, "type": "offline_fixture"}) + "\n")

    @property
    def quote(self):
        return f"沈砚将唯一的温肺丹交给{self.owner}。"

    @property
    def owner_id(self):
        return "actor:luo" if self.owner == "洛蘅" else "actor:gu"

    @property
    def maker(self):
        return "程" if self.owner == "洛蘅" else "闻"

    @property
    def paragraphs(self):
        return [
            f"沈砚清点药匣，只有一枚温肺丹。{self.owner}手中没有温肺丹。",
            self.quote,
            f"{self.owner}当场服下温肺丹，两人手中都不剩丹药。",
            f"沈砚告诉{self.owner}：旧炉由{self.maker}家铸造。{self.owner}这才得知旧炉的来历。",
            "砧角露出一道未合的铜纹，两人约定等炉钳背面的另一半纹路来核认。",
            "炉钳翻过背面，另一半铜纹与砧角严丝合缝；两人认清是同一副旧模留下的印。",
            "他把炉钳放在砧旁，等红灰落定。" * 40,
        ]

    @property
    def body(self):
        return "\n\n".join(self.paragraphs)[:500]

    @property
    def state_changes(self):
        labels = {
            "actor:shen": "沈砚",
            self.owner_id: self.owner,
            "item:pill": "温肺丹",
            "fact:furnace-maker": "旧炉来历",
            "hook:copper-mark": "砧角铜纹",
        }
        actions = [
            {
                "operation": "resource.establish",
                "paragraph": 1,
                "holder_id": "actor:shen",
                "resource_id": "item:pill",
                "quantity": 1,
            },
            {
                "operation": "resource.establish",
                "paragraph": 1,
                "holder_id": self.owner_id,
                "resource_id": "item:pill",
                "quantity": 0,
            },
            {
                "operation": "resource.transfer",
                "paragraph": 2,
                "from_holder_id": "actor:shen",
                "to_holder_id": self.owner_id,
                "resource_id": "item:pill",
                "quantity": 1,
            },
            {
                "operation": "resource.consume",
                "paragraph": 3,
                "holder_id": self.owner_id,
                "resource_id": "item:pill",
                "quantity": 1,
            },
            {
                "operation": "knowledge.learn",
                "paragraph": 4,
                "actor_id": self.owner_id,
                "fact_id": "fact:furnace-maker",
                "fact": f"旧炉由{self.maker}家铸造。",
            },
            {
                "operation": "hook.plant",
                "paragraph": 5,
                "hook_id": "hook:copper-mark",
                "description": "砧角尚未合上的铜纹",
                "payoff_condition": "以炉钳背面另一半铜纹核认砧角印迹。",
                "window_start": 100001,
                "window_end": 100002,
            },
            {
                "operation": "hook.resolve",
                "paragraph": 6,
                "hook_id": "hook:copper-mark",
                "explanation": "作者确认炉钳背面纹路与砧角铜纹相合，回应此前核认承诺。",
            },
        ]
        return [
            item | {"quote": self.paragraphs[item["paragraph"] - 1], "labels": labels}
            for item in actions
        ]

    async def text(self, rid, role, system, prompt, **kwargs):
        self.record(role)
        return self.body

    async def structured(self, rid, role, system, prompt, schema, **kwargs):
        self.record(role)
        if schema is ReviewReport:
            return ReviewReport(
                scores={
                    name: 8
                    for name in ("剧情一致性", "文笔流畅度", "人设吻合度", "原创性")
                },
                summary="离线工程夹具，不代表文学质量",
            )
        if schema is MemoryProposal:
            return MemoryProposal(
                patches=[
                    {
                        "name": "主角形象状态",
                        "content": f"温肺丹已交给{self.owner}并当场服下，没有余丹；{self.owner}已知旧炉由{self.maker}家铸造。",
                        "reason": "本章正文交付、服药与告知",
                        "quotes": [self.quote],
                    }
                ],
                facts=[
                    {
                        "subject": "温肺丹",
                        "predicate": "服用者",
                        "value": self.owner,
                        "kind": "item",
                        "quote": self.quote,
                    }
                ],
                state_changes=self.state_changes,
                chapter_summary=self.quote,
                next_plan=ChapterPlan(title="炉火", goal="修复旧炉", target_words=500),
            )
        raise AssertionError(schema)


async def wait_run(engine, rid, expected):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = engine.status(rid)
        if run["status"] == expected:
            # Let the graph task exit before operating on the same thread again.
            task = engine.tasks.get(rid)
            if task:
                await task
            return run
        if run["status"] in ("failed", "cancelled"):
            raise AssertionError(f"Unexpected terminal state: {run}")
        await asyncio.sleep(0.01)
    raise TimeoutError(f"run did not reach {expected}")


def authority(repo, pid, cid):
    db = repo.db
    chapter = db.one(
        "SELECT canonical,canonical_revision,summary,stale FROM chapters WHERE id=?",
        (cid,),
    )
    return {
        "chapter": chapter,
        "revision": db.one("SELECT revision FROM projects WHERE id=?", (pid,))[
            "revision"
        ],
        "memory": db.all(
            "SELECT name,content,revision FROM memory WHERE project_id=? ORDER BY name",
            (pid,),
        ),
        "facts": db.all(
            "SELECT * FROM facts WHERE project_id=? ORDER BY version,id", (pid,)
        ),
        "versions": db.all(
            "SELECT * FROM versions WHERE chapter_id=? ORDER BY version", (cid,)
        ),
        "run_commits": db.all(
            "SELECT * FROM run_commits WHERE project_id=? ORDER BY run_id", (pid,)
        ),
        "audit": db.all(
            "SELECT * FROM audit WHERE project_id=? ORDER BY created,id", (pid,)
        ),
        "story_events": db.all(
            "SELECT * FROM story_events WHERE project_id=? ORDER BY version,position,sequence,id",
            (pid,),
        ),
        "story_state": repo.story_state(pid),
    }


def assert_story_state(state, *, cid, version, model):
    """Assert typed content and provenance, rather than trusting a ready label."""
    report = state["story_state"]
    assert report["status"] == "ready" and not report["empty"]
    rows = state["story_events"]
    active = [row for row in rows if not row["stale"]]
    assert len(rows) == STATE_EVENT_COUNT * version
    assert len(active) == STATE_EVENT_COUNT
    assert len({row["id"] for row in rows}) == len(rows)
    assert [row["sequence"] for row in active] == list(range(1, STATE_EVENT_COUNT + 1))
    assert all(row["version"] == version for row in active)
    assert all(row["stale"] == 1 for row in rows if row["version"] < version)
    events = [json.loads(row["event"]) for row in active]
    assert [e["operation"] for e in events] == list(STATE_OPERATIONS)
    for row, event in zip(active, events, strict=True):
        assert row["chapter_id"] == event["source"]["chapter_id"] == cid
        assert row["version"] == event["source"]["version"] == version
        assert row["position"] == event["position"] == 100001
        assert row["sequence"] == event["sequence"]
        assert row["id"] == event["id"]
        assert event["status"] == "confirmed"
        assert event["source"]["quote"] in model.body
    snapshot = report["snapshot"]
    # Recompute from all persisted versions, explicitly excluding obsolete sources.
    all_replayed = replay(
        [json.loads(row["event"]) for row in rows], source_versions={cid: version}
    ).model_dump(mode="json")
    excluded = all_replayed.pop("excluded_events")
    assert len(excluded) == STATE_EVENT_COUNT * (version - 1)
    assert all(e["reason"] == "source_version_obsolete" for e in excluded)
    assert all_replayed == {k: v for k, v in snapshot.items() if k != "excluded_events"}
    assert snapshot["excluded_events"] == []
    assert snapshot["applied_event_ids"] == [e["id"] for e in events]
    assert snapshot["inventories"]["actor:shen"]["item:pill"]["quantity"] == 0
    assert snapshot["inventories"][model.owner_id]["item:pill"]["quantity"] == 0
    assert set(snapshot["inventories"]) == {"actor:shen", model.owner_id}
    assert snapshot["resource_totals"]["item:pill"] == {
        "established": 1,
        "remaining": 0,
        "consumed": 1,
    }
    assert snapshot["facts"] == {"fact:furnace-maker": f"旧炉由{model.maker}家铸造。"}
    assert set(snapshot["knowledge"]) == {model.owner_id}
    knowledge = snapshot["knowledge"][model.owner_id]["fact:furnace-maker"]
    assert knowledge["learned_at"] == 100001 and knowledge["learned_sequence"] == 5
    assert knowledge["learned_by_event"] == events[4]["id"]
    hook = snapshot["hooks"]["hook:copper-mark"]
    assert hook["status"] == "resolved" and hook["resolution_timing"] == "on_time"
    assert hook["planted_by"] == events[5]["id"]
    assert hook["resolved_by"] == events[6]["id"]
    assert hook["resolution_explanation"] == events[6]["explanation"]
    assert all(
        source["version"] == version for source in snapshot["event_sources"].values()
    )
    return {
        "all_rows": len(rows),
        "active_rows": len(active),
        "stale_rows": len(rows) - len(active),
        "active_version": version,
        "sequences": [row["sequence"] for row in active],
        "operations": [e["operation"] for e in events],
        "obsolete_sources_excluded": len(excluded),
        "snapshot_sha256": hashlib.sha256(encode(snapshot).encode()).hexdigest(),
        "resource_totals": snapshot["resource_totals"],
        "knowledge_actors": sorted(snapshot["knowledge"]),
        "hook_status": hook["status"],
    }


def park(root, scenario, **detail):
    write_json(
        root / "ready.json",
        {"scenario": scenario, "pid": __import__("os").getpid(), **detail},
    )
    # This process never closes the graph/DB gracefully. The parent SIGKILLs it.
    while True:
        time.sleep(0.1)


def inject(repo, retrieval, root, scenario):
    if scenario == "before_commit":
        original = repo.commit_chapter

        def commit(*args, **kwargs):
            park(root, scenario, boundary="before Repository.commit_chapter")
            return original(*args, **kwargs)

        repo.commit_chapter = commit
    elif scenario == "inside_transaction":
        original = repo.db.connect

        @contextmanager
        def traced():
            with original() as connection:

                def trace(sql):
                    if sql.startswith("INSERT INTO versions VALUES"):
                        assert connection.in_transaction
                        inserted = connection.execute(
                            "SELECT COUNT(*) FROM story_events WHERE version=2 AND stale=0"
                        ).fetchone()[0]
                        assert inserted == STATE_EVENT_COUNT
                        park(
                            root,
                            scenario,
                            boundary="after canonical/memory/fact/typed-event writes; before version INSERT",
                            in_transaction=True,
                            uncommitted_state_events=inserted,
                        )

                connection.set_trace_callback(trace)
                yield connection

        repo.db.connect = traced
    elif scenario == "committed_before_index":

        def index(cid):
            assert repo.chapter(cid)["canonical_revision"] == 2
            park(
                root,
                scenario,
                boundary="Repository transaction committed; Retrieval.index not entered",
            )

        retrieval.index = index


async def worker(root, scenario, recover=False):
    # Any unexpected network dependency fails closed; fixture runs cannot spend money.
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Network access is forbidden in the recovery probe")

    socket.socket.connect = forbidden_network
    settings = OfflineSettings(root)
    db = Database(settings.data / "studio.sqlite")
    repo = Repository(db, settings)
    retrieval = Retrieval(db, settings)
    model = FixtureModel(root, forbid=recover)
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.data / "checkpoints.sqlite")
    ) as saver:
        await saver.setup()
        engine = Engine(repo, retrieval, saver, model)
        try:
            if not recover:
                project = repo.create(
                    ProjectCreate(
                        title="SIGKILL工程夹具",
                        premise="丹药只有一枚，历史改稿后持有人必须一致。",
                        target_chapters=2,
                        chapter_words=500,
                    )
                )
                pid, cid = project["id"], project["chapters"][0]["id"]
                initial = {name: f"# {name}\n尚未交付丹药。" for name in MEMORY_NAMES}
                blueprint = Blueprint(
                    logline="交药与修炉",
                    alternatives=["交药", "留药"],
                    outline="交药后修炉。",
                    memory=initial,
                    chapters=[
                        ChapterPlan(title="交药", goal="交付丹药", target_words=500),
                        ChapterPlan(title="炉火", goal="修复旧炉", target_words=500),
                    ],
                )
                repo.apply_blueprint(pid, blueprint.model_dump(), 0)
                old_model = FixtureModel(root, owner="洛蘅")
                proposal = await old_model.structured(
                    "seed", "seed", "", "", MemoryProposal
                )
                repo.store_generated(
                    cid,
                    old_model.body,
                    review={"passed": True},
                    proposal=proposal.model_dump(),
                    context={"memory": initial},
                    reviewed_hash=digest(old_model.body),
                    reviewed_project_revision=1,
                )
                repo.commit_chapter(
                    cid,
                    Approve(
                        base_revision=1,
                        project_revision=1,
                        accepted_patches=["主角形象状态"],
                        accepted_state_changes=list(range(STATE_EVENT_COUNT)),
                    ),
                )
                retrieval.index(cid)
                before = authority(repo, pid, cid)
                assert_story_state(before, cid=cid, version=1, model=old_model)
                run = await engine.start(
                    RunCreate(project_id=pid, chapter_id=cid, auto_revise=False)
                )
                rid = run["id"]
                await wait_run(engine, rid, "awaiting_approval")
                chapter = repo.chapter(cid)
                approval = Approve(
                    base_revision=chapter["draft_revision"],
                    project_revision=2,
                    accepted_patches=[
                        p["name"] for p in chapter["proposal"]["patches"]
                    ],
                    accepted_state_changes=list(range(STATE_EVENT_COUNT)),
                )
                write_json(
                    root / "case.json",
                    {
                        "source_sha256": SOURCE_SHA256,
                        "pid": pid,
                        "cid": cid,
                        "rid": rid,
                        "approval": approval.model_dump(),
                        "before": before,
                        "body": model.body,
                    },
                )
                inject(repo, retrieval, root, scenario)
                await engine.resume(rid, approval.model_dump())
                await wait_run(engine, rid, "completed")
                if scenario == "response_lost":
                    park(
                        root,
                        scenario,
                        boundary="completed graph and index/mirror; caller acknowledgement not delivered",
                    )
                raise AssertionError("Injection point was not reached")
            meta = json.loads((root / "case.json").read_text())
            pid, cid, rid = (meta[key] for key in ("pid", "cid", "rid"))
            crashed = authority(repo, pid, cid)
            if scenario in ("before_commit", "inside_transaction"):
                assert crashed == meta["before"], (
                    "SIGKILL left a partial authority commit"
                )
                crash_state_checks = assert_story_state(
                    crashed,
                    cid=cid,
                    version=1,
                    model=FixtureModel(root, owner="洛蘅", forbid=True),
                )
            else:
                assert crashed["chapter"]["canonical_revision"] == 2
                assert len(crashed["versions"]) == 2
                assert len(crashed["run_commits"]) == 1
                crash_state_checks = assert_story_state(
                    crashed, cid=cid, version=2, model=model
                )
            if scenario == "committed_before_index":
                assert not db.all(
                    "SELECT * FROM chunks WHERE chapter_id=? AND stale=0", (cid,)
                )
                assert retrieval.search(pid, "温肺丹") == [], (
                    "Old sources leaked after canon commit"
                )
            # Same restart reconciliation as the application lifespan.
            db.execute(
                "UPDATE runs SET status='interrupted',error='probe SIGKILL restart',cancel_requested=0 WHERE status IN ('running','queued')"
            )
            if engine.status(rid)["status"] != "completed":
                await engine.resume(rid, meta["approval"])
                await wait_run(engine, rid, "completed")
            final = authority(repo, pid, cid)
            state_checks = assert_story_state(final, cid=cid, version=2, model=model)
            assert [row for row in final["story_events"] if row["version"] == 1] == [
                row | {"stale": 1} for row in meta["before"]["story_events"]
            ], (
                "Historical event content/source/order changed instead of only becoming stale"
            )
            assert repo.story_state(pid, 100001)["snapshot"]["applied_event_ids"] == []
            assert (
                repo.story_state(pid, 100002)["snapshot"]
                == final["story_state"]["snapshot"]
            )
            assert final["chapter"]["canonical"] == meta["body"]
            assert final["chapter"]["canonical_revision"] == 2
            assert final["revision"] == 3
            assert [row["version"] for row in final["versions"]] == [1, 2]
            assert (
                len(final["run_commits"]) == 1
                and final["run_commits"][0]["run_id"] == rid
            )
            active = [fact for fact in final["facts"] if not fact["stale"]]
            assert (
                len(active) == 1
                and active[0]["value"] == "顾舟"
                and active[0]["version"] == 2
            )
            assert all(f["stale"] for f in final["facts"] if f["version"] == 1)
            memory = {m["name"]: m["content"] for m in final["memory"]}
            assert memory == json.loads(final["versions"][-1]["memory_snapshot"])
            assert (
                "顾舟" in memory["主角形象状态"]
                and "洛蘅" not in memory["主角形象状态"]
            )
            assert json.loads(final["versions"][-1]["facts_snapshot"]) == active
            assert len(memory) == 10
            chunks = db.all("SELECT * FROM chunks WHERE chapter_id=?", (cid,))
            assert chunks and all(c["version"] == 2 and not c["stale"] for c in chunks)
            assert len(
                db.all("SELECT * FROM chunks_fts WHERE project_id=?", (pid,))
            ) == len(chunks)
            evidence = retrieval.search(pid, "温肺丹")
            assert evidence and all(
                e["version"] == 2 and "洛蘅" not in e["content"] for e in evidence
            )
            # Repository idempotence must not create v3 or apply memory twice.
            for _ in range(2):
                repo.commit_chapter(
                    cid, Approve.model_validate(meta["approval"]), run_id=rid
                )
            assert authority(repo, pid, cid) == final
            try:
                await engine.resume(rid, meta["approval"])
            except Conflict:
                confirmation = "completed run rejected without mutation (same as public approve route)"
            else:
                raise AssertionError(
                    "Completed run accepted a second active confirmation"
                )
            assert authority(repo, pid, cid) == final
            assert db.all("PRAGMA foreign_key_check") == []
            assert list(db.one("PRAGMA integrity_check").values()) == ["ok"]
            assert db.usage_summary()["calls"] == 0
            snapshot = (
                settings.data / "projects" / pid / "小说本体" / "第1卷" / "第1章.md"
            )
            assert snapshot.read_text() == meta["body"]
            write_json(
                root / "verified.json",
                {
                    "scenario": scenario,
                    "passed": True,
                    "before_restart": crashed,
                    "final": final,
                    "crash_state_checks": crash_state_checks,
                    "state_checks": state_checks
                    | {
                        "repeated_confirmation_unchanged": True,
                        "time_cutoff_checked": True,
                    },
                    "confirmation_replay": confirmation,
                    "chunks": len(chunks),
                    "model_calls_during_recovery": 0,
                    "usage_rows": 0,
                    "source_sha256": meta.get("source_sha256"),
                    "recovery_source_sha256": SOURCE_SHA256,
                    "scope": "offline fixture, real Engine/Repository/checkpointer/SQLite/FTS and seven typed resource/knowledge/hook events; no semantic-vector, fee/budget-recovery, natural-language extraction or model-effectiveness evidence",
                },
            )
        finally:
            await engine.shutdown()
            retrieval.close()


def run_case(root, scenario, timeout=40, receipts=None):
    root = safe_root(root)
    root.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--root",
        str(root),
        "--scenario",
        scenario,
    ]
    with (root / "crash.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT)
        try:
            deadline = time.monotonic() + timeout
            while not (root / "ready.json").exists():
                if process.poll() is not None:
                    raise AssertionError(
                        f"Worker exited before injection: {(root / 'crash.log').read_text()}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"No ready marker: {scenario}")
                time.sleep(0.02)
            marker = json.loads((root / "ready.json").read_text())
            assert marker["pid"] == process.pid and process.poll() is None
            process.kill()
            code = process.wait(timeout=5)
            assert code == -signal.SIGKILL
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    with (root / "recover.log").open("w") as log:
        recovered = subprocess.run(
            command + ["--recover"],
            check=False,
            stdout=log,
            stderr=log,
            cwd=ROOT,
            timeout=timeout,
        )
    assert recovered.returncode == 0, (root / "recover.log").read_text()
    result = json.loads((root / "verified.json").read_text())
    copies = copy_receipts(root, receipts) if receipts is not None else []
    receipt = (
        receipts / "verified.json" if receipts is not None else root / "verified.json"
    )
    return {
        "scenario": scenario,
        "passed": result["passed"],
        "signal": "SIGKILL",
        "child_returncode": code,
        "boundary": marker["boundary"],
        "receipt": str(receipt.relative_to(ROOT)),
        "receipt_sha256": hashlib.sha256(
            (root / "verified.json").read_bytes()
        ).hexdigest(),
        "source_sha256": result["source_sha256"],
        "recovery_source_sha256": result["recovery_source_sha256"],
        "source_consistent": result["source_sha256"]
        == result["recovery_source_sha256"],
        "evidence_copy": copies,
        "state_checks": result["state_checks"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--archive-report", type=Path)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker(safe_root(args.root), args.scenario, args.recover))
        return
    if args.archive_report:
        report = archive_report(args.archive_report)
        print(
            json.dumps(
                {
                    "report": str(args.archive_report),
                    "copied_cases": len(report["cases"]),
                    "measured_outcomes_changed": False,
                },
                ensure_ascii=False,
            )
        )
        return
    if not 1 <= args.repetitions <= 10:
        parser.error("repetitions must be 1–10")
    run_id = uuid4().hex
    cache = safe_root(args.root or ROOT / ".cache/process-recovery" / run_id)
    cache.mkdir(parents=True, exist_ok=False)
    output = ROOT / "artifacts/resume/recovery" / run_id
    output.mkdir(parents=True)
    results = []
    for iteration in range(args.repetitions):
        for scenario in SCENARIOS:
            name = f"{iteration + 1}-{scenario}"
            results.append(
                run_case(cache / name, scenario, receipts=output / "receipts" / name)
            )
    report = {
        "experiment": "process-sigkill-recovery-v2-typed-state",
        "repetitions": args.repetitions,
        "offline": True,
        "cases": results,
        "passed": all(row["passed"] and row["source_consistent"] for row in results),
        "source_sha256": results[0]["source_sha256"],
        "source_consistent": all(
            r["source_sha256"]
            == r["recovery_source_sha256"]
            == results[0]["source_sha256"]
            for r in results
        ),
        "evidence_copy": {
            "files": [f for r in results for f in r["evidence_copy"]],
            "database_policy": "Isolated SQLite files remain in .cache; raw JSON receipts and worker logs are retained under artifacts/resume/",
        },
        "limitation": "Deterministic no-network fixture with seven typed resource/knowledge/hook events, keyword index only; proves tested commit/recovery boundaries, not semantic-vector recovery, fee/budget recovery, natural-language extraction, LLM quality, or all crash locations",
    }
    write_json(output / "results.json", report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "cases": len(results),
                "report": str(output / "results.json"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
