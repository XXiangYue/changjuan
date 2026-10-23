from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import TypeVar

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel
from deepagents import HarnessProfile, register_harness_profile
from deepagents.profiles import GeneralPurposeSubagentProfile

from .db import Database, encode, now, uid

T = TypeVar("T", bound=BaseModel)
register_harness_profile(
    "deepseek:deepseek-flash",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
    ),
)


class BudgetExceeded(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


class ToolTrace(AsyncCallbackHandler):
    def __init__(self, db, run_id):
        self.db, self.run_id = db, run_id

    async def on_tool_start(self, serialized, input_str, **kwargs):
        self.db.event(
            self.run_id,
            "tool",
            {
                "name": (serialized or {}).get("name", "story_tool"),
                "input": input_str[:2000],
            },
        )

    async def on_tool_end(self, output, **kwargs):
        self.db.event(
            self.run_id,
            "tool.result",
            {"result": str(getattr(output, "content", output))[:6000]},
        )


class Meter(AsyncCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, db, settings, run_id, role, max_tokens, emit_tokens):
        self.db, self.settings, self.run_id, self.role = db, settings, run_id, role
        self.max_tokens, self.emit_tokens = max_tokens, emit_tokens
        self.calls = {}
        self.pending = ""
        self.last_flush = time.monotonic()

    def check_cancel(self):
        run = self.db.one(
            "SELECT cancel_requested FROM runs WHERE id=?",
            (self.run_id,),
            required=False,
        )
        if run and run["cancel_requested"]:
            raise Cancelled(
                "作者已暂停任务" if run["cancel_requested"] == 2 else "作者已取消任务"
            )

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.check_cancel()
        # UTF-8 bytes give a conservative upper bound for input token reservation.
        inp = len(encode([[m.content for m in ms] for ms in messages]).encode())
        inp += len(encode(kwargs.get("invocation_params", {})).encode())
        reserved = (inp * 2 + self.max_tokens * 8) / 1_000_000
        key = uid()
        with self.db.transaction() as c:
            used = c.execute(
                "SELECT COALESCE(SUM(cost+reserved),0) FROM usage"
            ).fetchone()[0]
            if used + reserved > self.settings.values()["budget"]:
                raise BudgetExceeded("本地费用预算不足，任务已保存；可在设置中查看用量")
            c.execute(
                "INSERT INTO usage(id,run_id,role,model,reserved,price,created) VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    self.run_id,
                    self.role,
                    "deepseek-flash",
                    reserved,
                    encode(
                        {
                            "input_per_m": 2,
                            "output_per_m": 8,
                            "basis": "官方高峰单价保守估算，未扣缓存优惠；实际以账单为准",
                            "checked": "2026-10-18",
                        }
                    ),
                    now(),
                ),
            )
        self.calls[str(run_id)] = (key, time.monotonic(), reserved)

    async def on_llm_new_token(self, token, **kwargs):
        self.check_cancel()
        if not self.emit_tokens:
            return
        self.pending += token
        if time.monotonic() - self.last_flush > 0.12:
            self.flush()

    async def on_tool_start(self, serialized, input_str, **kwargs):
        self.check_cancel()
        self.db.event(
            self.run_id,
            "tool",
            {
                "role": self.role,
                "name": (serialized or {}).get("name", "story_tool"),
                "input": input_str[:1500],
            },
        )

    def flush(self):
        if self.pending:
            self.db.event(
                self.run_id, "token", {"text": self.pending, "role": self.role}
            )
            self.pending = ""
        self.last_flush = time.monotonic()

    async def on_llm_end(self, response, *, run_id, **kwargs):
        self.flush()
        call = self.calls.pop(str(run_id), None)
        if not call:
            return
        message = response.generations[0][0].message
        usage = message.usage_metadata or {}
        meta = message.response_metadata or {}
        raw = (response.llm_output or {}).get("token_usage", {})
        inp = usage.get("input_tokens", raw.get("prompt_tokens", 0))
        out = usage.get("output_tokens", raw.get("completion_tokens", 0))
        known = bool(usage or raw)
        cost = (inp * 2 + out * 8) / 1_000_000 if known else call[2]
        self.db.execute(
            "UPDATE usage SET input_tokens=?,output_tokens=?,cost=?,reserved=0,state=?,duration=?,model=?,request_id=? WHERE id=?",
            (
                inp,
                out,
                cost,
                "settled_estimate" if known else "usage_unknown_conservative",
                time.monotonic() - call[1],
                meta.get("model_name", "deepseek-flash"),
                meta.get("request_id", meta.get("id", "")),
                call[0],
            ),
        )
        self.db.event(
            self.run_id,
            "usage",
            {
                "role": self.role,
                "input_tokens": inp,
                "output_tokens": out,
                "cost_estimate": cost,
                "duration": time.monotonic() - call[1],
                "finish_reason": meta.get("finish_reason"),
                "model": meta.get("model_name", "deepseek-flash"),
            },
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.flush()
        call = self.calls.pop(str(run_id), None)
        if not call:
            return
        # Unknown completion billing is retained conservatively, including disconnects.
        code = getattr(error, "status_code", None)
        cost = 0 if code in (400, 401, 403, 404, 422, 429) else call[2]
        self.db.execute(
            "UPDATE usage SET cost=?,reserved=0,state=?,duration=? WHERE id=?",
            (
                cost,
                "failed_known" if cost == 0 else "failed_usage_unknown",
                time.monotonic() - call[1],
                call[0],
            ),
        )


class ModelClient:
    def __init__(self, db: Database, settings, retrieval):
        self.db, self.settings, self.retrieval = db, settings, retrieval

    async def connection(self):
        v = self.settings.values()
        if not v["api_key"]:
            raise ValueError("请先在模型设置中配置 DeepSeek API 密钥")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                v["base_url"] + "/models",
                headers={"Authorization": "Bearer " + v["api_key"]},
            )
        if r.status_code != 200:
            raise ValueError(
                f"DeepSeek 连接失败（HTTP {r.status_code}），请检查密钥与账户状态"
            )
        models = [x["id"] for x in r.json().get("data", [])]
        if "deepseek-flash" not in models:
            raise ValueError("当前账户未提供 deepseek-flash")
        return {"ok": True, "model": "deepseek-flash", "available": models}

    def model(
        self,
        run_id,
        role,
        max_tokens=10000,
        stream=False,
        json_mode=False,
        reasoning=False,
        reasoning_effort="high",
    ):
        v = self.settings.values()
        if not v["api_key"]:
            raise ValueError("请先配置 DeepSeek API 密钥")
        meter = Meter(self.db, self.settings, run_id, role, max_tokens, stream)
        model = ChatDeepSeek(
            model="deepseek-flash",
            api_key=v["api_key"],
            base_url=v["base_url"],
            max_tokens=max_tokens,
            max_retries=0,
            timeout=180,
            streaming=stream,
            stream_usage=True,
            extra_body={
                "thinking": {"type": "enabled" if reasoning else "disabled"},
                **({"reasoning_effort": reasoning_effort} if reasoning else {}),
            },
            model_kwargs={"response_format": {"type": "json_object"}}
            if json_mode
            else {},
            callbacks=[meter],
        )
        return model, meter

    async def text(self, run_id, role, system, prompt, max_tokens=10000):
        for attempt in range(3):
            self.db.event(
                run_id,
                "model.input",
                {
                    "role": role,
                    "model": "deepseek-flash",
                    "system": system,
                    "prompt": prompt,
                    "max_tokens": max_tokens,
                    "attempt": attempt + 1,
                    "stream": True,
                },
            )
            model, meter = self.model(run_id, role, max_tokens, True)
            try:
                parts = []
                finish_reason = None
                async for chunk in model.astream(
                    [SystemMessage(system), HumanMessage(prompt)]
                ):
                    if isinstance(chunk.content, str):
                        parts.append(chunk.content)
                    finish_reason = (
                        chunk.response_metadata.get("finish_reason") or finish_reason
                    )
                meter.flush()
                result = "".join(parts).strip()
                self.db.event(
                    run_id,
                    "model.output",
                    {"role": role, "text": result, "finish_reason": finish_reason},
                )
                if finish_reason == "length":
                    raise ValueError(
                        "模型输出达到长度上限，未作为完整正文保存。请降低章节字数目标后重试；生成片段已保留。"
                    )
                if not result:
                    raise ValueError("模型返回空正文")
                return result
            except (BudgetExceeded, Cancelled):
                raise
            except Exception as e:
                code = getattr(e, "status_code", None)
                if (
                    attempt == 2
                    or code in (400, 401, 403, 404, 422)
                    or "长度上限" in str(e)
                ):
                    raise
                self.db.event(
                    run_id,
                    "retry",
                    {"role": role, "attempt": attempt + 1, "reason": type(e).__name__},
                )
                await asyncio.sleep(2**attempt)

    async def structured(
        self,
        run_id,
        role,
        system,
        prompt,
        schema: type[T],
        max_tokens=12000,
        agent=False,
        pid=None,
        validator=None,
        reasoning=False,
    ) -> T:
        if reasoning:
            max_tokens = max(max_tokens, 32000)
        instruction = (
            "\n返回唯一 JSON 对象，严格符合以下 JSON schema；不得增加字段或包裹代码围栏。\n"
            + encode(schema.model_json_schema())
        )
        error = ""
        effort = "high"
        for attempt in range(3):
            self.db.event(
                run_id,
                "model.input",
                {
                    "role": role,
                    "model": "deepseek-flash",
                    "system": system + instruction,
                    "prompt": prompt + error,
                    "max_tokens": max_tokens,
                    "attempt": attempt + 1,
                    "harness": agent,
                    "reasoning": effort if reasoning else "none",
                },
            )
            model, meter = self.model(
                run_id,
                role,
                max_tokens,
                False,
                json_mode=reasoning,
                reasoning=reasoning,
                reasoning_effort=effort,
            )
            try:
                if agent:
                    from deepagents import create_deep_agent
                    from deepagents.backends import FilesystemBackend
                    from deepagents.middleware.filesystem import FilesystemPermission
                    from langchain_core.tools import tool
                    from langchain.agents.structured_output import ToolStrategy
                    from importlib.metadata import version

                    skill = (
                        Path(__file__).parent.parent
                        / "craft"
                        / "skills"
                        / "xuanhuan"
                        / "SKILL.md"
                    )
                    self.db.event(
                        run_id,
                        "harness",
                        {
                            "framework": "DeepAgents",
                            "version": version("deepagents"),
                            "skill": "xuanhuan",
                            "skill_sha256": hashlib.sha256(
                                skill.read_bytes()
                            ).hexdigest(),
                            "permissions": "创作技能只读；故事检索绑定当前作品；禁止写入正式稿",
                            "attempt": attempt + 1,
                        },
                    )

                    @tool
                    def search_story(query: str) -> str:
                        """检索本作品已定稿历史，返回可引用的章节依据。"""
                        return encode(self.retrieval.search(pid, query))

                    backend = FilesystemBackend(
                        root_dir=str(Path(__file__).parent.parent / "craft"),
                        virtual_mode=True,
                    )
                    project = self.db.one(
                        "SELECT genre FROM projects WHERE id=?", (pid,), required=False
                    )
                    skill_instruction = (
                        "\n本任务为玄幻规划。请先用 read_file 读取 /skills/xuanhuan/SKILL.md，将适用的规则落实到方案中，保持作者的既定设定。"
                        if project and "玄幻" in project["genre"]
                        else ""
                    )
                    worker = create_deep_agent(
                        model=model,
                        tools=[search_story],
                        system_prompt=system + instruction + skill_instruction,
                        backend=backend,
                        skills=["/skills/"],
                        permissions=[
                            FilesystemPermission(operations=["read"], paths=["/**"]),
                            FilesystemPermission(
                                operations=["write"], paths=["/**"], mode="deny"
                            ),
                        ],
                        response_format=ToolStrategy(schema),
                        subagents=[],
                        checkpointer=False,
                        name="story_planner",
                    )
                    result = await worker.ainvoke(
                        {"messages": [HumanMessage(prompt + error)]},
                        {
                            "recursion_limit": 18,
                            "callbacks": [ToolTrace(self.db, run_id)],
                        },
                    )
                    structured = result.get("structured_response")
                    if structured is None:
                        raise ValueError("规划员未提交结构化结果")
                    text = (
                        structured.model_dump_json()
                        if isinstance(structured, BaseModel)
                        else encode(structured)
                    )
                elif reasoning:
                    raw = await model.ainvoke(
                        [
                            SystemMessage(system + instruction),
                            HumanMessage(prompt + error),
                        ]
                    )
                    text = raw.content
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("深度审阅未返回JSON结果")
                else:
                    result = await model.with_structured_output(
                        schema, method="function_calling", include_raw=True
                    ).ainvoke(
                        [
                            SystemMessage(system + instruction),
                            HumanMessage(prompt + error),
                        ]
                    )
                    parsed = result.get("parsed")
                    if parsed is None:
                        raw = result["raw"]
                        self.db.event(
                            run_id,
                            "model.output",
                            {
                                "role": role,
                                "text": str(raw.content),
                                "tool_calls": raw.tool_calls,
                                "invalid_tool_calls": raw.invalid_tool_calls,
                            },
                        )
                        calls = raw.tool_calls
                        if len(calls) == 1 and calls[0].get("name") == schema.__name__:
                            # Some SDK/provider combinations lose the parser error.
                            # Validate the original arguments strictly; never fill
                            # missing fields or accept an ambiguous tool result.
                            parsed = schema.model_validate(calls[0]["args"])
                        else:
                            raise result.get("parsing_error") or ValueError(
                                "未返回唯一的预期结构化结果"
                            )
                    text = (
                        parsed.model_dump_json(exclude_computed_fields=True)
                        if isinstance(parsed, BaseModel)
                        else encode(parsed)
                    )
                self.db.event(run_id, "model.output", {"role": role, "text": text})
                data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
                validated = schema.model_validate(data)
                if validator:
                    validator(validated)
                return validated
            except (BudgetExceeded, Cancelled):
                raise
            except Exception as e:
                code = getattr(e, "status_code", None)
                if attempt == 2 or code in (401, 403, 404):
                    raise
                if reasoning and type(e).__name__ == "LengthFinishReasonError":
                    if effort != "low":
                        self.db.event(
                            run_id,
                            "notice",
                            {
                                "role": role,
                                "message": "深度审阅达到输出上限；下一次重试降低思考强度，保留原失败与费用。",
                                "from_effort": effort,
                                "to_effort": "low",
                            },
                        )
                    effort = "low"
                # Validation messages contain schema paths, never credentials or request headers.
                detail = (
                    str(e)[:2000]
                    if isinstance(e, (ValueError, TypeError))
                    else type(e).__name__
                )
                error = (
                    "\n上次输出未通过校验，请重新生成符合 schema 的完整 JSON。校验说明："
                    + detail
                )
                self.db.event(
                    run_id,
                    "retry",
                    {
                        "role": role,
                        "attempt": attempt + 1,
                        "reason": type(e).__name__,
                        "detail": detail,
                    },
                )
                await asyncio.sleep(min(2**attempt, 4))
