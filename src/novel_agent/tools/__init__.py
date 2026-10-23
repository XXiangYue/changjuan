"""工具定义模块"""

from pathlib import Path
from typing import Callable

from langchain_core.tools import tool


# 需要确认的危险操作工具名
RISKY_TOOLS = frozenset({"write_file", "change_working_directory", "delete_file"})


@tool
def read_file(path: str) -> str:
    """读取指定路径的文件内容。

    Args:
        path: 文件的绝对路径或相对路径
    """
    file_path = Path(path)
    if not file_path.exists():
        return f"错误: 文件不存在 - {path}"
    if not file_path.is_file():
        return f"错误: 路径不是文件 - {path}"
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"错误: 读取文件失败 - {e}"


@tool
def write_file(path: str, content: str) -> str:
    """写入内容到指定路径的文件。

    Args:
        path: 文件的绝对路径或相对路径
        content: 要写入的内容
    """
    file_path = Path(path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"成功写入文件: {path}"
    except Exception as e:
        return f"错误: 写入文件失败 - {e}"


@tool
def list_directory(path: str = ".") -> str:
    """列出指定目录下的文件和文件夹。

    Args:
        path: 目录的绝对路径或相对路径，默认为当前目录
    """
    dir_path = Path(path)
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


@tool
def get_working_directory() -> str:
    """获取当前工作目录的路径"""
    return __import__("os").getcwd()


@tool
def change_working_directory(path: str) -> str:
    """切换工作目录。

    Args:
        path: 目标目录的绝对路径或相对路径
    """
    import os
    try:
        new_dir = Path(path).resolve()
        if not new_dir.exists():
            return f"错误: 目录不存在 - {path}"
        if not new_dir.is_dir():
            return f"错误: 路径不是目录 - {path}"
        os.chdir(new_dir)
        return f"已切换到目录: {new_dir}"
    except Exception as e:
        return f"错误: 切换目录失败 - {e}"


# 工具列表
ALL_TOOLS = [read_file, write_file, list_directory, get_working_directory, change_working_directory]


def is_risky_tool(tool_name: str) -> bool:
    """检查是否为危险工具"""
    return tool_name in RISKY_TOOLS


def create_tool_executor(
    tools: list,
    confirm_callback: Callable[[str, dict], bool],
    working_dir: str = None,
) -> Callable[[str, dict], str]:
    """
    创建带确认回调的工具执行器

    Args:
        tools: 工具列表
        confirm_callback: 确认回调 (tool_name, tool_input) -> bool
        working_dir: 工作目录

    Returns:
        执行器函数 (tool_name, tool_input) -> str
    """
    tool_map = {t.name: t for t in tools}

    def execute(tool_name: str, tool_input: dict) -> str:
        if tool_name not in tool_map:
            return f"错误: 未知工具 {tool_name}"

        # 危险工具需要确认
        if is_risky_tool(tool_name) and confirm_callback:
            if not confirm_callback(tool_name, tool_input):
                return f"[操作已取消] 用户拒绝了工具 {tool_name} 的执行"

        try:
            # get_working_directory 返回 Agent 的工作目录
            if tool_name == "get_working_directory":
                return working_dir or os.getcwd()

            return tool_map[tool_name].invoke(tool_input)
        except Exception as e:
            return f"错误: 工具执行失败 - {e}"

    return execute
