"""Independent source-retrieval benchmark; local embeddings, no chat API calls.

The corpus is AI-authored, pre-chunked evidence, not a human-labeled novel QA
benchmark. Same-scenario queries stay in one split and are not independent.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from importlib import metadata, util
from pathlib import Path
import json
import random
import statistics
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from .db import Database, now
from .retrieval import Retrieval
from .settings import ROOT, Settings


def _items(path, key):
    value = json.loads(path.read_text())
    return value if isinstance(value, list) else value[key]


def _percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(int((len(values) - 1) * fraction), len(values) - 1)], 3)


def _summary(trials):
    positives = [t for t in trials if t["expected_doc_ids"]]
    negatives = [t for t in trials if not t["expected_doc_ids"]]
    by_scenario = defaultdict(list)
    for t in positives:
        by_scenario[t["scenario_id"]].append(t["recall_at_5"])
    return {
        "queries": len(trials),
        "scenarios": len({t["scenario_id"] for t in trials}),
        "answerable_queries": len(positives),
        "no_answer_queries": len(negatives),
        "query_recall_at_5": round(
            statistics.mean(t["recall_at_5"] for t in positives), 6
        )
        if positives
        else None,
        "scenario_macro_recall_at_5": round(
            statistics.mean(statistics.mean(v) for v in by_scenario.values()), 6
        )
        if positives
        else None,
        "mrr_at_5": round(
            statistics.mean(t["reciprocal_rank_at_5"] for t in positives), 6
        )
        if positives
        else None,
        "leaked_results": sum(len(t["leaked_doc_ids"]) for t in trials),
        "empty_result_queries": sum(not t["retrieved_doc_ids"] for t in trials),
        "no_answer_queries_with_any_evidence": sum(
            bool(t["retrieved_doc_ids"]) for t in negatives
        ),
        "p50_search_ms": _percentile([t["elapsed_ms"] for t in trials], 0.5),
        "p95_search_ms": _percentile([t["elapsed_ms"] for t in trials], 0.95),
    }


def evaluate_retrieval(
    *, split="heldout", dataset="retrieval-v2", include_reranker=False, root=ROOT
):
    root = Path(root).resolve()
    if split not in {"dev", "heldout", "all"}:
        raise ValueError("split必须为dev、heldout或all")
    if dataset not in {"retrieval-v2", "retrieval-novel-v1", "retrieval-novel-v1.1"}:
        raise ValueError("未知冻结检索数据集")
    data_dir = root / "evaluations" / dataset
    documents = _items(data_dir / "corpus.json", "documents")
    queries = _items(data_dir / "queries.json", "queries")
    if split != "all":
        queries = [q for q in queries if q["split"] == split]
    if not queries:
        raise ValueError("评估分组为空")
    protocol_path = data_dir / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    # Hash freeze must already exist. Never bless an edited corpus here.
    frozen = protocol.get("sha256", protocol.get("files", {}))
    for name in ("corpus.json", "queries.json"):
        expected = frozen.get(name)
        if isinstance(expected, dict):
            expected = expected.get("sha256")
        if expected != sha256((data_dir / name).read_bytes()).hexdigest():
            raise ValueError(f"冻结输入哈希不匹配：{name}")
    trial_id = now().replace(":", "-") + "-" + split + "-" + uuid4().hex[:6]
    out = root / "artifacts/resume" / dataset / trial_id
    out.mkdir(parents=True)
    measured_sources = [
        Path(__file__),
        root / "src/novel_agent/studio/retrieval.py",
        root / "src/novel_agent/studio/reranker.py",
    ]
    frozen_source_hashes = {}
    for source in measured_sources:
        content = source.read_bytes()
        target = out / "source" / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        frozen_source_hashes[str(source.relative_to(root))] = sha256(
            content
        ).hexdigest()
    (out / "before-run.json").write_text(
        json.dumps(
            {
                "started_at": now(),
                "source_sha256": frozen_source_hashes,
                "protocol_sha256": sha256(protocol_path.read_bytes()).hexdigest(),
                "selected_before_heldout": "候选策略比较；最终产品策略必须按dev选择并另存，不能用heldout选最高分。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    work = root / ".cache/resume-retrieval-v2" / trial_id
    settings = Settings(work)
    settings.cache = root / ".cache"
    settings.update({"vector_enabled": True})
    db = Database(settings.data / "studio.sqlite")
    retrieval = Retrieval(db, settings)
    legacy_path = root / "artifacts/resume/baseline-v1/retrieval.py"
    spec = util.spec_from_file_location(
        "novel_agent.studio._frozen_retrieval_v1", legacy_path
    )
    legacy_module = util.module_from_spec(spec)
    spec.loader.exec_module(legacy_module)
    legacy = legacy_module.Retrieval(db, settings)
    uuid_to_doc = {}
    doc_by_id = {d["id"]: d for d in documents}
    chapters = {}
    for d in documents:
        chapter_key = (d["project"], d["volume"], d["chapter"])
        current = chapters.setdefault(chapter_key, {"version": 0, "stale": True})
        if not d["stale"]:
            current["version"] = max(current["version"], d["canonical_revision"])
            current["stale"] = False
    try:
        with db.transaction() as c:
            for project in sorted({d["project"] for d in documents}):
                c.execute(
                    "INSERT INTO projects(id,title,genre,premise,style) VALUES(?,?,?,?,?)",
                    (
                        project,
                        project,
                        "检索工程夹具",
                        "AI构造档案，非生成样书",
                        "原文",
                    ),
                )
            for (project, volume, number), state in chapters.items():
                c.execute(
                    "INSERT INTO chapters(id,project_id,volume,number,title,canonical_revision,stale) VALUES(?,?,?,?,?,?,?)",
                    (
                        f"{project}:{volume}:{number}",
                        project,
                        volume,
                        number,
                        "已切块档案",
                        state["version"],
                        int(state["stale"]),
                    ),
                )
            for d in documents:
                key = str(uuid5(NAMESPACE_URL, "retrieval-v2:" + d["id"]))
                uuid_to_doc[key] = d["id"]
                c.execute(
                    "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
                    (
                        key,
                        d["project"],
                        f"{d['project']}:{d['volume']}:{d['chapter']}",
                        d["canonical_revision"],
                        d["volume"] * 100000 + d["chapter"],
                        d["content"],
                        int(d["stale"]),
                    ),
                )
                c.execute(
                    "INSERT INTO chunks_fts VALUES(?,?,?)",
                    (key, d["project"], retrieval.tokens(d["content"])),
                )
        print(
            f"档案 {len(documents)} 段；{split} 查询 {len(queries)}；加载本地BGE。",
            flush=True,
        )
        started = time.perf_counter()
        retrieval.vector_init()
        from qdrant_client import models

        vectors = list(
            retrieval.embedder.embed([d["content"] for d in documents], batch_size=64)
        )
        retrieval.vector.upsert(
            "story",
            points=[
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, "retrieval-v2:" + d["id"])),
                    vector=v.tolist(),
                    payload={"project_id": d["project"]},
                )
                for d, v in zip(documents, vectors)
            ],
        )
        setup_seconds = time.perf_counter() - started
        legacy.embedder, legacy.vector = retrieval.embedder, retrieval.vector
        next(retrieval.embedder.query_embed("预热查询"))
        modes = ["legacy", "keyword", "semantic", "hybrid"]
        reranker_setup = None
        if include_reranker:
            reranker_setup = retrieval.prepare_reranker(allow_download=False)
            if not reranker_setup["ready"]:
                raise RuntimeError(
                    "重排器未准备，不能以降级结果冒充重排实验："
                    + reranker_setup["state"]
                )
            modes.append("reranked")
        trials = []
        randomizer = random.Random(20261021)
        for index, q in enumerate(queries):
            order = modes.copy()
            randomizer.shuffle(order)
            for mode in order:
                started = time.perf_counter()
                rows = (
                    legacy.search(q["project"], q["query"], q["before"], 5)
                    if mode == "legacy"
                    else retrieval.search(
                        q["project"], q["query"], q["before"], 5, mode=mode
                    )
                )
                elapsed = 1000 * (time.perf_counter() - started)
                found = [uuid_to_doc[r["id"]] for r in rows]
                expected = set(q["expected_doc_ids"])
                leaked = []
                for did in found:
                    d = doc_by_id[did]
                    state = chapters[(d["project"], d["volume"], d["chapter"])]
                    if (
                        d["project"] != q["project"]
                        or d["stale"]
                        or d["volume"] * 100000 + d["chapter"] >= q["before"]
                        or state["version"] != d["canonical_revision"]
                    ):
                        leaked.append(did)
                trials.append(
                    {
                        "query_id": q["id"],
                        "scenario_id": q["scenario_id"],
                        "split": q["split"],
                        "category": q["category"],
                        "query_subset": q.get("query_subset", "unspecified"),
                        "stress_input": bool(q.get("stress_input", False)),
                        "mode": mode,
                        "expected_doc_ids": sorted(expected),
                        "retrieved_doc_ids": found,
                        "leaked_doc_ids": leaked,
                        "gold_semantics": q.get("gold_semantics", "all_documents"),
                        "recall_at_5": (
                            float(bool(expected & set(found)))
                            if q.get("gold_semantics") == "any_of"
                            else len(expected & set(found)) / len(expected)
                        )
                        if expected
                        else None,
                        "reciprocal_rank_at_5": next(
                            (
                                1 / (rank + 1)
                                for rank, did in enumerate(found)
                                if did in expected
                            ),
                            0,
                        )
                        if expected
                        else None,
                        "elapsed_ms": round(elapsed, 3),
                        "trace": [
                            {
                                "id": uuid_to_doc[r["id"]],
                                "score": r["score"],
                                "channels": r.get("channels"),
                            }
                            for r in rows
                        ],
                        "rerank_audit": retrieval.last_rerank_audit
                        if mode == "reranked"
                        else None,
                    }
                )
            if (index + 1) % 20 == 0:
                print(
                    f"完成 {index + 1}/{len(queries)} 查询 ×{len(modes)}配置",
                    flush=True,
                )
        result = {
            "id": trial_id,
            "dataset": dataset,
            "completed_at": now(),
            "split": split,
            "corpus_documents": len(documents),
            "query_count": len(queries),
            "scenario_count": len({q["scenario_id"] for q in queries}),
            "model": "BAAI/bge-small-zh-v1.5",
            "vector_status": retrieval.vector_status,
            "reranker_setup": reranker_setup,
            "index_setup_seconds": round(setup_seconds, 3),
            "libraries": {
                n: metadata.version(n) for n in ("fastembed", "qdrant-client", "jieba")
            },
            "hashes": {
                str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
                for p in [
                    protocol_path,
                    data_dir / "corpus.json",
                    data_dir / "queries.json",
                    legacy_path,
                    Path(__file__),
                    root / "src/novel_agent/studio/retrieval.py",
                ]
            },
            "by_mode": {
                mode: _summary([t for t in trials if t["mode"] == mode])
                for mode in modes
            },
            "by_category": {
                category: {
                    mode: _summary(
                        [
                            t
                            for t in trials
                            if t["mode"] == mode and t["category"] == category
                        ]
                    )
                    for mode in modes
                }
                for category in sorted({q["category"] for q in queries})
            },
            "by_subset": {
                group: {
                    mode: _summary(
                        [
                            t
                            for t in trials
                            if t["mode"] == mode and t["query_subset"] == group
                        ]
                    )
                    for mode in modes
                }
                for group in sorted({t["query_subset"] for t in trials})
            },
            "by_stress": {
                group: {
                    mode: _summary(
                        [
                            t
                            for t in trials
                            if t["mode"] == mode and t["stress_input"] == flag
                        ]
                    )
                    for mode in modes
                }
                for group, flag in (("candidate_starvation", True), ("regular", False))
            },
            "trials": trials,
            "limitations": [
                "AI构造档案与oracle；不是人工标注QA、自然长篇分布或生成质量实验。",
                "同情境查询共享事实，按情境分组；query均值不能当独立240样本推断。",
                "P50/P95含热模型的单次查询嵌入与本地检索，不含初始化/文档嵌入/生成；配置顺序按固定种子打散。",
                "档案事先切块，当前比较不证明原文切块策略或重排器的独立收益。",
                "无答案返回证据不等于模型编造答案；单列证据返回次数，未测回答Faithfulness。",
                "向量payload沿用旧格式；同一向量集合与SQL语料比较过滤策略，未替换模型。",
            ],
        }
        assert all(
            sha256((root / p).read_bytes()).hexdigest() == fingerprint
            for p, fingerprint in frozen_source_hashes.items()
        ), "运行中源码变化，此次不得作为固定配置验收"
        result["source_before_sha256"] = frozen_source_hashes
        if dataset.startswith("retrieval-novel-v1"):
            result["limitations"] = [
                "来自既有两卷24章的48个AI标注事实、96问法；源小说参与过开发，新冻结问题不是新故事外部泛化集。",
                "按来源整章隔离dev/heldout；同事实改写及同章事实相关，不能当独立96个案例。",
                "生产450字符/步长380切块；重叠gold为any_of，命中一个即满足事实证据需求。",
                "无无答案查询，不能判断拒答或回答Faithfulness；只测证据召回，不测创作质量或自然长篇遗忘率。",
                "检索延迟含热模型单查询嵌入与检索/重排；不含首次加载、文档嵌入与生成。候选顺序固定种子打散。",
                "重排score是未校准相关性logit；没有把有效来源或高分当作语义真实保证。",
            ]
        (out / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        lines = [
            "# 时序检索独立验收",
            "",
            f"分组 {split}；{result['scenario_count']} 个情境，{len(queries)} 条查询；原始档案 {len(documents)} 段。",
            "",
            "| 配置 | 可回答查询 | Recall@5 | 情境宏平均Recall@5 | MRR@5 | 非法来源数 | 无答案返回证据次数 | P50毫秒 | P95毫秒 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for mode, m in result["by_mode"].items():
            lines.append(
                f"| {mode} | {m['answerable_queries']} | {m['query_recall_at_5']} | {m['scenario_macro_recall_at_5']} | {m['mrr_at_5']} | {m['leaked_results']} | {m['no_answer_queries_with_any_evidence']}/{m['no_answer_queries']} | {m['p50_search_ms']} | {m['p95_search_ms']} |"
            )
        lines += [
            "",
            *["- " + x for x in result["limitations"]],
            "",
            "逐项命中、失败和分层结果见同目录 results.json。",
        ]
        (out / "报告.md").write_text("\n".join(lines))
        print(json.dumps(result["by_mode"], ensure_ascii=False, indent=2), flush=True)
        print(str(out / "报告.md"), flush=True)
        return result
    finally:
        retrieval.close()
