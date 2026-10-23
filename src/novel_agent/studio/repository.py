from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from .db import Database, encode, now, uid
from .models import (
    MEMORY_NAMES,
    STATE_CHANGE_ADAPTER,
    Approve,
    ChapterPlan,
    DraftSave,
    ProjectCreate,
    StateProposalUpdate,
    digest,
    ground_state_changes,
    numbered_paragraphs,
)
from .settings import Settings
from .story_state import StoryReplayError, replay, validate_event


class Conflict(ValueError):
    pass


def unpack(
    row: dict,
    fields: tuple = ("plan", "review", "proposal", "context", "config", "result"),
):
    for k in fields:
        if k in row:
            row[k] = json.loads(row[k] or "{}")
    return row


class Repository:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings

    def create(self, request: ProjectCreate, demo=False):
        pid = uid()
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO projects(id,title,genre,premise,style,target_chapters,chapter_words,is_demo,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    request.title,
                    request.genre,
                    request.premise,
                    request.style,
                    request.target_chapters,
                    request.chapter_words,
                    int(demo),
                    now(),
                    now(),
                ),
            )
            c.execute(
                "INSERT INTO volumes VALUES(?,?,?,?)",
                (pid, 1, "第一卷 · 故事的开始", ""),
            )
            for name in MEMORY_NAMES:
                c.execute(
                    "INSERT INTO memory VALUES(?,?,?,0)",
                    (pid, name, f"# {name}\n\n尚待规划。"),
                )
            for i in range(1, request.target_chapters + 1):
                c.execute(
                    "INSERT INTO chapters(id,project_id,volume,number,title,plan,updated) VALUES(?,?,?,?,?,?,?)",
                    (
                        uid(),
                        pid,
                        1,
                        i,
                        f"第{i}章",
                        encode(
                            {
                                "title": f"第{i}章",
                                "goal": "",
                                "target_words": request.chapter_words,
                            }
                        ),
                        now(),
                    ),
                )
        return self.project(pid)

    def projects(self):
        return self.db.all(
            "SELECT p.*, (SELECT COUNT(*) FROM chapters c WHERE c.project_id=p.id AND c.canonical_revision>0) AS completed, (SELECT COALESCE(SUM(length(canonical)),0) FROM chapters c WHERE c.project_id=p.id) AS words FROM projects p WHERE archived=0 ORDER BY updated DESC"
        )

    def project(self, pid):
        p = self.db.one("SELECT * FROM projects WHERE id=?", (pid,))
        p["volumes"] = self.db.all(
            "SELECT * FROM volumes WHERE project_id=? ORDER BY number", (pid,)
        )
        p["chapters"] = [
            unpack(r)
            for r in self.db.all(
                "SELECT * FROM chapters WHERE project_id=? ORDER BY volume,number",
                (pid,),
            )
        ]
        p["memory"] = self.db.all(
            "SELECT * FROM memory WHERE project_id=? ORDER BY rowid", (pid,)
        )
        p["facts"] = self.db.all(
            "SELECT * FROM facts WHERE project_id=? AND stale=0 ORDER BY position, rowid",
            (pid,),
        )
        p["runs"] = [
            unpack(r)
            for r in self.db.all(
                "SELECT * FROM runs WHERE project_id=? ORDER BY created DESC LIMIT 30",
                (pid,),
            )
        ]
        p["words"] = sum(len(c["canonical"]) for c in p["chapters"])
        return p

    def chapter(self, cid):
        return unpack(self.db.one("SELECT * FROM chapters WHERE id=?", (cid,)))

    def _story_data(self, connection, pid, before_position=None):
        """Load only canonical, nonstale sources in this same SQLite snapshot."""
        if not connection.execute(
            "SELECT 1 FROM projects WHERE id=?", (pid,)
        ).fetchone():
            raise LookupError("作品不存在")
        boundary, args = ("", [pid])
        if before_position is not None:
            boundary = " AND e.position<?"
            args.append(before_position)
        rows = connection.execute(
            "SELECT e.* FROM story_events e JOIN chapters c ON c.id=e.chapter_id "
            "WHERE e.project_id=? AND c.project_id=e.project_id AND e.stale=0 "
            "AND c.stale=0 AND c.canonical_revision=e.version"
            + boundary
            + " ORDER BY e.position,e.sequence,e.id",
            args,
        ).fetchall()
        manifest = {
            row["id"]: row["canonical_revision"]
            for row in connection.execute(
                "SELECT id,canonical_revision FROM chapters WHERE project_id=? AND canonical_revision>0 AND stale=0",
                (pid,),
            )
        }
        events, labels = [], {}
        for row in rows:
            try:
                event = validate_event(json.loads(row["event"]))
                event_labels = json.loads(row["labels"])
            except (ValueError, TypeError) as exc:
                raise StoryReplayError(
                    row["id"], "invalid_stored_event", "持久化事件必须可严格解析"
                ) from exc
            if (
                event.id != row["id"]
                or event.source.chapter_id != row["chapter_id"]
                or event.source.version != row["version"]
                or event.position != row["position"]
                or event.sequence != row["sequence"]
                or not isinstance(event_labels, dict)
            ):
                raise StoryReplayError(
                    row["id"],
                    "stored_event_binding_mismatch",
                    "事件内容必须与作品内的章节、版本及顺序绑定一致",
                )
            events.append(event)
            labels.update(event_labels)
        return events, manifest, labels

    def story_state(self, pid, before_position=None):
        """Read-only report. Invalid replay is reported as invalid, never as pass."""
        if before_position is not None and (
            type(before_position) is not int or before_position < 0
        ):
            raise ValueError("before 必须为非负的全局章节位置")
        with self.db.connect() as c:
            c.execute("BEGIN")
            project = c.execute(
                "SELECT revision FROM projects WHERE id=?", (pid,)
            ).fetchone()
            if project is None:
                raise LookupError("作品不存在")
            try:
                events, manifest, labels = self._story_data(c, pid, before_position)
                snapshot = replay(
                    events, before_position=before_position, source_versions=manifest
                )
            except StoryReplayError as exc:
                return {
                    "project_id": pid,
                    "project_revision": project["revision"],
                    "before_position": before_position,
                    "status": "invalid",
                    "snapshot": None,
                    "entity_labels": {},
                    "errors": [exc.as_dict()],
                }
        return {
            "project_id": pid,
            "project_revision": project["revision"],
            "before_position": before_position,
            "status": "ready",
            "snapshot": snapshot.model_dump(mode="json"),
            "entity_labels": labels,
            "errors": [],
            "scope": "仅作者明确接受的已发生事件；旧正文不自动推断状态，未知库存不是0。",
            "empty": not snapshot.applied_event_ids,
        }

    def story_context(self, pid, before_position):
        """A historical model view; full source quotes remain queryable via API."""
        report = self.story_state(pid, before_position)
        if report["status"] != "ready":
            error = report["errors"][0]
            raise StoryReplayError(
                error["event_id"],
                error["code"],
                error["precondition"],
                details=error["details"],
            )
        snapshot = report["snapshot"]
        return {
            key: snapshot[key]
            for key in (
                "schema_version",
                "inventories",
                "facts",
                "knowledge",
                "hooks",
                "resource_totals",
            )
        } | {
            "entity_labels": report["entity_labels"],
            "confirmed_event_count": len(snapshot["applied_event_ids"]),
            "scope": "仅本章之前确认的事件；未知不等于0，未记录知情不等于角色绝对不知情；伏笔兑现仅为作者确认。",
        }

    def update_state_proposal(self, cid, request: StateProposalUpdate):
        """Replace only a pending state proposal; never writes confirmed events.

        The manuscript review remains grounded in unchanged text. A new draft
        revision invalidates prior checkbox/index approvals; a freshly loaded
        author approval must explicitly select state changes again.
        """
        ch = self.chapter(cid)
        self.assert_idle(ch["project_id"], permit_approval=True)
        with self.db.transaction() as c:
            ch = unpack(
                dict(c.execute("SELECT * FROM chapters WHERE id=?", (cid,)).fetchone())
            )
            project = c.execute(
                "SELECT revision FROM projects WHERE id=?", (ch["project_id"],)
            ).fetchone()
            if (
                ch["draft_revision"] != request.base_revision
                or project["revision"] != request.project_revision
            ):
                raise Conflict("稿件或作品已变化，请重新读取后提交状态提案")
            if c.execute(
                "SELECT 1 FROM runs WHERE project_id=? AND status IN ('queued','running','interrupted')",
                (ch["project_id"],),
            ).fetchone():
                raise Conflict("作品有进行中的任务，不能覆盖其状态提案")
            if (
                ch["status"] not in ("draft", "review")
                or not ch["review"]
                or not ch["proposal"]
                or ch["reviewed_hash"] != digest(ch["content"])
                or ch["reviewed_project_revision"] != project["revision"]
            ):
                raise Conflict("只可修改已有正文审核和记忆提案的当前草稿，请先完成审阅")
            ground_state_changes(request.state_changes, ch["content"])
            ch["proposal"]["state_changes"] = [
                item.model_dump() for item in request.state_changes
            ]
            ch["proposal"]["accepted_state_changes"] = []
            bound = self._selected_state_changes(
                ch, list(range(len(request.state_changes)))
            )
            pos = ch["volume"] * 100000 + ch["number"]
            events, manifest, _ = self._story_data(c, ch["project_id"], pos)
            manifest[cid] = ch["canonical_revision"] + 1
            errors = []
            try:
                replay(
                    events + [item for _, _, item in bound], source_versions=manifest
                )
            except StoryReplayError as exc:
                errors.append(exc.as_dict())
            c.execute(
                "UPDATE chapters SET proposal=?,draft_revision=draft_revision+1,updated=? WHERE id=?",
                (encode(ch["proposal"]), now(), cid),
            )
            c.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?)",
                (
                    uid(),
                    ch["project_id"],
                    "state.proposal.edited",
                    encode(
                        {
                            "chapter_id": cid,
                            "base_revision": request.base_revision,
                            "draft_revision": request.base_revision + 1,
                            "count": len(bound),
                            "accepted_state_changes": [],
                            "precheck_status": "invalid" if errors else "ready",
                            "errors": errors,
                        }
                    ),
                    now(),
                ),
            )
        result = self.chapter(cid)
        result["state_preview"] = self.state_preview(cid)
        return result

    def _bind_state_change(self, ch, index, raw):
        version = ch["canonical_revision"] + 1
        event_id = (
            "state-"
            + uuid5(
                NAMESPACE_URL, f"{ch['project_id']}/{ch['id']}/{version}/{index}"
            ).hex
        )
        try:
            change = STATE_CHANGE_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            raise StoryReplayError(
                event_id,
                "invalid_state_proposal",
                "状态提案必须符合严格字段、整数数量与支持的operation；模型不能自带来源或确认权限",
                details={
                    "change_index": index,
                    "validation": exc.errors(
                        include_input=False, include_context=False
                    ),
                },
            ) from exc
        paragraphs = numbered_paragraphs(ch["content"])
        if (
            change.paragraph > len(paragraphs)
            or not change.quote
            or change.quote not in paragraphs[change.paragraph - 1]["text"]
        ):
            raise StoryReplayError(
                event_id,
                "invalid_source_quote",
                "提案必须含指定本章段落中的逐字引文，不能引用计划或其他章节",
                details={"change_index": index, "paragraph": change.paragraph},
            )
        payload = change.model_dump(exclude={"paragraph", "quote", "labels"})
        event = validate_event(
            payload
            | {
                "id": event_id,
                "position": ch["volume"] * 100000 + ch["number"],
                "sequence": index + 1,
                "status": "confirmed",
                "source": {
                    "chapter_id": ch["id"],
                    "version": version,
                    "quote": change.quote,
                },
            }
        )
        return change, event

    def _selected_state_changes(self, ch, selected):
        proposals = ch["proposal"].get("state_changes", [])
        if not isinstance(proposals, list) or len(proposals) > 12:
            raise ValueError("状态提案必须为不超过12项的列表")
        if len(set(selected)) != len(selected) or any(
            type(i) is not int or i < 0 or i >= len(proposals) for i in selected
        ):
            raise ValueError("状态提案选择有重复、越界或类型错误，请重新加载")
        bound = [
            (index, *self._bind_state_change(ch, index, proposals[index]))
            for index in sorted(selected)
        ]
        paragraphs = [change.paragraph for _, change, _ in bound]
        if paragraphs != sorted(paragraphs):
            raise StoryReplayError(
                bound[0][2].id,
                "state_order_not_source_order",
                "提案必须按正文发生顺序排列，同一段内按提案顺序执行",
            )
        return bound

    @staticmethod
    def _state_change_summary(change):
        values = change.model_dump()
        labels = change.labels

        def label(key):
            return labels.get(values.get(key), values.get(key, ""))

        if change.operation == "resource.establish":
            return f"明确初始库存：{label('holder_id')}持有{values['quantity']}单位{label('resource_id')}"
        if change.operation == "resource.transfer":
            return f"转移：{label('from_holder_id')} → {label('to_holder_id')}，{values['quantity']}单位{label('resource_id')}"
        if change.operation == "resource.consume":
            return f"消耗：{label('holder_id')}使用{values['quantity']}单位{label('resource_id')}"
        if change.operation == "knowledge.learn":
            return f"获知：{label('actor_id')}得知「{values['fact']}」"
        if change.operation == "hook.plant":
            return f"埋设伏笔：{values['description']}"
        return f"作者确认兑现：{label('hook_id')}；{values['explanation']}"

    def state_preview(self, cid, selected=None):
        """Simulate selection without persisting events or approving the chapter."""
        with self.db.connect() as c:
            c.execute("BEGIN")
            row = c.execute("SELECT * FROM chapters WHERE id=?", (cid,)).fetchone()
            if row is None:
                raise LookupError("章节不存在")
            ch = unpack(dict(row))
            project = c.execute(
                "SELECT revision FROM projects WHERE id=?", (ch["project_id"],)
            ).fetchone()
            proposals = ch["proposal"].get("state_changes", [])
            if not isinstance(proposals, list) or len(proposals) > 12:
                raise ValueError("状态提案必须为不超过12项的列表")
            selected = list(range(len(proposals))) if selected is None else selected
            if len(set(selected)) != len(selected) or any(
                type(i) is not int or i < 0 or i >= len(proposals) for i in selected
            ):
                raise ValueError("状态提案选择有重复、越界或类型错误")
            pos = ch["volume"] * 100000 + ch["number"]
            base_events, manifest, labels = self._story_data(c, ch["project_id"], pos)
            manifest[ch["id"]] = ch["canonical_revision"] + 1
            changes, errors, before, after = [], [], None, None
            for index, raw in enumerate(proposals):
                item = {"index": index, "selected": index in selected, "change": raw}
                try:
                    change, event = self._bind_state_change(ch, index, raw)
                    item.update(
                        schema_valid=True,
                        event_id=event.id,
                        summary=self._state_change_summary(change),
                        error=None,
                    )
                except StoryReplayError as exc:
                    item.update(
                        schema_valid=False,
                        event_id=exc.event_id,
                        summary="无效状态提案",
                        error=exc.as_dict(),
                    )
                changes.append(item)
            try:
                before = replay(
                    base_events, before_position=pos, source_versions=manifest
                ).model_dump(mode="json")
                bound = self._selected_state_changes(ch, selected)
                after = replay(
                    base_events + [event for _, _, event in bound],
                    source_versions=manifest,
                ).model_dump(mode="json")
                for _, change, _ in bound:
                    labels.update(change.labels)
            except StoryReplayError as exc:
                errors.append(exc.as_dict())
                for item in changes:
                    if item.get("event_id") == exc.event_id:
                        item["error"] = exc.as_dict()
        return {
            "chapter_id": cid,
            "project_id": ch["project_id"],
            "draft_revision": ch["draft_revision"],
            "project_revision": project["revision"],
            "proposed_version": ch["canonical_revision"] + 1,
            "selected": selected,
            "status": "invalid" if errors else "ready",
            "changes": changes,
            "errors": errors,
            "before_snapshot": before,
            "snapshot": after,
            "entity_labels": labels,
            "author_confirmation_required": True,
            "imported_provenance": self.imported_state_changes(cid, pending_only=False),
            "scope": "仅预演当前选择，不保存；同段按提案下标顺序。未勾选事件不会进入正式状态。",
        }

    def imported_state_changes(self, cid, *, pending_only=True):
        self.chapter(cid)
        rows = self.db.all(
            "SELECT * FROM imported_story_events WHERE chapter_id=?"
            + (" AND status='pending'" if pending_only else "")
            + " ORDER BY rowid",
            (cid,),
        )
        return [
            {
                "id": row["id"],
                "source_project_id": row["source_project_id"],
                "original_event": json.loads(row["source_event"]),
                "change": json.loads(row["proposal"]),
                "status": row["status"],
                "resolved_version": row["resolved_version"],
            }
            for row in rows
        ]

    def assert_idle(self, pid, *, permit_approval=False):
        states = (
            ("queued", "running", "interrupted")
            if permit_approval
            else ("queued", "running", "awaiting_approval", "interrupted")
        )
        q = ",".join("?" for _ in states)
        if self.db.one(
            f"SELECT id FROM runs WHERE project_id=? AND status IN ({q})",
            (pid, *states),
            required=False,
        ):
            raise Conflict("作品有进行中的任务，请先完成、取消或恢复该任务")

    def save_draft(self, cid, request: DraftSave):
        ch = self.chapter(cid)
        self.assert_idle(ch["project_id"], permit_approval=True)
        with self.db.transaction() as c:
            actual = c.execute(
                "SELECT draft_revision FROM chapters WHERE id=?", (cid,)
            ).fetchone()[0]
            if actual != request.base_revision:
                raise Conflict("稿件已变化，请重新加载最新版本")
            c.execute(
                "UPDATE chapters SET content=?,title=?,draft_revision=draft_revision+1,review='{}',proposal='{}',reviewed_hash='',status='draft',updated=? WHERE id=?",
                (request.content, request.title, now(), cid),
            )
            # A resumed approval must never commit a proposal for a different draft.
            c.execute(
                "UPDATE runs SET status='cancelled',updated=? WHERE chapter_id=? AND status='awaiting_approval'",
                (now(), cid),
            )
        return self.chapter(cid)

    def store_generated(self, cid, content, **fields):
        allowed = {
            "plan",
            "review",
            "proposal",
            "context",
            "status",
            "reviewed_hash",
            "reviewed_project_revision",
        }
        if not set(fields) <= allowed:
            raise ValueError("非法字段")
        with self.db.transaction() as c:
            assignments = ["content=?", "draft_revision=draft_revision+1", "updated=?"]
            values = [content, now()]
            for k, v in fields.items():
                assignments.append(f"{k}=?")
                values.append(encode(v) if isinstance(v, (dict, list)) else v)
            c.execute(
                f"UPDATE chapters SET {','.join(assignments)} WHERE id=?",
                (*values, cid),
            )

    def commit_chapter(self, cid, request: Approve, run_id=None):
        if run_id and self.db.one(
            "SELECT run_id FROM run_commits WHERE run_id=?", (run_id,), required=False
        ):
            return self.chapter(cid)
        ch = self.chapter(cid)
        pid = ch["project_id"]
        if not ch["content"].strip():
            raise ValueError("空章节不能定稿")
        if (
            ch["reviewed_hash"] != digest(ch["content"])
            or not ch["review"]
            or not ch["proposal"]
        ):
            raise Conflict("当前稿件尚未完成审核与记忆提案，请先重新审核")
        if (
            not ch["review"].get("passed")
            or not ch["review"].get("length_check", {}).get("within_range", True)
        ) and not request.note.strip():
            raise Conflict("审核或篇幅目标尚未达标；请修订，或填写继续定稿的原因")
        patches = ch["proposal"].get("patches", [])
        selected = request.accepted_patches
        if len(selected) != len(set(selected)) or any(
            sum(patch["name"] == name for patch in patches) != 1 for name in selected
        ):
            raise Conflict("选择的记忆文档有重复提案，请拒绝重复项或重新核对")
        available = {x["name"]: x for x in patches}
        if not set(request.accepted_patches) <= set(available) or not set(
            request.overrides
        ) <= set(request.accepted_patches):
            raise ValueError("记忆选择与当前提案不一致")
        summary = (
            request.summary_override
            if request.summary_override is not None
            else ch["proposal"]["chapter_summary"]
        )
        resolved_overrides = dict(request.overrides)
        if (
            request.summary_override is not None
            and "目前剧情" in request.accepted_patches
        ):
            content = resolved_overrides.get(
                "目前剧情", available["目前剧情"]["content"]
            )
            marker = "## " + ch["title"]
            prefix = (
                content.rsplit(marker, 1)[0] if marker in content else content + "\n\n"
            )
            resolved_overrides["目前剧情"] = prefix + marker + "\n" + summary
        pos = ch["volume"] * 100000 + ch["number"]
        with self.db.transaction() as c:
            current = dict(
                c.execute("SELECT * FROM chapters WHERE id=?", (cid,)).fetchone()
            )
            project = dict(
                c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            )
            if (
                current["draft_revision"] != request.base_revision
                or project["revision"] != request.project_revision
                or ch["reviewed_project_revision"] != project["revision"]
            ):
                raise Conflict("正文或故事记忆已变化，请重新审核后确认")
            if (
                ch["canonical"] == ch["content"]
                and ch["canonical_revision"]
                and not ch["stale"]
            ):
                raise Conflict("此版本已经定稿")
            version = ch["canonical_revision"] + 1
            later = c.execute(
                "SELECT 1 FROM chapters WHERE project_id=? AND canonical_revision>0 AND (volume>? OR (volume=? AND number>?)) LIMIT 1",
                (pid, ch["volume"], ch["volume"], ch["number"]),
            ).fetchone()
            if ch["canonical_revision"] or later:
                for name, content in ch["context"].get("memory", {}).items():
                    c.execute(
                        "UPDATE memory SET content=?,revision=revision+1 WHERE project_id=? AND name=?",
                        (content, pid, name),
                    )
            for name in request.accepted_patches:
                content = resolved_overrides.get(name, available[name]["content"])
                c.execute(
                    "UPDATE memory SET content=?,revision=revision+1 WHERE project_id=? AND name=?",
                    (content, pid, name),
                )
            c.execute(
                "UPDATE facts SET stale=1 WHERE project_id=? AND position>=?",
                (pid, pos),
            )
            c.execute(
                "UPDATE chunks SET stale=1 WHERE project_id=? AND position>=?",
                (pid, pos),
            )
            c.execute(
                "UPDATE story_events SET stale=1 WHERE project_id=? AND position>=?",
                (pid, pos),
            )
            c.execute(
                "UPDATE chapters SET stale=1,reviewed_hash='' WHERE project_id=? AND (volume>? OR (volume=? AND number>?)) AND canonical_revision>0",
                (pid, ch["volume"], ch["volume"], ch["number"]),
            )
            all_facts = ch["proposal"].get("facts", [])
            chosen = (
                request.accepted_facts
                if request.accepted_facts is not None
                else list(range(len(all_facts)))
            )
            if len(set(chosen)) != len(chosen) or any(
                i < 0 or i >= len(all_facts) for i in chosen
            ):
                raise ValueError("事实选择已过期")
            for i in chosen:
                fact = all_facts[i]
                if not fact.get("quote") or fact["quote"] not in ch["content"]:
                    raise ValueError("事实缺少可核验的正文来源")
                c.execute(
                    "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        uid(),
                        pid,
                        cid,
                        version,
                        fact["subject"],
                        fact["predicate"],
                        fact["value"],
                        fact["kind"],
                        fact["status"],
                        fact["quote"],
                        encode(fact.get("related", [])),
                        pos,
                    ),
                )
            approved_proposal = ch["proposal"] | {
                "accepted_facts": chosen,
                "accepted_state_changes": request.accepted_state_changes,
                "accepted_patches": request.accepted_patches,
                "chapter_summary": summary,
            }
            # Validate and bind only explicitly checked proposals. This is inside
            # the same transaction as memory/facts/body: any source/precondition
            # failure rolls every preceding mutation back, including stale flags.
            bound_changes = self._selected_state_changes(
                ch, request.accepted_state_changes
            )
            base_events, manifest, _ = self._story_data(c, pid, pos)
            manifest[cid] = version
            replay(
                base_events + [event for _, _, event in bound_changes],
                source_versions=manifest,
            )
            for _, change, event in bound_changes:
                c.execute(
                    "INSERT INTO story_events(id,project_id,chapter_id,version,position,sequence,event,labels,stale,created) VALUES(?,?,?,?,?,?,?,?,0,?)",
                    (
                        event.id,
                        pid,
                        cid,
                        version,
                        pos,
                        event.sequence,
                        event.model_dump_json(),
                        encode(change.labels),
                        now(),
                    ),
                )
            selected_imports = {
                encode(change.model_dump(exclude={"labels"}))
                for _, change, _ in bound_changes
            }
            for imported in c.execute(
                "SELECT id,proposal FROM imported_story_events WHERE project_id=? AND chapter_id=? AND status='pending'",
                (pid, cid),
            ).fetchall():
                original = STATE_CHANGE_ADAPTER.validate_python(
                    json.loads(imported["proposal"])
                )
                accepted = (
                    encode(original.model_dump(exclude={"labels"})) in selected_imports
                )
                c.execute(
                    "UPDATE imported_story_events SET status=?,resolved_version=? WHERE id=?",
                    ("accepted" if accepted else "rejected", version, imported["id"]),
                )
            approved_proposal["patches"] = [
                x | {"content": resolved_overrides.get(x["name"], x["content"])}
                for x in patches
            ]
            c.execute(
                "UPDATE chapters SET canonical=content,canonical_revision=?,status='final',stale=0,summary=?,proposal=?,updated=? WHERE id=?",
                (version, summary, encode(approved_proposal), now(), cid),
            )
            c.execute(
                "UPDATE projects SET revision=revision+1,updated=? WHERE id=?",
                (now(), pid),
            )
            snapshot = {
                r["name"]: r["content"]
                for r in c.execute("SELECT * FROM memory WHERE project_id=?", (pid,))
            }
            facts = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM facts WHERE project_id=? AND stale=0", (pid,)
                )
            ]
            c.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    cid,
                    version,
                    ch["content"],
                    ch["title"],
                    encode(snapshot),
                    encode(facts),
                    summary,
                    encode(ch["plan"]),
                    request.note,
                    now(),
                ),
            )
            next_ch = c.execute(
                "SELECT id,plan FROM chapters WHERE project_id=? AND (volume>? OR (volume=? AND number>?)) ORDER BY volume,number LIMIT 1",
                (pid, ch["volume"], ch["volume"], ch["number"]),
            ).fetchone()
            if (
                next_ch
                and "当前章节设计规划" in request.accepted_patches
                and ch["proposal"].get("next_plan")
            ):
                old = json.loads(next_ch["plan"])
                proposed = dict(ch["proposal"]["next_plan"])
                authored = False
                if "当前章节设计规划" in request.overrides:
                    raw = request.overrides["当前章节设计规划"]
                    try:
                        proposed = ChapterPlan.model_validate(
                            json.loads(raw[raw.index("{") :])
                        ).model_dump()
                        authored = True
                    except (ValueError, TypeError):
                        proposed = old
                if old.get("goal") and not authored:
                    proposed.update(
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
                        proposed[k] = list(
                            dict.fromkeys(old.get(k, []) + proposed.get(k, []))
                        )
                c.execute(
                    "UPDATE chapters SET plan=?,updated=? WHERE id=? AND canonical_revision=0",
                    (encode(proposed), now(), next_ch["id"]),
                )
            c.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?)",
                (
                    uid(),
                    pid,
                    "chapter.approved",
                    encode(
                        {
                            "chapter_id": cid,
                            "version": version,
                            "memory": request.accepted_patches,
                            "state_changes": request.accepted_state_changes,
                            "note": request.note,
                        }
                    ),
                    now(),
                ),
            )
            if run_id:
                c.execute(
                    "INSERT INTO run_commits VALUES(?,?,?,?,?)",
                    (run_id, pid, cid, version, now()),
                )
        return self.chapter(cid)

    def apply_blueprint(self, pid, blueprint, base_revision, run_id=None):
        with self.db.transaction() as c:
            if (
                run_id
                and c.execute(
                    "SELECT 1 FROM run_commits WHERE run_id=?", (run_id,)
                ).fetchone()
            ):
                return
            p = dict(c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
            if p["revision"] != base_revision:
                raise Conflict("作品已变化，请重新规划")
            if c.execute(
                "SELECT 1 FROM chapters WHERE project_id=? AND canonical_revision>0",
                (pid,),
            ).fetchone():
                raise Conflict("已有定稿，请使用分卷规划或手动调整，避免覆盖既有设定")
            for name in MEMORY_NAMES:
                c.execute(
                    "UPDATE memory SET content=?,revision=revision+1 WHERE project_id=? AND name=?",
                    (blueprint["memory"][name], pid, name),
                )
            c.execute(
                "INSERT OR REPLACE INTO initial_memory VALUES(?,?)",
                (pid, encode(blueprint["memory"])),
            )
            c.execute(
                "UPDATE projects SET outline=?,logline=?,revision=revision+1,updated=? WHERE id=?",
                (blueprint["outline"], blueprint["logline"], now(), pid),
            )
            for i, plan in enumerate(blueprint["chapters"], 1):
                updated = c.execute(
                    "UPDATE chapters SET title=?,plan=?,updated=? WHERE project_id=? AND volume=1 AND number=?",
                    (plan["title"], encode(plan), now(), pid, i),
                )
                if not updated.rowcount:
                    c.execute(
                        "INSERT INTO chapters(id,project_id,volume,number,title,plan,updated) VALUES(?,?,?,?,?,?,?)",
                        (uid(), pid, 1, i, plan["title"], encode(plan), now()),
                    )
            if run_id:
                c.execute(
                    "INSERT INTO run_commits VALUES(?,?,?,?,?)",
                    (run_id, pid, None, None, now()),
                )
        self.db.audit(pid, "blueprint.approved", {"base_revision": base_revision})

    def versions(self, cid):
        self.chapter(cid)
        return self.db.all(
            "SELECT id,version,title,content,note,created FROM versions WHERE chapter_id=? ORDER BY version DESC",
            (cid,),
        )

    def restore(self, cid, version, base_revision):
        old = self.db.one(
            "SELECT * FROM versions WHERE chapter_id=? AND version=?", (cid, version)
        )
        current = self.chapter(cid)
        if current["draft_revision"] != base_revision:
            raise Conflict("稿件已变化，请重新载入")
        if current["content"] == old["content"] and current["title"] == old["title"]:
            return current
        return self.save_draft(
            cid,
            DraftSave(
                content=old["content"], title=old["title"], base_revision=base_revision
            ),
        )

    def update_memory(self, pid, name, content, revision):
        if name not in MEMORY_NAMES:
            raise ValueError("未知记忆")
        self.assert_idle(pid)
        with self.db.transaction() as c:
            p = c.execute("SELECT revision FROM projects WHERE id=?", (pid,)).fetchone()
            if not p:
                raise LookupError("作品不存在")
            if p[0] != revision:
                raise Conflict("作品已更新")
            c.execute(
                "UPDATE memory SET content=?,revision=revision+1 WHERE project_id=? AND name=?",
                (content, pid, name),
            )
            c.execute(
                "UPDATE projects SET revision=revision+1,updated=? WHERE id=?",
                (now(), pid),
            )
            c.execute("UPDATE chapters SET reviewed_hash='' WHERE project_id=?", (pid,))
            if not c.execute(
                "SELECT 1 FROM chapters WHERE project_id=? AND canonical_revision>0 AND stale=0 LIMIT 1",
                (pid,),
            ).fetchone():
                memory = {
                    r["name"]: r["content"]
                    for r in c.execute(
                        "SELECT * FROM memory WHERE project_id=?", (pid,)
                    )
                }
                c.execute(
                    "INSERT OR REPLACE INTO initial_memory VALUES(?,?)",
                    (pid, encode(memory)),
                )
        self.db.audit(pid, "memory.edited", {"name": name})

    def export(self, pid, fmt="md"):
        p = self.project(pid)
        chapters = [ch for ch in p["chapters"] if ch["canonical_revision"]]
        parts = [f"# {p['title']}"]
        last_volume = None
        for ch in chapters:
            if ch["volume"] != last_volume:
                volume = next(
                    (v for v in p["volumes"] if v["number"] == ch["volume"]), {}
                )
                parts.append("## " + (volume.get("title") or f"第{ch['volume']}卷"))
                last_volume = ch["volume"]
            title = (
                ch["title"]
                if re.match(r"第[一二三四五六七八九十百千万零〇两0-9]+章", ch["title"])
                else f"第{ch['number']}章 {ch['title']}"
            )
            parts.append(f"### {title}\n\n{ch['canonical']}")
        text = "\n\n".join(parts)
        if fmt == "txt":
            return text.encode(), "text/plain; charset=utf-8", "txt"
        if fmt == "md":
            return text.encode(), "text/markdown; charset=utf-8", "md"
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            meta = {
                k: p[k]
                for k in (
                    "title",
                    "genre",
                    "premise",
                    "style",
                    "target_chapters",
                    "chapter_words",
                    "outline",
                    "logline",
                )
            }
            meta["chapters"] = [
                {k: ch[k] for k in ("volume", "number", "title", "plan", "summary")}
                for ch in chapters
            ]
            meta["volumes"] = p["volumes"]
            z.writestr("project.json", encode(meta))
            z.writestr(
                "story-events.json", encode(self._story_export_bundle(p, chapters))
            )
            for m in p["memory"]:
                prefix = "" if m["name"] in MEMORY_NAMES[:2] else "剧情/"
                z.writestr(f"workspace/{prefix}{m['name']}.md", m["content"])
            for ch in chapters:
                z.writestr(
                    f"小说本体/第{ch['volume']}卷/第{ch['number']}章.md",
                    ch["canonical"],
                )
            z.writestr(
                "README.txt",
                "此备份包含已定稿正文、记忆和当前有效的故事事件，不包含密钥。story-events.json保留源章节/版本/正文哈希；导入为新作品后事件仅为待确认提案，须重新审阅并由作者逐项接受。pending_imports也只保留待确认来源，不作为正式状态。完整历史版本与已失效事件请保留本地数据库备份。",
            )
        return out.getvalue(), "application/zip", "zip"

    def _story_export_bundle(self, project, chapters):
        by_id = {chapter["id"]: chapter for chapter in chapters}
        with self.db.connect() as c:
            c.execute("BEGIN")
            events, manifest, _ = self._story_data(c, project["id"])
            replay(events, source_versions=manifest)
            labels = {
                row["id"]: json.loads(row["labels"])
                for row in c.execute(
                    "SELECT id,labels FROM story_events WHERE project_id=? AND stale=0",
                    (project["id"],),
                )
            }
            pending = c.execute(
                "SELECT * FROM imported_story_events WHERE project_id=? AND status='pending' ORDER BY rowid",
                (project["id"],),
            ).fetchall()
        for event in events:
            chapter = by_id.get(event.source.chapter_id)
            if (
                chapter is None
                or event.source.version != chapter["canonical_revision"]
                or event.source.quote not in chapter["canonical"]
            ):
                raise ValueError("故事事件与导出正文版本不一致，请重新导出")
        return {
            "format": "changjuan-story-events-v1",
            "source_project_id": project["id"],
            "scope": "active_author_confirmed_events; imported events require confirmation again; historical invalidated events remain in database backup",
            "chapters": [
                {
                    "id": ch["id"],
                    "volume": ch["volume"],
                    "number": ch["number"],
                    "version": ch["canonical_revision"],
                    "stale": bool(ch["stale"]),
                    "body_sha256": digest(ch["canonical"]),
                }
                for ch in chapters
            ],
            "events": [
                {"event": event.model_dump(mode="json"), "labels": labels[event.id]}
                for event in events
            ],
            "pending_imports": [
                {
                    "target_chapter_id": row["chapter_id"],
                    "source_project_id": row["source_project_id"],
                    "source_body_sha256": digest(by_id[row["chapter_id"]]["canonical"]),
                    "original_event": json.loads(row["source_event"]),
                    "change": json.loads(row["proposal"]),
                }
                for row in pending
                if row["chapter_id"] in by_id
            ],
        }

    @staticmethod
    def _read_story_import_bundle(archive):
        """Validate provenance, then return UNCONFIRMED proposals for new IDs."""
        if "story-events.json" not in archive.namelist():
            return []
        data = json.loads(archive.read("story-events.json"))
        if (
            not isinstance(data, dict)
            or data.get("format") != "changjuan-story-events-v1"
        ):
            raise ValueError("不支持的story-events.json格式")
        if (
            not isinstance(data.get("source_project_id"), str)
            or not 1 <= len(data["source_project_id"]) <= 128
        ):
            raise ValueError("事件备份缺少有效来源作品ID")
        if any(
            not isinstance(data.get(key, []), list)
            for key in ("chapters", "events", "pending_imports")
        ):
            raise ValueError("故事事件备份目录必须为列表")
        if len(data.get("events", [])) + len(data.get("pending_imports", [])) > 36000:
            raise ValueError("故事事件数量超限")
        declarations, declared_locations = {}, set()
        for item in data.get("chapters", []):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or item["id"] in declarations
            ):
                raise ValueError("事件来源章节目录无效或重复")
            if (
                any(
                    type(item.get(key)) is not int
                    for key in ("volume", "number", "version")
                )
                or not 1 <= item["volume"] <= 9999
                or not 1 <= item["number"] < 100000
                or item["version"] <= 0
            ):
                raise ValueError("事件来源卷章或版本无效")
            location = (item["volume"], item["number"])
            if location in declared_locations:
                raise ValueError("事件来源目录卷章位置重复")
            declared_locations.add(location)
            filename = f"小说本体/第{item['volume']}卷/第{item['number']}章.md"
            if filename not in archive.namelist():
                raise ValueError("事件来源章节缺少对应正文")
            body = archive.read(filename).decode()
            if item.get("body_sha256") != digest(body):
                raise ValueError("事件来源正文哈希与备份不符")
            declarations[item["id"]] = item | {"body": body}
        results, formal, event_ids = [], [], set()
        per_chapter = {}

        def add_pending(chapter, source_project_id, original, change):
            if (
                not isinstance(source_project_id, str)
                or not 1 <= len(source_project_id) <= 128
            ):
                raise ValueError("待确认事件缺少来源作品ID")
            per_chapter[chapter["id"]] = per_chapter.get(chapter["id"], 0) + 1
            if per_chapter[chapter["id"]] > 12:
                raise ValueError("单章导入状态提案不能超过12项")
            validated = STATE_CHANGE_ADAPTER.validate_python(change)
            if original.position != chapter["volume"] * 100000 + chapter["number"]:
                raise ValueError("导入事件的原章节位置与目标章节映射不符")
            paragraphs = numbered_paragraphs(chapter["body"])
            if (
                validated.paragraph > len(paragraphs)
                or not validated.quote
                or validated.quote not in paragraphs[validated.paragraph - 1]["text"]
            ):
                raise ValueError("导入状态提案的引文与目标章节段落不符")
            expected = original.model_dump(
                exclude={"id", "position", "sequence", "source", "status"}
            )
            if (
                validated.model_dump(exclude={"paragraph", "quote", "labels"})
                != expected
                or validated.quote != original.source.quote
            ):
                raise ValueError("待确认提案与保存的原事件来源不符")
            results.append(
                {
                    "volume": chapter["volume"],
                    "number": chapter["number"],
                    "source_project_id": source_project_id,
                    "source_body_sha256": digest(chapter["body"]),
                    "original_event": original.model_dump(mode="json"),
                    "change": validated.model_dump(),
                }
            )

        for row in data.get("events", []):
            if not isinstance(row, dict):
                raise ValueError("故事事件条目无效")
            event = validate_event(row.get("event", {}))
            chapter = declarations.get(event.source.chapter_id)
            if (
                chapter is None
                or chapter.get("stale")
                or chapter["version"] != event.source.version
                or event.position != chapter["volume"] * 100000 + chapter["number"]
            ):
                raise ValueError("故事事件的源章节/版本/位置与正文目录不符")
            if event.id in event_ids or event.sequence > 12:
                raise ValueError("故事事件ID重复或章内序号超限")
            event_ids.add(event.id)
            paragraphs = numbered_paragraphs(chapter["body"])
            matches = [
                item["paragraph"]
                for item in paragraphs
                if event.source.quote in item["text"]
            ]
            if not matches:
                raise ValueError("故事事件引用不在来源正文中")
            change = event.model_dump(
                exclude={"id", "position", "sequence", "source", "status"}
            ) | {
                "paragraph": matches[0],
                "quote": event.source.quote,
                "labels": row.get("labels", {}),
            }
            add_pending(chapter, data["source_project_id"], event, change)
            formal.append(event)
        replay(
            formal,
            source_versions={
                key: value["version"] for key, value in declarations.items()
            },
        )
        for row in data.get("pending_imports", []):
            if (
                not isinstance(row, dict)
                or row.get("target_chapter_id") not in declarations
            ):
                raise ValueError("待确认导入事件缺少目标章节映射")
            original = validate_event(row.get("original_event", {}))
            if row.get("source_body_sha256") != digest(
                declarations[row["target_chapter_id"]]["body"]
            ):
                raise ValueError("待确认来源正文哈希与目标备份不符")
            add_pending(
                declarations[row["target_chapter_id"]],
                row.get("source_project_id"),
                original,
                row.get("change", {}),
            )
        return results

    def mirror(self, pid):
        p = self.project(pid)
        root = self.settings.within(self.settings.data / "projects" / pid)
        for m in p["memory"]:
            prefix = (
                Path("workspace")
                if m["name"] in MEMORY_NAMES[:2]
                else Path("workspace/剧情")
            )
            target = self.settings.within(root / prefix / f"{m['name']}.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".tmp")
            temp.write_text(m["content"])
            temp.replace(target)
        for ch in p["chapters"]:
            if not ch["canonical_revision"]:
                continue
            target = self.settings.within(
                root / "小说本体" / f"第{ch['volume']}卷" / f"第{ch['number']}章.md"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".tmp")
            temp.write_text(ch["canonical"])
            temp.replace(target)

    def import_zip(self, raw: bytes):
        if len(raw) > 20_000_000:
            raise ValueError("文件不能超过 20 MB")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if sum(i.file_size for i in infos) > 50_000_000 or len(infos) > 6000:
                raise ValueError("备份解压大小超限")
            for i in infos:
                path = Path(i.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in i.filename:
                    raise ValueError("备份中含不安全路径")
            meta = (
                json.loads(z.read("project.json"))
                if "project.json" in z.namelist()
                else {
                    "title": "导入的作品",
                    "premise": "从既有 Markdown 目录导入，请补充故事方向。",
                }
            )
            if not isinstance(meta, dict):
                raise ValueError("project.json 必须是有效作品对象")
            if not isinstance(meta.get("chapters", []), list) or not isinstance(
                meta.get("volumes", []), list
            ):
                raise ValueError("备份中的目录格式无效")
            req = ProjectCreate.model_validate(
                {k: v for k, v in meta.items() if k in ProjectCreate.model_fields}
            )
            imported_states = self._read_story_import_bundle(z)
            pid = self.create(req)["id"]
            try:
                with self.db.transaction() as c:
                    c.execute("DELETE FROM chapters WHERE project_id=?", (pid,))
                    for name in MEMORY_NAMES:
                        match = next(
                            (
                                i.filename
                                for i in infos
                                if i.filename.endswith(f"/{name}.md")
                            ),
                            None,
                        )
                        if match:
                            c.execute(
                                "UPDATE memory SET content=?,revision=1 WHERE project_id=? AND name=?",
                                (z.read(match).decode(), pid, name),
                            )
                    memory = {
                        r["name"]: r["content"]
                        for r in c.execute(
                            "SELECT * FROM memory WHERE project_id=?", (pid,)
                        )
                    }
                    c.execute(
                        "INSERT OR REPLACE INTO initial_memory VALUES(?,?)",
                        (pid, encode(memory)),
                    )
                    for v in meta.get("volumes", []):
                        if (
                            isinstance(v, dict)
                            and isinstance(v.get("number"), int)
                            and v["number"] > 0
                        ):
                            c.execute(
                                "INSERT OR REPLACE INTO volumes VALUES(?,?,?,?)",
                                (
                                    pid,
                                    v["number"],
                                    str(v.get("title", ""))[:150],
                                    str(v.get("outline", ""))[:100000],
                                ),
                            )
                    chapter_mapping = {}
                    for i in infos:
                        m = re.search(
                            r"小说本体/第(\d+)卷/第(\d+)章(?:[^/]*)\.md$", i.filename
                        )
                        if not m:
                            continue
                        vol, num = map(int, m.groups())
                        if not 1 <= vol <= 9999 or not 1 <= num < 100000:
                            raise ValueError("卷号或章号超出范围")
                        body = z.read(i).decode()
                        info = next(
                            (
                                x
                                for x in meta.get("chapters", [])
                                if isinstance(x, dict)
                                and x.get("volume") == vol
                                and x.get("number") == num
                            ),
                            {},
                        )
                        cid = uid()
                        chapter_mapping[(vol, num)] = cid
                        title = str(info.get("title", f"第{num}章"))[:150]
                        raw_plan = info.get("plan", {})
                        plan = (
                            ChapterPlan.model_validate(raw_plan).model_dump()
                            if isinstance(raw_plan, dict) and raw_plan.get("goal")
                            else {
                                "title": title,
                                "goal": "",
                                "target_words": req.chapter_words,
                            }
                        )
                        c.execute(
                            "INSERT OR IGNORE INTO volumes VALUES(?,?,?,?)",
                            (pid, vol, f"第{vol}卷", ""),
                        )
                        c.execute(
                            "INSERT INTO chapters(id,project_id,volume,number,title,content,canonical,canonical_revision,draft_revision,status,stale,updated,plan) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                cid,
                                pid,
                                vol,
                                num,
                                title,
                                body,
                                body,
                                1,
                                1,
                                "final",
                                1,
                                now(),
                                encode(plan),
                            ),
                        )
                        c.execute(
                            "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                uid(),
                                cid,
                                1,
                                body,
                                title,
                                encode(memory),
                                "[]",
                                str(info.get("summary", ""))[:3000],
                                encode(plan),
                                "导入原稿，尚待复核",
                                now(),
                            ),
                        )
                    pending_by_chapter = {}
                    provenance_mapping = []
                    for imported in imported_states:
                        cid = chapter_mapping.get(
                            (imported["volume"], imported["number"])
                        )
                        if cid is None:
                            raise ValueError("导入事件缺少安全的目标章节映射")
                        c.execute(
                            "INSERT INTO imported_story_events(id,project_id,chapter_id,source_project_id,source_event,proposal,created) VALUES(?,?,?,?,?,?,?)",
                            (
                                uid(),
                                pid,
                                cid,
                                imported["source_project_id"],
                                encode(imported["original_event"]),
                                encode(imported["change"]),
                                now(),
                            ),
                        )
                        pending_by_chapter.setdefault(cid, []).append(
                            imported["change"]
                        )
                        provenance_mapping.append(
                            {
                                "source_project_id": imported["source_project_id"],
                                "source_chapter_id": imported["original_event"][
                                    "source"
                                ]["chapter_id"],
                                "source_version": imported["original_event"]["source"][
                                    "version"
                                ],
                                "source_body_sha256": imported["source_body_sha256"],
                                "target_chapter_id": cid,
                                "original_event_id": imported["original_event"]["id"],
                            }
                        )
                    for cid, changes in pending_by_chapter.items():
                        c.execute(
                            "UPDATE chapters SET proposal=? WHERE id=?",
                            (
                                encode(
                                    {
                                        "state_changes": changes,
                                        "imported_state_notice": "来自备份的未确认提案；重新审阅后仍需作者逐项选择，不能直接作为正式状态。",
                                    }
                                ),
                                cid,
                            ),
                        )
                    if imported_states:
                        c.execute(
                            "INSERT INTO audit VALUES(?,?,?,?,?)",
                            (
                                uid(),
                                pid,
                                "state.events.imported",
                                encode(
                                    {
                                        "status": "pending_author_confirmation",
                                        "count": len(imported_states),
                                        "mapping": provenance_mapping,
                                    }
                                ),
                                now(),
                            ),
                        )
                    c.execute(
                        "UPDATE projects SET revision=1,outline=?,logline=? WHERE id=?",
                        (meta.get("outline", ""), meta.get("logline", ""), pid),
                    )
                self.db.audit(pid, "project.imported", {"chapters_need_review": True})
            except BaseException:
                self.db.execute("UPDATE projects SET archived=1 WHERE id=?", (pid,))
                raise
        return self.project(pid)
