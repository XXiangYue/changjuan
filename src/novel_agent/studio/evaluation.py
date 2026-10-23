"""Live evaluations. No synthetic model completions are used in this module."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from argparse import Namespace
from collections import Counter
from datetime import datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .db import Database, encode, now, uid
from .engine import AUTHOR_SYSTEM
from .llm import ModelClient
from .models import ProjectCreate
from .repository import Repository, unpack
from .retrieval import Retrieval
from .settings import ROOT, Settings

CASES = [
    (
        "丹药去向",
        "温肺丹已在上一夜交给洛蘅，沈砚手中没有剩余丹药。药铺三个月才到下一批。",
        "沈砚的肺伤再次发作，他需要继续修炉。",
        ["沈砚不能从自己身上再拿出温肺丹", "不得无来源增加同类丹药"],
    ),
    (
        "角色知情",
        "旧账副本只有洛蘅读过。沈砚只知道有一本旧账，不知道页上沈记欠数。洛蘅尚未与沈砚重逢。",
        "沈砚独自去见楚衡，试图谈判。",
        ["沈砚不能准确引用沈记的欠数", "不能让沈砚知道洛蘅未告诉他的账册内容"],
    ),
    (
        "能力触发",
        "沈砚必须以裸露手指接触器物才能听见火息，不能隔空听息。炉壁在十步外，被封条封着。",
        "沈砚躲在巷口观察被封的镇脉炉。",
        ["听息必须实际接触器物", "不能凭空获得炉内火息细节"],
    ),
    (
        "境界条件",
        "沈砚仍处火息初阶。进入纳炉境需三日调息、赤砂一钱、完整炉砖。他没有赤砂，离限时只剩半日。",
        "沈砚面对一名纳炉境守卫，需要把炉砖送进镇。",
        ["不能无材料或时间完成境界突破", "不能无代价正面越境击败守卫"],
    ),
    (
        "伤势延续",
        "沈砚的右手三指在昨夜听息后失去触觉，至少持续两日。洛蘅已经替他包扎，当前仍是次日清晨。",
        "沈砚必须在炉砖上刻一条细槽。",
        ["三指触觉不能在次日无解释恢复", "行动应体现失去触觉的困难"],
    ),
    (
        "承诺兑现",
        "沈砚答应陈婶在今日午时前归还借走的炉钳；现在午时将到，炉钳仍在沈砚身上。陈婶要靠它生火做饭。",
        "洛蘅催促沈砚立刻离镇去废窑取证。",
        ["不能写炉钳早已归还", "应处理归还承诺与取证时限的冲突"],
    ),
    (
        "人物动机",
        "洛蘅修炉的首要目的，是找失踪同门的物证；她明确拒绝为了沈砚的考核而放弃同门线索。她与沈砚相识仅三日。",
        "沈砚请求洛蘅交出唯一的矿道地图，让他先赶考核。",
        ["洛蘅不能无理由放弃寻找同门", "双方关系不能突然成为无条件牺牲"],
    ),
    (
        "时间顺序",
        "户主会定在明日申时，楚衡尚未公开旧账，今日仍是辰时。账册副本只有洛蘅读过。",
        "今日早市上，沈砚遇到议论炉灾的邻居。",
        ["不能写户主会已经举行", "不能让普通邻居已知未公开账册细节"],
    ),
    (
        "资源守恒",
        "沈砚只有三枚铜钱。进山渡船需两枚铜钱，修炉用的砂纸需两枚铜钱。没有赊账或额外收入的约定。",
        "沈砚必须在渡船开航前准备修炉工具。",
        ["不能同时无代价购买两项共四枚铜钱的物品", "若获得新钱，必须交代来源与代价"],
    ),
    (
        "伏笔边界",
        "红灰无焦油味，后来证实来自被烧过的私接管填料；红灰不含魂魄、传承或功法。铜铃的秘密尚未揭开。",
        "沈砚把红灰放在旧砧上，尝试辨认它与废窑的关系。",
        ["不能给红灰突然添加魂魄或传承", "辨认应与已建立的管道证据关联"],
    ),
    (
        "因果与距离",
        "洛蘅正被困在离镇三十里的旧矿道，沈砚在镇内。最快往返也需两个时辰，尚无传讯法器。",
        "镇内炉火突然倒灌，沈砚只有一刻钟处置。",
        ["洛蘅不能无解释在一刻钟内回镇救场", "解决必须来自在场角色已有的资源与行动"],
    ),
    (
        "稳定性格",
        "楚衡一直优先避免镇民恐慌和无力偿还的债务公开。他曾私用官仓垫火租，承担着被追责的风险。他并不嗜杀。",
        "沈砚当面对质账册，并提出在户主会上公开分摊旧债。",
        ["楚衡不能突然仅为作恶而无理由屠杀镇民", "应回应稳住镇民与承担追责的动机"],
    ),
]


class CheckResult(BaseModel):
    constraint: str
    outcome: Literal["preserved", "violation", "not_tested"]
    quote: str = ""
    reason: str


class Verdict(BaseModel):
    checks: list[CheckResult] = Field(
        min_length=2,
        max_length=2,
        description="按给定顺序逐项评价恰好两个约束，不合并、不增删",
    )
    readability: float = Field(ge=0, le=10)
    momentum: float = Field(ge=0, le=10)
    comment: str


class AuditEvidence(BaseModel):
    chapter: int = Field(ge=1, le=12)
    quote: str = Field(min_length=1, description="该章节中逐字复制的一段连续原文")


class AuditCheck(BaseModel):
    name: str
    outcome: Literal["preserved", "violation", "not_tested"]
    reason: str
    evidence: list[AuditEvidence]


class HookCheck(BaseModel):
    name: str
    outcome: Literal["resolved", "partial", "open"]
    reason: str
    evidence: list[AuditEvidence]


class ManuscriptAudit(BaseModel):
    constraints: list[AuditCheck] = Field(min_length=5, max_length=5)
    hooks: list[HookCheck] = Field(min_length=3, max_length=3)
    momentum: float = Field(ge=0, le=10)
    repetition_risk: float = Field(
        ge=0, le=10, description="越高表示重复风险越大，不是客观语义重复率"
    )
    ending_comment: str


def volume_rules():
    rules = {
        "frozen_at": now(),
        "scope": "整卷完成前补充的验收规则；不声称开发前预注册",
        "constraints": [
            "听息需要实际接触器物并付出肺息代价",
            "人物不能无来源知道他人尚未透露的账册或同门秘密",
            "温肺丹等消耗物需要明确来源和使用去向",
            "突破与胜利不能无铺垫、无代价或违背既定条件",
            "楚衡与洛蘅的行动应符合护镇和寻同门的独立动机",
        ],
        "hooks": ["红灰的来源与作用", "账册的隐瞒与公开代价", "断舌铜铃与失踪线索"],
    }
    path = ROOT / "evaluations" / "volume-acceptance.json"
    if path.exists():
        rules = json.loads(path.read_text())
    else:
        path.write_text(json.dumps(rules, ensure_ascii=False, indent=2))
    return rules


async def audit_volumes(titles=("完整样书", "旧流程策略对照")):
    s, db, repo, retrieval = infrastructure()
    rules = volume_rules()
    for title in titles:
        exp = db.one(
            "SELECT * FROM experiments WHERE name=? ORDER BY created DESC LIMIT 1",
            (title,),
        )
        cfg = json.loads(exp["config"])
        p = repo.project(cfg["project_id"])
        chapters = p["chapters"][:12]
        if len(chapters) != 12 or any(
            not x["canonical_revision"] or x["stale"] for x in chapters
        ):
            raise ValueError("请先完成两部12章样书再进行整卷审阅")
        fingerprint = hashlib.sha256(
            encode([x["canonical"] for x in chapters]).encode()
        ).hexdigest()
        prior = db.one(
            "SELECT * FROM experiments WHERE name=? ORDER BY created DESC LIMIT 1",
            (title + " · 整卷定点审阅",),
            required=False,
        )
        if (
            prior
            and json.loads(prior["config"]).get("source_hash") == fingerprint
            and prior["status"] == "completed"
        ):
            continue
        eid = uid()
        rid = uid()
        db.execute(
            "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
            (
                eid,
                title + " · 整卷定点审阅",
                encode(
                    {"project_id": p["id"], "source_hash": fingerprint, "rules": rules}
                ),
                "{}",
                "running",
                now(),
            ),
        )
        db.execute(
            "INSERT INTO runs(id,project_id,kind,config,status,created,updated) VALUES(?,?,?,?,?,?,?)",
            (
                rid,
                p["id"],
                "evaluation",
                encode({"experiment_id": eid}),
                "evaluating",
                now(),
                now(),
            ),
        )

        def grounded(value):
            for row in [*value.constraints, *value.hooks]:
                if (
                    row.outcome in ("violation", "resolved", "partial")
                    and not row.evidence
                ):
                    raise ValueError("违例或兑现结论必须有原文证据")
                for e in row.evidence:
                    if e.quote not in chapters[e.chapter - 1]["canonical"]:
                        raise ValueError("引文不是所指章节的连续原文：" + e.quote[:120])

        try:
            value = await ModelClient(db, s, retrieval).structured(
                rid,
                "整卷验收评测员",
                "按冻结规则核对整部玄幻短卷，严格逐项评价五个约束和三个伏笔。规则符合不等于故事精彩，不要因篇幅长奖励。没有实际涉及记not_tested；推测不记确凿违例。发现前后冲突需引用两处连续原文，注明章号。伏笔的出现不等于兑现，必须解释其因果作用。与作者使用同一模型，结论仅为模型判断。",
                encode(
                    {
                        "rules": rules,
                        "chapters": [
                            {"chapter": x["number"], "text": x["canonical"]}
                            for x in chapters
                        ],
                    }
                ),
                ManuscriptAudit,
                max_tokens=8000,
                validator=grounded,
                reasoning=True,
            )
            result = {
                "summary": "整卷约束与三条伏笔的同模型审阅，附逐字引文；不是人工结论。",
                "metrics": {
                    "约束违例（模型）": sum(
                        x.outcome == "violation" for x in value.constraints
                    ),
                    "未触及约束": sum(
                        x.outcome == "not_tested" for x in value.constraints
                    ),
                    "伏笔兑现（模型）": sum(
                        x.outcome == "resolved" for x in value.hooks
                    ),
                    "部分兑现": sum(x.outcome == "partial" for x in value.hooks),
                    "剧情推进（模型）": value.momentum,
                    "重复风险（模型，越低越好）": value.repetition_risk,
                },
                "audit": value.model_dump(),
                "limitations": [
                    "同模型评审，可能误报或漏报；引文存在不等于解释正确",
                    "三条伏笔、五项约束为本样书的验收范围",
                    "验收规则在整卷生成过程中补充，不是正式预注册",
                ],
            }
            db.execute(
                "UPDATE experiments SET result=?,status='completed' WHERE id=?",
                (encode(result), eid),
            )
            db.execute(
                "UPDATE runs SET status='completed',result=?,updated=? WHERE id=?",
                (encode(result), now(), rid),
            )
        except Exception as e:
            result = {
                "summary": "整卷审阅失败，保留运行记录",
                "error": type(e).__name__ + ": " + str(e)[:500],
            }
            db.execute(
                "UPDATE experiments SET result=?,status='failed' WHERE id=?",
                (encode(result), eid),
            )
            db.execute(
                "UPDATE runs SET status='failed',error=?,updated=? WHERE id=?",
                (result["error"], now(), rid),
            )
        out = ROOT / "artifacts" / "evaluation" / "raw"
        out.mkdir(parents=True, exist_ok=True)
        (out / (rid + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(title + " · 整卷审阅已记录", flush=True)


def studio_http_client(url, timeout=60):
    """Loopback workbench traffic must not depend on a system HTTP proxy.

    The official model client keeps its own transport and proxy settings.
    """
    loopback = httpx.URL(url).host in {"127.0.0.1", "localhost", "::1"}
    return httpx.AsyncClient(base_url=url, timeout=timeout, trust_env=not loopback)


def prepare_trial_recovery(db, item):
    """Keep completed candidates after controller transport failures, without grading selection."""
    rid = item.get("run_id")
    if not rid:
        return
    ch = db.one(
        "SELECT content,canonical FROM chapters WHERE id=?", (item["chapter_id"],)
    )
    if ch["canonical"]:
        item["preserved_canonical_sha256"] = hashlib.sha256(
            ch["canonical"].encode()
        ).hexdigest()
    if db.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] != "cancelled":
        return
    if db.one("SELECT run_id FROM run_commits WHERE run_id=?", (rid,), required=False):
        item["commit_completed_before_transport_failure"] = True
        return
    item["prior_run_ids"] = list(dict.fromkeys([*item.get("prior_run_ids", []), rid]))
    saved = db.one(
        "SELECT seq FROM run_events WHERE run_id=? AND type='draft.saved' LIMIT 1",
        (rid,),
        required=False,
    )
    approval = db.one(
        "SELECT seq FROM run_events WHERE run_id=? AND type='awaiting_approval' LIMIT 1",
        (rid,),
        required=False,
    )
    unchanged = (
        item.get("preserved_draft_sha256")
        == hashlib.sha256(ch["content"].encode()).hexdigest()
    )
    if (
        ch["content"].strip()
        and (saved or unchanged)
        and (item["mode"] in ("baseline", "no_revision") or approval or unchanged)
    ):
        item["recovery_kind"] = "review"
        item["preserved_draft_sha256"] = hashlib.sha256(
            ch["content"].encode()
        ).hexdigest()
    item.pop("run_id")


def infrastructure():
    s = Settings(ROOT)
    db = Database(s.data / "studio.sqlite")
    repo = Repository(db, s)
    retrieval = Retrieval(db, s)
    return s, db, repo, retrieval


def manifest():
    path = ROOT / "evaluations" / "frozen-cases.json"
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "1.0",
        "frozen_at": now(),
        "freeze_stage": "基础工程实现后、文学质量调优前；不声称正式预注册",
        "model": "deepseek-flash",
        "repetitions": 2,
        "modes": ["baseline", "full", "no_memory", "no_revision"],
        "target_chars": 600,
        "cases": [
            {
                "id": str(i + 1).zfill(2),
                "name": name,
                "archive": archive,
                "task": task,
                "constraints": checks,
            }
            for i, (name, archive, task, checks) in enumerate(CASES)
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


async def request(c, method, path, **kwargs):
    r = await c.request(method, path, **kwargs)
    if not r.is_success:
        raise RuntimeError(f"{path}: HTTP {r.status_code} {r.text[:400]}")
    return r.json()


async def wait_run(c, rid):
    failures = 0
    while True:
        r = await request(c, "GET", "/api/runs/" + rid)
        if r["status"] in ("completed", "awaiting_approval"):
            return r
        if r["status"] == "failed":
            if failures < 2:
                failures += 1
                await request(c, "POST", "/api/runs/" + rid + "/resume")
            else:
                raise RuntimeError("运行失败：" + r["error"])
        elif r["status"] in ("interrupted", "cancelled"):
            raise RuntimeError("任务已暂停或取消：" + r["error"])
        await asyncio.sleep(1)


async def volume(c, mode, args):
    s, db, repo, _ = infrastructure()
    exp = db.one(
        "SELECT * FROM experiments WHERE name=? ORDER BY created DESC LIMIT 1",
        ("完整样书" if mode == "full" else "旧流程策略对照",),
        required=False,
    )
    if exp:
        cfg = json.loads(exp["config"])
        pid = cfg["project_id"]
        eid = exp["id"]
    else:
        books = await request(c, "GET", "/api/projects")
        source = next((p for p in books if p["title"] == "余烬照山河"), None)
        if not source:
            raise RuntimeError("请先在工作台创建并确认《余烬照山河》的故事蓝图")
        original = await request(c, "GET", "/api/projects/" + source["id"])
        if not original["revision"]:
            raise RuntimeError("请先在工作台确认故事蓝图")
        if mode == "full":
            pid = source["id"]
        else:
            req = ProjectCreate(**{k: original[k] for k in ProjectCreate.model_fields})
            req.title += " · 旧策略对照"
            p = repo.create(req)
            pid = p["id"]
            blueprint_run = db.one(
                "SELECT result FROM runs WHERE project_id=? AND kind='blueprint' AND status='completed' ORDER BY created LIMIT 1",
                (original["id"],),
            )
            bp = json.loads(blueprint_run["result"])
            repo.apply_blueprint(pid, bp, 0)
        eid = uid()
        cfg = {
            "project_id": pid,
            "mode": mode,
            "chapters": args.chapters,
            "target_words": args.words,
            "operator": "自动化验收操作者；不属于真实用户评价",
            "description": "同一 DeepSeek 模型、初始设定与蓝图；基线采用全量核心记忆、上一章、单次审核和不自动返工，共用新存储与记忆提案基础设施",
        }
        db.execute(
            "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
            (
                eid,
                "完整样书" if mode == "full" else "旧流程策略对照",
                encode(cfg),
                "{}",
                "running",
                now(),
            ),
        )
    print(f"开始 {mode} 整卷创作：{pid}", flush=True)
    for _ in range(args.chapters):
        p = await request(c, "GET", "/api/projects/" + pid)
        ch = next(
            (
                x
                for x in p["chapters"][: args.chapters]
                if not x["canonical_revision"] or x["stale"]
            ),
            None,
        )
        if ch is None:
            break
        pending = next(
            (
                r
                for r in p["runs"]
                if r["status"]
                in ("queued", "running", "awaiting_approval", "interrupted")
            ),
            None,
        )
        if pending:
            r = pending
        else:
            r = await request(
                c,
                "POST",
                "/api/runs",
                json={
                    "project_id": pid,
                    "chapter_id": ch["id"],
                    "kind": "chapter",
                    "ablation": mode if mode == "baseline" else "full",
                    "auto_revise": mode != "baseline",
                },
            )
        state = await wait_run(c, r["id"])
        if state["status"] == "awaiting_approval":
            ch = await request(c, "GET", "/api/chapters/" + ch["id"])
            if mode == "full" and not ch["review"].get("passed"):
                raise RuntimeError(
                    "样书第"
                    + str(ch["number"])
                    + "章未通过审核，需测试操作者在工作台处理，已保留全文与意见"
                )
            p = await request(c, "GET", "/api/projects/" + pid)
            await request(
                c,
                "POST",
                "/api/runs/" + r["id"] + "/approve",
                json={
                    "base_revision": ch["draft_revision"],
                    "project_revision": p["revision"],
                    "accepted_patches": [x["name"] for x in ch["proposal"]["patches"]],
                    "note": "自动化实测操作者按预设规则确认；非人工盲评"
                    if mode == "full"
                    else "基线对照按预设策略保留当前输出，包括未通过的审核",
                },
            )
            await wait_run(c, r["id"])
        print(f"{mode} 第{ch['number']}章完成", flush=True)
        update_volume_result(db, repo, eid, pid, args.chapters)
    update_volume_result(db, repo, eid, pid, args.chapters)
    out = ROOT / "artifacts" / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    (out / (mode + "-novel.md")).write_bytes(repo.export(pid, "md")[0])
    return pid


def update_volume_result(db, repo, eid, pid, target):
    p = repo.project(pid)
    chapters = [
        ch
        for ch in p["chapters"][:target]
        if ch["canonical_revision"] and not ch["stale"]
    ]
    scores = [ch["review"]["total"] for ch in chapters if "total" in ch["review"]]
    cost = db.one(
        "SELECT COALESCE(SUM(u.cost),0) cost,COALESCE(SUM(u.input_tokens),0) inp,COALESCE(SUM(u.output_tokens),0) out FROM usage u JOIN runs r ON r.id=u.run_id WHERE r.project_id=?",
        (pid,),
    )
    count = sum(len(ch["canonical"]) for ch in chapters)
    body = "\n\n".join(ch["canonical"] for ch in chapters)
    paragraphs = [p.strip() for p in body.split("\n") if len(p.strip()) >= 30]
    duplicate = sum(len(p) * (n - 1) for p, n in Counter(paragraphs).items() if n > 1)
    runs = db.all("SELECT id,status,kind,created FROM runs WHERE project_id=?", (pid,))
    first_tokens = []
    click_to_text = []
    chapter_ready = []
    rewrites = 0
    for run in runs:
        if run["kind"] == "chapter":
            for event_type, destination in (
                ("token", click_to_text),
                ("approval", chapter_ready),
            ):
                event = db.one(
                    "SELECT created FROM run_events WHERE run_id=? AND "
                    + (
                        "type='token'"
                        if event_type == "token"
                        else "type='stage' AND json_extract(data,'$.stage')='approval'"
                    )
                    + " ORDER BY seq LIMIT 1",
                    (run["id"],),
                    required=False,
                )
                if event:
                    destination.append(
                        (
                            datetime.fromisoformat(event["created"])
                            - datetime.fromisoformat(run["created"])
                        ).total_seconds()
                    )
        start = None
        drafts = 0
        for e in db.all(
            "SELECT type,created FROM run_events WHERE run_id=? AND type IN ('draft.start','token') ORDER BY seq",
            (run["id"],),
        ):
            if e["type"] == "draft.start":
                start = e["created"]
                drafts += 1
            elif start:
                first_tokens.append(
                    (
                        datetime.fromisoformat(e["created"])
                        - datetime.fromisoformat(start)
                    ).total_seconds()
                )
                start = None
        rewrites += max(0, drafts - 1)
    durations = [
        x["duration"]
        for x in db.all(
            "SELECT u.duration FROM usage u JOIN runs r ON r.id=u.run_id WHERE r.project_id=? AND u.duration>0",
            (pid,),
        )
    ]

    def percentile(values, q):
        return (
            round(sorted(values)[min(len(values) - 1, int((len(values) - 1) * q))], 2)
            if values
            else None
        )

    metrics = {
        "定稿章数": len(chapters),
        "正文字符数": count,
        "汉字数（基本区）": sum(
            len(re.findall(r"[\u4e00-\u9fff]", ch["canonical"])) for ch in chapters
        ),
        "篇幅达标章数": sum(
            0.8 <= len(ch["canonical"]) / ch["plan"].get("target_words", 2500) <= 1.2
            for ch in chapters
        ),
        "平均编辑分": round(sum(scores) / len(scores), 2) if scores else None,
        "费用估算": round(cost["cost"], 4),
        "每万字符费用": round(cost["cost"] / count * 10000, 3) if count else None,
        "输入tokens": cost["inp"],
        "输出tokens": cost["out"],
        "模型调用P50秒": percentile(durations, 0.5),
        "模型调用P95秒": percentile(durations, 0.95),
        "首段流式P50秒": percentile(first_tokens, 0.5),
        "点击至首正文P50秒": percentile(click_to_text, 0.5),
        "整章待审P50秒（含中断）": percentile(chapter_ready, 0.5),
        "整章待审P95秒（含中断）": percentile(chapter_ready, 0.95),
        "额外正文生成轮次": rewrites,
        "完全重复长段字符占比": round(duplicate / max(count, 1), 4),
    }
    result = {
        "summary": f"已定稿 {len(chapters)}/{target} 章，正文 {count} 字符；模型编辑分不代表真实读者评价。",
        "metrics": metrics,
        "project_id": pid,
        "limitations": [
            "一部玄幻短卷，不能外推到百万字或其他题材",
            "基线是旧流程策略适配，共用新存储与提案服务；不是旧代码逐字复刻",
            "字符数包含标点与排版符号，不等同平台计费字数",
            "费用包含该作品失败、规划、改写、工程重试与整卷评测；两组存在操作与篇幅差异",
            "完全重复仅统计至少30字符的逐字相同段落，不代表语义重复率",
            "首段流式延迟为每轮作者开始到首次持久化token，不含前置检索与规划",
            "整章待审统计已到达首个作者确认点的写章任务，包含后来取消的任务、中断与开发修复等待，不是受控性能基准",
        ],
    }
    db.execute(
        "UPDATE experiments SET result=?,status=? WHERE id=?",
        (encode(result), "completed" if len(chapters) == target else "running", eid),
    )


async def ablations(c):
    s, db, repo, retrieval = infrastructure()
    m = manifest()
    payload = encode(m)
    sha = hashlib.sha256(payload.encode()).hexdigest()
    existing = db.one(
        "SELECT * FROM experiments WHERE name='固定场景消融 v1' ORDER BY created DESC LIMIT 1",
        required=False,
    )
    if existing:
        eid = existing["id"]
        saved = json.loads(existing["result"])
        results = saved.get("trials", [])
        if json.loads(existing["config"])["manifest_sha256"] != sha:
            raise RuntimeError("冻结实验输入已改变，请创建新版本")
    else:
        eid = uid()
        results = []
        db.execute(
            "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
            (
                eid,
                "固定场景消融 v1",
                encode(
                    {
                        "manifest_sha256": sha,
                        "manifest": m,
                        "description": "固定场景的上下文与返工机制探针，独立于端到端整卷验收；同模型评估",
                    }
                ),
                "{}",
                "running",
                now(),
            ),
        )
    project = repo.create(
        ProjectCreate(
            title="评估夹具 · 非样书",
            premise="手工编写的虚构故事状态，专用于可重复执行的评估。",
            target_chapters=1,
            chapter_words=600,
        )
    )
    db.execute("UPDATE projects SET archived=1 WHERE id=?", (project["id"],))
    model = ModelClient(db, s, retrieval)
    semaphore = asyncio.Semaphore(4)
    lock = asyncio.Lock()

    async def trial(case, mode, rep):
        key = (case["id"], mode, rep)
        if any(
            (x["case_id"], x["mode"], x["repeat"]) == key and not x.get("error")
            for x in results
        ):
            return
        async with semaphore:
            rid = uid()
            db.execute(
                "INSERT INTO runs(id,project_id,kind,config,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    rid,
                    project["id"],
                    "evaluation",
                    encode(
                        {
                            "experiment_id": eid,
                            "case_id": case["id"],
                            "mode": mode,
                            "repeat": rep,
                        }
                    ),
                    "evaluating",
                    now(),
                    now(),
                ),
            )
            item = {
                "case_id": case["id"],
                "case_name": case["name"],
                "mode": mode,
                "repeat": rep,
                "run_id": rid,
            }
            try:
                base = "玄幻铸器小镇，沈砚是有肺伤的铸器学徒，洛蘅是寻找同门的修炉师，楚衡是镇守。能力、物品、时间与信息必须遵循已有来源。"
                prompt = (
                    "故事当前方向："
                    + case["task"]
                    + "\n核心记忆："
                    + base
                    + "\n写约600个中文字的完整场景，不要写提纲。"
                )
                if mode in ("full", "no_revision"):
                    prompt += (
                        "\n从带来源的历史状态检索到的依据（已发生，必须遵守）："
                        + case["archive"]
                    )
                text = await model.text(
                    rid,
                    "探针作者",
                    AUTHOR_SYSTEM
                    if mode != "baseline"
                    else "你是小说创作助手，续写给定的玄幻故事。保持与记忆文档一致，只返回正文。",
                    prompt,
                    max_tokens=2400,
                )

                async def judge(body):
                    def grounded(v):
                        if len(v.checks) != len(case["constraints"]):
                            raise ValueError("评测约束条数不符")
                        if any(
                            x.outcome == "violation"
                            and (not x.quote or x.quote not in body)
                            for x in v.checks
                        ):
                            raise ValueError(
                                "评测违例引用不在正文中，必须逐字引用一段连续原文"
                            )

                    v = await model.structured(
                        rid,
                        "独立评测员",
                        "按提供的既定事实核对续写。每个约束都必须逐项评估。没有触及的约束记not_tested，不能默认为通过。violation必须逐字引用续写中的具体原句。可读性与剧情推动评分为0–10，不以篇幅长短奖励。",
                        encode(
                            {
                                "canon": case["archive"],
                                "constraints": case["constraints"],
                                "task": case["task"],
                                "continuation": body,
                            }
                        ),
                        Verdict,
                        max_tokens=3000,
                        validator=grounded,
                    )
                    return v

                first = await judge(text)
                item["first_verdict"] = first.model_dump()
                if mode in ("full", "no_memory") and any(
                    x.outcome == "violation" for x in first.checks
                ):
                    text = await model.text(
                        rid,
                        "探针修订员",
                        AUTHOR_SYSTEM,
                        prompt
                        + "\n编辑查证的原始事实："
                        + case["archive"]
                        + "\n原稿："
                        + text
                        + "\n具体问题："
                        + first.model_dump_json()
                        + "\n只修复有证据的问题，输出完整修订正文。",
                        max_tokens=2400,
                    )
                    verdict = await judge(text)
                    item["revisions"] = 1
                else:
                    verdict = first
                    item["revisions"] = 0
                item |= {
                    "text": text,
                    "verdict": verdict.model_dump(),
                    "chars": len(text),
                    "retrieval": "冻结故事状态证据注入；此探针不替代端到端检索评估",
                }
                db.execute(
                    "UPDATE runs SET status='completed',result=?,updated=? WHERE id=?",
                    (encode(item), now(), rid),
                )
            except Exception as e:
                item["error"] = type(e).__name__ + ": " + str(e)[:300]
                db.execute(
                    "UPDATE runs SET status='failed',error=?,updated=? WHERE id=?",
                    (item["error"], now(), rid),
                )
            async with lock:
                results.append(item)
                result = summarize_trials(results)
                db.execute(
                    "UPDATE experiments SET result=? WHERE id=?", (encode(result), eid)
                )
                raw = ROOT / "artifacts" / "evaluation" / "raw"
                raw.mkdir(parents=True, exist_ok=True)
                (raw / (rid + ".json")).write_text(
                    json.dumps(item, ensure_ascii=False, indent=2)
                )
                print(
                    f"消融 {len(results)}/96 · {case['name']} · {mode} · 重复{rep} · "
                    + ("失败已记录" if item.get("error") else "完成"),
                    flush=True,
                )

    await asyncio.gather(
        *(
            trial(case, mode, rep)
            for case in m["cases"]
            for mode in m["modes"]
            for rep in range(1, 3)
        )
    )
    result = summarize_trials(results)
    db.execute(
        "UPDATE experiments SET result=?,status=? WHERE id=?",
        (
            encode(result),
            "completed"
            if len([x for x in results if not x.get("error")]) == 96
            else "partial",
            eid,
        ),
    )


def summarize_trials(results):
    modes = {}
    for mode in ("baseline", "full", "no_memory", "no_revision"):
        rows = [x for x in results if x["mode"] == mode and not x.get("error")]
        counts = Counter(k["outcome"] for x in rows for k in x["verdict"]["checks"])
        modes[mode] = {
            "successful_trials": len(rows),
            "violations_model_judged": counts["violation"],
            "preserved": counts["preserved"],
            "not_tested": counts["not_tested"],
            "readability": round(
                sum(x["verdict"]["readability"] for x in rows) / len(rows), 2
            )
            if rows
            else None,
            "momentum": round(
                sum(x["verdict"]["momentum"] for x in rows) / len(rows), 2
            )
            if rows
            else None,
        }
    return {
        "summary": "固定场景机制探针；违例由同一模型按给定事实判定，需结合人工阅读。",
        "metrics": {
            "已记录试次": len(results),
            "成功试次": sum(not x.get("error") for x in results),
            "失败试次": sum(bool(x.get("error")) for x in results),
        },
        "by_mode": modes,
        "trials": results,
        "limitations": [
            "证据注入与返工的机制探针，不是整套产品的端到端消融",
            "同模型评审存在偏差；重复2次不构成充分统计功效",
            "未触及约束单独记录，不能算作正确",
            "冻结时间在基础工程实现后、质量调优前",
        ],
    }


def integration_performance(db, result):
    """Reporting only: count all attempts, while normalizing by valid new text."""
    performance = {}
    for mode in ("baseline", "full", "no_memory", "no_revision"):
        trials = [t for t in result.get("trials", []) if t["mode"] == mode]
        valid = [t for t in trials if t.get("status") == "completed"]
        pids = sorted({t["project_id"] for t in trials if t.get("project_id")})
        usage = []
        if pids:
            usage = db.all(
                "SELECT u.*,r.kind FROM usage u JOIN runs r ON r.id=u.run_id WHERE r.project_id IN ("
                + ",".join("?" for _ in pids)
                + ")",
                tuple(pids),
            )
        writer = [u for u in usage if u["kind"] != "evaluation"]
        chars = sum(t.get("chars", 0) for t in valid)
        costs = sum(u["cost"] + u["reserved"] for u in writer)
        durations = sorted(u["duration"] for u in writer if u["duration"] > 0)
        performance[mode] = {
            "valid_text_chars": chars,
            "attempts_including_failures": len(trials),
            "writer_calls": len(writer),
            "writer_input_tokens": sum(u["input_tokens"] for u in writer),
            "writer_output_tokens": sum(u["output_tokens"] for u in writer),
            "writer_cost_cny": round(costs, 4),
            "independent_grader_cost_cny": round(
                sum(
                    u["cost"] + u["reserved"]
                    for u in usage
                    if u["kind"] == "evaluation"
                ),
                4,
            ),
            "writer_cost_per_10k_chars": round(costs / chars * 10000, 4)
            if chars
            else None,
            "writer_model_call_p50_seconds": durations[int((len(durations) - 1) * 0.5)]
            if durations
            else None,
            "writer_model_call_p95_seconds": durations[int((len(durations) - 1) * 0.95)]
            if durations
            else None,
        }
    return performance


def report():
    s, db, repo, _ = infrastructure()
    out = ROOT / "artifacts" / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    for e in db.all(
        "SELECT * FROM experiments WHERE name IN ('完整样书','旧流程策略对照')"
    ):
        cfg = json.loads(e["config"])
        update_volume_result(
            db, repo, e["id"], cfg["project_id"], cfg.get("chapters", 12)
        )
    exps = [unpack(x) for x in db.all("SELECT * FROM experiments ORDER BY created")]
    for experiment in exps:
        if experiment["name"] == "真实执行图续写验收 v1":
            experiment["result"]["performance"] = integration_performance(
                db, experiment["result"]
            )
    models = db.all(
        "SELECT model,MIN(created) first_call,MAX(created) last_call,COUNT(*) calls FROM usage GROUP BY model"
    )
    failures = db.all(
        "SELECT id,project_id,kind,status,error,created,updated FROM runs WHERE status IN ('failed','interrupted','cancelled') ORDER BY created"
    )
    recovery = db.all(
        "SELECT run_id,type,data,created FROM run_events WHERE type IN ('retry','error','control','notice') ORDER BY seq"
    )
    raw = {
        "generated_at": now(),
        "usage": db.usage_summary(),
        "model_identifiers": models,
        "experiments": exps,
        "failures": failures,
        "recovery_events": recovery,
        "human_votes": db.all("SELECT id,preference,reason,voted_at FROM blind_pairs"),
    }
    (out / "results.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    lines = [
        "# 长卷 · 实测记录",
        "",
        "生成时间：" + raw["generated_at"],
        "",
        "费用按官方高峰价格保守估算，未扣缓存优惠；实际以账单为准。",
        "",
        "## 模型与样本",
        "创作与场景评测主模型 deepseek-flash，独立角色仍使用同一模型。另列Codex子代理片段评审，其用量不计入DeepSeek账本。样书验收、机制探针与真实执行图的历史缺口压力验收分别报告。两个场景实验复用相同12任务，不合并为独立样本。",
        "",
    ]
    lines += (
        [
            "| 实际返回标识 | 首次调用 UTC | 最近调用 UTC | 调用数 |",
            "|---|---|---|---:|",
        ]
        + [
            f"| {m['model']} | {m['first_call']} | {m['last_call']} | {m['calls']} |"
            for m in models
        ]
        + [
            "",
            "返回别名不能证明供应商内部精确快照。开发与实写并行，期间修复格式、恢复和作者提案控制；第8章起校准审阅提示并使用思考型编辑，第9章起前置篇幅检查。样书还包含Codex AI测试操作者通过界面的编辑，不能冒充自然人作者体验或纯DeepSeek自动创作。故这是探索性产品验收，不能当作软件与推理配置固定的随机对照。原始失败、输入和配置见 results.json、runs 与 protocol-changes.json。",
            "",
        ]
    )
    for e in exps:
        lines += [
            "## " + e["name"],
            "",
            e["result"].get("summary", "运行中"),
            "",
            "状态：" + e["status"],
            "",
            "| 指标 | 实测 |",
            "|---|---|",
        ]
        lines += [f"| {k} | {v} |" for k, v in e["result"].get("metrics", {}).items()]
        if reading := e["result"].get("agent_reading"):
            names = {
                "full": "完整工作流",
                "baseline": "旧策略",
                "tie": "接近",
                "mixed": "无多数",
            }
            lines += [
                "",
                reading["interpretation"],
                "",
                "| 片段 | 来源章节 | 完整工作流 | 旧策略 | 接近 | 多数选择 |",
                "|---|---:|---:|---:|---:|---|",
                *[
                    f"| {x['sample_id']} | {x['chapter']} | {x['full']} | {x['baseline']} | {x['tie']} | {names[x['majority']]} |"
                    for x in reading["by_sample"]
                ],
                "",
                *["- " + item for item in reading["observations"]],
                "",
                "[逐位评审理由、原文依据与方法](AI代理片段评审.md)。按用户选择由子代理完成，未写入真人投票。",
                "",
            ]
        if audit := e["result"].get("audit"):
            lines += [
                "",
                "以下均为同模型判断；逐字引文经过程序核验，语义解释仍可能有误。",
                "",
            ]
            for item in [*audit["constraints"], *audit["hooks"]]:
                lines += [
                    "### " + item["name"] + " · " + item["outcome"],
                    "",
                    item["reason"],
                    "",
                ]
                for evidence in item["evidence"]:
                    lines += [
                        f"> 第{evidence['chapter']}章："
                        + evidence["quote"].replace("\n", "\n> "),
                        "",
                    ]
            lines += ["结尾评价（模型）：" + audit["ending_comment"], ""]
        if e["result"].get("by_mode"):
            lines += [
                "",
                "| 配置 | 成功试次 | 模型判定违例 | 保持约束 | 未触及约束 | 可读性 | 推进分 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            lines += [
                f"| {k} | {v['successful_trials']} | {v['violations_model_judged']} | {v['preserved']} | {v['not_tested']} | {v['readability']} | {v['momentum']} |"
                for k, v in e["result"]["by_mode"].items()
            ]
        if performance := e["result"].get("performance"):
            lines += [
                "",
                "| 配置 | 历史档案命中 | 结构化状态存在 | 平均额外正文轮次 | 有效续写字符 | 创作费用¥ | 独立评测费用¥ | 创作每万字符¥ |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
            for mode, values in performance.items():
                group = e["result"]["by_mode"][mode]
                lines.append(
                    f"| {mode} | {group['archive_retrieved']} | {group['structured_state_present']} | {group['mean_extra_drafts']} | {values['valid_text_chars']} | {values['writer_cost_cny']} | {values['independent_grader_cost_cny']} | {values['writer_cost_per_10k_chars']} |"
                )
            lines += [
                "",
                "费用包含同配置所有失败和恢复调用，已完成稿件评测失败时复用原稿；十一段输入档案不计入生成字数。创作调用与独立评测费用分开。额外正文轮次包含篇幅返工，不都是一致性纠错。每个配置仅24个预置短场景，不能当作普通长篇创作的平均费用。调用延迟、token与每次试验的完整上下文见results.json。最多8个在途验收共用2个服务执行位置，试次墙钟时间包含排队，不能用来比较配置速度。",
            ]
            if (out / "transport-recovery.json").exists():
                lines += [
                    "",
                    "首轮87有效、9次ConnectTimeout，原控制器源码与首轮结果另存。系统代理接管了本地HTTP，补跑时改为回环直连；官方模型传输、输入、生成/审阅提示、模型参数与判分规则未改。3个定稿和4个完整草稿核对哈希复用，2个没有完整稿才重新写。仅补失败项，全部原错误、正文与费用保留。详见transport-recovery.json、integration-first-pass.json与frozen-source-v1。",
                ]
        lines += [""] + ["- " + x for x in e["result"].get("limitations", [])] + [""]
    votes = db.all("SELECT * FROM blind_pairs")
    has_agent_reading = any(e["result"].get("agent_reading") for e in exps)
    lines += [
        "## 可选的真实用户评价" if has_agent_reading else "## 真实用户盲评",
        "",
        f"已完成 {sum(bool(x['preference']) for x in votes)} / {len(votes)} 组。未提交的阅读选择不作推断。",
        "",
    ]
    if has_agent_reading:
        lines += [
            "本轮依用户选择，使用上述AI子代理评审完成片段比较；未取得自然人评价，真人入口为可选功能。已看本轮解盲结果的人再评同组片段，不作为严格盲评。",
            "",
        ]
    if any(x["preference"] for x in votes):
        counts = Counter()
        for x in votes:
            if x["preference"]:
                counts[
                    "接近"
                    if x["preference"] == "tie"
                    else json.loads(x["labels"])[x["preference"]]
                ] += 1
        lines += (
            ["选择计数：" + encode(dict(counts)), ""]
            + ["- " + x["reason"] for x in votes if x["preference"] and x["reason"]]
            + [""]
        )
    full = next((e for e in exps if e["name"] == "完整样书"), None)
    base = next((e for e in exps if e["name"] == "旧流程策略对照"), None)
    if full and base and not votes:
        a = repo.project(full["config"]["project_id"])
        b = repo.project(base["config"]["project_id"])
        if (
            len([c for c in a["chapters"] if c["canonical_revision"]]) >= 12
            and len([c for c in b["chapters"] if c["canonical_revision"]]) >= 12
        ):
            rng = random.Random(20261018)
            for number in (1, 3, 5, 7, 9, 12):
                x = a["chapters"][number - 1]["canonical"]
                y = b["chapters"][number - 1]["canonical"]
                length = min(500, len(x), len(y))
                x = x[:length]
                y = y[:length]
                reverse = bool(rng.randrange(2))
                db.execute(
                    "INSERT INTO blind_pairs(id,experiment_id,a,b,labels) VALUES(?,?,?,?,?)",
                    (
                        uid(),
                        full["id"],
                        y if reverse else x,
                        x if reverse else y,
                        encode(
                            {
                                "a": "baseline" if reverse else "full",
                                "b": "full" if reverse else "baseline",
                                "chapter": number,
                                "selection": "各章开头500字符，取两稿较短长度；固定位置，未按分数选择",
                            }
                        ),
                    ),
                )
            lines = [
                line.replace("已完成 0 / 0 组。", "已完成 0 / 6 组。") for line in lines
            ]
            raw["human_votes"] = db.all(
                "SELECT id,preference,reason,voted_at FROM blind_pairs"
            )
            (out / "results.json").write_text(
                json.dumps(raw, ensure_ascii=False, indent=2)
            )
    lines += [
        "## 可靠性与范围",
        "",
        "回归测试为离线夹具验证，不计入真实模型效果；可复核 artifacts/testing/pytest.xml。实际界面操作、暂停和恢复记录保留在本地运行与审计中。",
        "",
        "本报告区分程序规则、模型判断、人工偏好和工程用量。当前结果仅覆盖该模型、一部玄幻短卷与十二个固定场景；不能外推到百万字作品、其他题材或所有作者。",
        "",
    ]
    (out / "实测报告.md").write_text("\n".join(lines))
    # Persist all run traces without secrets, including historical failed attempts.
    traces = out / "runs"
    traces.mkdir(exist_ok=True)
    for r in db.all("SELECT * FROM runs ORDER BY created"):
        if r["kind"] == "evaluation":
            continue
        events = db.all(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY seq", (r["id"],)
        )
        (traces / (r["id"] + ".json")).write_text(
            json.dumps(
                {
                    "run": unpack(r),
                    "events": [x | {"data": json.loads(x["data"])} for x in events],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    print(str(out / "实测报告.md"), flush=True)


async def evaluate(args: Namespace):
    manifest()
    if args.mode == "report":
        report()
        return
    if args.mode == "audit":
        await audit_volumes()
        report()
        return
    if args.mode == "integration":
        from .integration_eval import run_integration

        await run_integration(args)
        report()
        return
    async with studio_http_client(args.url) as c:
        if args.mode in ("suite", "sample"):
            await volume(c, "full", args)
        if args.mode in ("suite", "baseline"):
            await volume(c, "baseline", args)
        if args.mode in ("suite", "ablations"):
            await ablations(c)
    report()
