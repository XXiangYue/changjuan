"""Validate and seal independent AI reading judgments before revealing source labels.

Usage: .venv/bin/python scripts/import_agent_reading.py /absolute/path/to/study
No model calls and no writes to human blind-vote fields.
"""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Literal
import argparse
import json

from pydantic import BaseModel, ConfigDict, Field
from novel_agent.studio.db import Database, encode, now, uid

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    "full": "完整工作流",
    "baseline": "旧策略对照",
    "tie": "接近",
    "mixed": "无多数",
}


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    side: Literal["a", "b"]
    quote: str = Field(min_length=15, max_length=70)


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str
    preference: Literal["a", "b", "tie"]
    confidence: Literal["low", "medium", "high"]
    reason: str = Field(min_length=40, max_length=500)
    evidence: list[Evidence] = Field(min_length=2, max_length=2)


class AgentReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_id: str
    reviewer_type: Literal["ai_subagent"]
    reviews: list[Judgment] = Field(min_length=6, max_length=6)
    scope_note: str


def import_study(folder: Path):
    folder = folder.resolve()
    if not folder.is_relative_to(ROOT / "artifacts/evaluation/agent-reading"):
        raise ValueError("研究目录必须在本工作目录的agent-reading下")
    protocol = json.loads((folder / "protocol.json").read_text())
    db = Database(ROOT / "data/studio.sqlite")
    human_before = db.all("SELECT * FROM blind_pairs ORDER BY id")
    source = [{k: p[k] for k in ("id", "a", "b")} for p in human_before]
    assert (
        sha256(
            json.dumps(source, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        == protocol["source_pairs_sha256"]
    ), "片段已经变更"
    readings, receipts, packets = [], [], {}
    for index, packet_meta in enumerate(protocol["packets"], 1):
        packet_path = ROOT / packet_meta["path"]
        assert packet_path.resolve().parent == folder
        assert sha256(packet_path.read_bytes()).hexdigest() == packet_meta["sha256"]
        packet = json.loads(packet_path.read_text())
        path = folder / f"review-{index:02}.json"
        reading = AgentReading.model_validate_json(path.read_text())
        assert (
            reading.reviewer_id == packet["reviewer_id"] == packet_meta["reviewer_id"]
        )
        samples = {s["sample_id"]: s for s in packet["samples"]}
        packets[reading.reviewer_id] = samples
        assert (
            len({r.sample_id for r in reading.reviews}) == 6
            and {r.sample_id for r in reading.reviews} == samples.keys()
        )
        for review in reading.reviews:
            assert {e.side for e in review.evidence} == {"a", "b"}
            for evidence in review.evidence:
                assert evidence.quote in samples[review.sample_id][evidence.side], (
                    "引文不属于对应原文"
                )
        readings.append(reading)
        receipts.append(
            {
                "reviewer_id": reading.reviewer_id,
                "file": path.name,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        )
    # Commit receipts before loading any source labels or presentation mapping.
    seal_path = folder / "sealed-results.json"
    if seal_path.exists():
        assert json.loads(seal_path.read_text())["receipts"] == receipts, (
            "已封存的评审结果发生变化"
        )
    else:
        seal_path.write_text(
            json.dumps(
                {"sealed_at": now(), "receipts": receipts}, ensure_ascii=False, indent=2
            )
        )
    private_mapping = (
        ROOT / ".cache/agent-reading" / (protocol["run_id"] + "-mapping.json")
    )
    mapping = json.loads(
        (
            private_mapping if private_mapping.exists() else folder / "mapping.json"
        ).read_text()
    )
    pairs = {p["id"]: p for p in human_before}
    reviews = []
    for reading in readings:
        for review in reading.reviews:
            assignment = mapping[reading.reviewer_id + ":" + review.sample_id]
            pair = pairs[assignment["pair_id"]]
            for side in ("a", "b"):
                assert (
                    packets[reading.reviewer_id][review.sample_id][side]
                    == pair[assignment["presented_" + side + "_is"]]
                ), "版本映射与实际片段不符"
            labels = json.loads(pair["labels"])
            presented = {
                side: labels[assignment["presented_" + side + "_is"]]
                for side in ("a", "b")
            }
            reviews.append(
                review.model_dump()
                | {
                    "reviewer_id": reading.reviewer_id,
                    "pair_id": pair["id"],
                    "chapter": labels["chapter"],
                    "selected_version": "tie"
                    if review.preference == "tie"
                    else presented[review.preference],
                    "presented_labels": presented,
                }
            )
    counts = Counter(r["selected_version"] for r in reviews)
    by_sample = []
    for sid in sorted({r["sample_id"] for r in reviews}):
        group = [r for r in reviews if r["sample_id"] == sid]
        tally = Counter(r["selected_version"] for r in group)
        majority = next((k for k, v in tally.items() if v >= 2), "mixed")
        by_sample.append(
            {
                "sample_id": sid,
                "chapter": group[0]["chapter"],
                "full": tally["full"],
                "baseline": tally["baseline"],
                "tie": tally["tie"],
                "majority": majority,
                "unanimous": max(tally.values()) == 3,
            }
        )
    result = {
        "summary": "3位Codex子代理在独立上下文中各比较6组匿名片段，共18个AI选择；与自然人盲评分别保存。",
        "metrics": {
            "AI评审代理": 3,
            "片段组数": 6,
            "有效AI选择": len(reviews),
            "选择完整工作流": counts["full"],
            "选择旧策略": counts["baseline"],
            "认为接近": counts["tie"],
        },
        "agent_reading": {
            "reviewer_type": "ai_subagent",
            "interpretation": "完整工作流8票、旧策略7票、接近3票，未显示明确的整体追读优势。六组中两组多数偏好完整工作流、两组偏好旧策略、一组接近、一组无多数。",
            "observations": [
                "完整工作流的身体代价、押砧损失与人物互动在第3、5章片段获得一致偏好。",
                "第9章三位代理均偏好旧策略中更直接的公共危机；完整工作流的账页与手续辨认增加了理解负担。",
                "第7章对现场调查与限时查访的偏好有分歧，第1章无多数；这些意见用于后续编辑，不改写本轮评测稿。",
            ],
            "by_sample": by_sample,
            "reviews": reviews,
            "receipts": receipts,
            "protocol": str((folder / "protocol.json").relative_to(ROOT)),
        },
        "limitations": protocol["limitations"]
        + ["Codex子代理用量不计入DeepSeek费用账本；独立模型响应标识未取得"],
    }
    (folder / "mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2)
    )
    (folder / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    title = "Codex子代理匿名片段评审 v1"
    old = db.one("SELECT id FROM experiments WHERE name=?", (title,), required=False)
    with db.transaction() as c:
        if old:
            c.execute(
                "UPDATE experiments SET config=?,result=?,status='completed' WHERE id=?",
                (encode(protocol), encode(result), old["id"]),
            )
        else:
            c.execute(
                "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
                (uid(), title, encode(protocol), encode(result), "completed", now()),
            )
    assert db.all("SELECT * FROM blind_pairs ORDER BY id") == human_before
    lines = [
        "# Codex子代理匿名片段评审",
        "",
        result["summary"],
        "",
        result["agent_reading"]["interpretation"],
        "",
        *["- " + item for item in result["agent_reading"]["observations"]],
        "",
        "| 片段 | 来源章节 | 完整工作流 | 旧策略 | 接近 | 多数选择 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    lines += [
        f"| {s['sample_id']} | {s['chapter']} | {s['full']} | {s['baseline']} | {s['tie']} | {NAMES[s['majority']]} |"
        for s in by_sample
    ]
    for sid in sorted({r["sample_id"] for r in reviews}):
        lines += ["", "## " + sid, ""]
        for r in (r for r in reviews if r["sample_id"] == sid):
            lines += [
                f"### {r['reviewer_id']}：{NAMES[r['selected_version']]} · 置信度{r['confidence']}",
                "",
                f"原显示 A：{NAMES[r['presented_labels']['a']]}；B：{NAMES[r['presented_labels']['b']]}。",
                "",
                r["reason"],
                "",
            ]
            lines += [
                f"> {e['side'].upper()}：" + e["quote"].replace("\n", "\n> ") + "\n"
                for e in r["evidence"]
            ]
    lines += [
        "",
        "## 方法与范围",
        "",
        *["- " + item for item in result["limitations"]],
        "",
        "评审文件先核对引文并封存哈希，再读取版本映射；未更改真实用户的投票。原始片段、代理结果、封存回执和映射位于 "
        + str(folder.relative_to(ROOT))
        + "。",
    ]
    (ROOT / "artifacts/evaluation/AI代理片段评审.md").write_text("\n".join(lines))
    print(json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    import_study(parser.parse_args().directory)
