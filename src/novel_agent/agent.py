"""Agent 核心模块"""

import json
import os
from typing import Generator, Dict, List, Callable, Optional
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from .client import APIClient
from .skills import SkillLoader, create_skill_loader, RISKY_TOOLS
from .utils.config import Config, load_config


class AgentEvents:
    THINKING = "thinking"
    CONTENT = "content"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


class NovelAgent:
    """小说创作 Agent"""

    def __init__(
        self,
        config: Optional[Config] = None,
        working_dir: Optional[str] = None,
        confirm_callback: Optional[Callable[[str, dict], bool]] = None,
        skills_dir: Optional[Path] = None,
    ):
        if config is None:
            config = load_config()

        self.config = config
        self.working_dir = working_dir or config.working_dir

        # 将工作目录写入环境变量，供 skill 工具读取
        os.environ["NOVEL_AGENT_WORKING_DIR"] = str(self.working_dir)
        os.environ["NOVEL_AGENT_ROOT"] = str(Path(self.working_dir).resolve())
        self.confirm_callback = confirm_callback

        # 加载 Skills
        if skills_dir is None:
            skills_dir = Path(__file__).parent / "skills"
        self.skill_loader = create_skill_loader(skills_dir)

        self.api_client = APIClient(config)
        self.model_name = config.model_name

        self.system_prompt = SystemMessage(
            content=f"""你是一个小说创作助手，在工作目录 "{self.working_dir}" 下运行。

## 可用工具

{self._get_tools_description()}

## 规则

1. 所有路径必须使用绝对路径
2. 危险操作（file_write）需要用户确认"""
        )

    def _get_tools_description(self) -> str:
        """生成工具描述列表"""
        lines = []
        for tool in self.skill_loader.get_tools():
            dangerous = " [危险]" if tool.name in RISKY_TOOLS else ""
            lines.append(f"- {tool.name}: {tool.description}{dangerous}")
        return "\n".join(lines)

    def _get_tools(self):
        """获取工具列表"""
        return self.skill_loader.get_tools()

    def _invoke_model(self, messages: List, retries: int = 6) -> List:
        last_error = None
        for attempt in range(retries):
            try:
                client = self.api_client.bind_tools(self._get_tools())
                return client.stream(messages)
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    import time
                    delay = (attempt + 1) * 2
                    time.sleep(delay)
                continue
        raise last_error

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """执行工具"""
        tools = {t.name: t for t in self._get_tools()}
        if tool_name not in tools:
            return f"错误: 未知工具 '{tool_name}'"

        tool_func = tools[tool_name]

        # 危险操作确认
        if tool_name in RISKY_TOOLS:
            if not self.confirm_callback or not self.confirm_callback(tool_name, args):
                return f"[操作已取消] 用户拒绝了 {tool_name} 的执行"

        try:
            return tool_func.invoke(args)
        except Exception as e:
            return f"错误: 执行失败 - {e}"

    def stream_response(self, user_input: str, continue_from: List = None) -> Generator[dict, None, None]:
        """流式响应

        Args:
            user_input: 用户输入
            continue_from: 如果提供，在此消息历史上继续对话，而非新建
        """
        if continue_from:
            messages = continue_from + [HumanMessage(content=user_input)]
            print(f"\n[DEBUG] stream_response: 从历史继续，消息数={len(messages)}，新提示词长度={len(user_input)}")
        else:
            messages = [self.system_prompt, HumanMessage(content=user_input)]
            print(f"\n[DEBUG] stream_response: 新建对话，提示词长度={len(user_input)}")

        import time as _time
        for iteration in range(20):
            _t0 = _time.time()
            print(f"[DEBUG] --- 第 {iteration+1} 轮 开始，消息数={len(messages)} ---")
            chunks = self._invoke_model(messages)
            _t1 = _time.time()
            print(f"[DEBUG] _invoke_model 返回，耗时={_t1-_t0:.1f}s")

            text_parts = []
            tool_calls: Dict[str, dict] = {}
            tool_calls_by_index: Dict[int, str] = {}
            stop_reason = None
            block_types_seen = []
            thinking_len = 0

            for chunk in chunks:
                if hasattr(chunk, 'response_metadata'):
                    stop_reason = chunk.response_metadata.get('stop_reason')

                if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, 'id', None)
                        if tc_id and tc_id not in tool_calls:
                            tool_calls[tc_id] = {"id": tc_id, "name": "", "args": {}}
                        if tc_id and isinstance(tc, dict):
                            if tc.get("name"):
                                tool_calls[tc_id]["name"] = tc["name"]
                            if tc.get("args") and isinstance(tc["args"], dict):
                                tool_calls[tc_id]["args"].update(tc["args"])

                if hasattr(chunk, 'content') and chunk.content:
                    for block in chunk.content:
                        if not isinstance(block, dict):
                            continue

                        btype = block.get("type")
                        if btype not in block_types_seen:
                            block_types_seen.append(btype)

                        if btype == "thinking":
                            t = block.get("thinking", "")
                            if t:
                                thinking_len += len(t)
                                yield {"type": AgentEvents.THINKING, "content": t}

                        elif btype == "text":
                            t = block.get("text", "")
                            if t:
                                text_parts.append(t)
                                yield {"type": AgentEvents.CONTENT, "content": t}

                        elif btype == "tool_use":
                            idx = block.get("index")
                            tc_id = block.get("id")
                            name = block.get("name", "")
                            if tc_id:
                                tool_calls[tc_id] = {"id": tc_id, "name": name, "args": {}}
                                if idx is not None:
                                    tool_calls_by_index[idx] = tc_id

                        elif btype == "input_json_delta":
                            idx = block.get("index")
                            partial = block.get("partial_json", "")
                            if idx is not None and partial and idx in tool_calls_by_index:
                                matched_id = tool_calls_by_index[idx]
                                tool_calls[matched_id]["partial_str"] = \
                                    tool_calls[matched_id].get("partial_str", "") + partial
                                try:
                                    ps = tool_calls[matched_id]["partial_str"]
                                    if ps.startswith('{') and ps.endswith('}'):
                                        tool_calls[matched_id]["args"] = json.loads(ps)
                                except json.JSONDecodeError:
                                    pass

            if not tool_calls or stop_reason != 'tool_use':
                text_total = sum(len(p) for p in text_parts)
                _t2 = _time.time()
                print(f"[DEBUG] 循环结束: 第{iteration+1}轮, tool_calls={len(tool_calls)}, stop_reason={stop_reason}, 文本长度={text_total}, thinking长度={thinking_len}, block类型={block_types_seen}, 总耗时={_t2-_t0:.1f}s")
                break

            tool_messages = []
            for tc_id, tc in tool_calls.items():
                tool_name = tc.get("name")
                tool_args = tc.get("args", {})

                if not tool_name:
                    continue

                yield {
                    "type": AgentEvents.TOOL_CALL,
                    "name": tool_name,
                    "input": tool_args,
                }

                result = self._execute_tool(tool_name, tool_args)

                yield {
                    "type": AgentEvents.TOOL_RESULT,
                    "name": tool_name,
                    "content": result,
                }

                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=tc_id, name=tool_name)
                )

            if text_parts:
                messages.append(AIMessage(content="".join(text_parts)))

            for tc_id, tm in zip(tool_calls.keys(), tool_messages):
                tc_data = tool_calls[tc_id]
                tool_name = tc_data.get("name")
                tool_args = tc_data.get("args", {})

                messages.append(
                    AIMessage(content=[
                        {
                            "type": "tool_use",
                            "name": tool_name,
                            "input": tool_args,
                            "id": tc_id,
                        }
                    ])
                )
                messages.append(tm)

        # 保存消息历史，供 continue_stream 使用
        self._last_messages = messages

    def continue_stream(self, follow_up: str = "请继续完成任务。") -> Generator[dict, None, None]:
        """在上一次对话的基础上继续

        Args:
            follow_up: 继续的提示词
        """
        if not hasattr(self, '_last_messages') or self._last_messages is None:
            print("[DEBUG] continue_stream: 无对话历史")
            yield {"type": AgentEvents.CONTENT, "content": "[错误] 没有可继续的对话历史"}
            return
        print(f"[DEBUG] continue_stream: 历史消息数={len(self._last_messages)}")
        yield from self.stream_response(follow_up, continue_from=self._last_messages)

    def chat(self, user_input: str) -> str:
        result_parts = []
        for event in self.stream_response(user_input):
            if event["type"] == AgentEvents.CONTENT:
                result_parts.append(event["content"])
        return "".join(result_parts)


def create_agent(
    working_dir: Optional[str] = None,
    confirm_callback: Optional[Callable[[str, dict], bool]] = None,
) -> NovelAgent:
    return NovelAgent(working_dir=working_dir, confirm_callback=confirm_callback)
