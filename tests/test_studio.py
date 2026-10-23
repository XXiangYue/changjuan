import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from novel_agent.studio.api import create_app
from novel_agent.studio.models import (
    Blueprint,
    ChapterPlan,
    MEMORY_NAMES,
    MemoryProposal,
    ReviewReport,
)


@pytest.mark.asyncio
async def test_local_eval_reaches_workbench_with_unreachable_system_proxy(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    import httpx
    from novel_agent.studio.evaluation import studio_http_client

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"local-workbench")

        def log_message(self, *args):
            pass

    for variable in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        async with httpx.AsyncClient(timeout=1) as proxied:
            with pytest.raises(httpx.ConnectError):
                await proxied.get(url)
        async with studio_http_client(url, timeout=1) as direct:
            response = await direct.get("/api/health")
        assert response.status_code == 200
        assert response.text == "local-workbench"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_private_settings_recheck_boundaries_and_do_not_follow_fixed_temp(
    tmp_path, monkeypatch
):
    import stat
    from novel_agent.studio.settings import Settings

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    root = tmp_path / "workspace"
    settings = Settings(root)
    settings.secret_file.parent.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("preserve me")
    (settings.secret_file.parent / "settings.tmp").symlink_to(outside)
    settings.update({"api_key": "fixture-credential-only"})
    assert outside.read_text() == "preserve me"
    assert settings.values()["api_key"] == "fixture-credential-only"
    assert stat.S_IMODE(settings.secret_file.stat().st_mode) == 0o600
    assert not list(settings.secret_file.parent.glob("settings-*.tmp"))
    env = root / ".env"
    env.symlink_to(outside)
    with pytest.raises(ValueError, match="工作目录"):
        settings.values()
    env.unlink()
    settings.secret_file.unlink()
    settings.secret_file.symlink_to(outside)
    with pytest.raises(ValueError, match="工作目录"):
        settings.update({"api_key": "another-fixture-only"})
    assert outside.read_text() == "preserve me"


class FixtureModel:
    """Offline fixture: never reported as a real model experiment."""

    is_test = True

    def __init__(self):
        self.review_count = 0

    async def text(self, rid, role, system, prompt, **kwargs):
        return (
            "沈砚将唯一的温肺丹交给洛蘅。他掌心的红灰已经冷了。\n\n远处的铜铃没有响。\n\n"
            + "他把炉钳放在砧旁，等红灰落定。" * 28
        )

    async def structured(self, rid, role, system, prompt, schema, **kwargs):
        if schema is Blueprint:
            return Blueprint(
                logline="带着代价修复旧炉",
                alternatives=["修复旧炉，损耗肺息", "放弃考核，先保护镇民"],
                outline="第一章交药，第二章修炉。",
                memory={x: "# " + x + "\n唯一温肺丹尚未使用。" for x in MEMORY_NAMES},
                chapters=[
                    ChapterPlan(
                        title="一枚丹药", goal="交药付出代价", target_words=500
                    ),
                    ChapterPlan(title="炉火", goal="修复旧炉", target_words=500),
                ],
            )
        if schema is ChapterPlan:
            return ChapterPlan(title="炉火", goal="修复旧炉", target_words=500)
        if schema is ReviewReport:
            self.review_count += 1
            return ReviewReport(
                scores={"剧情一致性": 8, "文笔流畅度": 8, "人设吻合度": 8, "原创性": 8},
                summary="离线测试审核",
            )
        if schema is MemoryProposal:
            return MemoryProposal(
                patches=[
                    {
                        "name": "主角形象状态",
                        "content": "沈砚已将温肺丹交给洛蘅。",
                        "reason": "正文明确交付",
                        "quotes": ["沈砚将唯一的温肺丹交给洛蘅。"],
                    }
                ],
                facts=[
                    {
                        "subject": "温肺丹",
                        "predicate": "持有人",
                        "value": "洛蘅",
                        "kind": "item",
                        "quote": "沈砚将唯一的温肺丹交给洛蘅。",
                    }
                ],
                chapter_summary="沈砚将丹药交给洛蘅。",
                next_plan=ChapterPlan(title="炉火", goal="修复旧炉", target_words=500),
            )
        raise AssertionError(schema)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, model=FixtureModel())
    with TestClient(app) as c:
        yield c


def create(c):
    r = c.post(
        "/api/projects",
        json={
            "title": "测试作品",
            "premise": "一名铸器学徒必须修复旧炉，能力有代价。",
            "target_chapters": 2,
            "chapter_words": 500,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def wait(c, rid, status="awaiting_approval"):
    import time

    for _ in range(300):
        r = c.get("/api/runs/" + rid).json()
        if r["status"] == status:
            return r
        if r["status"] in ("failed", "cancelled"):
            pytest.fail(str(r))
        time.sleep(0.02)
    pytest.fail("run timeout: " + str(r))


def setup(c, p):
    r = c.post("/api/runs", json={"project_id": p["id"], "kind": "blueprint"})
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    wait(c, rid)
    c.post("/api/runs/" + rid + "/approve", json={"project_revision": 0})
    wait(c, rid, "completed")
    return c.get("/api/projects/" + p["id"]).json()


def draft(c, p, cid):
    r = c.post(
        "/api/runs", json={"project_id": p["id"], "chapter_id": cid, "kind": "chapter"}
    )
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    wait(c, rid)
    return rid, c.get("/api/chapters/" + cid).json()


def approve(c, p, rid, ch):
    r = c.post(
        "/api/runs/" + rid + "/approve",
        json={
            "base_revision": ch["draft_revision"],
            "project_revision": p["revision"],
            "accepted_patches": [x["name"] for x in ch["proposal"]["patches"]],
        },
    )
    assert r.status_code == 202, r.text
    wait(c, rid, "completed")
    return c.get("/api/projects/" + p["id"]).json()


def test_zero_scores_weighted_and_missing_fail():
    report = ReviewReport(
        scores={"剧情一致性": 0, "文笔流畅度": 10, "人设吻合度": 10, "原创性": 0}
    )
    assert report.total == 5 and report.passed is False
    with pytest.raises(ValueError):
        ReviewReport(scores={"文笔流畅度": 10})
    with pytest.raises(ValueError):
        ReviewReport(
            scores={"剧情一致性": 11, "文笔流畅度": 10, "人设吻合度": 10, "原创性": 0}
        )


def test_legacy_regressions_numeric_order_full_context_and_empty(tmp_path):
    from novel_agent.workflows.base import BaseWorkflow
    from novel_agent.workflows.reviewer import ChapterReviewer
    from novel_agent.core.models import MEMORY_DOC_FILES, MEMORY_DOC_TEMPLATES
    from novel_agent.core.review_prompts import parse_review_result

    root = tmp_path / "小说本体"
    v = root / "第1卷"
    v.mkdir(parents=True)
    for number in (1, 2, 9, 10, 11, 12):
        (v / f"第{number}章.md").write_text(str(number) + "界" * 3000)
    w = BaseWorkflow(None, lambda _: None)
    rows = w._read_recent_chapters(root, 1, 12)
    assert [x[0] for x in rows] == ["第1章", "第2章", "第9章", "第10章", "第11章"]
    assert "界" * 3000 in w._format_recent_chapters_context(rows)
    assert "workspace/剧情/目前剧情.md" in MEMORY_DOC_FILES
    assert len(MEMORY_DOC_TEMPLATES) == 10
    assert not ChapterReviewer(None, lambda _: None).review("", "")["passed"]
    text = "\n".join(
        f"### {k}\n- 得分: {v}/10"
        for k, v in [
            ("剧情一致性", 0),
            ("文笔流畅度", 10),
            ("人设吻合度", 10),
            ("原创性", 0),
        ]
    )
    assert parse_review_result(text)["scores"]["综合评分"] == 5
    assert not parse_review_result(text)["passed"]


def test_paragraph_citations_use_source_not_model_spelling():
    from novel_agent.studio.models import Finding, ground_citations

    body = "第一段。\n\n“一起。”沈砚把砖推回原处。"
    f = Finding(
        dimension="一致性",
        severity="warning",
        paragraph=2,
        quote="“乔。”沈砚摸黑把砖往原处推。",
        suggestion="核对动作",
    )
    ground_citations([f], body)
    assert f.quote == "“一起。”沈砚把砖推回原处。"
    f.paragraph = 3
    with pytest.raises(ValueError, match="编号"):
        ground_citations([f], body)


def test_complete_flow_confirmed_memory_and_sources(client):
    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    before = client.get("/api/projects/" + p["id"]).json()
    assert before["facts"] == []
    assert before["chapters"][0]["canonical"] == ""
    assert all(m["content"].endswith("尚未使用。") for m in before["memory"])
    p = approve(client, p, rid, ch)
    assert p["chapters"][0]["canonical_revision"] == 1
    assert p["facts"][0]["quote"] in p["chapters"][0]["canonical"]
    assert len(client.get("/api/chapters/" + cid + "/versions").json()) == 1
    assert client.get("/api/projects/" + p["id"] + "/search?q=温肺丹").json()
    for fmt in ("md", "txt", "zip"):
        assert (
            client.get("/api/projects/" + p["id"] + "/export?format=" + fmt).status_code
            == 200
        )


def test_manual_edit_invalidates_review_and_proposal(client):
    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    r = client.put(
        "/api/chapters/" + cid + "/draft",
        json={
            "content": "手工修改后的正文",
            "title": "新标题",
            "base_revision": ch["draft_revision"],
        },
    )
    assert r.status_code == 200
    assert (
        not r.json()["review"]
        and not r.json()["proposal"]
        and not r.json()["reviewed_hash"]
    )
    assert client.get("/api/runs/" + rid).json()["status"] == "cancelled"
    assert (
        client.put(
            "/api/chapters/" + cid + "/draft",
            json={
                "content": "不应覆盖",
                "title": "新标题",
                "base_revision": ch["draft_revision"],
            },
        ).status_code
        == 409
    )


def test_exclusive_run_and_sequential_chapters(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    r = client.post(
        "/api/runs",
        json={
            "project_id": p["id"],
            "kind": "chapter",
            "chapter_id": p["chapters"][1]["id"],
        },
    )
    assert r.status_code == 409
    client.post("/api/runs/" + rid + "/cancel")
    r = client.post(
        "/api/runs",
        json={
            "project_id": p["id"],
            "kind": "chapter",
            "chapter_id": p["chapters"][1]["id"],
        },
    )
    import time

    for _ in range(200):
        state = client.get("/api/runs/" + r.json()["id"]).json()
        if state["status"] == "failed":
            break
        time.sleep(0.02)
    assert state["status"] == "failed" and "按顺序" in state["error"]


def test_stale_approval_does_not_commit(client):
    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    r = client.post(
        "/api/runs/" + rid + "/approve",
        json={
            "base_revision": 0,
            "project_revision": p["revision"],
            "accepted_patches": [],
        },
    )
    assert r.status_code == 202
    import time

    for _ in range(200):
        if client.get("/api/runs/" + rid).json()["status"] == "failed":
            break
        time.sleep(0.02)
    assert client.get("/api/chapters/" + cid).json()["canonical_revision"] == 0


def test_import_export_and_path_traversal(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    approve(client, p, rid, ch)
    raw = client.get("/api/projects/" + p["id"] + "/export").content
    r = client.post(
        "/api/projects/import", content=raw, headers={"Content-Type": "application/zip"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["chapters"][0]["stale"] == 1
    assert r.json()["chapters"][0]["canonical"]
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape.md", "unsafe")
    assert (
        client.post("/api/projects/import", content=bad.getvalue()).status_code == 400
    )


def test_sse_replay_and_isolation(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    rows = client.get("/api/runs/" + rid + "/events?once=true").json()
    assert rows and all(
        rows[i]["seq"] < rows[i + 1]["seq"] for i in range(len(rows) - 1)
    )
    assert not client.get(
        "/api/runs/" + rid + "/events?once=true&after=" + str(rows[-1]["seq"])
    ).json()
    other = create(client)
    assert client.get("/api/projects/" + other["id"] + "/search?q=温肺丹").json() == []
    assert (
        client.post(
            "/api/projects/" + p["id"] + "/archive",
            headers={"Origin": "https://malicious.example"},
        ).status_code
        == 403
    )


def test_restart_preserves_approval_and_commits_once(tmp_path):
    with TestClient(create_app(tmp_path, FixtureModel())) as c:
        p = setup(c, create(c))
        rid, ch = draft(c, p, p["chapters"][0]["id"])
    with TestClient(create_app(tmp_path, FixtureModel())) as c:
        assert c.get("/api/runs/" + rid).json()["status"] == "awaiting_approval"
        p = approve(c, p, rid, ch)
        assert p["chapters"][0]["canonical_revision"] == 1
        assert c.post("/api/runs/" + rid + "/approve", json={}).status_code == 409


def test_historical_edit_marks_later_sources_stale(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    p = approve(client, p, rid, ch)
    rid, ch = draft(client, p, p["chapters"][1]["id"])
    p = approve(client, p, rid, ch)
    first = p["chapters"][0]
    r = client.put(
        "/api/chapters/" + first["id"] + "/draft",
        json={
            "content": first["content"] + "\n新增一句话。",
            "title": first["title"],
            "base_revision": first["draft_revision"],
        },
    )
    assert r.status_code == 200
    r = client.post(
        "/api/runs",
        json={"project_id": p["id"], "chapter_id": first["id"], "kind": "review"},
    )
    rid = r.json()["id"]
    wait(client, rid)
    ch = client.get("/api/chapters/" + first["id"]).json()
    p = approve(client, p, rid, ch)
    assert p["chapters"][1]["stale"] == 1
    assert all(f["chapter_id"] == first["id"] for f in p["facts"])
    second = p["chapters"][1]
    rid = client.post(
        "/api/runs",
        json={"project_id": p["id"], "chapter_id": second["id"], "kind": "review"},
    ).json()["id"]
    wait(client, rid)
    ch = client.get("/api/chapters/" + second["id"]).json()
    p = approve(client, p, rid, ch)
    assert (
        p["chapters"][1]["stale"] == 0 and p["chapters"][1]["canonical_revision"] == 2
    )


def test_settings_redact_credentials(client, tmp_path):
    r = client.put(
        "/api/settings",
        json={"api_key": "test-private-secret", "budget": 20, "vector_enabled": False},
    )
    assert r.status_code == 200
    assert "test-private-secret" not in client.get("/api/settings").text
    assert (tmp_path / ".local/settings.json").stat().st_mode & 0o777 == 0o600
    assert client.put("/api/settings", json={"budget": 301}).status_code == 422


def test_commit_retry_after_index_failure_is_idempotent(client, monkeypatch):
    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    original = client.app.state.retrieval.index
    count = 0

    def fail_once(cid):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("simulated index interruption after canonical transaction")
        original(cid)

    monkeypatch.setattr(client.app.state.retrieval, "index", fail_once)
    client.post(
        "/api/runs/" + rid + "/approve",
        json={
            "base_revision": ch["draft_revision"],
            "project_revision": p["revision"],
            "accepted_patches": [],
        },
    )
    import time

    for _ in range(200):
        state = client.get("/api/runs/" + rid).json()
        if state["status"] == "failed":
            break
        time.sleep(0.02)
    assert state["status"] == "failed"
    assert client.get("/api/chapters/" + cid).json()["canonical_revision"] == 1
    assert client.post("/api/runs/" + rid + "/resume").status_code == 202
    wait(client, rid, "completed")
    assert len(client.get("/api/chapters/" + cid + "/versions").json()) == 1
    assert client.get("/api/projects/" + p["id"] + "/search?q=温肺丹").json()


def test_second_server_cannot_interrupt_first(tmp_path):
    with TestClient(create_app(tmp_path, FixtureModel())) as first:
        p = setup(first, create(first))
        rid, ch = draft(first, p, p["chapters"][0]["id"])
        with pytest.raises(RuntimeError, match="已有长卷服务"):
            with TestClient(create_app(tmp_path, FixtureModel())):
                pass
        assert first.get("/api/runs/" + rid).json()["status"] == "awaiting_approval"


def test_chapter_memory_transaction_rolls_back(client):
    from novel_agent.studio.db import encode
    from novel_agent.studio.models import Approve

    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    proposal = ch["proposal"]
    proposal["facts"][0]["quote"] = "这句话不在正文"
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?", (encode(proposal), cid)
    )
    with pytest.raises(ValueError, match="来源"):
        client.app.state.repo.commit_chapter(
            cid,
            Approve(
                base_revision=ch["draft_revision"],
                project_revision=p["revision"],
                accepted_patches=["主角形象状态"],
            ),
        )
    after = client.get("/api/projects/" + p["id"]).json()
    assert after["revision"] == p["revision"] and not after["chapters"][0]["canonical"]
    assert after["memory"] == p["memory"] and not after["facts"]


def test_cancelled_author_checkpoint_can_be_reopened_without_regenerating(client):
    p = create(client)
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    before = client.get("/api/runs/" + rid + "/events?once=true").json()
    client.post("/api/runs/" + rid + "/cancel")
    response = client.post("/api/runs/" + rid + "/reopen-approval")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_approval"
    current = client.get("/api/chapters/" + ch["id"]).json()
    assert current == ch
    after = client.get("/api/runs/" + rid + "/events?once=true").json()
    assert [e for e in after if e["type"].startswith("model.")] == [
        e for e in before if e["type"].startswith("model.")
    ]
    p = approve(client, p, rid, ch)
    assert p["chapters"][0]["canonical_revision"] == 1


def test_changed_draft_cannot_reopen_old_author_checkpoint(client):
    p = create(client)
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    client.post("/api/runs/" + rid + "/cancel")
    response = client.put(
        "/api/chapters/" + ch["id"] + "/draft",
        json={
            "content": ch["content"] + "\n\n作者修改。",
            "title": ch["title"],
            "base_revision": ch["draft_revision"],
        },
    )
    assert response.status_code == 200
    assert client.post("/api/runs/" + rid + "/reopen-approval").status_code == 409
    assert client.get("/api/chapters/" + ch["id"]).json()["canonical_revision"] == 0


def test_reopening_cannot_displace_another_active_run(client):
    p = create(client)
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    client.post("/api/runs/" + rid + "/cancel")
    another = client.post(
        "/api/runs",
        json={
            "project_id": p["id"],
            "chapter_id": ch["id"],
            "kind": "review",
        },
    ).json()
    wait(client, another["id"])
    assert client.post("/api/runs/" + rid + "/reopen-approval").status_code == 409
    assert (
        client.get("/api/runs/" + another["id"]).json()["status"] == "awaiting_approval"
    )


def test_cross_volume_uses_same_review_and_commit_flow(client):
    p = setup(client, create(client))
    for cid in [x["id"] for x in p["chapters"]]:
        rid, ch = draft(client, p, cid)
        p = approve(client, p, rid, ch)
    r = client.post("/api/runs", json={"project_id": p["id"], "kind": "volume"})
    rid = r.json()["id"]
    wait(client, rid)
    client.post(
        "/api/runs/" + rid + "/approve", json={"project_revision": p["revision"]}
    )
    wait(client, rid, "completed")
    p = client.get("/api/projects/" + p["id"]).json()
    assert [x["number"] for x in p["volumes"]] == [1, 2]
    assert [(x["volume"], x["number"]) for x in p["chapters"]] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]
    rid, ch = draft(client, p, p["chapters"][2]["id"])
    p = approve(client, p, rid, ch)
    assert p["chapters"][2]["canonical_revision"] == 1


def test_truncated_model_output_is_retained_but_never_accepted(client, monkeypatch):
    from langchain_core.messages import AIMessageChunk
    from novel_agent.studio.llm import ModelClient

    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])

    class Stream:
        async def astream(self, messages):
            yield AIMessageChunk(
                content="被截断的正文", response_metadata={"finish_reason": "length"}
            )

    class Meter:
        def flush(self):
            pass

    model = ModelClient(
        client.app.state.db, client.app.state.settings, client.app.state.retrieval
    )
    monkeypatch.setattr(model, "model", lambda *args, **kwargs: (Stream(), Meter()))
    with pytest.raises(ValueError, match="长度上限"):
        asyncio.run(model.text(rid, "作者", "system", "prompt"))
    rows = client.get("/api/runs/" + rid + "/events?once=true").json()
    assert any(
        x["type"] == "model.output" and x["data"]["text"] == "被截断的正文"
        for x in rows
    )
    assert not client.get("/api/chapters/" + ch["id"]).json()["canonical"]


def test_partial_recovery_cannot_cancel_on_old_revision(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    client.post("/api/runs/" + rid + "/cancel")
    db = client.app.state.db
    db.event(rid, "draft.start", {"mode": "chapter"})
    db.event(rid, "token", {"text": "中断时保留的中文片段", "role": "作者"})
    bad = client.post(
        "/api/runs/" + rid + "/recover-draft",
        json={"base_revision": ch["draft_revision"] - 1},
    )
    assert bad.status_code == 409
    good = client.post(
        "/api/runs/" + rid + "/recover-draft",
        json={"base_revision": ch["draft_revision"]},
    )
    assert good.status_code == 200 and good.json()["content"] == "中断时保留的中文片段"
    assert not good.json()["review"] and not good.json()["canonical"]


def test_direction_change_reports_conservative_impact(client):
    p = setup(client, create(client))
    r = client.put(
        "/api/projects/" + p["id"] + "/direction",
        json={
            "premise": "主角放弃考核，先兑现对镇民的承诺。",
            "style": "更多有行动代价的对话",
            "project_revision": p["revision"],
        },
    )
    assert r.status_code == 200
    assert len(r.json()["impact"]["memory"]) == 10
    assert len(r.json()["impact"]["chapter_plans"]) == 2
    p = r.json()["project"]
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    assert (
        client.put(
            "/api/projects/" + p["id"] + "/direction",
            json={
                "premise": "另一个方向必须先处理已在运行的任务。",
                "style": "克制",
                "project_revision": p["revision"],
            },
        ).status_code
        == 409
    )


def test_editor_failure_cancels_parallel_peer(tmp_path):
    class FailingEditors(FixtureModel):
        cancelled = False

        async def structured(self, rid, role, system, prompt, schema, **kwargs):
            if role == "一致性编辑":
                await asyncio.sleep(0.01)
                raise ValueError("simulated structured editor failure")
            if role == "叙事编辑":
                try:
                    await asyncio.sleep(20)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return await super().structured(rid, role, system, prompt, schema, **kwargs)

    model = FailingEditors()
    with TestClient(create_app(tmp_path, model)) as c:
        p = setup(c, create(c))
        rid = c.post(
            "/api/runs",
            json={
                "project_id": p["id"],
                "chapter_id": p["chapters"][0]["id"],
                "kind": "chapter",
            },
        ).json()["id"]
        wait(c, rid, "failed")
        assert model.cancelled


@pytest.mark.parametrize("failure", ["limit", "disconnect"])
def test_transport_retries_are_bounded_and_recorded(client, monkeypatch, failure):
    import httpx
    from langchain_core.messages import AIMessageChunk
    from novel_agent.studio.llm import ModelClient

    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])

    class Transport:
        calls = 0

        async def astream(self, messages):
            self.calls += 1
            if self.calls < 3:
                if failure == "disconnect":
                    raise httpx.ReadTimeout("offline test disconnect")
                error = RuntimeError("offline test rate limit")
                error.status_code = 429
                raise error
            yield AIMessageChunk(
                content="恢复后的完整正文", response_metadata={"finish_reason": "stop"}
            )

    class Meter:
        def flush(self):
            pass

    sdk = Transport()
    model = ModelClient(
        client.app.state.db, client.app.state.settings, client.app.state.retrieval
    )
    monkeypatch.setattr(model, "model", lambda *args, **kwargs: (sdk, Meter()))
    assert (
        asyncio.run(model.text(rid, "作者", "system", "prompt")) == "恢复后的完整正文"
    )
    assert sdk.calls == 3
    assert (
        len(
            [
                x
                for x in client.get("/api/runs/" + rid + "/events?once=true").json()
                if x["type"] == "retry"
            ]
        )
        == 2
    )


def test_invalid_structured_json_never_automatically_passes(client, monkeypatch):
    from langchain_core.messages import AIMessage
    from novel_agent.studio.llm import ModelClient

    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])

    class InvalidSDK:
        calls = 0

        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            return {
                "parsed": None,
                "raw": AIMessage(content="{"),
                "parsing_error": ValueError("invalid JSON"),
            }

    sdk = InvalidSDK()
    model = ModelClient(
        client.app.state.db, client.app.state.settings, client.app.state.retrieval
    )
    monkeypatch.setattr(model, "model", lambda *args, **kwargs: (sdk, None))
    with pytest.raises(ValueError, match="invalid JSON"):
        asyncio.run(model.structured(rid, "审核", "system", "prompt", ReviewReport))
    assert sdk.calls == 3
    assert not client.get("/api/chapters/" + ch["id"]).json()["canonical"]


def test_author_summary_and_next_plan_rejection_are_respected(client):
    from novel_agent.studio.db import encode

    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    before = p["chapters"][1]["plan"]
    proposal = ch["proposal"]
    proposal["next_plan"]["scenes"] = [
        {
            "title": "不应偷偷接受的场景",
            "goal": "改变场景",
            "conflict": "测试",
            "cost": "",
            "change": "",
        }
    ]
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?", (encode(proposal), cid)
    )
    summary = "午前已将唯一丹药交给洛蘅；约定今夜服药尚未发生。"
    r = client.post(
        "/api/runs/" + rid + "/approve",
        json={
            "base_revision": ch["draft_revision"],
            "project_revision": p["revision"],
            "accepted_patches": ["目前剧情"],
            "summary_override": summary,
        },
    )
    assert r.status_code == 202
    wait(client, rid, "completed")
    p = client.get("/api/projects/" + p["id"]).json()
    assert p["chapters"][0]["summary"] == summary
    assert p["chapters"][1]["plan"] == before
    assert summary in next(m["content"] for m in p["memory"] if m["name"] == "目前剧情")
    assert p["chapters"][0]["proposal"]["chapter_summary"] == summary


def test_import_can_be_revalidated_and_preserves_titles_plans_versions(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    p = approve(client, p, rid, ch)
    raw = client.get("/api/projects/" + p["id"] + "/export?format=zip").content
    imported = client.post("/api/projects/import", content=raw).json()
    first = imported["chapters"][0]
    assert first["title"] == p["chapters"][0]["title"]
    assert first["plan"] == p["chapters"][0]["plan"]
    assert len(client.get("/api/chapters/" + first["id"] + "/versions").json()) == 1
    r = client.post(
        "/api/runs",
        json={
            "project_id": imported["id"],
            "chapter_id": first["id"],
            "kind": "review",
        },
    )
    rid = r.json()["id"]
    wait(client, rid)
    first = client.get("/api/chapters/" + first["id"]).json()
    imported = approve(client, imported, rid, first)
    assert imported["chapters"][0]["stale"] == 0
    assert imported["chapters"][0]["canonical_revision"] == 2


def test_append_chapter_after_import_and_version_protection(client):
    p = setup(client, create(client))
    r = client.post(
        "/api/projects/" + p["id"] + "/chapters",
        json={"volume": 1, "project_revision": p["revision"], "goal": "新的抉择"},
    )
    assert r.status_code == 201 and r.json()["number"] == 3
    assert r.json()["plan"]["goal"] == "新的抉择"
    assert (
        client.post(
            "/api/projects/" + p["id"] + "/chapters",
            json={"volume": 1, "project_revision": p["revision"]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/projects/" + p["id"] + "/chapters",
            json={"volume": 2, "project_revision": p["revision"] + 1},
        ).status_code
        == 400
    )


def test_authored_next_chapter_goal_is_preserved(client):
    p = setup(client, create(client))
    ch = p["chapters"][1]
    plan = ch["plan"] | {
        "title": "作者指定的第二章",
        "goal": "寻找陈婶而非直接修炉",
        "target_words": 700,
        "constraints": ["必须先兑现借物承诺"],
    }
    assert (
        client.put(
            "/api/chapters/" + ch["id"] + "/plan",
            json={"base_revision": ch["draft_revision"], "plan": plan},
        ).status_code
        == 200
    )
    p = client.get("/api/projects/" + p["id"]).json()
    rid, first = draft(client, p, p["chapters"][0]["id"])
    p = approve(client, p, rid, first)
    assert p["chapters"][1]["plan"]["goal"] == plan["goal"]
    assert p["chapters"][1]["plan"]["target_words"] == 700
    assert plan["constraints"][0] in p["chapters"][1]["plan"]["constraints"]


def test_rejected_facts_are_not_committed(client):
    p = setup(client, create(client))
    cid = p["chapters"][0]["id"]
    rid, ch = draft(client, p, cid)
    r = client.post(
        "/api/runs/" + rid + "/approve",
        json={
            "base_revision": ch["draft_revision"],
            "project_revision": p["revision"],
            "accepted_patches": [],
            "accepted_facts": [],
        },
    )
    assert r.status_code == 202
    wait(client, rid, "completed")
    p = client.get("/api/projects/" + p["id"]).json()
    assert p["facts"] == []
    assert p["chapters"][0]["proposal"]["accepted_facts"] == []


def test_historical_context_does_not_read_future_memory(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    p = approve(client, p, rid, ch)
    rid, ch = draft(client, p, p["chapters"][1]["id"])
    p = approve(client, p, rid, ch)
    ctx = client.app.state.retrieval.context(p["id"], p["chapters"][0]["id"])
    assert "尚未使用" in ctx.memory["主角形象状态"]
    assert not ctx.facts and not ctx.evidence


@pytest.mark.asyncio
async def test_budget_reservation_is_atomic(tmp_path):
    from novel_agent.studio.settings import Settings
    from novel_agent.studio.db import Database, uid, now
    from novel_agent.studio.llm import Meter, BudgetExceeded
    from langchain_core.messages import HumanMessage

    s = Settings(tmp_path)
    s.update({"budget": 0.05})
    d = Database(s.data / "studio.sqlite")
    rid = uid()
    d.execute(
        "INSERT INTO runs(id,kind,status,created) VALUES(?,?,?,?)",
        (rid, "fixture", "evaluating", now()),
    )
    a = Meter(d, s, rid, "fixture-a", 5000, False)
    b = Meter(d, s, rid, "fixture-b", 5000, False)
    results = await asyncio.gather(
        a.on_chat_model_start(None, [[HumanMessage("离线预算测试")]], run_id="a"),
        b.on_chat_model_start(None, [[HumanMessage("离线预算测试")]], run_id="b"),
        return_exceptions=True,
    )
    assert sum(isinstance(x, BudgetExceeded) for x in results) == 1
    assert d.usage_summary()["reserved"] <= 0.05


def test_numeric_order_and_1000_chapter_context(client):
    p = client.post(
        "/api/projects",
        json={
            "title": "工程规模夹具",
            "premise": "合成的千章记录，只用于工程验证，不作为真实小说。",
            "target_chapters": 1000,
        },
    ).json()
    db = client.app.state.db
    with db.transaction() as c:
        c.execute(
            "UPDATE chapters SET canonical='千章规模测试',canonical_revision=1,summary='合成摘要',status='final' WHERE project_id=? AND number<1000",
            (p["id"],),
        )
        c.execute(
            "UPDATE chapters SET canonical='第999章的完整正文' WHERE project_id=? AND number=999",
            (p["id"],),
        )
    last = p["chapters"][-1]
    ctx = client.app.state.retrieval.context(p["id"], last["id"])
    assert ctx.previous_chapter == "第999章的完整正文"
    assert [x["number"] for x in ctx.recent_summaries] == list(range(990, 1000))


def test_reasoning_review_uses_json_and_does_not_store_private_reasoning(
    client, monkeypatch
):
    from langchain_core.messages import AIMessage
    from novel_agent.studio.llm import ModelClient

    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    report = ReviewReport(
        scores={"剧情一致性": 8, "文笔流畅度": 8, "人设吻合度": 8, "原创性": 8}
    )

    class SDK:
        async def ainvoke(self, messages):
            return AIMessage(
                content=report.model_dump_json(exclude_computed_fields=True),
                additional_kwargs={
                    "reasoning_content": "private internal reasoning must not be logged"
                },
            )

    called = {}

    def factory(*args, **kwargs):
        called.update(kwargs)
        return SDK(), None

    model = ModelClient(
        client.app.state.db, client.app.state.settings, client.app.state.retrieval
    )
    monkeypatch.setattr(model, "model", factory)
    result = asyncio.run(
        model.structured(
            rid, "一致性编辑", "system", "prompt", ReviewReport, reasoning=True
        )
    )
    assert result.passed and called["reasoning"] and called["json_mode"]
    assert (
        "private internal reasoning"
        not in client.get("/api/runs/" + rid + "/events?once=true").text
    )


def test_length_gate_runs_full_editors_only_after_final_sized_draft(tmp_path):
    class Oversize(FixtureModel):
        writes = 0

        async def text(self, *args, **kwargs):
            self.writes += 1
            if self.writes < 4:
                return "过长的离线工程草稿。" * 150
            return await super().text(*args, **kwargs)

    model = Oversize()
    with TestClient(create_app(tmp_path, model)) as c:
        p = setup(c, create(c))
        rid, ch = draft(c, p, p["chapters"][0]["id"])
        assert model.writes == 4 and model.review_count == 2
        assert ch["review"]["passed"] and ch["review"]["length_check"]["within_range"]
        assert not ch["canonical"]


def test_truncated_reasoning_retries_with_lower_effort_and_keeps_failure(
    client, monkeypatch
):
    from langchain_core.messages import AIMessage
    from novel_agent.studio.llm import ModelClient

    p = setup(client, create(client))
    rid, _ = draft(client, p, p["chapters"][0]["id"])
    report = ReviewReport(
        scores={"剧情一致性": 8, "文笔流畅度": 8, "人设吻合度": 8, "原创性": 8}
    )
    attempts = []
    failure = type("LengthFinishReasonError", (Exception,), {})

    class SDK:
        async def ainvoke(self, messages):
            if len(attempts) == 1:
                raise failure("simulated reasoning budget exhausted")
            return AIMessage(
                content=report.model_dump_json(exclude_computed_fields=True)
            )

    def factory(*args, **kwargs):
        attempts.append(kwargs["reasoning_effort"])
        return SDK(), None

    model = ModelClient(
        client.app.state.db, client.app.state.settings, client.app.state.retrieval
    )
    monkeypatch.setattr(model, "model", factory)
    result = asyncio.run(
        model.structured(
            rid, "一致性编辑", "system", "prompt", ReviewReport, reasoning=True
        )
    )
    assert result.passed and attempts == ["high", "low"]
    events = client.get("/api/runs/" + rid + "/events?once=true").json()
    assert any(
        e["type"] == "retry" and e["data"].get("reason") == "LengthFinishReasonError"
        for e in events
    )
    assert any(
        e["type"] == "notice" and e["data"].get("to_effort") == "low" for e in events
    )


def test_raw_tool_result_reports_schema_error_without_relaxing_validation(
    client, monkeypatch
):
    from langchain_core.messages import AIMessage
    from novel_agent.studio.llm import ModelClient

    p = setup(client, create(client))
    rid, _ = draft(client, p, p["chapters"][0]["id"])
    report = ReviewReport(
        scores={"剧情一致性": 8, "文笔流畅度": 8, "人设吻合度": 8, "原创性": 8}
    )

    class SDK:
        calls = 0
        feedback = ""

        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            self.feedback = str(messages[-1].content)
            args = (
                {}
                if self.calls == 1
                else report.model_dump(exclude_computed_fields=True)
            )
            return {
                "parsed": None,
                "parsing_error": None,
                "raw": AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "call_test", "name": "ReviewReport", "args": args}
                    ],
                ),
            }

    sdk = SDK()
    model = ModelClient(
        client.app.state.db, client.app.state.settings, client.app.state.retrieval
    )
    monkeypatch.setattr(model, "model", lambda *a, **k: (sdk, None))
    result = asyncio.run(
        model.structured(rid, "审核", "system", "prompt", ReviewReport)
    )
    assert sdk.calls == 2 and result.passed
    assert "scores" in sdk.feedback and "Field required" in sdk.feedback
    events = client.get("/api/runs/" + rid + "/events?once=true").json()
    assert any(
        e["type"] == "retry" and e["data"]["reason"] == "ValidationError"
        for e in events
    )


def test_model_cannot_override_application_owned_memory_documents():
    from pydantic import ValidationError
    from novel_agent.studio.models import MemoryProposal

    value = {
        "chapter_summary": "已发生的行动摘要",
        "next_plan": {"title": "下一章", "goal": "作者目标"},
        "patches": [
            {"name": "目前剧情", "content": "模型想覆盖近十章滚动摘要"},
            {"name": "当前章节设计规划", "content": "模型想覆盖应用维护的规划"},
            {"name": "伏笔", "content": "新线索待回收", "reason": "本章新增"},
        ],
    }
    proposal = MemoryProposal.model_validate(value)
    assert [patch.name for patch in proposal.patches] == ["伏笔"]
    assert proposal.chapter_summary == "已发生的行动摘要"
    value["patches"][-1].pop("reason")
    with pytest.raises(ValidationError, match="reason"):
        MemoryProposal.model_validate(value)
    value["patches"] = []
    value.pop("next_plan")
    with pytest.raises(ValidationError, match="next_plan"):
        MemoryProposal.model_validate(value)


def test_duplicate_memory_proposals_cannot_shadow_author_decisions(client):
    from pydantic import ValidationError
    from novel_agent.studio.models import Approve
    from novel_agent.studio.repository import Conflict
    from novel_agent.studio.db import encode

    value = {
        "chapter_summary": "摘要",
        "next_plan": {"title": "下章", "goal": "目标"},
        "patches": [
            {"name": "伏笔", "content": "第一份", "reason": "新增"},
            {"name": "伏笔", "content": "第二份", "reason": "又改"},
        ],
    }
    with pytest.raises(ValidationError, match="重复提案"):
        MemoryProposal.model_validate(value)
    p = setup(client, create(client))
    _, ch = draft(client, p, p["chapters"][0]["id"])
    proposal = ch["proposal"]
    proposal["patches"].append(
        proposal["patches"][0] | {"content": "不可悄悄覆盖第一份"}
    )
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?", (encode(proposal), ch["id"])
    )
    req = {"base_revision": ch["draft_revision"], "project_revision": p["revision"]}
    with pytest.raises(Conflict, match="重复提案"):
        client.app.state.repo.commit_chapter(
            ch["id"], Approve(**req, accepted_patches=["主角形象状态"])
        )
    assert not client.get("/api/chapters/" + ch["id"]).json()["canonical"]
    saved = client.app.state.repo.commit_chapter(
        ch["id"], Approve(**req, accepted_patches=[])
    )
    assert saved["canonical_revision"] == 1


def test_local_export_stays_in_workspace_and_keeps_only_canon(client):
    from pathlib import Path

    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    approve(client, p, rid, ch)
    response = client.post("/api/projects/" + p["id"] + "/exports?format=zip")
    assert response.status_code == 201, response.text
    path = Path(response.json()["path"])
    assert path.is_relative_to(
        client.app.state.settings.root / "data/projects" / p["id"] / "exports"
    )
    with zipfile.ZipFile(path) as archive:
        chapters = [
            name
            for name in archive.namelist()
            if name.startswith("小说本体/") and name.endswith(".md")
        ]
        assert len(chapters) == 1
        assert archive.read(chapters[0]).decode() == ch["content"]
    other = create(client)
    folder = client.app.state.settings.root / "data/projects" / other["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "exports").symlink_to(
        client.app.state.settings.root.parent, target_is_directory=True
    )
    denied = client.post("/api/projects/" + other["id"] + "/exports?format=md")
    assert denied.status_code == 400 and "工作目录" in denied.text


def test_rewriting_last_canon_uses_prior_snapshot_and_rejects_old_post_state(client):
    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    p = approve(client, p, rid, ch)
    context = client.app.state.retrieval.context(p["id"], ch["id"])
    assert "尚未使用" in context.memory["主角形象状态"]
    assert "已将" not in context.memory["主角形象状态"]
    r = client.put(
        "/api/chapters/" + ch["id"] + "/draft",
        json={
            "content": ch["content"].replace("铜铃", "铁铃"),
            "title": ch["title"],
            "base_revision": ch["draft_revision"],
        },
    )
    assert r.status_code == 200
    r = client.post(
        "/api/runs",
        json={"project_id": p["id"], "chapter_id": ch["id"], "kind": "review"},
    )
    rid = r.json()["id"]
    wait(client, rid)
    ch = client.get("/api/chapters/" + ch["id"]).json()
    r = client.post(
        "/api/runs/" + rid + "/approve",
        json={
            "base_revision": ch["draft_revision"],
            "project_revision": p["revision"],
            "accepted_patches": [],
            "accepted_facts": [],
        },
    )
    assert r.status_code == 202, r.text
    wait(client, rid, "completed")
    p = client.get("/api/projects/" + p["id"]).json()
    assert (
        next(m["content"] for m in p["memory"] if m["name"] == "主角形象状态")
        == context.memory["主角形象状态"]
    )
    assert p["chapters"][0]["canonical_revision"] == 2


def test_graph_evaluation_seed_preserves_sources_and_actual_ablation_switches(client):
    from novel_agent.studio.integration_eval import seed_scene, NEUTRAL

    case = {
        "id": "offline",
        "name": "丹药去向",
        "archive": "温肺丹昨夜已交给洛蘅，沈砚没有余丹。",
        "task": "沈砚肺伤发作，继续修炉。",
    }
    for mode in ("full", "baseline", "no_memory", "no_revision"):
        state = seed_scene(client.app.state.db, client.app.state.repo, case, mode, 1)
        client.app.state.retrieval.index(state["archive_chapter_id"])
        context = client.app.state.retrieval.context(
            state["project_id"], state["chapter_id"], mode in ("baseline", "no_memory")
        )
        assert (
            len(context.recent_summaries) == 10 and context.previous_chapter == NEUTRAL
        )
        assert case["archive"] not in str(context.memory)
        if mode in ("full", "no_revision"):
            assert context.facts[0]["quote"] == case["archive"]
            assert any(
                x["chapter_id"] == state["archive_chapter_id"] for x in context.evidence
            )
        else:
            assert not context.facts and not context.evidence

        assert all(
            x["id"] != state["project_id"] for x in client.get("/api/projects").json()
        )
        if mode == "full":
            p = client.app.state.repo.project(state["project_id"])
            rid, ch = draft(client, p, state["chapter_id"])
            response = client.post(
                "/api/runs/" + rid + "/approve",
                json={
                    "base_revision": ch["draft_revision"],
                    "project_revision": p["revision"],
                    "accepted_patches": [x["name"] for x in ch["proposal"]["patches"]],
                    "note": "离线流程夹具篇幅不作为模型效果，只验证执行图的确认与提交。",
                },
            )
            assert response.status_code == 202, response.text
            wait(client, rid, "completed")
            p = client.app.state.repo.project(state["project_id"])
            assert p["chapters"][-1]["canonical_revision"] == 1


def test_eval_recovery_rechecks_cancelled_final_draft_without_regeneration(client):
    from hashlib import sha256
    from novel_agent.studio.evaluation import prepare_trial_recovery

    p = setup(client, create(client))
    rid, ch = draft(client, p, p["chapters"][0]["id"])
    expected = ch["content"]
    assert client.post("/api/runs/" + rid + "/cancel").status_code == 200
    item = {"run_id": rid, "chapter_id": ch["id"], "mode": "full"}
    prepare_trial_recovery(client.app.state.db, item)
    assert "run_id" not in item
    assert item["recovery_kind"] == "review"
    assert item["preserved_draft_sha256"] == sha256(expected.encode()).hexdigest()
    r = client.post(
        "/api/runs",
        json={
            "project_id": p["id"],
            "chapter_id": ch["id"],
            "kind": item["recovery_kind"],
        },
    )
    resumed = r.json()["id"]
    wait(client, resumed)
    ch = client.get("/api/chapters/" + ch["id"]).json()
    assert ch["content"] == expected
    p = approve(client, p, resumed, ch)
    assert p["chapters"][0]["canonical"] == expected
    assert not client.app.state.db.one(
        "SELECT seq FROM run_events WHERE run_id=? AND type='draft.start'",
        (resumed,),
        required=False,
    )


def test_author_can_disable_auto_revision_but_still_get_independent_review(client):
    p = setup(client, create(client))
    ch = p["chapters"][0]
    plan = dict(ch["plan"], target_words=1200)
    assert (
        client.put(
            "/api/chapters/" + ch["id"] + "/plan",
            json={"base_revision": ch["draft_revision"], "plan": plan},
        ).status_code
        == 200
    )
    r = client.post(
        "/api/runs",
        json={
            "project_id": p["id"],
            "chapter_id": ch["id"],
            "kind": "chapter",
            "auto_revise": False,
        },
    )
    assert r.status_code == 202
    rid = r.json()["id"]
    run = wait(client, rid)
    ch = client.get("/api/chapters/" + ch["id"]).json()
    assert run["config"]["auto_revise"] is False
    assert not ch["review"]["length_check"]["within_range"]
    assert client.app.state.engine.model.review_count == 2
    assert (
        len(
            client.app.state.db.all(
                "SELECT seq FROM run_events WHERE run_id=? AND type='draft.start'",
                (rid,),
            )
        )
        == 1
    )
    assert ch["canonical_revision"] == 0 and ch["proposal"]
