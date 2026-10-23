"""终端交互界面模块 - 兼容性别名"""

# 为了向后兼容，从 ui 模块重新导出
from .ui.terminal import TerminalInterface, main

__all__ = ["TerminalInterface", "main"]
