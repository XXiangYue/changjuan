"""Seal Oct 21 delivery after the operator finishes UI/document updates.

Default mode only reads readiness. --finalize takes exclusive, new-name online
SQLite backups, preserves the previous integrity report, runs verify_delivery.py
without --backup, and writes the first resume delivery index. No model calls,
manuscript updates, old backup replacement or credential output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/resume/delivery.json"
TEST_XML = ROOT / "artifacts/resume/iteration-02-tests-final.xml"
ACTIVE = ("queued", "running", "evaluating", "awaiting_approval", "interrupted")
SKIP = {".local", ".cache", ".venv", "node_modules", "__pycache__", ".git"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp():
    return datetime.now(UTC).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record(path):
    require(path.resolve().is_relative_to(ROOT), "交付路径必须在工作目录内")
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha(path),
        "bytes": path.stat().st_size,
    }


def connection(path):
    path = path.resolve(strict=True)
    require(path.is_relative_to(ROOT / "data"), "数据库实际路径必须在当前data内")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    actual = Path(db.execute("PRAGMA database_list").fetchone()["file"]).resolve()
    require(actual == path, "实际SQLite连接路径不符")
    return db


def readiness():
    paths = {
        name: ROOT / "data" / (name + ".sqlite") for name in ("studio", "checkpoints")
    }
    with closing(connection(paths["studio"])) as db:
        active = [
            dict(row)
            for row in db.execute(
                "SELECT id,status FROM runs WHERE status IN (?,?,?,?,?)", ACTIVE
            )
        ]
        usage = dict(
            db.execute(
                "SELECT COALESCE(SUM(cost),0) spent,COALESCE(SUM(reserved),0) reserved,COUNT(*) calls FROM usage"
            ).fetchone()
        )
        signature = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "projects",
                "chapters",
                "versions",
                "runs",
                "run_events",
                "usage",
                "audit",
                "experiments",
                "story_events",
            )
        }
        signature["max_event_seq"] = db.execute(
            "SELECT COALESCE(MAX(seq),0) FROM run_events"
        ).fetchone()[0]
        signature["chapter_revision_sum"] = db.execute(
            "SELECT COALESCE(SUM(draft_revision+canonical_revision),0) FROM chapters"
        ).fetchone()[0]
        signature["memory_revision_sum"] = db.execute(
            "SELECT COALESCE(SUM(revision),0) FROM memory"
        ).fetchone()[0]
        signature["project_revision_sum"] = db.execute(
            "SELECT COALESCE(SUM(revision),0) FROM projects"
        ).fetchone()[0]
    with closing(connection(paths["checkpoints"])) as db:
        checkpoint_counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("checkpoints", "writes")
        }
    xml = ET.parse(TEST_XML).getroot()
    suites = [xml] if xml.tag == "testsuite" else list(xml.iter("testsuite"))
    tests = {
        key: sum(int(row.attrib.get(key, 0)) for row in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    require(
        tests == {"tests": 180, "failures": 0, "errors": 0, "skipped": 0},
        "当前XML不是180项全通过",
    )
    dist = sorted(path for path in (ROOT / "web/dist").rglob("*") if path.is_file())
    require(bool(dist) and (ROOT / "web/dist/index.html").is_file(), "缺少前端构建产物")
    return {
        "checked_at": stamp(),
        "database_paths": {name: str(path.resolve()) for name, path in paths.items()},
        "active_runs": active,
        "usage": usage,
        "studio_signature": signature,
        "checkpoint_counts": checkpoint_counts,
        "tests": tests,
        "tests_xml": record(TEST_XML),
        "frontend_dist": [record(path) for path in dist],
    }


def selected_files():
    files = {}
    for folder, category in (
        ("src", "core_and_skills"),
        ("web/src", "frontend_source"),
        ("web/dist", "frontend_build"),
        ("scripts", "verification_and_eval_scripts"),
        ("docs", "documents"),
        ("evaluations", "frozen_evaluation_protocols"),
        ("artifacts/resume", "experiments_tests_and_ui"),
        ("artifacts/screenshots", "product_ui_screenshots"),
    ):
        for path in sorted((ROOT / folder).rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or path == OUT
                or set(path.relative_to(ROOT).parts) & SKIP
                or path.name == ".env"
            ):
                continue
            files[str(path.relative_to(ROOT))] = record(path) | {"category": category}
    for relative in (
        "README.md",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "web/package.json",
        "web/pnpm-lock.yaml",
        "web/tsconfig.json",
        "web/vite.config.ts",
        "web/index.html",
        "启动长卷.command",
    ):
        path = ROOT / relative
        if path.is_file():
            files[relative] = record(path) | {
                "category": "dependencies_and_entrypoints"
            }
    return files


def finalize():
    require(not OUT.exists(), "resume delivery索引已存在，不覆盖")
    before = readiness()
    require(not before["active_runs"], "有活动/待确认/中断任务，停止交付封存")
    require(before["usage"]["reserved"] == 0, "仍有费用预留，停止封存")
    require(before["usage"]["spent"] <= 300, "总费用超出原预算")
    backups = {
        name: ROOT / "artifacts/backups" / f"resume-20261021-{name}.sqlite"
        for name in ("studio", "checkpoints")
    }
    require(
        not any(path.exists() for path in backups.values()),
        "Oct21备份名已存在，不覆盖；先保留并协调新名字",
    )
    previous_integrity = ROOT / "artifacts/delivery-integrity.json"
    old_report = ROOT / "artifacts/resume/delivery-integrity-before-20261021.json"
    require(not old_report.exists(), "旧完整性报告保存名已存在，不能覆盖")
    old_backups = {
        str(path.relative_to(ROOT)): sha(path)
        for path in (ROOT / "artifacts/backups").glob("delivery-*.sqlite")
    }
    preserved = None
    if previous_integrity.exists():
        old_report.parent.mkdir(parents=True, exist_ok=True)
        with old_report.open("xb") as target:
            target.write(previous_integrity.read_bytes())
        shutil.copystat(previous_integrity, old_report)
        preserved = record(old_report)
    backup_records = {}
    for name, target in backups.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb"):
            pass
        started = stamp()
        with closing(connection(Path(before["database_paths"][name]))) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination, pages=256)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
                require(integrity == "ok", "新备份完整性失败：" + name)
        backup_records[name] = record(target) | {
            "source": before["database_paths"][name],
            "started_at": started,
            "completed_at": stamp(),
            "method": "sqlite3.Connection.backup在线备份，含已提交WAL内容；新文件独占创建",
            "integrity_check": integrity,
        }
    command = [sys.executable, str(ROOT / "scripts/verify_delivery.py")]
    verified = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )
    require(
        verified.returncode == 0,
        "verify_delivery.py失败；保留新备份与旧报告，不生成成功交付索引。"
        + verified.stderr[-600:],
    )
    integrity = json.loads(previous_integrity.read_text())
    require(
        integrity["active_runs"] == 0
        and integrity["credential_matches"] == 0
        and all(value == "ok" for value in integrity["sqlite_integrity"].values()),
        "最终完整性报告未通过",
    )
    after = readiness()
    require(
        not after["active_runs"] and after["usage"]["reserved"] == 0,
        "封存中出现新活动任务",
    )
    require(
        before["studio_signature"] == after["studio_signature"]
        and before["checkpoint_counts"] == after["checkpoint_counts"]
        and before["usage"] == after["usage"],
        "在线备份期间数据变化，保留本次产物但不声明最终快照",
    )
    require(
        all(sha(ROOT / path) == expected for path, expected in old_backups.items()),
        "旧Oct18备份发生变化",
    )
    files = selected_files()
    package = json.loads((ROOT / "web/package.json").read_text())
    result = {
        "schema_version": "resume-delivery/v1",
        "sealed_at_utc": stamp(),
        "workspace": str(ROOT),
        "scope": "Oct21当前实现及实测交付索引；保留Oct18报告/旧备份/失败记录，不冒充所有实验前冻结的代码。",
        "readiness_before": before,
        "readiness_after": after,
        "budget": {
            "spent_yuan": round(after["usage"]["spent"], 6),
            "reserved_yuan": after["usage"]["reserved"],
            "hard_limit_yuan": 300,
            "expected_spent_46_86299_matches": abs(after["usage"]["spent"] - 46.86299)
            < 0.000001,
            "basis": "生产usage实际token按保存单价保守估算，非核对后的供应商账单。",
        },
        "tests": after["tests"] | {"xml": after["tests_xml"], "rerun_by_sealer": False},
        "frontend_build": {
            "status": "successful_build_reported_by_root_and_output_present",
            "script": package["scripts"]["build"],
            "independently_rerun_by_sealer": False,
            "artifacts": after["frontend_dist"],
            "basis": "父代理已成功执行构建；封存进程核对当前dist文件并记录SHA，不虚构新的构建执行日志。",
            "root_reported_last_build": {
                "modules": 1648,
                "javascript_asset": "assets/index-B4_tXK4H.js",
                "stylesheet_asset": "assets/index-BTjzKE58.css",
                "description": "最终伏笔ID显示为可读名、AI选择/真人投票脚注文案调整后，父代理报告1648模块pnpm build成功；此为运行记录，非封存脚本重复构建。",
            },
        },
        "databases": backup_records,
        "backup_boundary": "每个SQLite在线备份各自一致；两库不是跨库原子事务。无活动任务且备份前后计数、修订总和、事件序号与费用相同，仍不作为跨库事务证明。",
        "integrity_verification": {
            "command": [
                str(Path(sys.executable).relative_to(ROOT))
                if Path(sys.executable).is_relative_to(ROOT)
                else sys.executable,
                "scripts/verify_delivery.py",
            ],
            "backup_flag_used": False,
            "exit_code": verified.returncode,
            "stdout": verified.stdout.strip(),
            "report": record(previous_integrity),
            "result": integrity,
            "legacy_backup_reference_note": "verify_delivery.py的backups字段仍引用保留的Oct18 delivery-*；本次新备份以本索引databases字段为准。",
        },
        "preserved_previous_integrity_report": preserved,
        "legacy_oct18_backup_hashes_unchanged": old_backups,
        "files": files,
        "preservation": {
            "existing_experiment_reports_overwritten": False,
            "integrity_report_refreshed_after_preserving_previous_copy": True,
            "old_backups_overwritten": False,
            "failed_records_deleted": False,
            "manuscripts_modified": False,
            "model_calls": 0,
            "credential_values_recorded": False,
        },
        "source_claim_boundary": "交付时源码与前端指纹，不等同于早期实验实际加载版本；各实验generation-source/receipt与结果报告保留独立边界。",
    }
    with OUT.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "delivery": str(OUT),
                "sha256": sha(OUT),
                "budget": result["budget"],
                "backup_integrity": {
                    name: data["integrity_check"]
                    for name, data in backup_records.items()
                },
                "credential_matches": integrity["credential_matches"],
                "tests": after["tests"],
                "indexed_files": len(files),
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.finalize:
        finalize()
    else:
        print(json.dumps(readiness(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
