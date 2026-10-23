"""Record tested source/dependency fingerprints without project secrets or manuscripts."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import json
import platform
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "artifacts/implementation-manifest.json"
if out.exists():
    previous = json.loads(out.read_text())
    if previous.get("frozen_at"):
        snapshot = out.with_name("implementation-manifest.initial.json")
        if not snapshot.exists():
            snapshot.write_bytes(out.read_bytes())
paths = []
for folder in ("src", "web/src", "tests", "scripts", "docs", "evaluations"):
    paths.extend(
        p
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and p.suffix
        in (
            ".py",
            ".ts",
            ".tsx",
            ".css",
            ".html",
            ".md",
            ".json",
            ".sh",
            ".yaml",
            ".yml",
            ".toml",
            ".txt",
            ".svg",
        )
    )
paths.extend(
    ROOT / p
    for p in (
        "README.md",
        ".python-version",
        ".gitignore",
        "pyproject.toml",
        "uv.lock",
        "web/package.json",
        "web/pnpm-lock.yaml",
        "web/tsconfig.json",
        "web/vite.config.ts",
        "web/index.html",
        "启动长卷.command",
        ".env.example",
    )
)
paths.extend((ROOT / "web/dist").rglob("*"))
paths = sorted({p for p in paths if p.is_file() and "__pycache__" not in p.parts})
xml = ET.parse(ROOT / "artifacts/testing/pytest.xml").getroot()
suites = list(xml.iter("testsuite"))
result = {
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "scope": "交付快照；开发过程有调整，不能冒充在所有实验前冻结的软件版本。原始快照另存。",
    "python": platform.python_version(),
    "libraries": {
        k: version(k)
        for k in (
            "fastapi",
            "langgraph",
            "deepagents",
            "langchain-deepseek",
            "pydantic",
            "qdrant-client",
            "fastembed",
            "langgraph-checkpoint-sqlite",
        )
    },
    "tests": {
        key: sum(int(s.attrib.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    },
    "files": {
        str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in paths
    },
    "omissions": [
        "密钥与私有设置",
        "data中的正文及检查点",
        "第三方安装包",
        "模型缓存",
        "不稳定的生成时间戳文件",
    ],
}
out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(f"{len(paths)} files, {result['tests']}")
