"""工作流基类"""

import re
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any

from ..core import (
    WorkflowType,
    WorkflowState,
    AgentEvents,
    MEMORY_DOC_TEMPLATES,
    MEMORY_DOC_FILES,
    WorkflowProgress,
)


class BaseWorkflow:
    """工作流基类"""

    def __init__(self, agent, output_callback: Callable[[str], None]):
        self.agent = agent
        self.output_callback = output_callback
        self.state = WorkflowState.IDLE
        self.workflow_type = WorkflowType.IDLE
        self.persistence = None  # 稍后设置
        self.work_dir = None

    def output(self, text: str):
        """输出文本"""
        self.output_callback(text)

    def set_persistence(self, persistence):
        """设置持久化管理器"""
        self.persistence = persistence

    def save_progress(self, step_index: int = 0, generated_content: str = "", data: dict = None):
        """保存当前进度"""
        if self.persistence:
            progress = WorkflowProgress(
                workflow_type=self.workflow_type.value,
                state=self.state.value,
                step_index=step_index,
                generated_content=generated_content,
                data=data or {}
            )
            self.persistence.save(progress)

    def _read_doc(self, path: str, work_dir: Path) -> str:
        """读取文档"""
        fpath = work_dir / path
        if fpath.exists():
            return fpath.read_text(encoding="utf-8")
        return ""

    def _write_doc(self, path: str, content: str, work_dir: Path):
        """写入文档"""
        fpath = work_dir / path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content.strip(), encoding="utf-8")

    def _read_all_memory_docs(self, work_dir: Path) -> Dict[str, str]:
        """读取所有记忆文档"""
        return {f: self._read_doc(f, work_dir) for f in MEMORY_DOC_FILES}

    def _read_recent_chapters(
        self,
        novel_dir: Path,
        current_volume_num: int,
        current_chapter_num: int,
        limit: int = 10
    ) -> List[tuple]:
        """
        读取近 limit 章的章节内容

        Args:
            novel_dir: 小说本体目录
            current_volume_num: 当前卷号
            current_chapter_num: 当前章节号
            limit: 读取章节数量，默认10

        Returns:
            [(章节标题, 章节内容), ...] 按时间顺序（最早在前）
        """
        candidates = []
        for path in novel_dir.glob("第*卷/第*章*.md"):
            volume = re.fullmatch(r"第(\d+)卷", path.parent.name)
            chapter = re.match(r"第(\d+)章", path.stem)
            if not volume or not chapter:
                continue
            position = (int(volume[1]), int(chapter[1]))
            if position < (current_volume_num, current_chapter_num):
                candidates.append((position, path))
        candidates.sort(key=lambda item: item[0])
        return [(path.stem, path.read_text(encoding="utf-8"))
                for _, path in candidates[-limit:]] if limit > 0 else []

    def _format_recent_chapters_context(self, chapters: List[tuple]) -> str:
        """
        格式化近3章内容为上下文字符串

        Args:
            chapters: [(章节标题, 章节内容), ...]

        Returns:
            格式化的上下文字符串
        """
        if not chapters:
            return "（暂无已完成章节）"

        result = []
        for title, content in chapters:
            result.append(f"【{title}】\n{content}")

        return "\n\n".join(result)

    def _create_memory_doc_templates(self, work_dir: Path):
        """创建记忆文档模板"""
        for path, content in MEMORY_DOC_TEMPLATES.items():
            fpath = work_dir / path
            if not fpath.exists():
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(content, encoding="utf-8")

    def _stream_generate(self, prompt: str):
        """流式生成内容"""
        content_parts = []
        thinking_shown = False
        for event in self.agent.stream_response(prompt):
            if event.get("type") == AgentEvents.THINKING:
                if not thinking_shown:
                    self.output("\n[模型思考中...]")
                    thinking_shown = True
            elif event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                inp = event.get("input", {})
                if name == "file_read":
                    path = inp.get("path", inp.get("file_path", ""))
                    self.output(f"\n[调用工具: {name}  {path}]")
                else:
                    self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                result = str(event.get("content", ""))
                if name == "file_read":
                    path = result.split("\n")[0][:80] if result else ""
                    self.output(f"\n[{name} 完成]")
                else:
                    snippet = result[:200].replace("\n", " ") if result else ""
                    self.output(f"\n[{name} 完成]: {snippet}")

        return "".join(content_parts)

    def _continue_generate(self, prompt: str = "请继续完成任务。"):
        """继续之前的生成（保留对话上下文）"""
        print(f"[DEBUG] _continue_generate: 开始，prompt={prompt[:80]}...")
        content_parts = []
        thinking_shown = False
        for event in self.agent.continue_stream(prompt):
            if event.get("type") == AgentEvents.THINKING:
                if not thinking_shown:
                    self.output("\n[模型思考中...]")
                    thinking_shown = True
            elif event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                inp = event.get("input", {})
                if name == "file_read":
                    path = inp.get("path", inp.get("file_path", ""))
                    self.output(f"\n[调用工具: {name}  {path}]")
                else:
                    self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                result = str(event.get("content", ""))
                if name == "file_read":
                    self.output(f"\n[{name} 完成]")
                else:
                    snippet = result[:200].replace("\n", " ") if result else ""
                    self.output(f"\n[{name} 完成]: {snippet}")

        result = "".join(content_parts)
        print(f"[DEBUG] _continue_generate: 结束，返回文本长度={len(result)}")
        return result

    def _format_docs_list(self, docs: List[tuple]) -> str:
        """格式化文档列表"""
        return "\n".join(f"  {i+1}. {name}" for i, (name, _) in enumerate(docs))

    def _format_volume_list(self, volumes: List[tuple]) -> str:
        """格式化卷列表"""
        if not volumes:
            return "  （暂无卷）"
        return "\n".join(f"  {v.name} - {c} 章" for v, c in volumes)

    def is_idle(self) -> bool:
        return self.state == WorkflowState.IDLE

    def is_ready(self) -> bool:
        return self.state == WorkflowState.READY

    def restore(self, progress: WorkflowProgress):
        """恢复进度（子类实现）"""
        pass

    def continue_from_saved(self):
        """从保存的进度继续（子类实现）"""
        pass
