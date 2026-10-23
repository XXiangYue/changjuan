"""新卷创作工作流"""

from pathlib import Path
from typing import Optional, Callable, List

from ..core import (
    WorkflowType,
    WorkflowState,
    WORKFLOW_PROMPTS,
    NewVolumePrompts,
)

from .base import BaseWorkflow


class NewVolumeWorkflow(BaseWorkflow):
    """新卷创作工作流"""

    def __init__(self, agent, output_callback: Callable[[str], None]):
        super().__init__(agent, output_callback)
        self.workflow_type = WorkflowType.NEW_VOLUME

        # 工作流数据
        self.work_dir: Optional[Path] = None
        self.novel_dir: Optional[Path] = None
        self.volume_num: int = 0
        self.volume_dir: Optional[Path] = None
        self.volume_docs: List[tuple] = []
        self.current_doc_index: int = 0
        self.has_previous_volumes: bool = False
        self.generated_content: str = ""

    def start(self, work_dir: Path, novel_dir: Path):
        """启动工作流"""
        self.work_dir = work_dir
        self.novel_dir = novel_dir

        # 列出已有卷
        volumes = []
        for d in self.novel_dir.iterdir():
            if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷"):
                chapters = [c for c in d.iterdir() if c.suffix == ".md"]
                volumes.append((d, len(chapters)))
        volumes.sort(key=lambda x: x[0].name)

        volume_list = self._format_volume_list(volumes)
        self.output(WORKFLOW_PROMPTS["new_volume_welcome"].format(volume_list=volume_list))
        self.state = WorkflowState.NEW_VOLUME_GET_NUM

    def handle(self, cmd: str) -> bool:
        """处理命令"""
        if self.state == WorkflowState.NEW_VOLUME_GET_NUM:
            return self._handle_num(cmd)
        elif self.state == WorkflowState.NEW_VOLUME_GENERATING:
            return True
        elif self.state == WorkflowState.NEW_VOLUME_CONFIRM:
            return self._handle_confirm(cmd)
        return True

    def _handle_num(self, cmd: str) -> bool:
        """处理卷编号"""
        if cmd.lower() == "q":
            self.state = WorkflowState.IDLE
            self.output("已取消。")
            return True

        try:
            volume_num = int(cmd.strip())
        except ValueError:
            self.output("请输入有效的数字，如：1、2、3...")
            return True

        self.volume_num = volume_num
        self.volume_dir = self.novel_dir / f"第{volume_num}卷"
        self.volume_dir.mkdir(parents=True, exist_ok=True)

        # 检查是否有前卷
        has_prev = False
        for d in self.novel_dir.iterdir():
            if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷") and d != self.volume_dir:
                chapters = [f for f in d.iterdir() if f.suffix == ".md"]
                if chapters:
                    has_prev = True
                    break

        self.has_previous_volumes = has_prev

        # 设置要更新的文档
        if has_prev:
            self.volume_docs = [
                ("前卷剧情概述", "workspace/剧情/前卷剧情概述.md"),
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]
        else:
            self.volume_docs = [
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]

        self.current_doc_index = 0

        docs_list = self._format_docs_list(self.volume_docs)
        self.output(WORKFLOW_PROMPTS["new_volume_start"].format(
            volume_num=volume_num,
            volume_dir=self.volume_dir.name,
            docs_list=docs_list,
        ))

        return self._generate_current()

    def _generate_current(self) -> bool:
        """生成当前文档"""
        doc_name, doc_path = self.volume_docs[self.current_doc_index]
        full_prompt = self._build_prompt(doc_name, doc_path)

        self.output(f"""
************************************************************
文档 {self.current_doc_index + 1}/{len(self.volume_docs)}: {doc_name}
************************************************************

[正在生成内容，请稍候...]

""")

        self.state = WorkflowState.NEW_VOLUME_GENERATING
        self.generated_content = ""

        self.generated_content = self._stream_generate(full_prompt)

        # 保存进度
        self.save_progress(
            step_index=self.current_doc_index,
            generated_content=self.generated_content,
            data={
                "volume_num": self.volume_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "has_previous_volumes": self.has_previous_volumes,
            }
        )

        self.output(WORKFLOW_PROMPTS["new_volume_confirm"].format(doc_name=doc_name))
        self.state = WorkflowState.NEW_VOLUME_CONFIRM
        return True

    def _handle_confirm(self, cmd: str) -> bool:
        """处理确认"""
        cmd_lower = cmd.lower()
        doc_name, doc_path = self.volume_docs[self.current_doc_index]

        if cmd_lower == "q":
            self._finish()
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._generate_current()

        if cmd_lower == "continue":
            self.output("[继续生成中...]")
            self.state = WorkflowState.NEW_VOLUME_GENERATING
            self.generated_content = self._continue_generate(
                "请继续完成任务，使用 file_write 将文档内容写入对应文件路径。"
            )
            self.output(WORKFLOW_PROMPTS["new_volume_confirm"].format(doc_name=doc_name))
            self.state = WorkflowState.NEW_VOLUME_CONFIRM
            return True

        if cmd_lower == "skip":
            self.output("[已跳过]")
        elif self.generated_content:
            self.output(f"[已确认] {doc_name}（已通过 file_write 保存）")

        # 保存进度
        self.save_progress(
            step_index=self.current_doc_index,
            generated_content="",
            data={
                "volume_num": self.volume_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "has_previous_volumes": self.has_previous_volumes,
            }
        )

        # 下一个文档
        self.current_doc_index += 1
        if self.current_doc_index >= len(self.volume_docs):
            self._finish()
        else:
            return self._generate_current()

        return True

    def _build_prompt(self, doc_name: str, doc_path: str) -> str:
        """构建提示词"""
        all_docs = self._read_all_memory_docs(self.work_dir)
        template_content = self._read_doc(doc_path, self.work_dir)

        # 获取下一卷剧情内容（用于本卷核心剧情的参考）
        next_plot_content = all_docs.get("workspace/剧情/下一卷剧情.md", "")

        # 获取前卷核心剧情内容（用于前卷剧情概述的参考）
        # 需要找到前卷的目录并读取其核心剧情
        prev_plot_content = ""
        if self.volume_num > 1:
            prev_volume_dir = self.novel_dir / f"第{self.volume_num - 1}卷"
            if prev_volume_dir.exists():
                # 尝试从前卷剧情概述获取前卷内容
                prev_summary = all_docs.get("workspace/剧情/前卷剧情概述.md", "")
                # 实际应该从工作流中获取已更新的前卷核心剧情
                # 这里暂时使用记忆文档中的内容
                prev_plot_content = all_docs.get("workspace/剧情/本卷核心剧情.md", "")

        return NewVolumePrompts.build_new_volume_node_context(
            doc_name=doc_name,
            doc_path=doc_path,
            volume_num=self.volume_num,
            all_docs=all_docs,
            template_content=template_content,
            next_plot_content=next_plot_content,
            prev_plot_content=prev_plot_content,
        )

    def _finish(self):
        """完成工作流"""
        self.state = WorkflowState.READY

        if self.has_previous_volumes:
            docs_msg = "  1. 前卷剧情概述\n  2. 本卷核心剧情\n  3. 下一卷剧情"
        else:
            docs_msg = "  1. 本卷核心剧情\n  2. 下一卷剧情"

        self.output(WORKFLOW_PROMPTS["new_volume_finish"].format(
            volume_num=self.volume_num,
            updated_docs=docs_msg,
            volume_dir=self.volume_dir.name,
        ))

    def reset(self):
        """重置工作流"""
        self.__init__(self.agent, self.output_callback)

    def restore(self, progress):
        """恢复工作流"""
        self.state = WorkflowState(progress.state) if progress.state else WorkflowState.IDLE
        self.volume_num = progress.data.get("volume_num", 0)
        self.current_doc_index = progress.step_index
        self.generated_content = progress.generated_content
        self.work_dir = Path(progress.data.get("work_dir", ""))
        self.novel_dir = Path(progress.data.get("novel_dir", ""))
        self.volume_dir = self.novel_dir / f"第{self.volume_num}卷" if self.volume_num else None
        self.has_previous_volumes = progress.data.get("has_previous_volumes", False)

        if self.has_previous_volumes:
            self.volume_docs = [
                ("前卷剧情概述", "workspace/剧情/前卷剧情概述.md"),
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]
        else:
            self.volume_docs = [
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]

    def continue_from_saved(self):
        """从保存的进度继续"""
        if self.state == WorkflowState.NEW_VOLUME_CONFIRM and self.generated_content:
            doc_name = self.volume_docs[self.current_doc_index][0]
            self.output(WORKFLOW_PROMPTS["new_volume_confirm"].format(doc_name=doc_name))
        elif self.current_doc_index < len(self.volume_docs):
            self._generate_current()
