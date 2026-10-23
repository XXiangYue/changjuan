"""API controller for the frozen three-chapter baseline, with explicit approvals.

No prose edits, model calls, or canonical writes outside the production API.
init copies author-prepared shared inputs; draft stops at author approval; approve
requires a separately written operator decision artifact tied to a manuscript hash.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import httpx

from novel_agent.studio.db import encode, now
from novel_agent.studio.models import MemoryProposal, ReviewReport
from novel_agent.studio.settings import ROOT

BASE = ROOT / "artifacts/resume/live-story-v1/baseline"
SHARED = ROOT / "artifacts/resume/live-story-v1/shared-blueprint.json"
INSTRUCTION = "正文须在1200至1600个Unicode字符内（含标点换行、不含章节标题），不要用梗概替代场景；按本章既定事件顺序推进。"
LOCAL_GOAL = 5.0


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(value):
    return hashlib.sha256(
        value.encode()
        if isinstance(value, str)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def ledger(pid):
    with sqlite3.connect(f"file:{ROOT / 'data/studio.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT u.* FROM usage u JOIN runs r ON r.id=u.run_id WHERE r.project_id=? ORDER BY u.created,u.id",
                (pid,),
            )
        ]
    return {
        "spent": sum(row["cost"] for row in rows),
        "reserved": sum(row["reserved"] for row in rows),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "calls": len(rows),
        "rows": rows,
    }


def common_inputs(project):
    return {
        key: project[key]
        for key in (
            "title",
            "genre",
            "premise",
            "style",
            "target_chapters",
            "chapter_words",
            "outline",
        )
    } | {
        "memory": {item["name"]: item["content"] for item in project["memory"]},
        "chapters": [
            {key: item[key] for key in ("volume", "number", "title", "plan")}
            for item in project["chapters"]
        ],
    }


def run_reservation(project, chapter):
    """Planning estimate for a whole baseline run, not a new hard token bound.

    Baseline has one writer (8k output cap), one editor (12k), and one memory
    writer (18k); structured-format/transport retries may occur up to three times.
    Include full current memory, outline, plan, a 12k-character draft allowance,
    both structured schemas and a generous serialization/system/error margin.
    Actual provider usage and in-flight Meter reservations remain authoritative.
    """
    source_bytes = len(encode(common_inputs(project)).encode())
    schema_bytes = len(encode(ReviewReport.model_json_schema()).encode()) + len(
        encode(MemoryProposal.model_json_schema()).encode()
    )
    input_allowance = source_bytes * 3 + 12000 * 4 * 2 + schema_bytes + 30000
    estimate = 3 * (input_allowance * 2 + (8000 + 12000 + 18000) * 8) / 1_000_000
    return {
        "estimate_yuan": round(estimate, 6),
        "input_allowance_utf8_bytes_per_pass": input_allowance,
        "maximum_attempts_assumed": 3,
        "scope": "整run保守计划估计，非逐请求硬上限；全局Meter仍是硬守卫，本地¥5为实验控制目标",
        "chapter_id": chapter["id"],
    }


class Controller:
    def __init__(self, port):
        self.http = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30
        )

    async def call(self, method, path, payload=None):
        response = await self.http.request(method, path, json=payload)
        if method != "GET":
            with (BASE / "http-mutations.jsonl").open("a", encoding="utf-8") as output:
                output.write(
                    encode(
                        {
                            "at": now(),
                            "method": method,
                            "path": path,
                            "request": payload,
                            "http_status": response.status_code,
                            "response": response.json(),
                        }
                    )
                    + "\n"
                )
        response.raise_for_status()
        return response.json()

    async def initialize(self):
        if (BASE / "runtime.json").exists():
            raise ValueError("baseline已初始化；不重建、不按结果重新抽样")
        source = json.loads(SHARED.read_text())
        brief_path = ROOT / "evaluations/live-story-v1/brief.json"
        brief = json.loads(brief_path.read_text())
        expected_premise = (
            brief["premise"] + "\n三章安排：" + encode(brief["chapter_plans"])
        )
        if source["premise"] != expected_premise or any(
            item["canonical_revision"] or item["content"] for item in source["chapters"]
        ):
            raise ValueError(
                "共同初始快照不符合冻结brief或已有正文，拒绝创建不公平对照"
            )
        BASE.mkdir(parents=True, exist_ok=True)
        save(
            BASE / "execution-addendum.json",
            {
                "at": now(),
                "operator": "Codex AI测试操作者，非自然人作者/评委",
                "protocol_sha256": hashlib.sha256(
                    (ROOT / "evaluations/live-story-v1/protocol.json").read_bytes()
                ).hexdigest(),
                "brief_sha256": hashlib.sha256(brief_path.read_bytes()).hexdigest(),
                "shared_blueprint_sha256": hashlib.sha256(
                    SHARED.read_bytes()
                ).hexdigest(),
                "operation_difference": "full通过产品UI，baseline通过同一生产应用HTTP控制器；该操作差异不是算法质量指标，补充说明不覆盖改写原协议。",
                "configuration": {
                    "ablation": "baseline",
                    "auto_revise": False,
                    "target_words": 1400,
                    "instruction": INSTRUCTION,
                    "accepted_state_changes": [],
                },
                "plan_policy": "两组均拒绝当前章节设计规划记忆patch，逐章使用冻结原ChapterPlan及scenes，不外部修改正文。",
                "length_thresholds": {
                    "frozen_protocol": [1200, 1600],
                    "application_review": [1120, 1680],
                    "unit": "Unicode字符含标点换行；阈值不同分别报告，不改应用门槛造通过。",
                },
                "budget": {
                    "target_yuan": LOCAL_GOAL,
                    "hard_global_yuan": 300,
                    "strategy": "每个生产run前按当前输入/重试估整run剩余额；过程中监测真实cost+reserved接近目标则暂停；不宣传局部逐LLM硬上限。",
                },
                "source_sha256": {
                    str(path.relative_to(ROOT)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in (
                        Path(__file__).resolve(),
                        ROOT / "src/novel_agent/studio/engine.py",
                        ROOT / "src/novel_agent/studio/llm.py",
                        ROOT / "src/novel_agent/studio/retrieval.py",
                        ROOT / "src/novel_agent/studio/repository.py",
                        ROOT / "src/novel_agent/studio/models.py",
                        ROOT / "uv.lock",
                    )
                },
            },
        )
        project = await self.call(
            "POST",
            "/api/projects",
            {
                key: source[key]
                for key in (
                    "title",
                    "genre",
                    "premise",
                    "style",
                    "target_chapters",
                    "chapter_words",
                )
            },
        )
        save(
            BASE / "runtime.json",
            {
                "project_id": project["id"],
                "source_project_id": source["id"],
                "created_at": now(),
                "last_run_id": None,
            },
        )
        for item in source["memory"]:
            project = await self.call(
                "PUT",
                f"/api/projects/{project['id']}/memory/{item['name']}",
                {"content": item["content"], "project_revision": project["revision"]},
            )
        project = await self.call(
            "PUT",
            f"/api/projects/{project['id']}/outline",
            {"outline": source["outline"], "project_revision": project["revision"]},
        )
        for current, planned in zip(
            project["chapters"], source["chapters"], strict=True
        ):
            await self.call(
                "PUT",
                f"/api/chapters/{current['id']}/plan",
                {"base_revision": current["draft_revision"], "plan": planned["plan"]},
            )
        project = await self.call("GET", f"/api/projects/{project['id']}")
        with sqlite3.connect(
            f"file:{ROOT / 'data/studio.sqlite'}?mode=ro", uri=True
        ) as db:
            initial = json.loads(
                db.execute(
                    "SELECT content FROM initial_memory WHERE project_id=?",
                    (project["id"],),
                ).fetchone()[0]
            )
        expected, copied = common_inputs(source), common_inputs(project)
        proof = {
            "at": now(),
            "source_project_id": source["id"],
            "baseline_project_id": project["id"],
            "source_inputs_sha256": sha(expected),
            "baseline_inputs_sha256": sha(copied),
            "exact_shared_inputs_equal": expected == copied,
            "baseline_initial_memory_equal": initial == expected["memory"],
            "excluded_nonstory_fields": [
                "project/chapter IDs",
                "revision counters",
                "run history",
                "volume display title",
                "logline (not used by chapter writer)",
            ],
            "source_inputs": expected,
            "copied_inputs": copied,
        }
        save(BASE / "copy-proof.json", proof)
        save(BASE / "initial-project.json", project)
        if (
            not proof["exact_shared_inputs_equal"]
            or not proof["baseline_initial_memory_equal"]
        ):
            raise ValueError("共享输入复制不一致，停止而不生成")
        print(
            json.dumps(
                {
                    "initialized": True,
                    "project_id": project["id"],
                    "copy_sha256": proof["source_inputs_sha256"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def runtime(self):
        return json.loads((BASE / "runtime.json").read_text())

    async def capture(self, run_id):
        state = await self.call("GET", f"/api/runs/{run_id}")
        chapter = await self.call("GET", f"/api/chapters/{state['chapter_id']}")
        folder = BASE / "runs" / run_id
        save(folder / f"{state['status']}-run.json", state)
        save(folder / f"{state['status']}-chapter.json", chapter)
        events = await self.call("GET", f"/api/runs/{run_id}/events?once=true")
        save(folder / "events.json", events)
        save(BASE / "usage.json", ledger(state["project_id"]))
        return state, chapter

    async def wait(self, run_id):
        paused = False
        while True:
            state = await self.call("GET", f"/api/runs/{run_id}")
            usage = ledger(state["project_id"])
            if (
                not paused
                and state["status"] in ("running", "queued")
                and usage["spent"] + usage["reserved"] >= LOCAL_GOAL - 0.2
            ):
                await self.call("POST", f"/api/runs/{run_id}/pause")
                paused = True
            if state["status"] not in ("running", "queued"):
                state, chapter = await self.capture(run_id)
                print(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "status": state["status"],
                            "chapter": chapter["number"],
                            "characters": len(chapter["content"]),
                            "han": len(
                                re.findall(r"[\u4e00-\u9fff]", chapter["content"])
                            ),
                            "body_sha256": sha(chapter["content"]),
                            "review_passed": chapter["review"].get("passed"),
                            "within_protocol_length": 1200
                            <= len(chapter["content"])
                            <= 1600,
                            "cost_yuan": usage["spent"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return state, chapter
            await asyncio.sleep(0.8)

    async def draft(self, *, resume=False):
        runtime = self.runtime()
        project = await self.call("GET", f"/api/projects/{runtime['project_id']}")
        remaining = [
            item for item in project["chapters"] if not item["canonical_revision"]
        ]
        if not remaining:
            print("所有章节已定稿，无新模型调用。", flush=True)
            return
        chapter = remaining[0]
        if runtime.get("last_run_id"):
            previous = await self.call("GET", f"/api/runs/{runtime['last_run_id']}")
            if (
                previous["chapter_id"] == chapter["id"]
                and previous["status"] != "completed"
            ):
                if previous["status"] in ("running", "queued", "awaiting_approval"):
                    await self.wait(previous["id"])
                    return
                if not resume:
                    raise ValueError(
                        "原任务未完成；保留失败，需显式--resume，不能重新抽正文"
                    )
                save(
                    BASE
                    / "runs"
                    / previous["id"]
                    / f"recovery-before-{len(ledger(project['id'])['rows'])}.json",
                    {
                        "run": previous,
                        "draft_sha256": sha(chapter["content"]),
                        "body_present": bool(chapter["content"]),
                        "strategy": "恢复原检查点，不另抽样",
                    },
                )
        usage = ledger(project["id"])
        reservation = run_reservation(project, chapter)
        save(
            BASE
            / "budget-checks"
            / f"chapter-{chapter['number']}-calls-{usage['calls']}.json",
            {
                "at": now(),
                "usage": usage,
                "remaining_target_yuan": LOCAL_GOAL
                - usage["spent"]
                - usage["reserved"],
                "planned_run": reservation,
            },
        )
        if (
            usage["spent"] + usage["reserved"] + reservation["estimate_yuan"]
            > LOCAL_GOAL
        ):
            raise ValueError("本实验剩余预算不足保守整run估计，已停止，不能启动下一章")
        if resume:
            state = await self.call(
                "POST", f"/api/runs/{runtime['last_run_id']}/resume"
            )
        else:
            shared = json.loads(SHARED.read_text())
            expected = next(
                item["plan"]
                for item in shared["chapters"]
                if item["volume"] == chapter["volume"]
                and item["number"] == chapter["number"]
            )
            if chapter["plan"] != expected:
                raise ValueError("实际本章计划已偏离冻结scenes，停止生成")
            state = await self.call(
                "POST",
                "/api/runs",
                {
                    "project_id": project["id"],
                    "chapter_id": chapter["id"],
                    "kind": "chapter",
                    "ablation": "baseline",
                    "auto_revise": False,
                    "instruction": INSTRUCTION,
                },
            )
            runtime["last_run_id"] = state["id"]
            save(BASE / "runtime.json", runtime)
        await self.wait(state["id"])

    async def approve(self, decision_path):
        runtime = self.runtime()
        state, chapter = await self.capture(runtime["last_run_id"])
        if state["status"] != "awaiting_approval":
            raise ValueError("本任务不在作者确认位置")
        decision = json.loads(decision_path.read_text())
        if decision["run_id"] != state["id"] or decision["body_sha256"] != sha(
            chapter["content"]
        ):
            raise ValueError("作者决策未绑定当前正文，拒绝过期确认")
        if "当前章节设计规划" in decision["accepted_patches"] or decision.get(
            "accepted_state_changes"
        ):
            raise ValueError(
                "本baseline须保留冻结scenes，不接受typed state或下一章设计patch"
            )
        project = await self.call("GET", f"/api/projects/{runtime['project_id']}")
        save(BASE / "decisions" / f"chapter-{chapter['number']}.json", decision)
        await self.call(
            "POST",
            f"/api/runs/{state['id']}/approve",
            {
                "base_revision": chapter["draft_revision"],
                "project_revision": project["revision"],
                "accepted_patches": decision["accepted_patches"],
                "accepted_facts": decision["accepted_facts"],
                "accepted_state_changes": [],
                "note": decision["note"],
                **(
                    {"summary_override": decision["summary_override"]}
                    if decision.get("summary_override") is not None
                    else {}
                ),
            },
        )
        await self.wait(state["id"])

    async def export(self):
        runtime = self.runtime()
        project = await self.call("GET", f"/api/projects/{runtime['project_id']}")
        save(BASE / "final-project.json", project)
        for extension in ("md", "txt", "zip"):
            response = await self.http.get(
                f"/api/projects/{project['id']}/export", params={"format": extension}
            )
            response.raise_for_status()
            (BASE / f"baseline-novel.{extension}").write_bytes(response.content)
        save(BASE / "usage.json", ledger(project["id"]))
        summary = {
            "project_id": project["id"],
            "chapters": [
                {
                    "id": chapter["id"],
                    "number": chapter["number"],
                    "characters": len(chapter["canonical"]),
                    "han": len(re.findall(r"[\u4e00-\u9fff]", chapter["canonical"])),
                    "body_sha256": sha(chapter["canonical"]),
                    "canonical_revision": chapter["canonical_revision"],
                    "within_protocol_length": 1200 <= len(chapter["canonical"]) <= 1600,
                    "within_application_length": 1120
                    <= len(chapter["canonical"])
                    <= 1680,
                    "review_passed": chapter["review"].get("passed"),
                }
                for chapter in project["chapters"]
            ],
            "typed_events_accepted": 0,
            "operator": "Codex AI，经API明确审阅确认；正文未外部改写",
            "cost_yuan": ledger(project["id"])["spent"],
            "limits": "单卷3章，不是独立质量评委；baseline单编辑分不能与full双编辑分直接相比",
        }
        save(BASE / "result.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


async def main_async(args):
    controller = Controller(args.port)
    try:
        if args.mode == "init":
            await controller.initialize()
        elif args.mode == "draft":
            await controller.draft(resume=args.resume)
        elif args.mode == "approve":
            if not args.decision:
                raise ValueError("approve需提供明确作者决策JSON")
            await controller.approve(args.decision)
        else:
            await controller.export()
    finally:
        await controller.http.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("init", "draft", "approve", "export"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--decision", type=Path)
    asyncio.run(main_async(parser.parse_args()))
