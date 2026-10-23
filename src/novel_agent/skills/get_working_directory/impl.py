"""get_working_directory skill"""

import os
from langchain_core.tools import tool


@tool
def get_working_directory() -> str:
    """获取当前工作目录路径"""
    wd = os.environ.get("NOVEL_AGENT_WORKING_DIR")
    return wd if wd else os.getcwd()
