"""file_write skill"""

from pathlib import Path
from novel_agent.skills.paths import workspace_path
from langchain_core.tools import tool


@tool
def file_write(path: str, content: str) -> str:
    """写入内容到文件（危险操作，需确认）

    Args:
        path: 文件的绝对路径
        content: 要写入的内容
    """
    file_path = workspace_path(path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"成功写入文件: {path}"
    except Exception as e:
        return f"错误: 写入文件失败 - {e}"
