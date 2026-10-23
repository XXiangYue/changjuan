"""Write first immutable engineering summaries of the sealed primary experiment.

No model/API requests, manuscript edits or production DB writes. Run IDs come
from sealed project snapshots, so later rewrite experiments are excluded. Tokens
are provider usage recorded by Meter; currency is the ledger's conservative
price estimate, not a checked invoice. Incomplete blind reviews stay pending.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from novel_agent.studio.models import numbered_paragraphs
from novel_agent.studio.story_state import replay, validate_event

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/resume/live-story-v1"
PRIMARY = BASE / "review-packets/primary"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def load(path):
    return json.loads(path.read_text())


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def verify_bundle(folder):
    manifest = load(folder / "seal.json")
    for name, expected in manifest["files"].items():
        require(
            sha((folder / name).read_bytes()) == expected,
            "封存文件已变：" + str(folder / name),
        )


def usage_summary(rows):
    return {
        "calls": len(rows),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "estimated_yuan": round(sum(row["cost"] for row in rows), 6),
        "reserved_yuan": round(sum(row["reserved"] for row in rows), 6),
        "sum_call_duration_seconds": sum(row["duration"] for row in rows),
        "ledger_states": dict(Counter(row["state"] for row in rows)),
        "recorded_model_identifiers": sorted({row["model"] for row in rows}),
        "by_role": {
            role: {
                "calls": sum(row["role"] == role for row in rows),
                "estimated_yuan": round(
                    sum(row["cost"] for row in rows if row["role"] == role), 6
                ),
            }
            for role in sorted({row["role"] for row in rows})
        },
        "token_basis": "供应商usage经Meter记录；unknown/failed状态若出现则单列，不能当精确usage。",
        "money_basis": "按保存价格input ¥2/M、output ¥8/M保守估算，未扣缓存折扣；不是已核对供应商账单。",
        "prices_in_records": list(dict.fromkeys(row["price"] for row in rows)),
    }


def decode_model_json(text):
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))


def run_summary(run, events, usage):
    writers = [
        event
        for event in events
        if event["type"] == "model.output" and event["data"].get("role") == "作者"
    ]
    revisions = []
    for writer in writers[1:]:
        preceding = [
            event
            for event in events
            if event["seq"] < writer["seq"]
            and event["type"] in ("review", "length_check")
        ]
        trigger = preceding[-1] if preceding else None
        revisions.append(
            {
                "writer_event_seq": writer["seq"],
                "characters": len(writer["data"]["text"]),
                "body_sha256": sha(writer["data"]["text"]),
                "trigger_type": trigger["type"] if trigger else "unknown",
                "trigger_event_seq": trigger["seq"] if trigger else None,
            }
        )
    return {
        "run_id": run["id"],
        "kind": run["kind"],
        "status_at_primary_snapshot": run["status"],
        "error_at_primary_snapshot": run["error"],
        "created": run["created"],
        "completed_at": run["updated"],
        "configuration": run["config"],
        "writer_drafts": [
            {
                "event_seq": event["seq"],
                "characters": len(event["data"]["text"]),
                "han_basic_cjk": len(
                    re.findall(r"[\u4e00-\u9fff]", event["data"]["text"])
                ),
                "body_sha256": sha(event["data"]["text"]),
            }
            for event in writers
        ],
        "automatic_revisions": len(revisions),
        "revision_triggers": revisions,
        "retry_events": [event for event in events if event["type"] == "retry"],
        "failed_or_cancelled_events": [
            event
            for event in events
            if event["type"] in ("failed", "cancelled", "interrupted")
        ],
        "semantic_review_events": [
            {
                "event_seq": event["seq"],
                "passed": event["data"].get("passed"),
                "length_check": event["data"].get("length_check"),
                "finding_counts": dict(
                    Counter(
                        row.get("severity") for row in event["data"].get("findings", [])
                    )
                ),
            }
            for event in events
            if event["type"] == "review"
        ],
        "length_only_review_events": [
            {
                "event_seq": event["seq"],
                "length_check": event["data"].get("length_check"),
            }
            for event in events
            if event["type"] == "length_check"
        ],
        "usage": usage_summary(usage),
        "raw_events": "aggregate-source/run-events-" + run["id"] + ".json",
        "revision_boundary": "automatic_revisions=同run作者全文输出次数减一；结构化/图循环retry另列，不能混作正文返工，也不代表每次改得更好。",
    }


def author_decision(chapter, events):
    proposal = chapter["proposal"]
    memory_outputs = [
        event
        for event in events
        if event["type"] == "model.output" and event["data"].get("role") == "记忆整理员"
    ]
    require(bool(memory_outputs), "缺少原模型记忆输出，不能判断摘要干预")
    original = decode_model_json(memory_outputs[-1]["data"]["text"])
    before, after = original["chapter_summary"], chapter["summary"]
    available = [row["name"] for row in proposal["patches"]]
    accepted_patches = proposal["accepted_patches"]
    accepted_facts = proposal["accepted_facts"]
    accepted_state = proposal["accepted_state_changes"]
    return {
        "accepted_memory": accepted_patches,
        "rejected_memory": [name for name in available if name not in accepted_patches],
        "accepted_fact_indices": accepted_facts,
        "rejected_fact_indices": [
            i for i in range(len(proposal["facts"])) if i not in accepted_facts
        ],
        "accepted_state_indices": accepted_state,
        "rejected_state_indices": [
            i
            for i in range(len(proposal.get("state_changes", [])))
            if i not in accepted_state
        ],
        "summary_override": before != after,
        "model_summary_event_seq": memory_outputs[-1]["seq"],
        "model_summary": before,
        "approved_summary": after,
        "body_external_edit": False,
        "evidence_boundary": "接受/拒绝下标来自封存正式proposal；摘要前值来自最后成功模型输出，后值来自已确认summary。未记录的拒绝动机不由汇总脚本猜测。",
    }


def typed_state(project):
    events = []
    manifest = {}
    for chapter in project["chapters"]:
        manifest[chapter["id"]] = chapter["canonical_revision"]
        proposal = chapter["proposal"]
        paragraphs = numbered_paragraphs(chapter["canonical"])
        for index in proposal["accepted_state_changes"]:
            raw = proposal["state_changes"][index]
            require(
                raw["quote"] in paragraphs[raw["paragraph"] - 1]["text"],
                "已确认提案的引用与封存正文不符",
            )
            payload = {
                key: value
                for key, value in raw.items()
                if key not in ("paragraph", "quote", "labels")
            }
            payload |= {
                "id": "state-"
                + uuid5(
                    NAMESPACE_URL,
                    f"{project['id']}/{chapter['id']}/{chapter['canonical_revision']}/{index}",
                ).hex,
                "position": chapter["volume"] * 100000 + chapter["number"],
                "sequence": index + 1,
                "status": "confirmed",
                "source": {
                    "chapter_id": chapter["id"],
                    "version": chapter["canonical_revision"],
                    "quote": raw["quote"],
                },
            }
            events.append(validate_event(payload))
    snapshot = replay(events, source_versions=manifest).model_dump()
    return {
        "accepted_events": len(events),
        "operations": dict(Counter(event.operation for event in events)),
        "hooks": {
            key: {
                "status": value["status"],
                "payoff_condition": value["payoff_condition"],
                "window_start": value["window_start"],
                "window_end": value["window_end"],
                "resolution_timing": value["resolution_timing"],
            }
            for key, value in snapshot["hooks"].items()
        },
        "known_inventory_slots": sum(
            len(slots) for slots in snapshot["inventories"].values()
        ),
        "knowledge_per_actor": {
            key: sorted(value) for key, value in snapshot["knowledge"].items()
        },
        "snapshot": snapshot,
        "source": "从封存作者已接受的state_changes按生产同样ID/source/version绑定规则确定性回放；不读后来可能变化的当前章节或过期事件。",
        "limits": "confirmed仅表示作者已确认事件和reducer前置条件成立；不证明文学兑现或模型抽取正确。未知库存保持未知，空inventories不等于库存为0或守恒已验证。",
    }


def repair_result():
    folder = BASE / "repair-review"
    seal = load(folder / "seal.json")
    report = load(folder / "review.json")
    require(
        sha((folder / "review.json").read_bytes()) == seal["review_sha256"],
        "局部评审封存SHA不符",
    )
    source = BASE / "review-packets/local-repair-case"
    verify_bundle(source)
    mapping = load(source / "operator-only/mapping.json")
    results = {}
    for assessment in report["assessments"]:
        identity = mapping[assessment["entry_id"]]
        packet = load(source / "public" / (assessment["entry_id"] + ".json"))
        body = next(row["body"] for row in packet["chapters"] if row["number"] == 2)
        require(sha(body) == identity["body_sha256"], "局部映射正文SHA不符")
        for check in assessment["checks"]:
            for evidence in check["evidence"]:
                require(
                    evidence["quote"] in body and evidence["chapter"] == 2,
                    "局部评审引用不符",
                )
        results[identity["relative_to_first_semantic_review"]] = {
            "writer_event_seq": identity["writer_event_seq"],
            "body_sha256": sha(body),
            "satisfied": sum(check["satisfied"] for check in assessment["checks"]),
            "maximum": 5,
            "characters": len(body),
            "within_frozen_length": 1200 <= len(body) <= 1600,
            "within_application_length": 1120 <= len(body) <= 1680,
            "checks": {
                check["id"]: check["satisfied"] for check in assessment["checks"]
            },
        }
    require(set(results) == {"before", "after"}, "局部案例缺前/后角色")
    return {
        "status": "sealed_then_unblinded",
        "independent_ai_not_human": True,
        "review": "repair-review/review.json",
        "review_sha256": seal["review_sha256"],
        "pair": results,
        "characters_increased": results["after"]["characters"]
        - results["before"]["characters"],
        "observed_tradeoff": "指定五项从3/5变5/5，但1627→1990字符；相对协议上限超出由27增至390，应用字数检查也由通过变失败。",
        "limits": "有意选择的一个真实局部返工案例，不是自然错误率、一般修订收益或整卷文学质量提升。不能把计数当独立自然人评价。",
    }


def main():
    outputs = [BASE / "full/result.json", BASE / "result.json"]
    require(
        not any(path.exists() for path in outputs),
        "主汇总已存在，禁止覆盖；后续评审需另写评审汇总",
    )
    verify_bundle(PRIMARY)
    verify_bundle(BASE / "baseline")
    snapshots = load(PRIMARY / "operator-only/primary-snapshots.json")
    exported_full = load(BASE / "full/final-project.json")
    require(snapshots["full"] == exported_full, "full导出快照与primary封存不符")
    exported_baseline = load(BASE / "baseline/final-project.json")
    require(
        snapshots["baseline"] == exported_baseline, "baseline导出快照与primary封存不符"
    )
    began = datetime.now(UTC).isoformat()
    raw_documents = {}
    results = {}
    with sqlite3.connect(f"file:{ROOT / 'data/studio.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for label, project in snapshots.items():
            run_events, run_usage = {}, {}
            for run in project["runs"]:
                rows = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM run_events WHERE run_id=? ORDER BY seq",
                        (run["id"],),
                    )
                ]
                for row in rows:
                    row["data"] = json.loads(row["data"])
                run_events[run["id"]] = rows
                run_usage[run["id"]] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM usage WHERE run_id=? ORDER BY created,id",
                        (run["id"],),
                    )
                ]
                raw_documents["run-events-" + run["id"] + ".json"] = rows
            raw_documents["usage-" + label + ".json"] = run_usage
            chapter_runs = {
                run["chapter_id"]: run
                for run in project["runs"]
                if run["kind"] == "chapter"
            }
            chapters = []
            for chapter in project["chapters"]:
                run = chapter_runs[chapter["id"]]
                events = run_events[run["id"]]
                summary = run_summary(run, events, run_usage[run["id"]])
                require(
                    summary["writer_drafts"][-1]["body_sha256"]
                    == sha(chapter["canonical"]),
                    "末次模型正文不等于封存canon，不能声称无外部正文编辑",
                )
                chapters.append(
                    {
                        "number": chapter["number"],
                        "chapter_id": chapter["id"],
                        "characters": len(chapter["canonical"]),
                        "han_basic_cjk": len(
                            re.findall(r"[\u4e00-\u9fff]", chapter["canonical"])
                        ),
                        "body_sha256": sha(chapter["canonical"]),
                        "canonical_revision": chapter["canonical_revision"],
                        "stale_at_primary": chapter["stale"],
                        "within_frozen_length": 1200
                        <= len(chapter["canonical"])
                        <= 1600,
                        "within_application_length": 1120
                        <= len(chapter["canonical"])
                        <= 1680,
                        "run": summary,
                        "author_confirmation": author_decision(chapter, events),
                    }
                )
            all_rows = [row for rows in run_usage.values() for row in rows]
            chapter_rows = [
                row
                for run in project["runs"]
                if run["kind"] == "chapter"
                for row in run_usage[run["id"]]
            ]
            blueprint_rows = [
                row
                for run in project["runs"]
                if run["kind"] == "blueprint"
                for row in run_usage[run["id"]]
            ]
            results[label] = {
                "schema_version": "live-story-primary-configuration-result/v1",
                "created_at_utc": began,
                "configuration": label,
                "project_id": project["id"],
                "project_revision_at_primary": project["revision"],
                "chapters": chapters,
                "engineering_completed_chapters": len(chapters),
                "chapters_meeting_frozen_length": sum(
                    row["within_frozen_length"] for row in chapters
                ),
                "automatic_revisions_total": sum(
                    row["run"]["automatic_revisions"] for row in chapters
                ),
                "chapter_generation_usage": usage_summary(chapter_rows),
                "blueprint_usage": usage_summary(blueprint_rows),
                "all_primary_runs_usage": usage_summary(all_rows),
                "non_chapter_runs": [
                    run_summary(run, run_events[run["id"]], run_usage[run["id"]])
                    for run in project["runs"]
                    if run["kind"] != "chapter"
                ],
                "typed_final_state": typed_state(project),
                "operator": "Codex AI测试操作者；full通过产品UI、baseline通过同应用API控制器；不是自然人作者。",
                "score_boundary": "baseline单编辑评分与full双编辑评分不能作为质量横向分数；工程完成、长度、独立语义评审分开。",
                "primary_semantic_assessment": {
                    "status": "pending",
                    "reason": "当前工程汇总不读取未封存评审；独立整卷判定完成后另写评审汇总，不改本次primary原稿。",
                },
            }
    captured_state = load(BASE / "full/story-state.json")
    require(
        captured_state["snapshot"] == results["full"]["typed_final_state"]["snapshot"],
        "重建typed终态与full已保存终态不符",
    )
    results["full"]["typed_final_state"]["matches_saved_full_story_state"] = True
    proof = load(BASE / "baseline/copy-proof.json")
    initial_proof = load(BASE / "baseline/source-initial-proof.json")
    source = load(BASE / "generation-code-comparison.json")
    repair = repair_result()
    aggregate = {
        "schema_version": "live-story-primary-experiment-result/v1",
        "created_at_utc": began,
        "unit": "同一brief的两条三章真实创作轨迹，共六章；不是六个独立小说样本。",
        "configurations": {
            label: {
                "result_file": label + "/result.json",
                "characters_by_chapter": [
                    row["characters"] for row in result["chapters"]
                ],
                "han_by_chapter": [row["han_basic_cjk"] for row in result["chapters"]],
                "chapter_run_ids": [row["run"]["run_id"] for row in result["chapters"]],
                "engineering_completed_chapters": result[
                    "engineering_completed_chapters"
                ],
                "chapters_meeting_frozen_length": result[
                    "chapters_meeting_frozen_length"
                ],
                "automatic_revisions_total": result["automatic_revisions_total"],
                "chapter_generation_usage": result["chapter_generation_usage"],
                "blueprint_usage": result["blueprint_usage"],
                "all_primary_runs_usage": result["all_primary_runs_usage"],
                "typed_accepted_events": result["typed_final_state"]["accepted_events"],
            }
            for label, result in results.items()
        },
        "configuration_result_note": "baseline/result.json为先前已封存文件，不覆盖；本轮完整补充记录在aggregate-source/baseline-configuration-result.json。",
        "shared_initial_inputs": {
            "exact_equal": proof["exact_shared_inputs_equal"],
            "source_inputs_sha256": proof["source_inputs_sha256"],
            "baseline_inputs_sha256": proof["baseline_inputs_sha256"],
            "copy_proof": "baseline/copy-proof.json",
            "initial_memory_proof": initial_proof,
            "pre_prose_author_corrections": "author-preparation.json",
            "policy": "同一memory/outline/章计划/premise/style；两组共同拒绝当前章节设计规划patch以保持原scenes；实际接受其他记忆的选择各自保留，不声称无操作者差异。",
        },
        "source_and_runtime": source,
        "operation_differences": [
            "full通过UI，baseline通过同应用API控制器，均为Codex AI操作者，不是自然人使用实验。",
            "共同蓝图由full一次创建并校正后复制给baseline；full蓝图7次调用及图递归重试单列，baseline不重复付规划费。",
            "各章事实/记忆按正文选择接受；摘要均可作者纠错，属于可见操作干预，不是全自主写作。",
            "baseline首章拒绝伏笔时误称原初已有计划为新增，erratum已保留；此项不算模型错误，并限制严格算法因果归因。",
        ],
        "author_interventions": {
            label: [
                {
                    "chapter": row["number"],
                    "accepted_memory": row["author_confirmation"]["accepted_memory"],
                    "rejected_memory": row["author_confirmation"]["rejected_memory"],
                    "accepted_facts": row["author_confirmation"][
                        "accepted_fact_indices"
                    ],
                    "rejected_facts": row["author_confirmation"][
                        "rejected_fact_indices"
                    ],
                    "accepted_typed": row["author_confirmation"][
                        "accepted_state_indices"
                    ],
                    "rejected_typed": row["author_confirmation"][
                        "rejected_state_indices"
                    ],
                    "summary_override": row["author_confirmation"]["summary_override"],
                }
                for row in result["chapters"]
            ]
            for label, result in results.items()
        },
        "typed_final_state": {
            label: {
                key: value
                for key, value in result["typed_final_state"].items()
                if key != "snapshot"
            }
            for label, result in results.items()
        },
        "local_repair_case": repair,
        "primary_independent_assessment": {
            "status": "pending",
            "review_directory": "primary-review",
            "unsealed_results_used": False,
            "later_policy": "另写评审汇总；primary packet和本工程快照不覆盖。",
        },
        "acceptance_status": {
            "engineering": "两组各三章已明确确认并导出",
            "frozen_length": "两组各0/3在1200–1600；因此两组均已不能满足完整冻结验收，不需要等文学评分才能判长度失败。",
            "semantic_and_narrative": "pending，未推断整卷P1/P2/P3或叙事胜负。",
        },
        "excluded_followups": [
            "live-rewrite-v1单章UI全文改写属于额外干预，不包含于本轮调用、费用、正文或胜负。",
            "后续历史改稿/v2不得替换primary；如实施需另建验收。",
        ],
        "limits": [
            "每配置一条三章轨迹，不作统计显著性或总体文学质量推断。",
            "独立AI文本判断与自然人评价不同；没有真人满意度/追读率。",
            "局部3/5→5/5同时带来长度恶化，不能只展示改善一面。",
            "typed reducer保证被确认事件的确定语义与前置条件，不证明抽取准确或小说一定兑现。",
            "source副本于第2章保存；五项与baseline调用前指纹一致，但没有调用前全仓快照或进程代码内省。未reload依据为操作者运行记录，不以文件哈希独立证明。",
            "费用是保存价格下保守估算，真实账单未核对。",
        ],
        "provenance": {
            "primary_seal_sha256": sha((PRIMARY / "seal.json").read_bytes()),
            "full_final_project_sha256": sha(
                (BASE / "full/final-project.json").read_bytes()
            ),
            "baseline_final_project_sha256": sha(
                (BASE / "baseline/final-project.json").read_bytes()
            ),
            "summary_script_sha256": sha(Path(__file__).read_bytes()),
            "database_access": "SQLite mode=ro，仅查询封存runIDs对应run_events/usage；没有生产数据库写入或模型/API请求。",
        },
    }
    for name, data in raw_documents.items():
        write_new(BASE / "aggregate-source" / name, data)
    write_new(
        BASE / "aggregate-source/baseline-configuration-result.json",
        results["baseline"],
    )
    write_new(BASE / "full/result.json", results["full"])
    write_new(BASE / "result.json", aggregate)
    output_files = [
        BASE / "result.json",
        BASE / "full/result.json",
        *sorted((BASE / "aggregate-source").glob("*.json")),
    ]
    write_new(
        BASE / "aggregate-source/seal.json",
        {
            "created_at_utc": began,
            "files": {
                str(path.relative_to(BASE)): sha(path.read_bytes())
                for path in output_files
            },
            "policy": "首次工程汇总；后续评审另存，不修改这些文件。",
        },
    )
    print(
        json.dumps(
            {
                "aggregate": str(BASE / "result.json"),
                "full_result": str(BASE / "full/result.json"),
                "costs": {
                    label: data["all_primary_runs_usage"]["estimated_yuan"]
                    for label, data in results.items()
                },
                "automatic_revisions": {
                    label: data["automatic_revisions_total"]
                    for label, data in results.items()
                },
                "primary_semantic_review": "pending",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
