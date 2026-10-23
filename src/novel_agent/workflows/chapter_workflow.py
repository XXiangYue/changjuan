"""章节创作工作流"""

from pathlib import Path
from typing import Optional, Callable, List

from ..core import (
    WorkflowType,
    WorkflowState,
    WORKFLOW_PROMPTS,
    ChapterPrompts,
)

from .base import BaseWorkflow
from .reviewer import ChapterReviewer


class ChapterWorkflow(BaseWorkflow):
    """章节创作工作流"""

    def __init__(self, agent, output_callback: Callable[[str], None]):
        super().__init__(agent, output_callback)
        self.workflow_type = WorkflowType.CHAPTER

        # 工作流数据
        self.work_dir: Optional[Path] = None
        self.novel_dir: Optional[Path] = None
        self.volume_num: int = 0
        self.volume_dir: Optional[Path] = None
        self.chapter_num: int = 0
        self.chapter_file: Optional[Path] = None
        self.next_chapter_num: int = 1
        self.memory_docs: List[tuple] = []
        self.current_doc_index: int = 0
        self.generated_content: str = ""

        # 审核器
        self.reviewer = ChapterReviewer(agent, output_callback)
        self.last_review_result: Optional[dict] = None

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
        self.output(WORKFLOW_PROMPTS["chapter_welcome"].format(volume_list=volume_list))
        self.state = WorkflowState.CHAPTER_GET_VOLUME

    def handle(self, cmd: str) -> bool:
        """处理命令"""
        if self.state == WorkflowState.CHAPTER_GET_VOLUME:
            return self._handle_volume(cmd)
        elif self.state == WorkflowState.CHAPTER_GET_NUM:
            return self._handle_num(cmd)
        elif self.state == WorkflowState.CHAPTER_GENERATING:
            return True
        elif self.state == WorkflowState.CHAPTER_CONFIRM:
            return self._handle_confirm(cmd)
        elif self.state == WorkflowState.CHAPTER_REVIEWING:
            return True  # 审核中不处理
        elif self.state == WorkflowState.CHAPTER_REVIEW_CONFIRM:
            return self._handle_review_confirm(cmd)
        elif self.state == WorkflowState.CHAPTER_REVISING:
            return True  # 修订中不处理
        elif self.state == WorkflowState.CHAPTER_UPDATE_DOC:
            return True
        elif self.state == WorkflowState.CHAPTER_UPDATE_CONFIRM:
            return self._handle_update_confirm(cmd)
        return True

    def _handle_volume(self, cmd: str) -> bool:
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

        volume_dir = self.novel_dir / f"第{volume_num}卷"
        if not volume_dir.exists():
            self.output(f"卷文件夹不存在：第{volume_num}卷\n请先使用 /new_volume 创建")
            self.state = WorkflowState.IDLE
            return True

        self.volume_num = volume_num
        self.volume_dir = volume_dir

        # 计算下一章编号
        existing_chapters = [f for f in volume_dir.iterdir() if f.suffix == ".md"]
        self.next_chapter_num = len(existing_chapters) + 1

        self.output(WORKFLOW_PROMPTS["chapter_select_num"].format(
            volume_num=volume_num,
            chapter_count=len(existing_chapters),
            next_chapter=self.next_chapter_num,
        ))
        self.state = WorkflowState.CHAPTER_GET_NUM
        return True

    def _handle_num(self, cmd: str) -> bool:
        """处理章节编号"""
        if cmd.lower() == "q":
            self.state = WorkflowState.IDLE
            self.output("已取消。")
            return True

        if cmd.strip() == "":
            chapter_num = self.next_chapter_num
        else:
            try:
                chapter_num = int(cmd.strip())
            except ValueError:
                self.output("请输入有效的数字")
                return True

        self.chapter_num = chapter_num

        self.chapter_file = None

        self.output(WORKFLOW_PROMPTS["chapter_preparing"].format(
            volume_num=self.volume_num,
            chapter_num=chapter_num,
            chapter_file=f"第{chapter_num}章 - *.md",
        ))

        # 开始生成章节内容
        return self._generate_chapter_content()

    def _detect_chapter_file(self):
        """检测模型通过 file_write 创建的章节文件"""
        if not self.volume_dir:
            return
        matches = sorted(self.volume_dir.glob(f"第{self.chapter_num}章*.md"))
        if matches:
            self.chapter_file = matches[-1]
            self.output(f"\n[检测到章节文件: {self.chapter_file.name}]")

    def _generate_chapter_content(self) -> bool:
        """生成章节内容"""
        all_docs = self._read_all_memory_docs(self.work_dir)
        chapter_plan = all_docs.get("workspace/剧情/当前章节设计规划.md", "")

        # 自动读取近3章内容作为上下文
        recent_chapters = self._read_recent_chapters(
            self.novel_dir,
            self.volume_num,
            self.chapter_num,
            limit=3
        )
        recent_chapters_context = self._format_recent_chapters_context(recent_chapters)

        full_prompt = ChapterPrompts.build_chapter_prompt(
            volume_num=self.volume_num,
            chapter_num=self.chapter_num,
            chapter_plan=chapter_plan,
            recent_chapters_context=recent_chapters_context,
            all_docs=all_docs,
            save_dir=str(self.volume_dir),
        )

        self.output(WORKFLOW_PROMPTS["chapter_generating"].format(
            volume_num=self.volume_num,
            chapter_num=self.chapter_num,
        ))

        self.state = WorkflowState.CHAPTER_GENERATING
        self.generated_content = ""

        self.generated_content = self._stream_generate(full_prompt)

        # 检测模型通过 file_write 实际创建的章节文件
        self._detect_chapter_file()

        # 保存章节生成进度
        self.save_progress(
            step_index=0,
            generated_content=self.generated_content,
            data={
                "volume_num": self.volume_num,
                "chapter_num": self.chapter_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
            }
        )

        self.output(WORKFLOW_PROMPTS["chapter_confirm"].format(
            volume_num=self.volume_num,
            chapter_num=self.chapter_num,
        ))
        self.state = WorkflowState.CHAPTER_CONFIRM
        return True

    def _continue_chapter_generation(self) -> bool:
        """继续章节生成（保留对话上下文，让模型调用 file_write）"""
        self.state = WorkflowState.CHAPTER_GENERATING

        self.generated_content = self._continue_generate(
            "请继续完成章节创作任务，使用 file_write 将章节内容写入对应目录。"
        )

        self._detect_chapter_file()

        self.output(WORKFLOW_PROMPTS["chapter_confirm"].format(
            volume_num=self.volume_num,
            chapter_num=self.chapter_num,
        ))
        self.state = WorkflowState.CHAPTER_CONFIRM
        return True

    def _handle_confirm(self, cmd: str) -> bool:
        """处理章节确认"""
        cmd_lower = cmd.lower()

        if cmd_lower == "q":
            self.state = WorkflowState.IDLE
            self.output("已取消章节创作。")
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._generate_chapter_content()

        if cmd_lower == "continue":
            self.output("[继续生成中...]")
            return self._continue_chapter_generation()

        # 章节内容由模型通过 file_write 工具已保存
        if cmd_lower == "skip":
            self.output("[已跳过保存]")
        elif self.chapter_file and self.chapter_file.exists():
            self.output(f"[已确认] {self.chapter_file.name}（已通过 file_write 保存）")
        elif self.generated_content:
            self.output("[已确认]（已通过 file_write 保存）")

        # 重置审核器
        self.reviewer.reset()

        # 开始审核
        # 保存进度
        self.save_progress(
            step_index=0,
            generated_content=self.generated_content,
            data={
                "volume_num": self.volume_num,
                "chapter_num": self.chapter_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "last_review_result": self.last_review_result,
            }
        )
        return self._do_review()

    def _do_review(self) -> bool:
        """执行审核"""
        # 从文件读取章节内容（模型通过 file_write 已保存）
        chapter_content = ""
        if self.chapter_file and self.chapter_file.exists():
            chapter_content = self.chapter_file.read_text(encoding="utf-8")
        if not chapter_content:
            chapter_content = self.generated_content

        if not chapter_content:
            self.output("[警告] 章节内容为空，无法审核，跳过审核步骤")
            self.last_review_result = {"passed": True}
            self._show_review_confirm_prompt()
            return True

        # 构建记忆文档上下文
        all_docs = self._read_all_memory_docs(self.work_dir)
        memory_context = self._format_memory_context(all_docs)

        # 执行审核
        self.last_review_result = self.reviewer.review(
            chapter_content,
            memory_context,
        )

        # 显示审核后操作提示
        self._show_review_confirm_prompt()

        return True

    def _show_review_confirm_prompt(self):
        """显示审核确认提示"""
        passed = self.last_review_result.get("passed", False)
        revision_count = self.reviewer.revision_count
        max_rounds = ChapterPrompts.MAX_REVISION_ROUNDS

        if passed:
            self.output(f"""
============================================================
[审核通过] 第 {self.volume_num} 卷 第 {self.chapter_num} 章
============================================================

下一步操作：
  - 输入任意内容：继续更新记忆文档
  - 输入 revise：根据审核建议修改章节
  - 输入 q：退出章节创作

""")
        else:
            can_revise = revision_count < max_rounds
            max_notice = "  [已达到最大返工轮次，继续更新记忆文档]\n" if not can_revise else ""
            revise_tag = "（推荐）" if can_revise else ""
            self.output(f"""
============================================================
[审核未通过] 第 {self.volume_num} 卷 第 {self.chapter_num} 章
============================================================

当前返工轮次: {revision_count}/{max_rounds}

下一步操作：
{max_notice}  - 输入任意内容：继续更新记忆文档（跳过审核）
  - 输入 redo：重新生成章节内容
  - 输入 revise{revise_tag}：根据审核建议修改章节
  - 输入 q：退出章节创作

""")

        self.state = WorkflowState.CHAPTER_REVIEW_CONFIRM

    def _handle_review_confirm(self, cmd: str) -> bool:
        """处理审核确认"""
        cmd_lower = cmd.lower()
        passed = self.last_review_result.get("passed", False)

        if cmd_lower == "q":
            self.state = WorkflowState.IDLE
            self.output("已退出章节创作。")
            return True

        if cmd_lower == "redo" and not passed:
            self.output("[重新生成中...]")
            return self._generate_chapter_content()

        if cmd_lower == "revise":
            return self._do_revise()

        # 继续更新记忆文档
        # 保存当前进度
        self.save_progress(
            step_index=0,
            generated_content=self.generated_content,
            data={
                "volume_num": self.volume_num,
                "chapter_num": self.chapter_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "last_review_result": self.last_review_result,
            }
        )
        return self._start_update_memory_docs()

    def _do_revise(self) -> bool:
        """根据审核建议修订章节内容"""
        if not self.last_review_result:
            self.output("[警告] 无审核结果，无法修订")
            self._show_review_confirm_prompt()
            return True

        if not self.chapter_file or not self.chapter_file.exists():
            self.output("[警告] 章节文件不存在，无法修订")
            self._show_review_confirm_prompt()
            return True

        # 读取所有记忆文档
        all_docs = self._read_all_memory_docs(self.work_dir)

        # 构建修订提示词（不嵌入章节内容，模型通过 file_read 自行读取）
        full_prompt = ChapterPrompts.build_revision_prompt(
            chapter_path=str(self.chapter_file),
            review_result=self.last_review_result,
            all_docs=all_docs,
        )

        self.output(f"""
============================================================
  根据审核建议修订章节
============================================================

当前章节: 第 {self.volume_num} 卷 第 {self.chapter_num} 章
返工轮次: {self.reviewer.revision_count}/{ChapterPrompts.MAX_REVISION_ROUNDS}

[正在根据审核建议修改章节...]

""")

        self.state = WorkflowState.CHAPTER_REVISING

        # 流式生成修订内容
        self._stream_generate(full_prompt)

        # 重新检测章节文件
        self._detect_chapter_file()

        self.output(f"""
[章节修订完成]

""")

        # 重新进入审核
        return self._do_review()

    def _format_memory_context(self, all_docs: dict) -> str:
        """格式化记忆文档上下文"""
        context = """
[世界观.md]
{worldview}

[主角形象状态.md]
{character}

[本卷核心剧情.md]
{plot}

[下一卷剧情.md]
{next_plot}

[前卷剧情概述.md]
{prev_summary}

[伏笔.md]
{foreshadow}

[有关角色.md]
{characters}

[事件.md]
{events}

[当前章节设计规划.md]
{chapter_plan}
""".format(
            worldview=all_docs.get('workspace/世界观.md', '(无)'),
            character=all_docs.get('workspace/主角形象状态.md', '(无)'),
            plot=all_docs.get('workspace/剧情/本卷核心剧情.md', '(无)'),
            next_plot=all_docs.get('workspace/剧情/下一卷剧情.md', '(无)'),
            prev_summary=all_docs.get('workspace/剧情/前卷剧情概述.md', '(无)'),
            foreshadow=all_docs.get('workspace/剧情/伏笔.md', '(无)'),
            characters=all_docs.get('workspace/剧情/有关角色.md', '(无)'),
            events=all_docs.get('workspace/剧情/事件.md', '(无)'),
            chapter_plan=all_docs.get('workspace/剧情/当前章节设计规划.md', '(无)'),
        )
        return context.strip()

    def _start_update_memory_docs(self) -> bool:
        """开始更新记忆文档"""
        self.memory_docs = ChapterPrompts.MEMORY_DOCS_ORDER.copy()
        self.current_doc_index = 0

        docs_list = self._format_docs_list(self.memory_docs)
        self.output(WORKFLOW_PROMPTS["chapter_update_welcome"].format(docs_list=docs_list))

        return self._generate_memory_doc()

    def _generate_memory_doc(self) -> bool:
        """生成记忆文档更新"""
        doc_name, doc_path = self.memory_docs[self.current_doc_index]
        full_prompt = self._build_memory_doc_prompt(doc_name, doc_path)

        self.output(f"""
************************************************************
  更新文档 {self.current_doc_index + 1}/{len(self.memory_docs)}: {doc_name}
************************************************************

[正在生成更新内容，请稍候...]

""")

        self.state = WorkflowState.CHAPTER_UPDATE_DOC
        self.generated_content = ""

        self.generated_content = self._stream_generate(full_prompt)

        # 保存记忆文档生成进度
        self.save_progress(
            step_index=self.current_doc_index,
            generated_content=self.generated_content,
            data={
                "volume_num": self.volume_num,
                "chapter_num": self.chapter_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "last_review_result": self.last_review_result,
            }
        )

        self.output(WORKFLOW_PROMPTS["chapter_update_confirm"].format(doc_name=doc_name))
        self.state = WorkflowState.CHAPTER_UPDATE_CONFIRM
        return True

    def _handle_update_confirm(self, cmd: str) -> bool:
        """处理记忆文档确认"""
        cmd_lower = cmd.lower()
        doc_name, doc_path = self.memory_docs[self.current_doc_index]

        if cmd_lower == "q":
            self.state = WorkflowState.IDLE
            self._finish()
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._generate_memory_doc()

        if cmd_lower == "continue":
            self.output("[继续生成中...]")
            self.state = WorkflowState.CHAPTER_UPDATE_DOC
            self.generated_content = self._continue_generate(
                "请继续完成任务，使用 file_write 将更新后的文档内容写入对应文件路径。"
            )
            self.output(WORKFLOW_PROMPTS["chapter_update_confirm"].format(doc_name=doc_name))
            self.state = WorkflowState.CHAPTER_UPDATE_CONFIRM
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
                "chapter_num": self.chapter_num,
                "work_dir": str(self.work_dir),
                "novel_dir": str(self.novel_dir),
                "last_review_result": self.last_review_result,
            }
        )

        # 下一个文档
        self.current_doc_index += 1
        if self.current_doc_index >= len(self.memory_docs):
            self.state = WorkflowState.READY
            self._finish()
        else:
            return self._generate_memory_doc()

        return True

    def _build_memory_doc_prompt(self, doc_name: str, doc_path: str) -> str:
        """构建记忆文档更新提示词"""
        chapter_content = ""
        if self.chapter_file and self.chapter_file.exists():
            chapter_content = self.chapter_file.read_text(encoding="utf-8")
        all_docs = self._read_all_memory_docs(self.work_dir)

        return ChapterPrompts.build_memory_doc_prompt(
            doc_name=doc_name,
            doc_path=doc_path,
            chapter_content=chapter_content,
            all_docs=all_docs,
        )

    def _finish(self):
        """完成工作流"""
        self.output(WORKFLOW_PROMPTS["chapter_finish"].format(
            volume_num=self.volume_num,
            chapter_num=self.chapter_num,
            chapter_file=self.chapter_file.name if self.chapter_file else "",
        ))

    def reset(self):
        """重置工作流"""
        self.__init__(self.agent, self.output_callback)

    def restore(self, progress):
        """恢复工作流"""
        self.state = WorkflowState(progress.state) if progress.state else WorkflowState.IDLE
        self.volume_num = progress.data.get("volume_num", 0)
        self.chapter_num = progress.data.get("chapter_num", 0)
        self.current_doc_index = progress.step_index
        self.generated_content = progress.generated_content
        self.work_dir = Path(progress.data.get("work_dir", ""))
        self.novel_dir = Path(progress.data.get("novel_dir", ""))
        self.volume_dir = self.novel_dir / f"第{self.volume_num}卷" if self.volume_num else None
        if self.volume_dir and self.chapter_num:
            matches = sorted(self.volume_dir.glob(f"第{self.chapter_num}章*.md"))
            self.chapter_file = matches[-1] if matches else None
        else:
            self.chapter_file = None
        self.memory_docs = ChapterPrompts.MEMORY_DOCS_ORDER.copy()
        self.last_review_result = progress.data.get("last_review_result")

    def continue_from_saved(self):
        """从保存的进度继续"""
        if self.state == WorkflowState.CHAPTER_UPDATE_CONFIRM and self.generated_content:
            doc_name, doc_path = self.memory_docs[self.current_doc_index]
            self.output(WORKFLOW_PROMPTS["chapter_update_confirm"].format(doc_name=doc_name))
        elif self.state == WorkflowState.CHAPTER_REVIEW_CONFIRM:
            self._show_review_confirm_prompt()
        elif self.current_doc_index < len(self.memory_docs):
            self._generate_memory_doc()
