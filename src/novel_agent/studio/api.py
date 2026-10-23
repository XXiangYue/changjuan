from __future__ import annotations

import asyncio
import json
import zipfile
import portalocker
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .db import Database, encode, now, uid
from .engine import Engine
from .models import (
    AppendChapter,
    BlindVote,
    ChapterPlan,
    DirectionUpdate,
    DraftSave,
    ProjectCreate,
    RunCreate,
    SettingsUpdate,
    StateProposalUpdate,
)
from .repository import Conflict, Repository, unpack
from .retrieval import Retrieval
from .settings import ROOT, Settings


def create_app(root: Path = ROOT, model=None):
    settings = Settings(root)
    db = Database(settings.within(settings.data / "studio.sqlite"))
    repo = Repository(db, settings)
    retrieval = Retrieval(db, settings)

    @asynccontextmanager
    async def lifespan(app):
        try:
            lock = portalocker.Lock(
                str(settings.data / "server.lock"),
                timeout=0,
                flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            lock.acquire()
        except portalocker.exceptions.LockException:
            raise RuntimeError(
                "这个数据目录已有长卷服务在运行，请使用已有服务。"
            ) from None
        try:
            async with AsyncSqliteSaver.from_conn_string(
                str(settings.data / "checkpoints.sqlite")
            ) as saver:
                await saver.setup()
                engine = Engine(repo, retrieval, saver, model)
                app.state.engine = engine
                db.execute(
                    "UPDATE runs SET status='interrupted',error='上次服务退出，进度已保存，可恢复',cancel_requested=0 WHERE status IN ('running','queued')"
                )
                for row in db.all(
                    "SELECT project_id,result FROM runs WHERE kind='blueprint' AND status='completed' ORDER BY created"
                ):
                    value = json.loads(row["result"])
                    if value.get("memory"):
                        db.execute(
                            "INSERT OR IGNORE INTO initial_memory VALUES(?,?)",
                            (row["project_id"], encode(value["memory"])),
                        )
                try:
                    yield
                finally:
                    await engine.shutdown()
                    retrieval.close()
        finally:
            lock.release()

    app = FastAPI(title="长卷 · 创作工作台", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    app.state.repo = repo
    app.state.db = db
    app.state.settings = settings
    app.state.retrieval = retrieval

    @app.middleware("http")
    async def local_origin(request, call_next):
        # A website in another tab cannot mutate the loopback application.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname not in (
                "localhost",
                "127.0.0.1",
                "testserver",
            ):
                return JSONResponse({"detail": "请求来源不受支持"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(LookupError)
    async def missing(_, exc):
        return JSONResponse({"detail": str(exc)}, 404)

    @app.exception_handler(zipfile.BadZipFile)
    async def invalid_zip(_, exc):
        return JSONResponse({"detail": "无法读取备份，请选择有效 ZIP 文件"}, 400)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": "1.0.0"}

    @app.get("/api/settings")
    async def get_settings():
        return settings.public() | {
            "usage": db.usage_summary(),
            "retrieval_mode": retrieval.vector_status,
            "reranker": retrieval.reranker.status(),
        }

    @app.put("/api/settings")
    async def set_settings(req: SettingsUpdate):
        settings.update(req.model_dump())
        return settings.public()

    @app.post("/api/settings/reranker/prepare")
    async def prepare_reranker(allow_download: bool = False):
        # Explicit setup only; regular searches never download or load it.
        return await asyncio.to_thread(
            retrieval.prepare_reranker, allow_download=allow_download
        )

    @app.post("/api/settings/connect")
    async def connect():
        return await app.state.engine.model.connection()

    @app.get("/api/projects")
    async def projects():
        return repo.projects()

    @app.post("/api/projects", status_code=201)
    async def create(req: ProjectCreate):
        return repo.create(req)

    @app.post("/api/projects/import", status_code=201)
    async def import_project(request: Request):
        # Read bounded data without extracting archive paths to disk.
        chunks = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > 20_000_000:
                raise ValueError("文件不能超过20 MB")
            chunks.append(chunk)
        return repo.import_zip(b"".join(chunks))

    @app.get("/api/projects/{pid}")
    async def project(pid: str):
        return repo.project(pid)

    @app.get("/api/projects/{pid}/story-state")
    async def story_state(pid: str, before: int | None = Query(default=None, ge=0)):
        return repo.story_state(pid, before_position=before)

    @app.post("/api/projects/{pid}/chapters", status_code=201)
    async def append_chapter(pid: str, req: AppendChapter):
        repo.assert_idle(pid)
        p = repo.project(pid)
        if not any(v["number"] == req.volume for v in p["volumes"]):
            raise ValueError("请选择该作品已有的分卷")
        cid = uid()
        with db.transaction() as c:
            actual = c.execute(
                "SELECT revision FROM projects WHERE id=?", (pid,)
            ).fetchone()[0]
            if actual != req.project_revision:
                raise Conflict("作品已变化，请重新载入")
            n = c.execute(
                "SELECT COALESCE(MAX(number),0)+1 FROM chapters WHERE project_id=? AND volume=?",
                (pid, req.volume),
            ).fetchone()[0]
            if n >= 100000:
                raise ValueError("本卷章节数已达上限，请创建新卷")
            plan = ChapterPlan(
                title=req.title.strip() or f"第{n}章",
                goal=req.goal,
                target_words=req.target_words or p["chapter_words"],
            )
            c.execute(
                "INSERT INTO chapters(id,project_id,volume,number,title,plan,updated) VALUES(?,?,?,?,?,?,?)",
                (cid, pid, req.volume, n, plan.title, plan.model_dump_json(), now()),
            )
            c.execute(
                "UPDATE projects SET revision=revision+1,updated=? WHERE id=?",
                (now(), pid),
            )
        db.audit(
            pid,
            "chapter.appended",
            {"chapter_id": cid, "volume": req.volume, "number": n},
        )
        return repo.chapter(cid)

    @app.put("/api/projects/{pid}/direction")
    async def direction(pid: str, req: DirectionUpdate):
        repo.assert_idle(pid)
        p = repo.project(pid)
        with db.transaction() as c:
            actual = c.execute(
                "SELECT revision FROM projects WHERE id=?", (pid,)
            ).fetchone()[0]
            if actual != req.project_revision:
                raise Conflict("作品已变化，请重新载入")
            c.execute(
                "UPDATE projects SET premise=?,style=?,revision=revision+1,updated=? WHERE id=?",
                (req.premise, req.style, now(), pid),
            )
            c.execute("UPDATE chapters SET reviewed_hash='' WHERE project_id=?", (pid,))
        impact = {
            "chapter_plans": [
                {"id": x["id"], "title": x["title"]} for x in p["chapters"]
            ],
            "memory": [x["name"] for x in p["memory"]],
            "review": "旧审核失效，需按新方向重新核对",
        }
        db.audit(
            pid,
            "direction.edited",
            {"previous_revision": req.project_revision, "impact": impact},
        )
        return {"project": repo.project(pid), "impact": impact}

    @app.post("/api/projects/{pid}/archive")
    async def archive(pid: str):
        repo.project(pid)
        repo.assert_idle(pid)
        db.execute("UPDATE projects SET archived=1 WHERE id=?", (pid,))
        db.audit(pid, "project.archived", {})
        return {"ok": True}

    @app.put("/api/projects/{pid}/outline")
    async def outline(pid: str, data: dict = Body(...)):
        repo.assert_idle(pid)
        p = repo.project(pid)
        if data.get("project_revision") != p["revision"]:
            raise Conflict("作品已变化")
        text = data.get("outline", "")
        if not isinstance(text, str) or len(text) > 100000:
            raise ValueError("卷纲内容过长")
        with db.transaction() as c:
            c.execute(
                "UPDATE projects SET outline=?,revision=revision+1,updated=? WHERE id=? AND revision=?",
                (text, now(), pid, p["revision"]),
            )
            c.execute("UPDATE chapters SET reviewed_hash='' WHERE project_id=?", (pid,))
        db.audit(pid, "outline.edited", {"previous_revision": p["revision"]})
        return repo.project(pid)

    @app.put("/api/projects/{pid}/memory/{name}")
    async def memory_edit(pid: str, name: str, data: dict = Body(...)):
        content = data.get("content")
        if not isinstance(content, str) or len(content) > 100000:
            raise ValueError("无效记忆内容")
        repo.update_memory(pid, name, content, data.get("project_revision"))
        repo.mirror(pid)
        return repo.project(pid)

    @app.get("/api/projects/{pid}/search")
    async def search(
        pid: str,
        q: str = "",
        before: int = Query(default=999999999, ge=0),
        limit: int = Query(default=8, ge=1, le=8),
    ):
        repo.project(pid)
        return await asyncio.to_thread(
            retrieval.search, pid, q, before=before, limit=limit
        )

    @app.post("/api/projects/{pid}/reindex")
    async def reindex(pid: str):
        repo.project(pid)
        return await asyncio.to_thread(retrieval.rebuild, pid)

    @app.get("/api/projects/{pid}/export")
    async def export(pid: str, format: Literal["md", "txt", "zip"] = "zip"):
        data, mime, ext = repo.export(pid, format)
        title = repo.project(pid)["title"]
        return Response(
            data,
            media_type=mime,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(title)}.{ext}"
            },
        )

    @app.post("/api/projects/{pid}/exports", status_code=201)
    async def export_local(pid: str, format: Literal["md", "txt", "zip"] = "zip"):
        project = repo.project(pid)
        filename = (
            now().split(".")[0].replace(":", "").replace("-", "") + "-" + uid()[:6]
        )
        path = settings.within(
            settings.data
            / "projects"
            / project["id"]
            / "exports"
            / (filename + "." + format)
        )
        data, _, _ = repo.export(pid, format)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        db.audit(
            pid,
            "project.exported",
            {"format": format, "path": str(path), "bytes": len(data)},
        )
        return {"path": str(path), "bytes": len(data), "format": format}

    @app.get("/api/chapters/{cid}")
    async def chapter(cid: str):
        return repo.chapter(cid)

    @app.get("/api/chapters/{cid}/state-preview")
    async def state_preview(cid: str, selected: str | None = None):
        # Omitted means preview all; selected= means none; selected=0,2 means
        # precisely those indices. Preview never grants confirmation authority.
        indices = None
        if selected is not None:
            if selected and any(not part.isdecimal() for part in selected.split(",")):
                raise ValueError("selected 需为逗号分隔的非负提案下标，或空字符串")
            indices = [int(part) for part in selected.split(",")] if selected else []
        return repo.state_preview(cid, indices)

    @app.put("/api/chapters/{cid}/state-proposal")
    async def state_proposal(cid: str, req: StateProposalUpdate):
        return repo.update_state_proposal(cid, req)

    @app.put("/api/chapters/{cid}/draft")
    async def save(cid: str, req: DraftSave):
        return repo.save_draft(cid, req)

    @app.put("/api/chapters/{cid}/plan")
    async def plan(cid: str, data: dict = Body(...)):
        ch = repo.chapter(cid)
        repo.assert_idle(ch["project_id"])
        validated = ChapterPlan.model_validate(data.get("plan", {}))
        with db.transaction() as c:
            current = c.execute(
                "SELECT draft_revision FROM chapters WHERE id=?", (cid,)
            ).fetchone()[0]
            if current != data.get("base_revision"):
                raise Conflict("章节已变化，请重新载入")
            c.execute(
                "UPDATE chapters SET plan=?,title=?,draft_revision=draft_revision+1,reviewed_hash='' WHERE id=?",
                (validated.model_dump_json(), validated.title, cid),
            )
            c.execute(
                "UPDATE projects SET revision=revision+1,updated=? WHERE id=?",
                (now(), ch["project_id"]),
            )
        db.audit(ch["project_id"], "plan.edited", {"chapter_id": cid})
        return repo.chapter(cid)

    @app.get("/api/chapters/{cid}/versions")
    async def versions(cid: str):
        return repo.versions(cid)

    @app.post("/api/chapters/{cid}/restore")
    async def restore(cid: str, data: dict = Body(...)):
        return repo.restore(cid, data.get("version"), data.get("base_revision"))

    @app.post("/api/runs", status_code=202)
    async def start(req: RunCreate):
        return await app.state.engine.start(req)

    @app.get("/api/runs/{rid}")
    async def run(rid: str):
        return app.state.engine.status(rid)

    @app.post("/api/runs/{rid}/approve", status_code=202)
    async def approve(rid: str, data: dict = Body(...)):
        return await app.state.engine.resume(rid, data)

    @app.post("/api/runs/{rid}/resume", status_code=202)
    async def resume(rid: str):
        return await app.state.engine.resume(rid)

    @app.post("/api/runs/{rid}/cancel")
    async def cancel(rid: str):
        return await app.state.engine.stop(rid)

    @app.post("/api/runs/{rid}/pause")
    async def pause(rid: str):
        return await app.state.engine.stop(rid, True)

    @app.post("/api/runs/{rid}/recover-draft")
    async def recover_draft(rid: str, data: dict = Body(...)):
        r = app.state.engine.status(rid)
        if (
            r["status"] not in ("failed", "cancelled", "interrupted")
            or not r["chapter_id"]
        ):
            raise Conflict("当前任务没有可恢复的章节片段")
        if r["config"].get("selection"):
            raise Conflict("选段改写请从运行记录复制片段，避免覆盖完整章节")
        start = db.one(
            "SELECT seq FROM run_events WHERE run_id=? AND type='draft.start' ORDER BY seq DESC LIMIT 1",
            (rid,),
            required=False,
        )
        rows = db.all(
            "SELECT type,data FROM run_events WHERE run_id=? AND seq>? ORDER BY seq",
            (rid, start["seq"] if start else 0),
        )
        outputs = [
            json.loads(x["data"])
            for x in rows
            if x["type"] == "model.output"
            and json.loads(x["data"]).get("role") == "作者"
        ]
        text = (
            outputs[-1].get("text", "")
            if outputs
            else "".join(
                json.loads(x["data"]).get("text", "")
                for x in rows
                if x["type"] == "token"
            )
        )
        if not text.strip():
            raise ValueError("没有可恢复的作者正文")
        ch = repo.chapter(r["chapter_id"])
        if data.get("base_revision") != ch["draft_revision"]:
            raise Conflict("稿件已变化，请重新载入再恢复")
        db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (rid,))
        restored = repo.save_draft(
            ch["id"],
            DraftSave(
                content=text, title=ch["title"], base_revision=data["base_revision"]
            ),
        )
        db.audit(
            r["project_id"],
            "draft.recovered",
            {"run_id": rid, "partial": not bool(outputs)},
        )
        return restored

    @app.get("/api/runs/{rid}/events")
    async def events(rid: str, request: Request, after: int = 0, once: bool = False):
        app.state.engine.status(rid)
        cursor = max(after, int(request.headers.get("last-event-id", "0") or 0))
        if once:
            return [
                dict(x, data=json.loads(x["data"]))
                for x in db.all(
                    "SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 10000",
                    (rid, cursor),
                )
            ]

        async def stream():
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                rows = db.all(
                    "SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500",
                    (rid, cursor),
                )
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\ndata: {encode({'type': row['type'], 'data': json.loads(row['data']), 'seq': cursor})}\n\n"
                status = app.state.engine.status(rid)["status"]
                if status not in ("queued", "running") and not rows:
                    yield f"data: {encode({'type': 'state', 'data': {'status': status}})}\n\n"
                    break
                heartbeat += 1
                if heartbeat % 40 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/research")
    async def research():
        experiments = [
            unpack(x) for x in db.all("SELECT * FROM experiments ORDER BY created DESC")
        ]
        pairs = db.all(
            "SELECT id,experiment_id,a,b,preference,reason,voted_at FROM blind_pairs ORDER BY id"
        )
        return {
            "usage": db.usage_summary(),
            "experiments": experiments,
            "pairs": pairs,
            "methodology": {
                "model": "deepseek-flash",
                "budget_cap": 300,
                "review_weights": {
                    "剧情一致性": 0.3,
                    "文笔流畅度": 0.25,
                    "人设吻合度": 0.25,
                    "原创性": 0.2,
                },
                "limitations": [
                    "自动评分与写作使用同一模型，存在相关偏差",
                    "一位真实用户的盲评不代表作者群体",
                    "费用为官方高峰价格的保守估算，实际以账单为准",
                ],
            },
        }

    @app.post("/api/blind/{bid}/vote")
    async def vote(bid: str, req: BlindVote):
        db.one("SELECT id FROM blind_pairs WHERE id=?", (bid,))
        db.execute(
            "UPDATE blind_pairs SET preference=?,reason=?,voted_at=? WHERE id=?",
            (req.preference, req.reason, now(), bid),
        )
        return {"ok": True}

    @app.get("/api/blind/reveal")
    async def reveal():
        if db.one(
            "SELECT id FROM blind_pairs WHERE preference IS NULL", required=False
        ):
            raise Conflict("请完成全部盲评后揭晓版本")
        return db.all("SELECT id,labels,preference,reason FROM blind_pairs ORDER BY id")

    @app.get("/api/audit/{pid}")
    async def audit(pid: str):
        repo.project(pid)
        return db.all(
            "SELECT * FROM audit WHERE project_id=? ORDER BY created DESC LIMIT 100",
            (pid,),
        )

    dist = root / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse(
            {
                "message": "后端已启动。请在 web 目录执行 pnpm build 构建界面。",
                "docs": "/docs",
            }
        )

    return app
