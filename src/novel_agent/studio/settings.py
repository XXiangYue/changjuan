from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]


class Settings:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.data = self.within(self.root / "data")
        self.cache = self.within(self.root / ".cache")
        self.secret_file = self.root / ".local" / "settings.json"
        self.within(self.secret_file)
        self.data.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)

    def values(self) -> dict:
        secret = self.within(self.secret_file)
        local = json.loads(secret.read_text()) if secret.exists() else {}
        env = dotenv_values(self.within(self.root / ".env"))
        return {
            "api_key": os.environ.get("DEEPSEEK_API_KEY")
            or env.get("DEEPSEEK_API_KEY")
            or local.get("api_key", ""),
            "budget": min(float(local.get("budget", 300)), 300),
            "vector_enabled": local.get("vector_enabled", False),
            "retrieval_policy": local.get("retrieval_policy", "keyword"),
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
        }

    def update(self, values: dict):
        secret = self.within(self.secret_file)
        old = json.loads(secret.read_text()) if secret.exists() else {}
        old.update({k: v for k, v in values.items() if v is not None})
        folder = self.within(self.secret_file.parent)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Credentials never pass into project data, model tools, or tracing events.
        fd, name = tempfile.mkstemp(dir=folder, prefix="settings-", suffix=".tmp")
        temp = Path(name)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(old, f, ensure_ascii=False)
            self.within(self.secret_file)
            temp.replace(self.secret_file)
            self.secret_file.chmod(0o600)
        finally:
            temp.unlink(missing_ok=True)

    def public(self) -> dict:
        v = self.values()
        return {k: value for k, value in v.items() if k != "api_key"} | {
            "configured": bool(v["api_key"])
        }

    def within(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_absolute():
            raise ValueError("必须使用绝对路径")
        p = p.resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("路径超出工作目录")
        return p
