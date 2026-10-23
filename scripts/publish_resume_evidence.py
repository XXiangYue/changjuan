"""Publish frozen local measurements; never touch prose, votes or model APIs."""

from pathlib import Path
from hashlib import sha256
import json
import random
import statistics

from novel_agent.studio.db import Database, encode, now

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    path = ROOT / relative
    return json.loads(path.read_text()), {
        "path": relative,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def publish():
    rel = "artifacts/resume/retrieval-novel-v1.1/2026-10-21T07-51-20.090641+00-00-heldout-d339cc/results.json"
    result, receipt = load(rel)
    selection, selection_receipt = load(
        "artifacts/resume/retrieval-novel-v1.1/selected-policy-before-heldout.json"
    )
    assert selection["selected"] == "keyword" and selection["heldout_seen"] is False
    chapter_scores = {}
    for trial in result["trials"]:
        chapter = trial["scenario_id"].split("-fact")[0]
        chapter_scores.setdefault(chapter, {}).setdefault(trial["mode"], []).append(
            trial["recall_at_5"]
        )
    deltas = [
        statistics.mean(v["keyword"]) - statistics.mean(v["legacy"])
        for v in chapter_scores.values()
    ]
    rng = random.Random(20261021)
    samples = sorted(
        statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(10000)
    )
    analysis = {
        "computed_at": now(),
        "source": receipt,
        "analysis_status": "post-result descriptive analysis; not preregistered significance test",
        "chapter_clusters": len(deltas),
        "cluster_deltas": dict(zip(chapter_scores, deltas)),
        "mean_delta": statistics.mean(deltas),
        "exploratory_paired_chapter_bootstrap_95_percentile": [
            samples[249],
            samples[9749],
        ],
        "seed": 20261021,
        "replicates": 10000,
        "limitation": "16章来自同设定两卷，章间仍相关；区间不证明一般泛化或文学质量。",
    }
    target = ROOT / "artifacts/resume/retrieval-novel-v1.1/heldout-analysis.json"
    if not target.exists():
        target.write_text(json.dumps(analysis, ensure_ascii=False, indent=2))
    db = Database(ROOT / "data/studio.sqlite")

    def insert(identifier, name, value):
        previous = db.one(
            "SELECT result FROM experiments WHERE id=?", (identifier,), required=False
        )
        if previous:
            assert json.loads(previous["result"]) == value, "已有证据不同，必须使用新ID"
            return
        db.execute(
            "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
            (
                identifier,
                name,
                encode(
                    {
                        "source": "frozen_artifact_import",
                        "no_model_call_during_import": True,
                    }
                ),
                encode(value),
                "completed",
                now(),
            ),
        )

    insert(
        "resume-novel-retrieval-v11",
        "真实正文检索 · 隔离验收与延迟取舍",
        {
            "metrics": {
                "来源章节": 16,
                "指定事实": 32,
                "查询问法": 64,
                "默认策略命中率": "81.25%",
                "原策略命中率": "76.56%",
                "默认策略P95": "7.69ms",
            },
            "retrieval_evaluation": {"by_mode": result["by_mode"]},
            "raw": receipt,
            "selection": selection_receipt,
            "interpretation": "关键词在dev按预设规则选定，heldout不用于改选。每事实两问相关；非法来源为0。重排命中62/64但P95约9秒，作为可选能力。不是生成正确率或小说质量结论。",
            "cluster_analysis": "artifacts/resume/retrieval-novel-v1.1/heldout-analysis.json",
        },
    )
    synthetic, sr = load(
        "artifacts/resume/retrieval-v2/2026-10-21T07-18-45.498162+00-00-heldout-43e797/results.json"
    )
    insert(
        "resume-synthetic-retrieval-v2",
        "短档案检索 · 保留融合未获益结果",
        {
            "metrics": {
                "情境": 60,
                "查询": 180,
                "融合Recall@5": "91.67%",
                "原策略Recall@5": "94.17%",
            },
            "retrieval_evaluation": {"by_mode": synthetic["by_mode"]},
            "raw": sr,
            "interpretation": "短AI构造档案；同事实改写不独立。新融合总体未优于原策略。无答案题返回证据不等于模型编造答案，未测试答案生成/拒答。",
        },
    )
    live, lr = load("artifacts/resume/mcp-live/2026-10-21-task-v1/result.json")
    insert(
        "resume-live-mcp-v1",
        "真实MCP工具任务 · 错误反馈与待确认提案",
        {
            "metrics": {
                "真实模型调用": 3,
                "工具调用": 4,
                "终态": "5项提案pending，未定稿",
            },
            "raw": lr,
            "result": live,
            "interpretation": "人工冻结短章/未通过审核的准备壳，真实模型完成查询、错引文拒绝和修正提案；不是文学审核或自动定稿成绩。",
        },
    )
    print(
        json.dumps(
            {
                "published": 3,
                "chapter_clusters": len(deltas),
                "exploratory_interval": analysis[
                    "exploratory_paired_chapter_bootstrap_95_percentile"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    publish()
