"""Create a non-destructive attribution erratum for the frozen novel suite.

No retrieval, scoring, source manuscript writes, or changes to the v1 inputs.
The correction is limited to four question strings identified by oracle review.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "evaluations" / "retrieval-novel-v1"
TARGET = ROOT / "evaluations" / "retrieval-novel-v1.1"
PARENT_SHA256 = {
    "corpus.json": "d39e5f6a570ed93d87a3091f931e312be86e202f03f0ef40e5f3bfecac3d573b",
    "queries.json": "094046cff71718694ddcc42509eb2552e7d46ee604043c813fc810776b4b281f",
    "protocol.json": "890af599cbdf49d8afac864ee6a10452009e88393faa477e0d9e3a13d9aeda6e",
}
REVISED_AT = "2026-10-21T15:41:40+08:00"
CORRECTIONS = {
    "full-ch07-fact1-direct": "洛蘅回铺后自述，她因锉灰咳得紧，提前服了多少温肺丹？",
    "full-ch07-fact1-paraphrase": "据修炉女子回铺后的说法，原来留到夜里的护肺药，她在初审前已经服用了多大一份？",
    "full-ch10-fact1-direct": "洛蘅回铺后自述，从矿道退出来时，什么东西刮伤了她的小臂？",
    "full-ch10-fact1-paraphrase": "按修炉女子回铺后的讲述，她从塌顶处退出借力时，小臂的伤是被哪件工具的哪部分划出的？",
}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def checked_parent():
    files = {name: (PARENT / name).read_bytes() for name in PARENT_SHA256}
    assert {name: sha(data) for name, data in files.items()} == PARENT_SHA256
    return files


def validate(original, revised, corpus):
    before = {q["id"]: q for q in original["queries"]}
    after = {q["id"]: q for q in revised["queries"]}
    assert set(before) == set(after) and len(after) == 96
    assert len({q["query"] for q in after.values()}) == 96
    assert original.keys() == revised.keys()
    assert {k: v for k, v in original.items() if k != "queries"} == {
        k: v for k, v in revised.items() if k != "queries"
    }
    changed = set()
    for qid, prior in before.items():
        current = after[qid]
        if prior == current:
            continue
        changed.add(qid)
        assert {k: v for k, v in prior.items() if k != "query"} == {
            k: v for k, v in current.items() if k != "query"
        }
        assert current["query"] == CORRECTIONS[qid]
    assert changed == set(CORRECTIONS)
    assert Counter(q["split"] for q in after.values()) == {
        "dev": 32,
        "heldout": 64,
    }
    assert len({q["scenario_id"] for q in after.values()}) == 48
    docs = {d["id"]: d for d in corpus["documents"]}
    for q in after.values():
        assert q["gold_semantics"] == "any_of"
        for span in q["evidence_spans"]:
            for gid in span["expected_doc_ids"]:
                d = docs[gid]
                assert d["project"] == q["project"]
                assert d["position"] < q["before"] and not d["stale"]
                assert d["start"] <= span["start"] and d["end"] >= span["end"]
                a, b = span["start"] - d["start"], span["end"] - d["start"]
                assert d["content"][a:b] == span["quote"]
                assert d["source_sha256"] == span["source_sha256"]
                assert d["source_revision"] == span["source_revision"]


def main():
    files = checked_parent()
    original = json.loads(files["queries.json"])
    revised = copy.deepcopy(original)
    for q in revised["queries"]:
        if q["id"] in CORRECTIONS:
            q["query"] = CORRECTIONS[q["id"]]
    corpus = json.loads(files["corpus.json"])
    validate(original, revised, corpus)
    query_bytes = dump(revised)
    protocol = copy.deepcopy(json.loads(files["protocol.json"]))
    protocol["version"] = "novel-v1.1"
    protocol["parent_sha256"] = PARENT_SHA256
    protocol["revision"] = {
        "revised_at": REVISED_AT,
        "parent_dataset": "retrieval-novel-v1",
        "review_source": "/root/novel_oracle_audit 独立AI oracle复核，经root转交两项P3来源归因措辞更正",
        "kind": "annotation_erratum",
        "errata": [
            {
                "scenario_id": sid,
                "query_ids": [sid + "-direct", sid + "-paraphrase"],
                "field": "query",
                "correction": "问句明确限定为洛蘅回铺后自述，保留人物报告的来源属性，不将自述升格为叙述者独立核实。",
                "severity": "P3",
            }
            for sid in ("full-ch07-fact1", "full-ch10-fact1")
        ],
        "unchanged": [
            "corpus.json逐字节与v1相同",
            "来源正文、revision、SHA、quote、span、gold及any_of语义",
            "所有split、before、类别与reference_answer",
            "48情境与96问法的分母；不是新增独立样本",
        ],
        "score_independence": "仅依独立来源归因复核修正；修订者未读取dev或heldout分数、排名或未来排序实现，未按得分挑题。v1原输入与已运行结果全部保留，后续验收应注明使用v1.1。",
        "format_version": "corpus.version与queries.version保留父版novel-v1以确保除四个query字段外输入完全不变；数据集修订号由本protocol.version标识。",
    }
    protocol["sha256"] = {
        "corpus.json": sha(files["corpus.json"]),
        "queries.json": sha(query_bytes),
    }
    protocol["validation"] = {
        "status": "passed",
        "checks": [
            "父版三文件SHA与预先固定值完全一致，运行后再次核验",
            "新corpus字节与父版相同",
            "仅四个指定query字符串变化，其余查询字段和所有源数据不变",
            "96唯一问句、48事实情境、dev32问/heldout64问不变",
            "全部gold项目、时序、版本、span与完整引文逐字匹配不变",
            "未执行检索或评分",
        ],
    }
    payloads = {
        "corpus.json": files["corpus.json"],
        "queries.json": query_bytes,
        "protocol.json": dump(protocol),
    }
    for name, data in payloads.items():
        destination = TARGET / name
        if destination.exists() and destination.read_bytes() != data:
            raise RuntimeError(f"拒绝覆盖不同内容的冻结文件：{destination}")
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        destination = TARGET / name
        if not destination.exists():
            destination.write_bytes(data)
    assert checked_parent() == files
    print(
        json.dumps(
            {
                "sha256": {name: sha(data) for name, data in payloads.items()},
                "parent_unchanged": True,
                "query_fields_changed": 4,
                "retrieval_or_scoring_executed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
