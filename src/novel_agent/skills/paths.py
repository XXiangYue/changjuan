"""Legacy file tools share the same absolute-path workspace boundary."""
import os
from pathlib import Path

def workspace_path(path:str)->Path:
    p=Path(path)
    root=Path(os.environ.get("NOVEL_AGENT_ROOT",os.environ.get("NOVEL_AGENT_WORKING_DIR",os.getcwd()))).resolve()
    if not p.is_absolute():raise ValueError("必须使用绝对路径")
    p=p.resolve()
    if not p.is_relative_to(root):raise ValueError("路径超出工作目录")
    return p
