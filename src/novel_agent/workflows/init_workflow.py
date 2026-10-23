"""初始化工作流"""

from pathlib import Path
from typing import Optional, Callable, List

from ..core import (
    WorkflowType,
    WorkflowState,
    AgentEvents,
    WORKFLOW_PROMPTS,
    InitDoc,
    InitPrompts,
    INIT_DOCS_CONFIG,
)

from .base import BaseWorkflow


class InitWorkflow(BaseWorkflow):
    """初始化工作流"""

    def __init__(self, agent, output_callback: Callable[[str], None]):
        super().__init__(agent, output_callback)
        self.workflow_type = WorkflowType.INIT

        # 工作流数据
        self.work_dir: Optional[Path] = None
        self.novel_dir: Optional[Path] = None
        self.workspace_dir: Optional[Path] = None
        self.user_prompt: str = ""
        self.docs: List[InitDoc] = []
        self.current_doc_index: int = 0
        self.docs_written: List[str] = []
        self.generated_content: str = ""

    def start(self):
        """启动工作流"""
        self.state = WorkflowState.INIT_GET_DIR
        self.output(WORKFLOW_PROMPTS["init_welcome"])

    def handle(self, cmd: str) -> bool:
        """处理命令"""
        if self.state == WorkflowState.INIT_GET_DIR:
            return self._handle_dir(cmd)
        elif self.state == WorkflowState.INIT_GET_PROMPT:
            return self._handle_prompt(cmd)
        elif self.state == WorkflowState.INIT_GENERATING:
            return True  # 生成中不处理
        elif self.state == WorkflowState.INIT_CONFIRM:
            return self._handle_confirm(cmd)
        return True

    def _handle_dir(self, cmd: str) -> bool:
        """处理目录输入"""
        if cmd.lower() == "q":
            self.state = WorkflowState.IDLE
            self.output("已取消。")
            return True

        work_dir = Path(cmd).resolve()
        if not work_dir.exists():
            self.output(f"目录不存在: {work_dir}\n请重新输入：")
            return True

        self.work_dir = work_dir
        self.workspace_dir = work_dir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.novel_dir = work_dir / "小说本体"
        self.novel_dir.mkdir(parents=True, exist_ok=True)

        # 创建目录结构
        self._create_memory_doc_templates(self.work_dir)

        self.output(WORKFLOW_PROMPTS["init_dir_success"].format(
            work_dir=self.work_dir,
            novel_dir=self.novel_dir.name,
            workspace_dir=self.workspace_dir,
        ))
        self.state = WorkflowState.INIT_GET_PROMPT
        return True

    def _handle_prompt(self, cmd: str) -> bool:
        """处理提示词输入"""
        if cmd.lower() == "q":
            self.state = WorkflowState.IDLE
            self.output("已取消。")
            return True

        self.user_prompt = cmd

        # 初始化文档列表
        self.docs = [
            InitDoc(name=name, path=path, prompt=prompt)
            for name, path, prompt in INIT_DOCS_CONFIG
        ]
        self.current_doc_index = 0
        self.docs_written = []

        # 显示文档列表
        docs_list = "\n".join(f"  {i+1}. {doc.name}" for i, doc in enumerate(self.docs))
        self.output(WORKFLOW_PROMPTS["init_docs_list"].format(docs_list=docs_list))

        # 开始生成第一个文档
        return self._generate_current()

    def _generate_current(self) -> bool:
        """生成当前文档"""
        doc = self.docs[self.current_doc_index]
        full_prompt = self._build_prompt(doc)

        self.output(WORKFLOW_PROMPTS["init_doc_generating"].format(
            index=self.current_doc_index + 1,
            total=len(self.docs),
            doc_name=doc.name,
        ))

        self.state = WorkflowState.INIT_GENERATING
        self.generated_content = ""

        self.generated_content = self._stream_generate(full_prompt)
        doc.content = self.generated_content
        doc.status = "已生成"

        # 保存生成的内容进度
        self.save_progress(
            step_index=self.current_doc_index,
            generated_content=self.generated_content,
            data={
                "user_prompt": self.user_prompt,
                "docs_written": self.docs_written,
                "work_dir": str(self.work_dir) if self.work_dir else "",
            }
        )

        self.output(WORKFLOW_PROMPTS["init_confirm"].format(doc_name=doc.name))
        self.state = WorkflowState.INIT_CONFIRM
        return True

    def _handle_confirm(self, cmd: str) -> bool:
        """处理确认"""
        cmd_lower = cmd.lower()
        doc = self.docs[self.current_doc_index]

        if cmd_lower == "q":
            self._finish()
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._generate_current()

        if cmd_lower == "continue":
            self.output("[继续生成中...]")
            self.state = WorkflowState.INIT_GENERATING
            self.generated_content = self._continue_generate(
                "请继续完成任务，使用 file_write 将文档内容写入对应文件路径。"
            )
            doc.content = self.generated_content
            self.output(WORKFLOW_PROMPTS["init_confirm"].format(doc_name=doc.name))
            self.state = WorkflowState.INIT_CONFIRM
            return True

        if cmd_lower == "skip":
            self.output("[已跳过]")
        elif doc.content:
            self.docs_written.append(doc.name)
            self.output(f"[已确认] {doc.name}（已通过 file_write 保存）")

        # 保存进度（文档已确认）
        self.save_progress(
            step_index=self.current_doc_index,
            generated_content="",
            data={
                "user_prompt": self.user_prompt,
                "docs_written": self.docs_written,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "workspace_dir": str(self.workspace_dir),
            }
        )

        # 下一个文档
        self.current_doc_index += 1
        if self.current_doc_index >= len(self.docs):
            self._finish()
        else:
            return self._generate_current()

        return True

    def _build_prompt(self, doc) -> str:
        """构建提示词"""
        template_content = self._read_doc(doc.path, self.work_dir)

        # 收集已生成文档
        generated_docs = []
        for i, d in enumerate(self.docs[:self.current_doc_index]):
            if d.content:
                generated_docs.append({"name": d.name, "content": d.content})

        return InitPrompts.build_init_doc_prompt(
            doc_index=self.current_doc_index,
            total_docs=len(self.docs),
            doc_name=doc.name,
            doc_path=doc.path,
            doc_prompt=doc.prompt,
            template_content=template_content,
            user_prompt=self.user_prompt,
            generated_docs=generated_docs,
            work_dir=str(self.work_dir),
        )

    def _finish(self):
        """完成工作流"""
        self.state = WorkflowState.READY

        written = "\n".join(f"  ✓ {name}" for name in self.docs_written)
        skipped = "\n".join(f"  - {doc.name}" for doc in self.docs if doc.status == "已跳过")

        self.output(WORKFLOW_PROMPTS["init_finish"].format(
            written_docs=written,
            skipped_docs=f"\n\n已跳过文档：\n{skipped}" if skipped else "",
        ))

    def reset(self):
        """重置工作流"""
        self.__init__(self.agent, self.output_callback)

    def restore(self, progress):
        """恢复工作流"""
        self.state = WorkflowState(progress.state) if progress.state else WorkflowState.IDLE
        self.current_doc_index = progress.step_index
        self.generated_content = progress.generated_content
        self.user_prompt = progress.data.get("user_prompt", "")

        # 恢复文档列表
        self.docs = [
            InitDoc(name=name, path=path, prompt=prompt)
            for name, path, prompt in INIT_DOCS_CONFIG
        ]
        self.docs_written = progress.data.get("docs_written", [])
        for i in range(self.current_doc_index):
            if i < len(self.docs) and self.docs[i].name in self.docs_written:
                self.docs[i].status = "已保存"
                self.docs[i].content = progress.data.get(f"doc_{i}_content", "")

        # 设置工作目录
        if progress.data.get("work_dir"):
            self.work_dir = Path(progress.data["work_dir"])
            self.workspace_dir = self.work_dir / "workspace"
            self.novel_dir = self.work_dir / "小说本体"

    def continue_from_saved(self):
        """从保存的进度继续"""
        if self.state == WorkflowState.INIT_CONFIRM and self.generated_content:
            # 上一步刚生成完文档，等待确认
            self.output(WORKFLOW_PROMPTS["init_confirm"].format(
                doc_name=self.docs[self.current_doc_index].name
            ))
        elif self.current_doc_index < len(self.docs):
            # 继续生成下一个文档
            self._generate_current()
