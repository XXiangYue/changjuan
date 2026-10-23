"""危险操作确认模块"""

from typing import Callable, Optional

from prompt_toolkit import prompt
from prompt_toolkit.history import InMemoryHistory


class RiskConfirm:
    """危险操作确认处理器"""

    def __init__(self):
        self.history = InMemoryHistory()

    def confirm(self, tool_name: str, tool_input: dict) -> bool:
        """
        确认危险操作

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            True 允许执行，False 拒绝
        """
        print("\n" + "=" * 50)
        print(f"WARNING: 危险操作请求")
        print("=" * 50)
        print(f"工具: {tool_name}")
        print(f"参数: {tool_input}")
        print("-" * 50)

        while True:
            try:
                confirm = prompt(
                    "> 是否允许此操作? (y/n): ",
                    history=self.history,
                ).strip().lower()

                if confirm in ("y", "yes", "是", "允许"):
                    print("已允许执行")
                    return True
                elif confirm in ("n", "no", "否", "拒绝"):
                    print("已拒绝执行")
                    return False
                else:
                    print("请输入 y 或 n")
            except KeyboardInterrupt:
                print("\n操作已取消")
                return False


def create_confirm_handler() -> Callable[[str, dict], bool]:
    """创建确认处理器"""
    handler = RiskConfirm()
    return handler.confirm
