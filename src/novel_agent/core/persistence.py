"""工作流状态持久化"""

import json
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, asdict


@dataclass
class WorkflowProgress:
    """工作流进度"""
    workflow_type: str = "idle"           # 当前工作流类型
    state: str = "idle"                   # 当前状态
    step_index: int = 0                    # 当前步骤索引
    generated_content: str = ""            # 已生成的内容（用于恢复）
    data: dict = None                      # 其他工作流特定数据

    def __post_init__(self):
        if self.data is None:
            self.data = {}


class WorkflowPersistence:
    """工作流状态持久化管理"""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.state_file = work_dir / ".workflow_state.json"
        self.progress: Optional[WorkflowProgress] = None

    def save(self, progress: WorkflowProgress):
        """保存工作流进度"""
        self.progress = progress
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        data = asdict(progress)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self) -> Optional[WorkflowProgress]:
        """加载工作流进度"""
        if not self.state_file.exists():
            return None

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.progress = WorkflowProgress(**data)
            return self.progress
        except Exception:
            return None

    def clear(self):
        """清除工作流进度"""
        if self.state_file.exists():
            self.state_file.unlink()
        self.progress = None

    def has_progress(self) -> bool:
        """是否有未完成的工作流进度"""
        if not self.state_file.exists():
            return False

        progress = self.load()
        if progress is None:
            return False

        # 如果是 IDLE 或 READY 状态，认为已完成
        return progress.workflow_type not in ("idle", "") and progress.state not in ("idle", "ready", "")

    def get_workflow_type(self) -> str:
        """获取当前工作流类型"""
        progress = self.load()
        if progress:
            return progress.workflow_type
        return "idle"
