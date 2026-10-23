"""Source eligibility and candidate starvation against a real local Qdrant.

Vectors are deterministic fixture vectors, not an embedding-quality benchmark.
"""

from uuid import NAMESPACE_URL, uuid5

import numpy as np
import pytest
from qdrant_client import QdrantClient, models

from novel_agent.studio.db import Database
from novel_agent.studio.retrieval import Retrieval
from novel_agent.studio.settings import Settings


def source(
    db,
    label,
    *,
    project="book",
    number=1,
    version=1,
    canonical=1,
    stale=0,
    chapter_stale=0,
    text="旧渡口的铜钥匙交给药师保管。",
):
    db.execute(
        "INSERT OR IGNORE INTO projects(id,title,genre,premise,style) VALUES(?,?,?,?,?)",
        (project, project, "测试", "离线检索夹具", "原文"),
    )
    cid = f"{project}:1:{number}"
    db.execute(
        "INSERT OR IGNORE INTO chapters(id,project_id,volume,number,title,canonical_revision,stale) VALUES(?,?,?,?,?,?,?)",
        (cid, project, 1, number, label, canonical, chapter_stale),
    )
    key = str(uuid5(NAMESPACE_URL, label))
    db.execute(
        "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
        (key, project, cid, version, 100000 + number, text, stale),
    )
    return key


@pytest.fixture
def retrieval(tmp_path):
    settings = Settings(tmp_path)
    settings.update({"vector_enabled": True})
    db = Database(settings.data / "studio.sqlite")
    r = Retrieval(db, settings)
    r.vector = QdrantClient(":memory:")
    r.vector.create_collection(
        "story",
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )

    class Embedder:
        def query_embed(self, query):
            yield np.array([1.0, 0.0])

    r.embedder = Embedder()
    yield r
    r.close()


def vector(r, key, values, project="book"):
    # Deliberately use legacy payloads without time/version: the SQL ID filter
    # must protect old indexes too, with no required mass re-embedding.
    r.vector.upsert(
        "story",
        points=[
            models.PointStruct(
                id=key,
                vector=values,
                payload={"project_id": project},
            )
        ],
    )


def test_future_and_obsolete_top30_cannot_starve_valid_history(retrieval):
    r = retrieval
    gold = source(r.db, "gold", number=1, canonical=2, version=2)
    vector(r, gold, [0.6, 0.8])
    for i in range(36):
        key = source(r.db, f"future-{i}", number=100 + i)
        vector(r, key, [1.0, 0.0])
    old = source(r.db, "old-version-unmarked", number=1, version=1, canonical=2)
    vector(r, old, [1.0, 0.0])
    stale = source(r.db, "unreviewed-history", number=2, chapter_stale=1)
    vector(r, stale, [1.0, 0.0])
    foreign = source(r.db, "other-book", project="foreign")
    vector(r, foreign, [1.0, 0.0], project="foreign")
    rows = r.search("book", "隐秘旧友", before=100100, mode="semantic")
    assert [x["id"] for x in rows] == [gold]
    assert rows[0]["channels"] == {"semantic": 1}


def test_keyword_channel_filters_current_revision_and_project(retrieval):
    r = retrieval
    gold = source(r.db, "current", version=2, canonical=2)
    old = source(r.db, "obsolete", version=1, canonical=2)
    stale = source(r.db, "stale", number=2, stale=1)
    future = source(r.db, "future", number=100)
    foreign = source(r.db, "foreign", project="foreign")
    for key in (gold, old, stale, future, foreign):
        r.db.execute(
            "INSERT INTO chunks_fts VALUES(?,?,?)",
            (key, "book", r.tokens("铜钥匙 药师 保管")),
        )
    assert [
        x["id"] for x in r.search("book", "铜钥匙", before=100100, mode="keyword")
    ] == [gold]


def test_semantic_failure_keeps_keyword_evidence_and_reports_degradation(
    retrieval, monkeypatch
):
    r = retrieval
    gold = source(r.db, "fallback")
    r.db.execute(
        "INSERT INTO chunks_fts VALUES(?,?,?)", (gold, "book", r.tokens("铜钥匙"))
    )

    def failed():
        raise RuntimeError("fixture vector failure")

    monkeypatch.setattr(r, "vector_init", failed)
    assert r.search("book", "铜钥匙", mode="hybrid")[0]["id"] == gold
    assert "RuntimeError" in r.vector_status
    assert r.search("book", "铜钥匙", mode="semantic") == []
    r.settings.update({"retrieval_policy": "semantic"})
    assert r.search("book", "铜钥匙")[0]["id"] == gold


def test_empty_and_invalid_search_cannot_run_unbounded_queries(retrieval):
    assert retrieval.search("book", " ") == []
    with pytest.raises(ValueError):
        retrieval.search("book", "钥匙", limit=100000)
    with pytest.raises(ValueError):
        retrieval.search("book", "钥匙", mode="unknown")
