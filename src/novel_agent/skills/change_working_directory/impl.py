"""change_working_directory skill implementation"""

import os
from pathlib import Path
from novel_agent.skills.paths import workspace_path


def run(path: str, working_dir: str) -> str:
    """切换工作目录"""
    try:
        new_dir = workspace_path(path).resolve()
        if not new_dir.exists():
            return f"错误: 目录不存在 - {path}"
        if not new_dir.is_dir():
            return f"错误: 路径不是目录 - {path}"
        os.chdir(new_dir)
        return f"已切换到目录: {new_dir}"
    except Exception as e:
        return f"错误: 切换目录失败 - {e}"
