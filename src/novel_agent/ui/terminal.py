"""终端交互界面"""

import os
from pathlib import Path
from typing import Optional

from prompt_toolkit import prompt
from prompt_toolkit.history import InMemoryHistory

from ..agent import NovelAgent, AgentEvents
from ..risk import create_confirm_handler
from ..utils.config import Config, load_config
from ..core import WorkflowType, WorkflowState, COMMAND_QUIT, WorkflowPersistence, WorkflowProgress
from ..workflows import InitWorkflow, NewVolumeWorkflow, ChapterWorkflow


class TerminalInterface:
    """终端交互界面"""

    def __init__(self, config: Optional[Config] = None, working_dir: Optional[str] = None):
        """
        初始化终端界面

        Args:
            config: 配置对象
            working_dir: 工作目录
        """
        self.config = config or load_config()
        self.working_dir = working_dir or self.config.working_dir

        if not os.path.isdir(self.working_dir):
            raise ValueError(f"目录不存在: {self.working_dir}")

        self.history = InMemoryHistory()
        self.running = False

        # 持久化管理（需在工作流之前创建）
        self.persistence = WorkflowPersistence(Path(self.working_dir))

        # 创建 Agent
        self.agent = NovelAgent(
            config=self.config,
            working_dir=self.working_dir,
            confirm_callback=create_confirm_handler(),
        )

        # 创建工作流
        self.init_workflow = InitWorkflow(self.agent, self._print)
        self.new_volume_workflow = NewVolumeWorkflow(self.agent, self._print)
        self.chapter_workflow = ChapterWorkflow(self.agent, self._print)

        # 设置持久化管理
        self.init_workflow.set_persistence(self.persistence)
        self.new_volume_workflow.set_persistence(self.persistence)
        self.chapter_workflow.set_persistence(self.persistence)

        # 当前工作流类型
        self.current_workflow_type = WorkflowType.IDLE

    def _print(self, text: str, end=""):
        """打印文本"""
        print(text, end=end, flush=True)

    def _print_event(self, event: dict):
        """打印事件"""
        event_type = event.get("type")
        content = event.get("content", "")

        if event_type == AgentEvents.CONTENT:
            print(content, end="", flush=True)
        elif event_type == AgentEvents.TOOL_CALL:
            name = event.get("name", "unknown")
            inp = event.get("input", {})
            if name == "file_read":
                path = inp.get("path", inp.get("file_path", ""))
                print(f"\n[调用工具: {name}  {path}]", end="", flush=True)
            else:
                print(f"\n[调用工具: {name}]", end="", flush=True)
        elif event_type == AgentEvents.TOOL_RESULT:
            name = event.get("name", "unknown")
            result = str(content)
            if name == "file_read":
                print(f"\n[{name} 完成]", end="", flush=True)
            else:
                snippet = result[:200].replace("\n", " ") if result else ""
                print(f"\n[{name} 完成]: {snippet}", end="", flush=True)

    def _print_welcome(self):
        """打印欢迎信息"""
        print("=" * 60)
        print("  小说创作 Agent 系统")
        print("=" * 60)
        print(f"  工作目录: {self.working_dir}")
        print(f"  模型: {self.config.model_name}")
        print()

        # 检查是否有未完成的工作流
        workflow_type = self.persistence.get_workflow_type()
        if workflow_type and workflow_type != "idle":
            print(f"  [检测到未完成的工作流: {workflow_type}]")
            print("  输入 /resume 继续之前的进度")
        print()

        print("  命令:")
        print("    /start       - 初始化新小说项目")
        print("    /new_volume  - 开启新一卷创作")
        print("    /chapter     - 开始创作章节")
        print("    /resume      - 继续未完成的工作流")
        print("    /quit        - 退出")
        print("=" * 60)
        print()

    def run(self):
        """运行终端界面"""
        self.running = True
        self._print_welcome()

        # 检查是否有未完成的工作流
        if self.persistence.has_progress():
            print("检测到未完成的工作流，输入 /resume 继续")
        elif self._is_initialized():
            print("输入 /new_volume 开启新一卷创作，或 /chapter 开始创作章节")
        else:
            print("输入 /start 开始初始化小说项目")
        print()

        while self.running:
            try:
                user_input = prompt("\n> ", history=self.history).strip()

                if not user_input:
                    continue

                # 检查退出命令
                if user_input.lower() in COMMAND_QUIT:
                    print("再见!")
                    self.running = False
                    break

                # 处理命令
                if not self._handle_command(user_input):
                    break

            except KeyboardInterrupt:
                print("\n使用 /quit 退出")
            except Exception as e:
                import traceback
                print(f"\n错误: {e}")
                traceback.print_exc()

    def _handle_command(self, cmd: str) -> bool:
        """处理命令"""
        # 如果当前工作流已完成或被取消，重置工作流类型
        if self.current_workflow_type == WorkflowType.INIT and self.init_workflow.state in (WorkflowState.READY, WorkflowState.IDLE):
            self.current_workflow_type = WorkflowType.IDLE
        elif self.current_workflow_type == WorkflowType.NEW_VOLUME and self.new_volume_workflow.state in (WorkflowState.READY, WorkflowState.IDLE):
            self.current_workflow_type = WorkflowType.IDLE
        elif self.current_workflow_type == WorkflowType.CHAPTER and self.chapter_workflow.state in (WorkflowState.READY, WorkflowState.IDLE):
            self.current_workflow_type = WorkflowType.IDLE

        # 当前工作流处理优先（工作流中的某些步骤需要接受以 / 开头的路径等输入）
        if self.current_workflow_type == WorkflowType.INIT:
            return self.init_workflow.handle(cmd)
        elif self.current_workflow_type == WorkflowType.NEW_VOLUME:
            return self.new_volume_workflow.handle(cmd)
        elif self.current_workflow_type == WorkflowType.CHAPTER:
            return self.chapter_workflow.handle(cmd)

        # 命令路由（仅在无活动工作流时处理）
        if cmd == "/start":
            return self._start_init()
        elif cmd == "/new_volume":
            return self._start_new_volume()
        elif cmd == "/chapter":
            return self._start_chapter()
        elif cmd == "/resume":
            return self._resume()
        elif cmd.startswith("/"):
            self._print(f"未知命令: {cmd}")
            return True

        # 空闲状态下的普通输入
        return True

    def _start_init(self) -> bool:
        """启动初始化"""
        if self.init_workflow.state == WorkflowState.READY:
            self._print("初始化已完成，使用 /status 查看进度")
            return True

        if self.init_workflow.state != WorkflowState.IDLE:
            self._print("初始化已在进行中")
            return True

        self.current_workflow_type = WorkflowType.INIT
        self.init_workflow.start()
        return True

    def _is_initialized(self) -> bool:
        """检查是否已完成初始化（workspace 目录存在即视为已初始化）"""
        return (Path(self.working_dir) / "workspace").exists()

    def _start_new_volume(self) -> bool:
        """启动新卷工作流"""
        if not self._is_initialized():
            self._print("请先完成初始化")
            return True

        if self.init_workflow.state != WorkflowState.READY and self._is_initialized():
            self.init_workflow.state = WorkflowState.READY

        work_dir = Path(self.working_dir)
        novel_dir = work_dir / "小说本体"

        if not novel_dir.exists():
            self._print("小说本体文件夹不存在，请先使用 /start 初始化")
            self._print("(或创建 {novel_dir} 目录)")
            return True

        self.current_workflow_type = WorkflowType.NEW_VOLUME
        self.new_volume_workflow.start(work_dir, novel_dir)
        return True

    def _resume(self) -> bool:
        """恢复未完成的工作流"""
        progress = self.persistence.load()

        if progress is None or progress.workflow_type in ("idle", ""):
            self._print("没有未完成的工作流")
            return True

        self._print(f"""
============================================================
  继续工作流: {progress.workflow_type}
============================================================
""")

        if progress.workflow_type == "init":
            self.current_workflow_type = WorkflowType.INIT
            self.init_workflow.restore(progress)
            # 继续到下一个需要用户输入的步骤
            self._print("[继续初始化工作流]")
            self.init_workflow.continue_from_saved()
        elif progress.workflow_type == "new_volume":
            self.current_workflow_type = WorkflowType.NEW_VOLUME
            self.new_volume_workflow.restore(progress)
            self._print("[继续新卷工作流]")
            self.new_volume_workflow.continue_from_saved()
        elif progress.workflow_type == "chapter":
            self.current_workflow_type = WorkflowType.CHAPTER
            self.chapter_workflow.restore(progress)
            self._print("[继续章节工作流]")
            self.chapter_workflow.continue_from_saved()
        else:
            self._print(f"未知的工作流类型: {progress.workflow_type}")
            return True

        return True

    def _start_chapter(self) -> bool:
        """启动章节工作流"""
        if not self._is_initialized():
            self._print("请先完成初始化")
            return True

        if self.init_workflow.state != WorkflowState.READY and self._is_initialized():
            self.init_workflow.state = WorkflowState.READY

        work_dir = Path(self.working_dir)
        novel_dir = work_dir / "小说本体"

        if not novel_dir.exists():
            self._print("小说本体文件夹不存在，请先使用 /new_volume 创建新卷")
            return True

        self.current_workflow_type = WorkflowType.CHAPTER
        self.chapter_workflow.start(work_dir, novel_dir)
        return True


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="小说创作 Agent 系统")
    parser.add_argument(
        "-d", "--directory",
        default=None,
        help="工作目录路径 (默认: 使用配置或当前目录)"
    )
    args = parser.parse_args()

    config = load_config()
    work_dir = args.directory or config.working_dir

    work_dir = Path(work_dir).resolve()
    if not work_dir.exists():
        print(f"错误: 目录不存在 - {work_dir}")
        import sys
        sys.exit(1)

    interface = TerminalInterface(config=config, working_dir=str(work_dir))
    interface.run()


if __name__ == "__main__":
    main()
