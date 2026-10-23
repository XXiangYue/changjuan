from __future__ import annotations

import json
import logging
import re
import threading
from uuid import NAMESPACE_URL, uuid5

import jieba

from .db import Database
from .models import ContextPack
from .reranker import LocalReranker

jieba.setLogLevel(logging.WARNING)


class Retrieval:
    def __init__(self, db: Database, settings):
        self.db, self.settings = db, settings
        self.lock = threading.RLock()
        self.embedder = None
        self.vector = None
        self.reranker = LocalReranker()
        self.last_rerank_audit = None
        self.vector_status = (
            "语义检索已开启，首次查询时加载本地模型"
            if settings.values()["vector_enabled"]
            else "未启用语义检索，关键词与故事状态可用"
        )
        jieba.dt.tmp_dir = str(settings.cache)

    def tokens(self, text):
        terms = [t for t in jieba.cut(text) if t.strip() and re.search(r"\w", t)]
        # Fictional names and invented terms are unstable under dictionary segmentation.
        for word in re.findall(r"[\u4e00-\u9fff]+", text):
            terms.extend(word[i : i + 2] for i in range(len(word) - 1))
        return " ".join(terms)

    def vector_init(self):
        if self.vector:
            return
        from fastembed import TextEmbedding
        from qdrant_client import QdrantClient, models

        self.embedder = TextEmbedding(
            "BAAI/bge-small-zh-v1.5",
            cache_dir=str(self.settings.cache / "fastembed"),
            threads=2,
        )
        self.vector = QdrantClient(
            path=str(self.settings.data / "vectors"),
            force_disable_check_same_thread=True,
        )
        if not self.vector.collection_exists("story"):
            self.vector.create_collection(
                "story",
                vectors_config=models.VectorParams(
                    size=512, distance=models.Distance.COSINE
                ),
            )
        self.vector_status = "混合检索 · 中文关键词 + BGE 语义 + 故事状态"

    def index(self, cid):
        ch = self.db.one("SELECT * FROM chapters WHERE id=?", (cid,))
        if not ch["canonical_revision"] or ch["stale"]:
            return
        with self.lock:
            pos = ch["volume"] * 100000 + ch["number"]
            pieces = [
                ch["canonical"][i : i + 450]
                for i in range(0, len(ch["canonical"]), 380)
            ]
            entries = []
            with self.db.transaction() as c:
                old = [
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM chunks WHERE chapter_id=?", (cid,)
                    )
                ]
                for k in old:
                    c.execute("DELETE FROM chunks_fts WHERE id=?", (k,))
                c.execute("DELETE FROM chunks WHERE chapter_id=?", (cid,))
                for i, body in enumerate(pieces):
                    key = str(
                        uuid5(NAMESPACE_URL, f"{cid}/{ch['canonical_revision']}/{i}")
                    )
                    c.execute(
                        "INSERT INTO chunks VALUES(?,?,?,?,?,?,0)",
                        (
                            key,
                            ch["project_id"],
                            cid,
                            ch["canonical_revision"],
                            pos,
                            body,
                        ),
                    )
                    c.execute(
                        "INSERT INTO chunks_fts VALUES(?,?,?)",
                        (key, ch["project_id"], self.tokens(body)),
                    )
                    entries.append((key, body))
            if self.settings.values()["vector_enabled"]:
                try:
                    self.vector_init()
                    from qdrant_client import models

                    # Replace every vector for this chapter; obsolete versions must
                    # not crowd out current evidence before the SQL version filter.
                    self.vector.delete(
                        "story",
                        points_selector=models.FilterSelector(
                            filter=models.Filter(
                                must=[
                                    models.FieldCondition(
                                        key="chapter_id",
                                        match=models.MatchValue(value=cid),
                                    )
                                ]
                            )
                        ),
                    )
                    vectors = list(self.embedder.embed([e[1] for e in entries]))
                    self.vector.upsert(
                        "story",
                        points=[
                            models.PointStruct(
                                id=e[0],
                                vector=v.tolist(),
                                payload={
                                    "project_id": ch["project_id"],
                                    "chapter_id": cid,
                                    "version": ch["canonical_revision"],
                                    "position": pos,
                                },
                            )
                            for e, v in zip(entries, vectors)
                        ],
                    )
                except Exception as e:
                    self.vector_status = (
                        f"语义检索降级：{type(e).__name__}；关键词与故事状态继续可用"
                    )

    def rebuild(self, pid):
        for ch in self.db.all(
            "SELECT id FROM chapters WHERE project_id=? AND canonical_revision>0 AND stale=0",
            (pid,),
        ):
            self.index(ch["id"])
        return {"mode": self.vector_status}

    def search(self, pid, query, before=999999999, limit=8, *, mode=None):
        """Retrieve only sources eligible in SQLite before either channel's top-k.

        The ID filter also works with older vector payloads without position.
        SQLite remains authoritative when a vector delete/update was interrupted.
        ``mode`` exposes the actual production channels for independent evaluation.
        """
        configured = mode is None
        mode = mode or self.settings.values().get("retrieval_policy", "keyword")
        if mode not in {"hybrid", "keyword", "semantic", "reranked"}:
            raise ValueError("检索模式必须是hybrid、keyword、semantic或reranked")
        if not 1 <= limit <= 50:
            raise ValueError("检索条数必须为1–50")
        if not query.strip():
            return []
        eligible = self.db.all(
            "SELECT c.id FROM chunks c JOIN chapters ch ON ch.id=c.chapter_id "
            "WHERE c.project_id=? AND ch.project_id=c.project_id AND c.position<? "
            "AND c.stale=0 AND ch.stale=0 AND ch.canonical_revision>0 "
            "AND c.version=ch.canonical_revision",
            (pid, before),
        )
        eligible_ids = {x["id"] for x in eligible}
        if not eligible_ids:
            return []
        stop = {
            "的",
            "了",
            "是",
            "在",
            "他",
            "她",
            "它",
            "这",
            "那",
            "与",
            "和",
            "一个",
            "本章",
        }
        terms = list(
            dict.fromkeys(
                t
                for t in jieba.cut(query)
                if t.strip() and t not in stop and re.search(r"\w", t)
            )
        )[:18]
        for word in re.findall(r"[\u4e00-\u9fff]+", query):
            terms.extend(word[i : i + 2] for i in range(min(len(word) - 1, 12)))
        ranked = {}
        if terms and mode != "semantic":
            match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
            rows = self.db.all(
                "SELECT c.*,bm25(chunks_fts) AS rank FROM chunks_fts "
                "JOIN chunks c ON c.id=chunks_fts.id JOIN chapters ch ON ch.id=c.chapter_id "
                "WHERE chunks_fts MATCH ? AND c.project_id=? AND ch.project_id=c.project_id "
                "AND c.position<? AND c.stale=0 AND ch.stale=0 "
                "AND ch.canonical_revision>0 AND c.version=ch.canonical_revision "
                "ORDER BY rank,c.id LIMIT ?",
                (match, pid, before, max(24, limit * 3)),
            )
            for i, row in enumerate(rows):
                ranked[row["id"]] = {
                    "row": row,
                    "score": 1 / (60 + i + 1),
                    "channels": {"keyword": i + 1},
                }
        if self.settings.values()["vector_enabled"] and mode != "keyword":
            with self.lock:
                try:
                    self.vector_init()
                    from qdrant_client import models

                    vec = next(self.embedder.query_embed(query))
                    hits = self.vector.query_points(
                        "story",
                        query=vec.tolist(),
                        query_filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="project_id", match=models.MatchValue(value=pid)
                                ),
                                models.HasIdCondition(has_id=sorted(eligible_ids)),
                            ]
                        ),
                        limit=max(30, limit * 3),
                    ).points
                    for i, hit in enumerate(hits):
                        if str(hit.id) not in eligible_ids:
                            continue
                        row = self.db.one(
                            "SELECT c.* FROM chunks c JOIN chapters ch ON ch.id=c.chapter_id "
                            "WHERE c.id=? AND c.project_id=? AND ch.project_id=c.project_id "
                            "AND c.stale=0 AND ch.stale=0 AND c.position<? "
                            "AND c.version=ch.canonical_revision AND ch.canonical_revision>0",
                            (str(hit.id), pid, before),
                            required=False,
                        )
                        if not row:
                            continue
                        entry = ranked.setdefault(
                            row["id"], {"row": row, "score": 0, "channels": {}}
                        )
                        entry["score"] += 1 / (60 + i + 1)
                        entry["channels"]["semantic"] = i + 1
                        entry["similarity"] = float(hit.score)
                except Exception as e:
                    self.vector_status = (
                        f"语义检索降级：{type(e).__name__}；关键词与故事状态继续可用"
                    )
        ranked_rows = [
            x["row"]
            | {
                "score": round(x["score"], 5),
                "channels": x["channels"],
                "semantic_similarity": x.get("similarity"),
            }
            for x in sorted(
                ranked.values(), key=lambda x: (-x["score"], x["row"]["id"])
            )[: max(limit, 24) if mode == "reranked" else limit]
        ]
        if mode == "reranked":
            ranked_rows, self.last_rerank_audit = self.reranker.rerank(
                query, ranked_rows, limit=limit
            )
        # Explicit channel benchmarks stay isolated. The configured product
        # semantic mode can continue with keyword evidence on missing models.
        if configured and mode == "semantic" and not ranked_rows:
            return self.search(pid, query, before, limit, mode="keyword")
        return ranked_rows

    def prepare_reranker(self, *, allow_download=False):
        """Explicit setup only: ordinary source queries never download a model."""
        self.reranker.enabled = True
        return self.reranker.prepare(allow_download=allow_download)

    def context(self, pid, cid, no_memory=False):
        ch = self.db.one(
            "SELECT * FROM chapters WHERE id=? AND project_id=?", (cid, pid)
        )
        project = self.db.one("SELECT revision FROM projects WHERE id=?", (pid,))
        pos = ch["volume"] * 100000 + ch["number"]
        memory = {
            m["name"]: m["content"]
            for m in self.db.all("SELECT * FROM memory WHERE project_id=?", (pid,))
        }
        # Rewriting an earlier chapter uses canon as of that point, not future summaries.
        historical = self.db.one(
            "SELECT id FROM chapters WHERE project_id=? AND canonical_revision>0 AND (volume>? OR (volume=? AND number>?)) LIMIT 1",
            (pid, ch["volume"], ch["volume"], ch["number"]),
            required=False,
        )
        if ch["canonical_revision"] or historical:
            snap = self.db.one(
                "SELECT v.memory_snapshot FROM versions v JOIN chapters c ON c.id=v.chapter_id WHERE c.project_id=? AND (c.volume<? OR (c.volume=? AND c.number<?)) AND c.stale=0 ORDER BY c.volume DESC,c.number DESC,v.version DESC LIMIT 1",
                (pid, ch["volume"], ch["volume"], ch["number"]),
                required=False,
            )
            initial = self.db.one(
                "SELECT content FROM initial_memory WHERE project_id=?",
                (pid,),
                required=False,
            )
            if snap:
                memory = json.loads(snap["memory_snapshot"])
            elif initial:
                memory = json.loads(initial["content"])
            else:
                raise ValueError("此历史稿缺少早期记忆快照，请先建立初始设定再重写")
        recent = self.db.all(
            "SELECT id,title,canonical,summary,volume,number FROM chapters WHERE project_id=? AND canonical_revision>0 AND stale=0 AND (volume<? OR (volume=? AND number<?)) ORDER BY volume DESC,number DESC LIMIT 10",
            (pid, ch["volume"], ch["volume"], ch["number"]),
        )
        plan = json.loads(ch["plan"])
        query = " ".join(
            [
                ch["title"],
                plan.get("goal", ""),
                *plan.get("characters", []),
                *plan.get("hooks", []),
            ]
        )
        facts = (
            []
            if no_memory
            else self.db.all(
                "SELECT * FROM (SELECT *,ROW_NUMBER() OVER(PARTITION BY subject,predicate ORDER BY position DESC,rowid DESC) AS latest FROM facts WHERE project_id=? AND position<? AND stale=0) WHERE latest=1 ORDER BY position DESC LIMIT 400",
                (pid, pos),
            )
        )
        return ContextPack(
            project_id=pid,
            project_revision=project["revision"],
            memory=memory,
            previous_chapter=recent[0]["canonical"] if recent else "",
            recent_summaries=[
                {k: r[k] for k in ("id", "title", "summary", "volume", "number")}
                for r in reversed(recent)
            ],
            evidence=[] if no_memory else self.search(pid, query, pos),
            facts=facts,
            retrieval_mode="消融：未启用历史检索与结构化状态"
            if no_memory
            else f"{self.settings.values().get('retrieval_policy', 'keyword')} · {self.vector_status}",
        )

    def close(self):
        if self.vector:
            self.vector.close()
