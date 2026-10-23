"""配置管理模块"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    """应用配置"""
    api_key: str
    base_url: str
    model_name: str
    working_dir: str
    max_tokens: int = 8192


def load_config(env_path: Optional[Path] = None) -> Config:
    """
    加载配置

    Args:
        env_path: .env 文件路径，默认从项目根目录加载

    Returns:
        Config 实例
    """
    if env_path is None:
        env_path = Path(__file__).parent.parent.parent.parent / ".env"

    load_dotenv(env_path)

    return Config(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic"),
        model_name=os.getenv("MODEL_NAME", "MiniMax-M2.7"),
        working_dir=os.getenv("WORKING_DIR", os.getcwd()),
        max_tokens=int(os.getenv("MAX_TOKENS", "8192")),
    )
