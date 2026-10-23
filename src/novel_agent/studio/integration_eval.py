"""Live graph-level history-gap evaluation; seeded archives are inputs, not model output."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from .db import encode, now, uid
from .models import ChapterPlan, MEMORY_NAMES, ProjectCreate

NAME = "真实执行图续写验收 v1"
SHARED = (
    "铸炉小镇的玄幻手艺世界。沈砚是火息初阶铸器学徒；听息须裸露手指实际接触器物、消耗肺息，不能隔空扫描。"
    "纳炉境需三日调息、赤砂一钱与完整炉砖，境界和胜利需材料、时间、身体条件与代价。"
    "洛蘅有寻找失踪同门的独立目标，不为沈砚无条件牺牲。楚衡优先稳住镇民，不嗜杀。"
)
NEUTRAL = "檐角一滴水悬着。铺前石板映着灰光，风把门边草屑推向墙脚。这个片刻没有人进出，屋里的东西仍在原处。"


def seed_scene(db, repo, case, mode, repeat):
    """Identical confirmed input per variant; hide fixture projects from the shelf."""
    p = repo.create(
        ProjectCreate(
            title=f"铸炉小镇 · 场景{case['id']}",
            genre="玄幻成长",
            premise=SHARED,
            style="具体、克制，以行动与对话推进场景。",
            target_chapters=12,
            chapter_words=600,
        )
    )
    pid = p["id"]
    memory = {
        name: "# "
        + name
        + "\n\n"
        + (SHARED if name == "世界观" else "暂无更多已整理的信息。")
        for name in MEMORY_NAMES
    }
    fact = {
        "id": uid(),
        "project_id": pid,
        "chapter_id": p["chapters"][0]["id"],
        "version": 1,
        "subject": "历史已确认状态",
        "predicate": "场景档案 " + case["id"],
        "value": case["archive"],
        "kind": "event",
        "status": "active",
        "quote": case["archive"],
        "related": encode(["沈砚", "洛蘅", "楚衡"]),
        "position": 100001,
        "stale": 0,
    }
    with db.transaction() as c:
        c.execute("UPDATE projects SET archived=1,revision=1 WHERE id=?", (pid,))
        for name, content in memory.items():
            c.execute(
                "UPDATE memory SET content=?,revision=1 WHERE project_id=? AND name=?",
                (content, pid, name),
            )
        c.execute("INSERT INTO initial_memory VALUES(?,?)", (pid, encode(memory)))
        for ch in p["chapters"][:11]:
            body = case["archive"] if ch["number"] == 1 else NEUTRAL
            summary = (
                case["archive"]
                if ch["number"] == 1
                else "同一片刻的街巷切面，时间、人物与物品状态没有变化。"
            )
            c.execute(
                "UPDATE chapters SET content=?,canonical=?,canonical_revision=1,draft_revision=1,status='final',summary=?,updated=? WHERE id=?",
                (body, body, summary, now(), ch["id"]),
            )
            c.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    ch["id"],
                    1,
                    body,
                    ch["title"],
                    encode(memory),
                    encode([fact]),
                    summary,
                    encode(ch["plan"]),
                    "冻结的测试输入；非模型生成、非自然人作者确认",
                    now(),
                ),
            )
        c.execute(
            "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(
                fact[k]
                for k in (
                    "id",
                    "project_id",
                    "chapter_id",
                    "version",
                    "subject",
                    "predicate",
                    "value",
                    "kind",
                    "status",
                    "quote",
                    "related",
                    "position",
                    "stale",
                )
            ),
        )
        target = p["chapters"][-1]
        plan = ChapterPlan(
            title=case["name"],
            goal=case["task"],
            characters=["沈砚", "洛蘅", "楚衡"],
            target_words=600,
        )
        c.execute(
            "UPDATE chapters SET title=?,plan=? WHERE id=?",
            (case["name"], plan.model_dump_json(), target["id"]),
        )
    db.audit(
        pid,
        "evaluation.seed",
        {
            "case_id": case["id"],
            "mode": mode,
            "repeat": repeat,
            "source": "frozen-cases.json",
            "seeded_chapters": 11,
            "scope": "历史摘要缺口压力输入，不是实际生成的前十一章",
        },
    )
    repo.mirror(pid)
    return {
        "project_id": pid,
        "chapter_id": target["id"],
        "archive_chapter_id": p["chapters"][0]["id"],
    }


def summary(trials):
    from .evaluation import summarize_trials

    completed = [t for t in trials if t.get("status") == "completed"]
    failed = [t for t in trials if t.get("status") == "failed"]
    result = summarize_trials(completed + failed)
    result["summary"] = (
        "固定历史信息缺口场景经过真实上下文、检索、LangGraph执行图、编辑、记忆提案与定稿；合成档案输入，不代表普通章节平均表现。"
    )
    result["metrics"] |= {
        "目标有效试次": 96,
        "进行中": sum(t.get("status") == "running" for t in trials),
    }
    for mode, group in result["by_mode"].items():
        rows = [t for t in completed if t["mode"] == mode]
        group["archive_retrieved"] = sum(
            t.get("retrieval", {}).get("archive_retrieved", False) for t in rows
        )
        group["structured_state_present"] = sum(
            t.get("retrieval", {}).get("fact_count", 0) > 0 for t in rows
        )
        group["mean_extra_drafts"] = (
            round(sum(t.get("revisions", 0) for t in rows) / len(rows), 2)
            if rows
            else None
        )
    result["trials"] = trials
    result["limitations"] = [
        "历史摘要有意不含远处动态事实；测试检索与状态补救，不代表自然写作中摘要遗漏的发生率",
        "稳定能力、境界条件与人物基本动机由四种配置共享；中性历史片段不推进时间、不计入真实生成字数",
        "所有配置环境相同，只有生产执行图的ablation开关不同；新稿无外部AI正文编辑，自动测试确认不是自然人评价",
        "使用与机制探针相同的12个场景，不是独立新holdout，两个实验不合并样本数",
        "同模型生成与评审；少量重复、受控历史缺口，不能外推文学质量与真实长篇效果",
    ]
    return result


async def run_integration(args):
    from .evaluation import (
        infrastructure,
        manifest,
        Verdict,
        studio_http_client,
        prepare_trial_recovery,
    )
    from .llm import ModelClient

    settings, db, repo, retrieval = infrastructure()
    inputs = manifest()
    protocol_path = settings.root / "evaluations/integration-protocol.json"
    protocol = {
        "version": "1.0",
        "frozen_at": now(),
        "source_manifest_sha256": hashlib.sha256(encode(inputs).encode()).hexdigest(),
        "shared_core_rules": SHARED,
        "neutral_history": NEUTRAL,
        "archive_position": 1,
        "target_position": 12,
        "recent_summaries": 10,
        "repetitions": 2,
        "target_chars": 600,
        "confirmation_policy": "测试操作者接受结构合规提案及最终正文，保留未通过审核并注明原因；不是自然人评价",
        "scope": "真实执行图的历史信息缺口压力验收，非自然长篇记忆生成质量实验",
        "software_sha256": {
            name: hashlib.sha256(
                (settings.root / "src/novel_agent/studio" / name).read_bytes()
            ).hexdigest()
            for name in (
                "engine.py",
                "models.py",
                "llm.py",
                "retrieval.py",
                "repository.py",
                "integration_eval.py",
            )
        },
    }
    if protocol_path.exists():
        protocol = json.loads(protocol_path.read_text())
    else:
        protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    if (
        protocol["shared_core_rules"] != SHARED
        or protocol["neutral_history"] != NEUTRAL
        or protocol["source_manifest_sha256"]
        != hashlib.sha256(encode(inputs).encode()).hexdigest()
    ):
        raise ValueError("冻结输入与当前种子实现不一致")
    existing = db.one(
        "SELECT * FROM experiments WHERE name=? ORDER BY created DESC LIMIT 1",
        (NAME,),
        required=False,
    )
    trials = json.loads(existing["result"]).get("trials", []) if existing else []
    eid = existing["id"] if existing else uid()
    if not existing:
        db.execute(
            "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
            (eid, NAME, encode(protocol), "{}", "running", now()),
        )
    out = settings.root / "artifacts/evaluation/integration"
    out.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(max(1, min(getattr(args, "parallel", 8), 8)))
    lock = asyncio.Lock()
    halted = asyncio.Event()

    def persist():
        value = summary(trials)
        db.execute(
            "UPDATE experiments SET result=?,status=? WHERE id=?",
            (
                encode(value),
                "completed"
                if len([t for t in trials if t.get("status") == "completed"]) == 96
                else "running",
                eid,
            ),
        )

    async with studio_http_client(args.url) as client:

        async def trial(case, mode, rep):
            key = (case["id"], mode, rep)
            if any(
                (t["case_id"], t["mode"], t["repeat"]) == key
                and t.get("status") == "completed"
                for t in trials
            ):
                return
            async with sem:
                if halted.is_set():
                    return
                item = next(
                    (
                        t
                        for t in reversed(trials)
                        if (t["case_id"], t["mode"], t["repeat"]) == key
                        and t.get("status") == "running"
                    ),
                    None,
                )
                if item is None:
                    prior = next(
                        (
                            t
                            for t in reversed(trials)
                            if (t["case_id"], t["mode"], t["repeat"]) == key
                        ),
                        None,
                    )
                    if prior:
                        item = {
                            k: v
                            for k, v in prior.items()
                            if k
                            not in (
                                "error",
                                "judge_run_id",
                                "grade_events",
                                "verdict",
                                "completed_at",
                            )
                        }
                        item["resumed_from_attempt"] = prior.get("attempt_id")
                        prepare_trial_recovery(db, item)
                    else:
                        item = {
                            "case_id": case["id"],
                            "case_name": case["name"],
                            "mode": mode,
                            "repeat": rep,
                        }
                        item |= seed_scene(db, repo, case, mode, rep)
                    item |= {
                        "attempt_id": uid(),
                        "status": "running",
                        "started_at": now(),
                        "transport_policy": "loopback_direct_v2",
                        "controller_sha256": hashlib.sha256(
                            (
                                settings.root
                                / "src/novel_agent/studio/integration_eval.py"
                            ).read_bytes()
                        ).hexdigest(),
                    }
                    async with lock:
                        trials.append(item)
                        persist()
                started = time.monotonic()
                try:
                    if not item.get("run_id"):
                        r = await client.post(
                            "/api/projects/" + item["project_id"] + "/reindex"
                        )
                        r.raise_for_status()
                        r = await client.post(
                            "/api/runs",
                            json={
                                "project_id": item["project_id"],
                                "chapter_id": item["chapter_id"],
                                "kind": item.get("recovery_kind", "chapter"),
                                "ablation": mode,
                                "instruction": "接续当前场景，约600字符。以行动和对话写成完整场景，不要提纲、标题或实验说明。只使用角色有来源的知识与资源，未知不等于事实。",
                            },
                        )
                        r.raise_for_status()
                        item["run_id"] = r.json()["id"]
                        async with lock:
                            persist()
                    recovered = 0
                    while True:
                        if item.get("commit_completed_before_transport_failure"):
                            break
                        r = await client.get("/api/runs/" + item["run_id"])
                        r.raise_for_status()
                        run = r.json()
                        if run["status"] == "awaiting_approval":
                            ch = (
                                await client.get("/api/chapters/" + item["chapter_id"])
                            ).json()
                            project = (
                                await client.get("/api/projects/" + item["project_id"])
                            ).json()
                            r = await client.post(
                                "/api/runs/" + item["run_id"] + "/approve",
                                json={
                                    "base_revision": ch["draft_revision"],
                                    "project_revision": project["revision"],
                                    "accepted_patches": [
                                        p["name"] for p in ch["proposal"]["patches"]
                                    ],
                                    "note": "自动测试操作者的统一确认；为保留策略产出允许未通过审核与篇幅偏差，不是自然人作者或阅读评价。",
                                },
                            )
                            r.raise_for_status()
                        elif run["status"] == "completed":
                            break
                        elif run["status"] in ("failed", "interrupted"):
                            if "预算" in run.get("error", ""):
                                raise RuntimeError("预算不足，保留图与试次；不继续调用")
                            if recovered >= 1:
                                raise RuntimeError(run.get("error") or run["status"])
                            r = await client.post(
                                "/api/runs/" + item["run_id"] + "/resume"
                            )
                            r.raise_for_status()
                            recovered += 1
                            item["recoveries"] = recovered
                        elif run["status"] == "cancelled":
                            raise RuntimeError("运行已取消")
                        await asyncio.sleep(3)
                    ch = (
                        await client.get("/api/chapters/" + item["chapter_id"])
                    ).json()
                    text = ch["canonical"]
                    text_hash = hashlib.sha256(text.encode()).hexdigest()
                    for key in ("preserved_draft_sha256", "preserved_canonical_sha256"):
                        if item.get(key) and item[key] != text_hash:
                            raise ValueError(
                                "恢复过程改变了已经完成的候选正文，不能当作原试次补评"
                            )
                    context = ch["context"]
                    item["retrieval"] = {
                        "mode": context["retrieval_mode"],
                        "fact_count": len(context["facts"]),
                        "source_ids": [x["chapter_id"] for x in context["evidence"]],
                        "archive_retrieved": any(
                            x["chapter_id"] == item["archive_chapter_id"]
                            for x in context["evidence"]
                        ),
                    }
                    if mode in ("full", "no_revision") and not context["facts"]:
                        raise ValueError("完整上下文未包含已确认档案状态")
                    if mode in ("baseline", "no_memory") and (
                        context["facts"] or context["evidence"]
                    ):
                        raise ValueError("关闭历史能力的配置泄漏了检索或结构化状态")
                    item |= {
                        "text": text,
                        "chars": len(text),
                        "review": ch["review"],
                        "context": context,
                        "revisions": max(
                            0,
                            sum(
                                len(
                                    db.all(
                                        "SELECT seq FROM run_events WHERE run_id=? AND type='draft.start'",
                                        (writer_id,),
                                    )
                                )
                                for writer_id in dict.fromkeys(
                                    [item["run_id"], *item.get("prior_run_ids", [])]
                                )
                            )
                            - 1,
                        ),
                        "canonical_version": ch["canonical_revision"],
                        "recoveries": recovered,
                    }
                    judge_id = uid()
                    item["judge_run_id"] = judge_id
                    db.execute(
                        "INSERT INTO runs(id,project_id,kind,config,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                        (
                            judge_id,
                            item["project_id"],
                            "evaluation",
                            encode({"experiment_id": eid, "case_id": case["id"]}),
                            "evaluating",
                            now(),
                            now(),
                        ),
                    )

                    def grounded(value):
                        if [check.constraint for check in value.checks] != case[
                            "constraints"
                        ]:
                            raise ValueError(
                                "constraint字段必须逐字复制输入的两项约束，并保持给定顺序"
                            )
                        if any(
                            c.outcome == "violation"
                            and (not c.quote or c.quote not in text)
                            for c in value.checks
                        ):
                            raise ValueError("违例必须逐字引用续写原文")

                    verdict = await ModelClient(db, settings, retrieval).structured(
                        judge_id,
                        "独立场景评测员",
                        "按已确认事实逐项核对续写。没有触及约束记not_tested，不奖励回避。违例必须逐字引用续写原句。可读性和推进分0–10，不因长篇或简短奖励。",
                        encode(
                            {
                                "canon": case["archive"],
                                "constraints": case["constraints"],
                                "task": case["task"],
                                "continuation": text,
                            }
                        ),
                        Verdict,
                        max_tokens=3000,
                        validator=grounded,
                    )
                    item |= {
                        "verdict": verdict.model_dump(),
                        "status": "completed",
                        "completed_at": now(),
                        "wall_seconds": round(time.monotonic() - started, 2),
                    }
                    db.execute(
                        "UPDATE runs SET status='completed',result=?,updated=? WHERE id=?",
                        (encode(verdict.model_dump()), now(), judge_id),
                    )
                except asyncio.CancelledError:
                    if item.get("run_id"):
                        try:
                            await client.post("/api/runs/" + item["run_id"] + "/pause")
                        except Exception:
                            pass
                    raise
                except Exception as exc:
                    budget_pause = "预算" in str(exc)
                    if budget_pause:
                        halted.set()
                    item |= {
                        "status": "paused_budget" if budget_pause else "failed",
                        "error": type(exc).__name__ + ": " + str(exc)[:500],
                        "completed_at": now(),
                    }
                    if item.get("judge_run_id"):
                        db.execute(
                            "UPDATE runs SET status='failed',error=?,updated=? WHERE id=?",
                            (item["error"], now(), item["judge_run_id"]),
                        )
                    if item.get("run_id") and not budget_pause:
                        run = db.one(
                            "SELECT status FROM runs WHERE id=?", (item["run_id"],)
                        )
                        if run["status"] in (
                            "queued",
                            "running",
                            "awaiting_approval",
                            "interrupted",
                        ):
                            item["background_task_retained"] = True
                finally:
                    if item.get("judge_run_id"):
                        item["grade_events"] = [
                            e | {"data": json.loads(e["data"])}
                            for e in db.all(
                                "SELECT * FROM run_events WHERE run_id=? ORDER BY seq",
                                (item["judge_run_id"],),
                            )
                        ]
                    (out / (item.get("attempt_id") or item["project_id"])).with_suffix(
                        ".json"
                    ).write_text(json.dumps(item, ensure_ascii=False, indent=2))
                    async with lock:
                        persist()
                        done = sum(t.get("status") == "completed" for t in trials)
                        failed = sum(t.get("status") == "failed" for t in trials)
                    print(
                        f"执行图验收 {done}/96 · 失败{failed} · {case['id']} {mode} #{rep} · {item['status']}",
                        flush=True,
                    )

        await asyncio.gather(
            *(
                trial(case, mode, rep)
                for case in inputs["cases"]
                for mode in inputs["modes"]
                for rep in (1, 2)
            )
        )
    value = summary(trials)
    complete = len([t for t in trials if t.get("status") == "completed"]) == 96
    db.execute(
        "UPDATE experiments SET result=?,status=? WHERE id=?",
        (encode(value), "completed" if complete else "partial", eid),
    )
