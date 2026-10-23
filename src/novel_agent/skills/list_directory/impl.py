"""list_directory skill"""

import os
from pathlib import Path
from novel_agent.skills.paths import workspace_path
from langchain_core.tools import tool


@tool
def list_directory(path: str = ".") -> str:
    """列出目录内容

    Args:
        path: 目录路径,默认为当前目录
    """

    dir_path = workspace_path(path)
    if not dir_path.exists():
        return f"错误: 目录不存在 - {path}"
    if not dir_path.is_dir():
        return f"错误: 路径不是目录 - {path}"
    try:
        items = []
        for item in sorted(dir_path.iterdir()):
            item_type = "DIR" if item.is_dir() else "FILE"
            items.append(f"[{item_type}] {item.name}")
        return "\n".join(items) if items else f"目录为空: {path}"
    except Exception as e:
        return f"错误: 读取目录失败 - {e}"
