from __future__ import annotations

import asyncio
import sqlite3
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from .context_budget import pack_context
from .db import encode, now, uid
from .llm import BudgetExceeded, Cancelled, ModelClient
from .length_control import check_length, writing_contract
from .models import (
    Approve,
    Blueprint,
    ChapterPlan,
    MemoryProposal,
    ReviewReport,
    digest,
    numbered_paragraphs,
    ground_citations,
    ground_state_changes,
)
from .repository import Conflict, unpack

AUTHOR_SYSTEM = """你是中文长篇小说作者。尊重作者的方向与已确认设定，人物行为必须由动机与处境推动。
每个场景应产生信息、关系、处境或决定上的变化。避免模板化赞叹、总结式升华、重复比喻和旁白解释。
世界观、作者规划与角色知情范围是不同层次；人物不能无缘无故知道秘密。
突破、胜利和资源变化必须有铺垫与代价。用动作与对话展示情绪。只输出小说正文，不输出说明或思考。"""

STATE_GUIDANCE = """\ncontext.story_state只含已确认的过去事实。检查本章位置接近兑现窗口的hooks，按payoff_condition设计可见行动、结果与见证；承诺和调查线索不等于履行。计划的条件不自动成为已发生事实。资源quantity=null表示未记录数量，不能当作零；人物只能使用已获得的knowledge，未知也不能自动判为矛盾。"""


class RunState(TypedDict, total=False):
    run_id: str
    project_id: str
    chapter_id: str | None
    kind: str
    config: dict
    context: dict
    project_revision: int
    plan: dict
    blueprint: dict
    iteration: int
    review: dict
    approval: dict


class Engine:
    def __init__(self, repo, retrieval, checkpointer, model=None):
        self.repo, self.db, self.retrieval = repo, repo.db, retrieval
        self.model = model or ModelClient(self.db, repo.settings, retrieval)
        self.tasks = {}
        self.slots = asyncio.Semaphore(2)
        graph = StateGraph(RunState)
        for name, fn in [
            ("setup", self._setup),
            ("context", self._context),
            ("plan", self._plan),
            ("write", self._write),
            ("review", self._review),
            ("memory", self._memory),
            ("approval", self._approval),
            ("commit", self._commit),
        ]:
            graph.add_node(name, fn)
        graph.add_conditional_edges(
            START,
            lambda s: "setup" if s["kind"] in ("blueprint", "volume") else "context",
        )
        graph.add_edge("setup", "approval")
        graph.add_edge("context", "plan")
        graph.add_conditional_edges(
            "plan", lambda s: "review" if s["kind"] == "review" else "write"
        )
        graph.add_edge("write", "review")
        graph.add_conditional_edges("review", self._after_review)
        graph.add_edge("memory", "approval")
        graph.add_edge("approval", "commit")
        graph.add_edge("commit", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    def status(self, rid):
        return unpack(self.db.one("SELECT * FROM runs WHERE id=?", (rid,)))

    def event(self, s, stage, message):
        rid = s["run_id"]
        self.db.execute(
            "UPDATE runs SET node=?,updated=? WHERE id=?", (stage, now(), rid)
        )
        self.db.event(rid, "stage", {"stage": stage, "message": message})
        self.check_cancel(rid)

    def check_cancel(self, rid):
        if self.db.one("SELECT cancel_requested FROM runs WHERE id=?", (rid,))[
            "cancel_requested"
        ]:
            raise Cancelled("运行已停止")

    async def start(self, req):
        p = self.repo.project(req.project_id)
        if req.chapter_id:
            ch = self.repo.chapter(req.chapter_id)
            if ch["project_id"] != p["id"]:
                raise ValueError("章节不属于该作品")
        elif req.kind not in ("blueprint", "volume"):
            raise ValueError("请选择章节")
        if not self.repo.settings.values()["api_key"] and not getattr(
            self.model, "is_test", False
        ):
            raise ValueError("请先配置 DeepSeek API 密钥")
        rid = uid()
        try:
            self.db.execute(
                "INSERT INTO runs(id,project_id,chapter_id,kind,config,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    rid,
                    p["id"],
                    req.chapter_id,
                    req.kind,
                    encode(req.model_dump()),
                    now(),
                    now(),
                ),
            )
        except sqlite3.IntegrityError:
            raise Conflict("该作品有未完成任务，请先确认、取消或恢复") from None
        state = RunState(
            run_id=rid,
            project_id=p["id"],
            chapter_id=req.chapter_id,
            kind=req.kind,
            config=req.model_dump(),
            iteration=0,
        )
        self._schedule(rid, state)
        return self.status(rid)

    def _schedule(self, rid, value):
        if rid in self.tasks and not self.tasks[rid].done():
            raise Conflict("任务正在运行")
        task = asyncio.create_task(self._work(rid, value), name=f"changjuan-{rid}")
        self.tasks[rid] = task
        task.add_done_callback(lambda t: self.tasks.pop(rid, None))

    async def _work(self, rid, value):
        async with self.slots:
            try:
                self.check_cancel(rid)
                self.db.execute(
                    "UPDATE runs SET status='running',error='',updated=? WHERE id=?",
                    (now(), rid),
                )
                output = await self.graph.ainvoke(
                    value, {"configurable": {"thread_id": rid}, "recursion_limit": 60}
                )
                run = self.status(rid)
                if output.get("__interrupt__"):
                    self.db.execute(
                        "UPDATE runs SET status='awaiting_approval',updated=? WHERE id=?",
                        (now(), rid),
                    )
                    self.db.event(
                        rid,
                        "awaiting_approval",
                        {"message": "检查点已保存，可以审阅与确认"},
                    )
                elif run["status"] == "running":
                    self.db.execute(
                        "UPDATE runs SET status='completed',updated=? WHERE id=?",
                        (now(), rid),
                    )
                    self.db.event(
                        rid, "completed", {"message": "作者确认的版本与故事记忆已保存"}
                    )
            except asyncio.CancelledError:
                self.db.execute(
                    "UPDATE runs SET status='interrupted',error='服务重启，进度已保存',updated=? WHERE id=?",
                    (now(), rid),
                )
            except Exception as exc:
                run = self.status(rid)
                is_pause = run["cancel_requested"] == 2
                status = (
                    "interrupted"
                    if is_pause or isinstance(exc, BudgetExceeded)
                    else "cancelled"
                    if isinstance(exc, Cancelled)
                    else "failed"
                )
                safe = (
                    "模型输出未符合该步骤要求，正文与进度已保留；恢复将重试当前步骤。"
                    if isinstance(exc, ValidationError)
                    else str(exc)
                    if isinstance(
                        exc, (ValueError, Conflict, BudgetExceeded, Cancelled)
                    )
                    else f"{type(exc).__name__}：本次任务未完成，进度与已生成片段已保留，可恢复或重试。"
                )
                secret = self.repo.settings.values()["api_key"]
                if secret:
                    safe = safe.replace(secret, "[已隐藏]")
                self.db.execute(
                    "UPDATE runs SET status=?,error=?,updated=? WHERE id=?",
                    (status, safe[:1000], now(), rid),
                )
                self.db.event(rid, "error", {"message": safe[:1000], "status": status})

    async def resume(self, rid, approval=None):
        r = self.status(rid)
        if r["status"] not in ("awaiting_approval", "interrupted", "failed"):
            raise Conflict("当前任务不需要恢复")
        if r["status"] == "awaiting_approval" and approval is None:
            raise ValueError("请提交审阅决定")
        if approval is not None and r["chapter_id"]:
            decision = Approve.model_validate(approval)
            ch = self.repo.chapter(r["chapter_id"])
            if (
                not ch["review"].get("passed")
                or not ch["review"].get("length_check", {}).get("within_range", True)
            ) and not decision.note.strip():
                # Keep the interrupt reviewable; invalid author input must not
                # consume it and strand the run at a failed commit checkpoint.
                raise Conflict("审核或篇幅目标尚未达标；请修订，或填写继续定稿的原因")
        try:
            self.db.execute(
                "UPDATE runs SET status='queued',cancel_requested=0,updated=? WHERE id=?",
                (now(), rid),
            )
        except sqlite3.IntegrityError:
            raise Conflict("该作品已有其他运行中的任务") from None
        self._schedule(rid, Command(resume=approval) if approval is not None else None)
        return self.status(rid)

    async def stop(self, rid, pause=False):
        r = self.status(rid)
        if r["status"] in ("completed", "cancelled"):
            return r
        flag = 2 if pause else 1
        if r["status"] == "running":
            self.db.execute(
                "UPDATE runs SET cancel_requested=?,updated=? WHERE id=?",
                (flag, now(), rid),
            )
        else:
            self.db.execute(
                "UPDATE runs SET cancel_requested=?,status=?,updated=? WHERE id=?",
                (flag, "interrupted" if pause else "cancelled", now(), rid),
            )
        self.db.event(
            rid,
            "control",
            {
                "message": "将在安全位置暂停，已生成片段保留"
                if pause
                else "正在取消任务"
            },
        )
        return self.status(rid)

    async def shutdown(self):
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)

    async def _setup(self, s):
        self.event(s, "planning", "规划故事主线、成长代价与分章安排")
        p = self.repo.project(s["project_id"])
        is_volume = s["kind"] == "volume"
        prompt = encode(
            {
                k: p[k]
                for k in (
                    "title",
                    "genre",
                    "premise",
                    "style",
                    "target_chapters",
                    "chapter_words",
                    "outline",
                )
            }
        )
        prompt += "\n作者补充：" + s["config"]["instruction"]
        count = min(p["target_chapters"], 30)
        prompt += f"\n请给出完整卷纲与 {count} 章具体计划，每章目标 {p['chapter_words']} 字；末章应完成本卷核心冲突。alternatives 提供两个不同冲突解决方案并说明代价。不要照搬已有作品的人名、设定和情节。"
        if is_volume:
            prompt += (
                "\n这是新一卷。所有已确认记忆如下："
                + encode(p["memory"])
                + "\n以前一卷核心剧情为依据更新前卷概述，以原下一卷剧情为依据展开本卷；保留稳定设定。"
            )
        bp = await self.model.structured(
            s["run_id"],
            "规划员",
            "你是长篇小说策划。先阅读适用的创作技能，按需查阅历史证据，输出完整可执行的故事规划。",
            prompt,
            Blueprint,
            max_tokens=24000,
            agent=True,
            pid=p["id"],
        )
        if len(bp.chapters) != count:
            raise ValueError(f"章节规划数量应为 {count}，请恢复任务重试")
        result = bp.model_dump() | {"project_revision": p["revision"]}
        self.db.execute(
            "UPDATE runs SET result=? WHERE id=?", (encode(result), s["run_id"])
        )
        return {"blueprint": bp.model_dump(), "project_revision": p["revision"]}

    async def _context(self, s):
        self.event(s, "context", "读取核心记忆、上一章与相关历史证据")
        ch = self.repo.chapter(s["chapter_id"])
        earlier = self.db.all(
            "SELECT title,canonical_revision,stale FROM chapters WHERE project_id=? AND (volume<? OR (volume=? AND number<?)) ORDER BY volume,number",
            (s["project_id"], ch["volume"], ch["volume"], ch["number"]),
        )
        if any(not x["canonical_revision"] or x["stale"] for x in earlier):
            raise Conflict("前面的章节尚未定稿或需要复核，请按顺序完成")
        if s["kind"] in ("review", "rewrite") and not ch["content"].strip():
            raise ValueError("请先写入正文")
        context = await asyncio.to_thread(
            self.retrieval.context,
            s["project_id"],
            ch["id"],
            s["config"]["ablation"] in ("no_memory", "baseline"),
        )
        if s["config"]["ablation"] == "baseline":
            context.retrieval_mode = (
                "旧流程策略：核心记忆全文 + 上一章；无结构化状态检索"
            )
        if s["config"]["ablation"] not in ("baseline", "no_memory"):
            context.story_state = self.repo.story_context(
                s["project_id"], ch["volume"] * 100000 + ch["number"]
            )
        self.db.execute(
            "UPDATE chapters SET context=? WHERE id=?",
            (context.model_dump_json(), ch["id"]),
        )
        self.db.event(
            s["run_id"],
            "evidence",
            {
                "mode": context.retrieval_mode,
                "sources": context.evidence,
                "fact_count": len(context.facts),
            },
        )
        return {
            "context": context.model_dump(),
            "project_revision": context.project_revision,
        }

    async def _plan(self, s):
        self.event(s, "scene_plan", "核对本章目标、人物动机与场景转折")
        ch = self.repo.chapter(s["chapter_id"])
        if ch["plan"].get("goal"):
            plan = ChapterPlan.model_validate(ch["plan"])
        else:
            p = self.repo.project(s["project_id"])
            plan = await self.model.structured(
                s["run_id"],
                "场景规划员",
                "设计推动故事的场景，明确冲突、选择、代价和变化，尊重已确认设定。",
                encode(
                    {
                        "project": p["premise"],
                        "chapter": ch["number"],
                        "target_words": p["chapter_words"],
                        "context": self._model_context(s, "writer"),
                        "instruction": s["config"]["instruction"],
                    }
                ),
                ChapterPlan,
            )
        self.db.execute(
            "UPDATE chapters SET plan=?,title=? WHERE id=?",
            (plan.model_dump_json(), plan.title, ch["id"]),
        )
        return {"plan": plan.model_dump()}

    def _model_context(self, s, role):
        # Keep chapters.context / checkpoint memory complete for historical
        # rollback and proposal reconciliation. The model gets a separate view.
        if s["config"].get("ablation") == "baseline":
            return s["context"]
        plan = s.get("plan") or self.repo.chapter(s["chapter_id"])["plan"]
        query = " ".join(
            [
                plan.get("goal", ""),
                *plan.get("characters", []),
                *plan.get("hooks", []),
                s["config"].get("instruction", ""),
            ]
        )
        view, audit = pack_context(
            s["context"],
            role=role,
            query=query,
            max_bytes=s["config"].get("context_budget_bytes", 60000),
        )
        self.db.event(
            s["run_id"],
            "context.budget",
            audit
            | {
                "message": f"{role}资料视图 {audit['serialized_bytes']:,}/{audit['max_bytes']:,} 字节；完整记忆快照保留",
            },
        )
        return view

    async def _write(self, s):
        self.event(
            s,
            "writing",
            "根据章节规划创作正文" if not s.get("review") else "根据编辑意见修订正文",
        )
        p = self.repo.project(s["project_id"])
        ch = self.repo.chapter(s["chapter_id"])
        prompt = encode(
            {
                "title": p["title"],
                "genre": p["genre"],
                "premise": p["premise"],
                "style": p["style"],
                "outline": p["outline"],
                "volume": ch["volume"],
                "chapter": ch["number"],
                "plan": s["plan"],
                "context": self._model_context(s, "writer"),
                "instruction": s["config"]["instruction"],
            }
        )
        selection = s["config"].get("selection", "")
        rewrite = s["kind"] == "rewrite" and not s.get("review")
        if rewrite:
            if not selection or ch["content"].count(selection) != 1:
                raise ValueError("选段已变化或不唯一，请重新选择")
            prompt += (
                "\n完整原章："
                + ch["content"]
                + "\n只改写以下选段并仅返回替换文字："
                + selection
            )
            if selection == ch["content"]:
                prompt += writing_contract(s["plan"], ch["content"])
        elif s.get("review"):
            prompt += (
                "\n待修订正文："
                + ch["content"]
                + "\n编辑意见："
                + encode(s["review"])
                + "\n保留有效情节与文风，只修改有依据的问题，输出修订后的完整正文。"
            )
            if not s["review"].get("length_check", {}).get("within_range", True):
                prompt += writing_contract(s["plan"], ch["content"])
        else:
            prompt += writing_contract(s["plan"])
        self.db.event(
            s["run_id"],
            "draft.start",
            {"chapter_id": ch["id"], "mode": "selection" if rewrite else "chapter"},
        )
        text = await self.model.text(
            s["run_id"],
            "作者",
            AUTHOR_SYSTEM
            + (
                STATE_GUIDANCE
                if s["config"]["ablation"] not in ("baseline", "no_memory")
                else ""
            ),
            prompt,
            max_tokens=min(32000, max(8000, s["plan"]["target_words"] * 4)),
        )
        if rewrite:
            text = ch["content"].replace(selection, text, 1)
        self.repo.store_generated(
            ch["id"], text, status="draft", review={}, proposal={}, reviewed_hash=""
        )
        self.db.event(
            s["run_id"], "draft.saved", {"chapter_id": ch["id"], "words": len(text)}
        )
        return {"iteration": s.get("iteration", 0) + 1}

    async def _review(self, s):
        self.event(s, "reviewing", "一致性编辑与叙事编辑独立审阅")
        ch = self.repo.chapter(s["chapter_id"])
        if not ch["content"].strip():
            raise ValueError("模型返回空章节，审核未通过")
        length_check = check_length(ch["content"], s["plan"])
        within = length_check["within_range"]
        if (
            not within
            and s.get("iteration", 0) < 4
            and s["config"]["auto_revise"]
            and s["config"]["ablation"] not in ("baseline", "no_revision")
            and s["kind"] not in ("review", "rewrite")
        ):
            # Run cheap deterministic checks before spending on two reasoning editors.
            data = {
                "passed": False,
                "length_check": length_check,
                "findings": [],
                "summary": "先将篇幅调整到目标范围，再进行独立编辑审阅。",
            }
            self.db.event(s["run_id"], "length_check", data)
            return {"review": data}
        p = self.repo.project(s["project_id"])
        prompt = encode(
            {
                "chapter": numbered_paragraphs(ch["content"]),
                "volume": ch["volume"],
                "chapter_number": ch["number"],
                "plan": s["plan"],
                "context": self._model_context(s, "editor"),
                "author_requirements": p["premise"],
                "style": p["style"],
                "outline": p["outline"],
                "editorial_focus": s["config"]["instruction"],
            }
        )
        system = "审核中文小说。四项分数必须客观为0–10，严重错误不能靠文笔抵消。问题需引用待审核正文中的原句，证据需来自已给定资料，未知不等于矛盾。原创性仅评估重复或过度模仿风险，不宣称完成全网查重。"
        if s["config"]["ablation"] != "baseline":
            system += STATE_GUIDANCE
            system += "\n先寻找正文已有的解释或后续揭示来反证疑点；已经解释的内容不应再要求补写。角色说将来要做不代表已经履行。只有已发生的事实与证据不可同时成立才列critical；风格偏好、可调整的场景计划、暂时未知的信息不得列critical。可以没有问题，不要为了显得专业而罗列问题。没有实质矛盾时一致性可以给7–10分，有打磨建议不等于不通过。"
        system += "每个finding请填写paragraph为待审核正文的对应段落编号，quote可留空，由程序提取原文，禁止从历史上下文选段当作本章原文。"

        def grounded(report):
            ground_citations(report.findings, ch["content"])

        if s["config"]["ablation"] == "baseline":
            report = await self.model.structured(
                s["run_id"],
                "旧流程审核",
                system,
                prompt,
                ReviewReport,
                validator=grounded,
            )
        else:
            workers = [
                asyncio.create_task(
                    self.model.structured(
                        s["run_id"],
                        "一致性编辑",
                        system
                        + "你重点负责事实、时间、人物动机、角色知情、道具资源、境界规则与伏笔。",
                        prompt,
                        ReviewReport,
                        validator=grounded,
                        reasoning=True,
                    )
                ),
                asyncio.create_task(
                    self.model.structured(
                        s["run_id"],
                        "叙事编辑",
                        system
                        + "你重点负责行文、对话、节奏、张力、场景推进、重复与阅读欲望。",
                        prompt,
                        ReviewReport,
                        validator=grounded,
                        reasoning=True,
                    )
                ),
            ]
            try:
                a, b = await asyncio.gather(*workers)
            except BaseException:
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
            report = ReviewReport(
                scores={
                    "剧情一致性": a.scores["剧情一致性"],
                    "人设吻合度": a.scores["人设吻合度"],
                    "文笔流畅度": b.scores["文笔流畅度"],
                    "原创性": b.scores["原创性"],
                },
                findings=a.findings + b.findings,
                summary=a.summary + "\n" + b.summary,
            )
        for f in report.findings:
            if f.quote and f.quote not in ch["content"]:
                raise ValueError("编辑引用未出现在正文中，需重新审核")
        data = report.model_dump()
        data["review_mode"] = (
            "基础编辑" if s["config"]["ablation"] == "baseline" else "深度编辑"
        )
        data["length_check"] = length_check
        self.db.execute(
            "UPDATE chapters SET review=?,reviewed_hash=?,reviewed_project_revision=? WHERE id=?",
            (encode(data), digest(ch["content"]), s["project_revision"], ch["id"]),
        )
        self.db.event(s["run_id"], "review", data)
        return {"review": data}

    def _after_review(self, s):
        can = (
            s["config"]["auto_revise"]
            and s["config"]["ablation"] not in ("baseline", "no_revision")
            and s["kind"] not in ("review", "rewrite")
        )
        needs_revision = not s["review"]["passed"] or not s["review"].get(
            "length_check", {}
        ).get("within_range", True)
        return (
            "write"
            if can and needs_revision and s.get("iteration", 0) < 4
            else "memory"
        )

    async def _memory(self, s):
        self.event(s, "memory", "提取可核验事实并准备记忆差异")
        ch = self.repo.chapter(s["chapter_id"])
        p = self.repo.project(s["project_id"])
        next_ch = next(
            (
                x
                for x in p["chapters"]
                if (x["volume"], x["number"]) > (ch["volume"], ch["number"])
            ),
            None,
        )
        prompt = encode(
            {
                "chapter": numbered_paragraphs(ch["content"]),
                "context": self._model_context(s, "memory"),
                "plan": s["plan"],
                "author_requirements": p["premise"],
                "outline": p["outline"],
                "next_chapter_plan": next_ch["plan"] if next_ch else {},
                "remaining_goals": [
                    x["plan"].get("goal", "")
                    for x in p["chapters"]
                    if (x["volume"], x["number"]) > (ch["volume"], ch["number"])
                ],
                "target_words": p["chapter_words"],
                "next_chapter": ch["number"] + 1,
                "state_change_binding": {
                    "position": ch["volume"] * 100000 + ch["number"],
                    "source": "来源章节、版本、事件id、章内sequence及确认权限由后端绑定，模型禁止提供",
                },
            }
        )
        system = """你是故事记忆整理员。仅从本章已发生的正文提出增量记忆变更。
不得把未来计划写为已发生事实。每个 fact 填写支持该事实的paragraph段落编号，quote可留空由程序取得本章原文，最多12条关键事实。
稳定世界规则和角色性格保持不变，只更新动态状态。角色信息过期时归档，不抹去历史。
patches 仅包含实际变化的文档，每份保留完整精简内容，不得凭空添加；单份控制在1500字以内。
目前剧情、当前章节设计规划由程序根据chapter_summary和next_plan自动组装，patches不要生成这两份派生文档。每个patch都必须含name、content、reason，reason用一句话说明，不可漏填。
chapter_summary 记录本章关键行动、因果与状态变化，约150字。next_plan 为下一章具体计划。
请同时提供涉及的本卷核心剧情、伏笔和事件的状态更新；本卷完成时明确收束与下一卷引子。"""
        system += "\n作者已确认的下一章目标、标题与字数不能擅改；基于给定下一章规划细化场景衔接。已有未登场人物属于计划，保留其设定并标明未登场，不把计划当成已发生事实。"
        system += """\nstate_changes为可选、最多12项的独立状态提案，按正文发生顺序排列；不确定时留空，不影响自由文本facts。
只提议正文明确已发生的资源建立/转移/消耗、人物获知事实、伏笔埋设/作者待确认兑现。否定、打算、承诺或尝试不等于发生。
每项必须有paragraph及逐字quote（quote可留空由程序按段取得）。labels以稳定ASCII实体ID映射中文名，沿用context.story_state已有ID，同名不能自动认作同一实体。
resource.establish只用于正文明确给出某持有人初始整数数量且目前该库存未知；未知绝不能补0，接收方没有明确初始库存时不要为了让转移通过而虚构建立事件。已有余额不能用establish追加。
knowledge.learn按actor_id/fact_id记录角色确实得知的命题，不把作者知道的秘密扩散给角色；同一fact_id必须沿用相同命题原文。
hook.plant提供兑现条件及全局章节位置窗口window_start/window_end，位置=卷号*100000+章号；hook.resolve仅提出带explanation的待作者确认解释，不宣称程序证明文学兑现。
模型不得提供id、source、status、position或sequence。所有提案默认不被接受，作者勾选后应用绑定来源、检验前置条件再与正文同事务提交。"""

        def grounded(proposal):
            ground_citations(proposal.facts, ch["content"])
            ground_state_changes(proposal.state_changes, ch["content"])

        proposal = await self.model.structured(
            s["run_id"],
            "记忆整理员",
            system,
            prompt,
            MemoryProposal,
            max_tokens=18000,
            validator=grounded,
        )
        if len(proposal.facts) > 12:
            proposal.facts = proposal.facts[:12]
        for f in proposal.facts:
            if not f.quote or f.quote not in ch["content"]:
                raise ValueError("提取事实缺少准确正文引用，请恢复重试")
        data = proposal.model_dump()
        imported_pending = self.repo.imported_state_changes(ch["id"])
        if imported_pending:
            # Importing a new book does not recreate formal state. Keep its
            # source-bound proposals visible until the author accepts/rejects;
            # do not silently overwrite them during a model re-review or merge
            # duplicate initial-inventory events from two extraction passes.
            data["alternative_state_changes"] = data["state_changes"]
            data["state_changes"] = [item["change"] for item in imported_pending]
            data["imported_state_notice"] = (
                "备份事件仍待作者逐项确认；本轮模型建议另存alternative_state_changes，未自动替换或合并。"
            )
        if next_ch and next_ch["plan"].get("goal"):
            old = next_ch["plan"]
            data["next_plan"].update(
                {
                    k: old[k]
                    for k in (
                        "goal",
                        "title",
                        "target_words",
                        "pov",
                        "min_characters",
                        "max_characters",
                    )
                    if k in old
                }
            )
            for k in ("constraints", "hooks"):
                data["next_plan"][k] = list(
                    dict.fromkeys(old.get(k, []) + data["next_plan"].get(k, []))
                )
        summaries = s["context"]["recent_summaries"][-9:] + [
            {"title": ch["title"], "summary": proposal.chapter_summary}
        ]
        rolling = "# 目前剧情\n\n" + "\n\n".join(
            f"## {x['title']}\n{x['summary']}" for x in summaries
        )
        data["patches"] = [
            x
            for x in data["patches"]
            if x["name"] not in ("目前剧情", "当前章节设计规划")
        ]
        data["patches"] += [
            {
                "name": "目前剧情",
                "content": rolling,
                "reason": "滚动维护近十章已确认摘要",
                "quotes": [],
            },
            {
                "name": "当前章节设计规划",
                "content": "# 当前章节设计规划\n\n" + encode(data["next_plan"]),
                "reason": "为下一章准备计划",
                "quotes": [],
            },
        ]
        self.db.execute(
            "UPDATE chapters SET proposal=?,status='review' WHERE id=?",
            (encode(data), ch["id"]),
        )
        self.db.execute(
            "UPDATE runs SET result=? WHERE id=?",
            (
                encode(
                    {
                        "chapter_id": ch["id"],
                        "draft_revision": ch["draft_revision"],
                        "project_revision": s["project_revision"],
                    }
                ),
                s["run_id"],
            ),
        )
        self.db.event(
            s["run_id"],
            "proposal",
            {
                "count": len(data["patches"]),
                "fact_count": len(data["facts"]),
                "state_change_count": len(data["state_changes"]),
            },
        )
        if data["state_changes"]:
            preview = self.repo.state_preview(ch["id"])
            self.db.event(
                s["run_id"],
                "state.preview",
                {
                    "status": preview["status"],
                    "errors": preview["errors"],
                    "count": len(data["state_changes"]),
                    "author_confirmation_required": True,
                },
            )
        return {}

    async def _approval(self, s):
        self.event(s, "approval", "等待作者审阅；确认前不修改正式正文与记忆")
        decision = interrupt(
            {
                "run_id": s["run_id"],
                "chapter_id": s.get("chapter_id"),
                "project_revision": s["project_revision"],
            }
        )
        self.db.execute(
            "UPDATE runs SET status='running',updated=? WHERE id=?",
            (now(), s["run_id"]),
        )
        return {"approval": decision}

    async def _commit(self, s):
        self.event(s, "committing", "保存作者确认的版本与故事记忆")
        already = self.db.one(
            "SELECT run_id FROM run_commits WHERE run_id=?",
            (s["run_id"],),
            required=False,
        )
        if already:
            self.db.event(
                s["run_id"],
                "notice",
                {"message": "正式提交已完成，恢复仅重建检索与阅读镜像"},
            )
        elif s["kind"] == "blueprint":
            if s["approval"].get("project_revision") != s["project_revision"]:
                raise Conflict("规划版本已过期")
            self.repo.apply_blueprint(
                s["project_id"],
                s["blueprint"],
                s["project_revision"],
                run_id=s["run_id"],
            )
        elif s["kind"] == "volume":
            p = self.repo.project(s["project_id"])
            if p["revision"] != s["approval"].get("project_revision"):
                raise Conflict("规划版本已过期")
            number = max(v["number"] for v in p["volumes"]) + 1
            with self.db.transaction() as c:
                actual = c.execute(
                    "SELECT revision FROM projects WHERE id=?", (p["id"],)
                ).fetchone()[0]
                if actual != s["project_revision"]:
                    raise Conflict("规划版本已过期")
                c.execute(
                    "INSERT INTO volumes VALUES(?,?,?,?)",
                    (p["id"], number, f"第{number}卷", s["blueprint"]["outline"]),
                )
                for name in ("前卷剧情概述", "本卷核心剧情", "下一卷剧情"):
                    c.execute(
                        "UPDATE memory SET content=?,revision=revision+1 WHERE project_id=? AND name=?",
                        (s["blueprint"]["memory"][name], p["id"], name),
                    )
                for i, plan in enumerate(s["blueprint"]["chapters"], 1):
                    c.execute(
                        "INSERT INTO chapters(id,project_id,volume,number,title,plan,updated) VALUES(?,?,?,?,?,?,?)",
                        (uid(), p["id"], number, i, plan["title"], encode(plan), now()),
                    )
                c.execute(
                    "UPDATE projects SET outline=outline||?,revision=revision+1,updated=? WHERE id=?",
                    (
                        f"\n\n## 第{number}卷\n" + s["blueprint"]["outline"],
                        now(),
                        p["id"],
                    ),
                )
                c.execute(
                    "INSERT INTO run_commits VALUES(?,?,?,?,?)",
                    (s["run_id"], p["id"], None, number, now()),
                )
        else:
            self.repo.commit_chapter(
                s["chapter_id"],
                Approve.model_validate(s["approval"]),
                run_id=s["run_id"],
            )
        if s["kind"] not in ("blueprint", "volume"):
            await asyncio.to_thread(self.retrieval.index, s["chapter_id"])
        try:
            await asyncio.to_thread(self.repo.mirror, s["project_id"])
        except OSError:
            self.db.event(
                s["run_id"],
                "notice",
                {"message": "正式版本已保存至数据库，Markdown 镜像待重建"},
            )
        return {}
