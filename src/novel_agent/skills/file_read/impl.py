"""file_read skill"""

from pathlib import Path
from novel_agent.skills.paths import workspace_path
from langchain_core.tools import tool


@tool
def file_read(path: str) -> str:
    """读取文件内容

    Args:
        path: 文件的绝对路径
    """
    file_path = workspace_path(path)
    if not file_path.exists():
        return f"错误: 文件不存在 - {path}"
    if not file_path.is_file():
        return f"错误: 路径不是文件 - {path}"
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"错误: 读取文件失败 - {e}"
