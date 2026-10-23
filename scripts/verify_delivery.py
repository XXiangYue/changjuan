"""Verify local delivery and optionally take idle, online SQLite backups.

Never print credential content. This script does not call a model.
"""

from argparse import ArgumentParser
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import stat
import zipfile

from novel_agent.studio.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
parser = ArgumentParser()
parser.add_argument("--backup", action="store_true")
args = parser.parse_args()
private = ROOT / ".local/settings.json"
key = Settings(ROOT).values()["api_key"]
secret = key.encode() if key else b""
report = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "secret_file_mode": oct(stat.S_IMODE(private.stat().st_mode))
    if private.exists()
    else None,
    "private_settings_excluded": True,
    "scanned_files": 0,
    "zip_entries_checked": 0,
    "credential_matches": 0,
    "credential_scan_performed": bool(secret),
    "sqlite_integrity": {},
    "backups": {},
}
with sqlite3.connect(ROOT / "data/studio.sqlite") as conn:
    active = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running','evaluating','awaiting_approval')"
    ).fetchone()[0]
    report["active_runs"] = active
    if args.backup and active:
        raise SystemExit("仍有进行中任务；请完成或暂停后再建立交付备份。")
for name in ("studio", "checkpoints"):
    source = ROOT / "data" / (name + ".sqlite")
    with sqlite3.connect(source) as conn:
        report["sqlite_integrity"][name] = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        backup = ROOT / "artifacts/backups" / ("delivery-" + name + ".sqlite")
        if args.backup:
            backup.parent.mkdir(exist_ok=True)
            with sqlite3.connect(backup) as dest:
                conn.backup(dest)
        if backup.exists():
            report["backups"][name] = {
                "path": str(backup.relative_to(ROOT)),
                "sha256": sha256(backup.read_bytes()).hexdigest(),
                "bytes": backup.stat().st_size,
                "refreshed_this_run": args.backup,
            }
skip = {".local", ".cache", ".venv", "node_modules", "__pycache__", ".git"}
for path in ROOT.rglob("*"):
    if (
        not path.is_file()
        or path.is_symlink()
        or set(path.relative_to(ROOT).parts) & skip
    ):
        continue
    if path.name == ".env":
        continue
    report["scanned_files"] += 1
    if secret and secret in path.read_bytes():
        report["credential_matches"] += 1
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                report["zip_entries_checked"] += 1
                if secret and secret in archive.read(entry):
                    report["credential_matches"] += 1
report["credential_scan_scope"] = (
    "核对当前配置的完整密钥是否出现在可交付文件、数据库和ZIP内；"
    "不扫描私有配置、环境文件、第三方依赖或缓存，不保证识别其他未配置凭据。"
)
(ROOT / "artifacts/delivery-integrity.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(
    json.dumps(
        {
            "scanned_files": report["scanned_files"],
            "zip_entries_checked": report["zip_entries_checked"],
            "credential_matches": report["credential_matches"],
            "sqlite_integrity": report["sqlite_integrity"],
            "backups_refreshed": args.backup,
        },
        ensure_ascii=False,
    )
)
if report["credential_matches"] or any(
    v != "ok" for v in report["sqlite_integrity"].values()
):
    raise SystemExit(1)
