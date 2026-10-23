"""Real SDK stdio round trips into an isolated real workbench HTTP backend.

Fixtures establish deterministic reviewed drafts; these tests prove protocol and
permission contracts, not model tool-selection accuracy or literary quality.
"""

import hashlib
import json
import socket
import sys
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
import uvicorn
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from mcp import Client, StdioServerParameters

from novel_agent.studio import api
from novel_agent.studio.models import (
    MEMORY_NAMES,
    Approve,
    Blueprint,
    ChapterPlan,
    MemoryProposal,
    ProjectCreate,
    digest,
)
from novel_agent.studio.story_mcp import MAX_RESULT_BYTES, ROOT, StoryMCPConfig

FIRST = "沈砚清点药匣，里面只有两枚温肺丹。\n\n洛蘅摊开空药袋，温肺丹一枚也没有。"
DRAFT = "沈砚把一枚温肺丹交给洛蘅。\n\n他沿石阶返回炉房。"
FUTURE = "洛蘅三日后发现铜铃里藏着未来密文。"


class OfflineSettings:
    def __init__(self, root):
        self.root = root.resolve()
        self.data, self.cache = self.root / "data", self.root / ".cache"
        self.data.mkdir(parents=True)
        self.cache.mkdir()

    def values(self):
        # Never consult environment credentials, .env, or the real private settings.
        return {"api_key": "", "vector_enabled": False, "budget": 0}

    def within(self, path):
        path = Path(path).resolve()
        assert path.is_relative_to(self.root)
        return path


class NoModel:
    is_test = True

    async def text(self, *args, **kwargs):
        raise AssertionError("MCP contract tests must not call a model")

    async def structured(self, *args, **kwargs):
        raise AssertionError("MCP contract tests must not call a model")


def transfer(quantity=1):
    return {
        "operation": "resource.transfer",
        "paragraph": 1,
        "quote": DRAFT.split("\n\n")[0],
        "from_holder_id": "actor:shen",
        "to_holder_id": "actor:luo",
        "resource_id": "item:pill",
        "quantity": quantity,
    }


def reviewed(repo, cid, body, changes=()):
    chapter = repo.chapter(cid)
    revision = repo.db.one(
        "SELECT revision FROM projects WHERE id=?", (chapter["project_id"],)
    )["revision"]
    proposal = MemoryProposal(
        state_changes=list(changes),
        chapter_summary="离线工程夹具。",
        next_plan=ChapterPlan(title="下章", goal="继续查证", target_words=200),
    )
    repo.store_generated(
        cid,
        body,
        status="review",
        review={"passed": True},
        proposal=proposal.model_dump(),
        reviewed_hash=digest(body),
        reviewed_project_revision=revision,
        context={"memory": {name: f"# {name}\n初始设定。" for name in MEMORY_NAMES}},
    )


def make_book(repo, retrieval, title):
    project = repo.create(
        ProjectCreate(
            title=title,
            premise="铸器学徒交付丹药，事实必须有正文依据。",
            target_chapters=3,
            chapter_words=200,
        )
    )
    pid = project["id"]
    blueprint = Blueprint(
        logline="交药查证",
        alternatives=["交药", "留药"],
        outline="清点、交付、查证。",
        memory={name: f"# {name}\n初始设定。" for name in MEMORY_NAMES},
        chapters=[
            ChapterPlan(title=f"章{i}", goal="查证温肺丹", target_words=200)
            for i in range(1, 4)
        ],
    )
    repo.apply_blueprint(pid, blueprint.model_dump(), 0)
    chapters = repo.project(pid)["chapters"]
    first, draft, future = (chapter["id"] for chapter in chapters)
    changes = [
        {
            "operation": "resource.establish",
            "holder_id": "actor:shen",
            "resource_id": "item:pill",
            "quantity": 2,
            "paragraph": 1,
            "quote": FIRST.split("\n\n")[0],
        },
        {
            "operation": "resource.establish",
            "holder_id": "actor:luo",
            "resource_id": "item:pill",
            "quantity": 0,
            "paragraph": 2,
            "quote": FIRST.split("\n\n")[1],
        },
    ]
    reviewed(repo, first, FIRST, changes)
    repo.commit_chapter(
        first,
        Approve(base_revision=1, project_revision=1, accepted_state_changes=[0, 1]),
    )
    retrieval.index(first)
    future_change = {
        "operation": "knowledge.learn",
        "actor_id": "actor:luo",
        "fact_id": "fact:future",
        "fact": FUTURE,
        "paragraph": 1,
        "quote": FUTURE,
    }
    reviewed(repo, future, FUTURE, [future_change])
    repo.commit_chapter(
        future, Approve(base_revision=1, project_revision=2, accepted_state_changes=[0])
    )
    retrieval.index(future)
    reviewed(repo, draft, DRAFT)
    return SimpleNamespace(pid=pid, first=first, draft=draft, future=future)


def formal_snapshot(repo, pid):
    return {
        "chapters": repo.db.all(
            "SELECT id,canonical,canonical_revision FROM chapters WHERE project_id=? ORDER BY id",
            (pid,),
        ),
        **{
            table: repo.db.all(
                f"SELECT * FROM {table} WHERE project_id=? ORDER BY rowid", (pid,)
            )
            for table in ("memory", "facts", "story_events", "run_commits")
        },
        "versions": repo.db.all(
            "SELECT v.* FROM versions v JOIN chapters c ON c.id=v.chapter_id WHERE c.project_id=? ORDER BY v.rowid",
            (pid,),
        ),
        "revision": repo.db.one("SELECT revision FROM projects WHERE id=?", (pid,))[
            "revision"
        ],
    }


@pytest.fixture
def backend(monkeypatch, request):
    root = ROOT / ".cache/story-mcp-tests" / uuid4().hex
    root.mkdir(parents=True)
    monkeypatch.setattr(api, "Settings", OfflineSettings)
    app = api.create_app(root, model=NoModel())
    first = make_book(app.state.repo, app.state.retrieval, "绑定作品")
    second = make_book(app.state.repo, app.state.retrieval, "其他作品")
    controls = {"mode": "normal"}

    @app.middleware("http")
    async def adversarial_backend(request, call_next):
        if request.url.path.endswith("/search"):
            if controls["mode"] == "redirect":
                return RedirectResponse("http://127.0.0.1:1/forbidden")
            if controls["mode"] in ("oversize", "future"):
                return JSONResponse(
                    [
                        {
                            "id": "test-chunk",
                            "project_id": first.pid,
                            "chapter_id": first.first,
                            "version": 1,
                            "position": 100003
                            if controls["mode"] == "future"
                            else 100001,
                            "content": "未来密文不得泄漏"
                            if controls["mode"] == "future"
                            else "完整证据" * 12000,
                        }
                    ]
                )
            if controls["mode"] == "huge_http":
                return JSONResponse({"oversized": "界" * 210000})
            if controls["mode"] == "invalid_json":
                return PlainTextResponse("not-json")
        if (
            request.url.path.endswith("/story-state")
            and controls["mode"] == "oversize_state"
        ):
            state = app.state.repo.story_state(first.pid, before_position=100003)
            state["snapshot"]["facts"]["fact:large"] = "完整状态" * 10000
            return JSONResponse(state)
        return await call_next(request)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="critical", access_log=False
        )
    )
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(0.01)
    value = SimpleNamespace(
        root=root,
        port=port,
        app=app,
        first=first,
        second=second,
        controls=controls,
        calls=[],
        protocols=[],
        verified=False,
    )
    try:
        yield value
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
        audits = []
        for path in (root / ".cache/story-mcp").rglob("*.jsonl"):
            audits.extend(json.loads(line) for line in path.read_text().splitlines())
        destination = ROOT / "artifacts/resume/mcp-contract" / root.name
        destination.mkdir(parents=True)
        report = {
            "test": request.node.nodeid,
            "scope": "Real official SDK stdio client/server + loopback production HTTP routes + isolated real SQLite; deterministic fixtures, no LLM or semantic vector claims",
            "verified": value.verified,
            "sdk": version("mcp"),
            "dependencies": {
                name: version(name)
                for name in ("mcp", "mcp-types", "httpx", "pydantic")
            },
            "protocol_versions": value.protocols,
            "calls": value.calls,
            "audit": audits,
            "usage": app.state.db.usage_summary(),
            "source_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in (
                    ROOT / "tests/test_story_mcp.py",
                    ROOT / "src/novel_agent/studio/story_mcp.py",
                    ROOT / "src/novel_agent/studio/api.py",
                    ROOT / "src/novel_agent/studio/repository.py",
                    ROOT / "src/novel_agent/studio/models.py",
                    ROOT / "uv.lock",
                )
            },
        }
        (destination / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )


@asynccontextmanager
async def session(backend, mode="auto"):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "novel_agent.studio.story_mcp",
            "--root",
            str(backend.root),
            "--project-id",
            backend.first.pid,
            "--before-position",
            "100003",
            "--port",
            str(backend.port),
        ],
        cwd=str(ROOT),
        env={
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        },
    )
    async with Client(
        parameters, mode=mode, cache=None, read_timeout_seconds=20
    ) as client:
        backend.protocols.append(client.protocol_version)
        tools = await client.list_tools()
        backend.calls.append(
            {
                "method": "tools/list",
                "mode": mode,
                "protocol_version": client.protocol_version,
                "result": tools.model_dump(mode="json", by_alias=True),
            }
        )
        yield client, tools


async def call(backend, client, name, arguments=None):
    result = await client.call_tool(name, arguments or {})
    backend.calls.append(
        {
            "method": "tools/call",
            "name": name,
            "arguments": arguments or {},
            "result": result.model_dump(mode="json", by_alias=True),
        }
    )
    assert len(result.content[0].text.encode()) <= MAX_RESULT_BYTES
    assert len(result.model_dump_json(by_alias=True).encode()) <= MAX_RESULT_BYTES
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("auto", "2026-07-28"), ("legacy", "2025-11-25")]
)
async def test_real_stdio_discovery_reads_and_pending_proposal_preserve_formal_state(
    backend, mode, expected
):
    repo = backend.app.state.repo
    original = formal_snapshot(repo, backend.first.pid)
    async with session(backend, mode) as (client, tools):
        assert client.protocol_version == expected
        assert {tool.name for tool in tools.tools} == {
            "search_story",
            "get_story_state",
            "propose_state_changes",
        }
        for tool in tools.tools:
            assert tool.input_schema["additionalProperties"] is False
            assert (
                not {"project_id", "before", "before_position", "root", "url", "port"}
                & tool.input_schema["properties"].keys()
            )
        state = await call(backend, client, "get_story_state")
        assert not state.is_error
        snapshot = state.structured_content["data"]["snapshot"]
        assert snapshot["inventories"]["actor:shen"]["item:pill"]["quantity"] == 2
        assert "fact:future" not in snapshot["facts"]
        search = await call(
            backend, client, "search_story", {"query": "温肺丹", "limit": 8}
        )
        assert not search.is_error
        matches = search.structured_content["data"]["matches"]
        assert matches and all(
            row["project_id"] == backend.first.pid and row["position"] < 100003
            for row in matches
        )
        draft_revision = repo.chapter(backend.first.draft)["draft_revision"]
        proposal = await call(
            backend,
            client,
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": draft_revision,
                "changes": [transfer()],
            },
        )
        data = proposal.structured_content["data"]
        assert (
            not proposal.is_error
            and data["proposal_saved"]
            and data["pending"]
            and not data["committed"]
        )
        assert (
            data["precheck"]["status"] == "ready"
            and data["accepted_state_changes"] == []
        )
        assert repo.chapter(backend.first.draft)["content"] == DRAFT
        assert formal_snapshot(repo, backend.first.pid) == original
        duplicate = await call(
            backend,
            client,
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": draft_revision,
                "changes": [transfer()],
            },
        )
        assert (
            duplicate.is_error
            and duplicate.structured_content["error"]["code"] == "stale_draft"
        )
        state = await call(backend, client, "get_story_state")
        assert (
            state.structured_content["data"]["snapshot"]["inventories"]["actor:shen"][
                "item:pill"
            ]["quantity"]
            == 2
        )
    backend.verified = True


@pytest.mark.asyncio
async def test_stdio_rejects_cross_book_future_stale_schema_and_quote_attacks(backend):
    repo = backend.app.state.repo
    draft = repo.chapter(backend.first.draft)
    formal = formal_snapshot(repo, backend.first.pid)
    invalid_cases = [
        ("commit_chapter", {}, "unknown_tool"),
        ("file_write", {"path": "../../.local/settings.json"}, "unknown_tool"),
        (
            "search_story",
            {"query": "丹药", "project_id": backend.second.pid},
            "invalid_arguments",
        ),
        ("search_story", {"query": "丹药", "before": 999999999}, "invalid_arguments"),
        ("search_story", {"query": "丹药", "limit": 9}, "invalid_arguments"),
        (
            "propose_state_changes",
            {
                "chapter_id": backend.second.draft,
                "base_revision": 1,
                "changes": [transfer()],
            },
            "chapter_project_denied",
        ),
        (
            "propose_state_changes",
            {
                "chapter_id": backend.first.future,
                "base_revision": 1,
                "changes": [transfer()],
            },
            "chapter_time_denied",
        ),
        (
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": 0,
                "changes": [transfer()],
            },
            "stale_draft",
        ),
        (
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": 1,
                "changes": [transfer() | {"quote": FUTURE}],
            },
            "invalid_source_quote",
        ),
        (
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": 1,
                "changes": [
                    transfer() | {"source": {"chapter_id": backend.first.first}}
                ],
            },
            "invalid_arguments",
        ),
        (
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": 1,
                "changes": [transfer() | {"quantity": True}],
            },
            "invalid_arguments",
        ),
    ]
    async with session(backend) as (client, _):
        for name, arguments, expected in invalid_cases:
            result = await call(backend, client, name, arguments)
            assert (
                result.is_error
                and result.structured_content["error"]["code"] == expected
            )
    assert repo.chapter(backend.first.draft) == draft
    assert formal_snapshot(repo, backend.first.pid) == formal
    backend.verified = True


@pytest.mark.asyncio
async def test_stdio_replay_precheck_preserves_invalid_pending_without_committing(
    backend,
):
    repo = backend.app.state.repo
    formal = formal_snapshot(repo, backend.first.pid)
    async with session(backend) as (client, _):
        result = await call(
            backend,
            client,
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": 1,
                "changes": [transfer(quantity=3)],
            },
        )
        assert (
            result.is_error
            and result.structured_content["error"]["code"] == "proposal_precheck_failed"
        )
        data = result.structured_content["data"]
        assert data["proposal_saved"] and data["draft_revision"] == 2
        assert data["precheck"]["status"] == "invalid"
        assert data["precheck"]["errors"][0]["code"] == "insufficient_resource"
        fixed = await call(
            backend,
            client,
            "propose_state_changes",
            {
                "chapter_id": backend.first.draft,
                "base_revision": 2,
                "changes": [transfer()],
            },
        )
        assert (
            not fixed.is_error
            and fixed.structured_content["data"]["draft_revision"] == 3
        )
    assert formal_snapshot(repo, backend.first.pid) == formal
    logs = repo.db.all("SELECT detail FROM audit WHERE action='state.proposal.edited'")
    assert [json.loads(row["detail"])["precheck_status"] for row in logs] == [
        "invalid",
        "ready",
    ]
    backend.verified = True


@pytest.mark.asyncio
async def test_stdio_output_budget_drops_whole_chunks_and_refuses_redirects_or_scope_leaks(
    backend,
):
    async with session(backend) as (client, _):
        backend.controls["mode"] = "oversize"
        result = await call(backend, client, "search_story", {"query": "丹药"})
        assert not result.is_error
        data = result.structured_content["data"]
        assert (
            data["matches"] == [] and data["omitted_count"] == 1 and data["truncated"]
        )
        assert "完整证据" not in result.content[0].text
        backend.controls["mode"] = "redirect"
        result = await call(backend, client, "search_story", {"query": "丹药"})
        assert (
            result.is_error
            and result.structured_content["error"]["code"] == "backend_redirect_denied"
        )
        backend.controls["mode"] = "future"
        result = await call(backend, client, "search_story", {"query": "丹药"})
        assert (
            result.is_error
            and result.structured_content["error"]["code"] == "backend_scope_violation"
        )
        assert "未来密文不得泄漏" not in result.content[0].text
    backend.verified = True


@pytest.mark.asyncio
async def test_stdio_input_http_and_state_budgets_fail_without_partial_evidence(
    backend,
):
    async with session(backend) as (client, _):
        result = await call(backend, client, "search_story", {"query": "x" * 17000})
        assert (
            result.is_error
            and result.structured_content["error"]["code"] == "input_budget_exceeded"
        )
        for mode, code in (
            ("huge_http", "backend_response_too_large"),
            ("invalid_json", "invalid_backend_json"),
        ):
            backend.controls["mode"] = mode
            result = await call(backend, client, "search_story", {"query": "丹药"})
            assert (
                result.is_error and result.structured_content["error"]["code"] == code
            )
        backend.controls["mode"] = "oversize_state"
        result = await call(backend, client, "get_story_state")
        assert (
            result.is_error
            and result.structured_content["error"]["code"] == "result_budget_exceeded"
        )
        assert (
            result.structured_content["data"] is None
            and result.structured_content["truncated"]
        )
        assert "完整状态" not in result.content[0].text
    backend.verified = True


@pytest.mark.asyncio
async def test_stdio_rate_limit_does_not_mutate_formal_state(backend):
    formal = formal_snapshot(backend.app.state.repo, backend.first.pid)
    async with session(backend) as (client, _):
        for _ in range(30):
            assert not (await call(backend, client, "get_story_state")).is_error
        result = await call(backend, client, "get_story_state")
        assert (
            result.is_error
            and result.structured_content["error"]["code"] == "rate_limited"
        )
    assert formal_snapshot(backend.app.state.repo, backend.first.pid) == formal
    backend.verified = True


def test_startup_scope_cannot_escape_workspace_or_use_remote_url():
    with pytest.raises(ValueError):
        StoryMCPConfig(root=Path("/tmp"), project_id="a" * 32, before_position=100003)
    with pytest.raises(ValueError):
        StoryMCPConfig(root=ROOT, project_id="../data", before_position=100003)
    with pytest.raises(ValueError):
        StoryMCPConfig(
            root=ROOT,
            project_id="a" * 32,
            before_position=100003,
            url="https://example.com",
        )
