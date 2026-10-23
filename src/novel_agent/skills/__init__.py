"""Skills 加载器 - 简化版"""

import importlib.util
from pathlib import Path
from typing import List

from langchain_core.tools import BaseTool

# 危险工具列表
RISKY_TOOLS = frozenset({"file_write"})


class SkillLoader:
    """扫描 skills/ 目录，动态加载工具"""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._tools = []
        self._load_all()

    def _load_all(self):
        """扫描目录，加载所有 @tool 装饰的函数"""
        if not self.skills_dir.exists():
            return

        for skill_path in self.skills_dir.iterdir():
            if not skill_path.is_dir():
                continue

            impl_file = skill_path / "impl.py"
            if not impl_file.exists():
                continue

            try:
                spec = importlib.util.spec_from_file_location(f"skill_{skill_path.name}", impl_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # 从模块中提取 @tool 装饰的函数
                for attr_name in dir(module):
                    if attr_name.startswith('_'):
                        continue
                    attr = getattr(module, attr_name)
                    # 检查是否是 LangChain Tool 对象
                    if isinstance(attr, BaseTool):
                        self._tools.append(attr)
            except Exception as e:
                print(f"加载 skill {skill_path.name} 失败: {e}")

    def get_tools(self) -> List[BaseTool]:
        """获取所有工具"""
        return self._tools

    def is_dangerous(self, tool_name: str) -> bool:
        """检查是否为危险工具"""
        return tool_name in RISKY_TOOLS


def create_skill_loader(skills_dir: Path = None) -> SkillLoader:
    if skills_dir is None:
        skills_dir = Path(__file__).parent
    return SkillLoader(skills_dir)
