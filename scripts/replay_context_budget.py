"""Freeze old stored chapter contexts, then replay without a database or model.

snapshot reads only the 24 chapters named by the previous offline experiment.
verify reads only the self-contained artifact bundle and its frozen Python source.
Neither command imports application settings, reads credentials, or calls a model.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Also let the archived script run directly from source/ inside its bundle.
ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
    and (parent / "src/novel_agent/studio/context_budget.py").is_file()
)
REFERENCE = ROOT / "artifacts/resume/context-saved-samples.json"
DESTINATION = ROOT / "artifacts/resume/context-replay"
MECHANISM = ROOT / "src/novel_agent/studio/context_budget.py"
OLD_PROJECTS = {
    "815825904710442c9e549e83f140c725",
    "9730fab365eb44d3985896a64253fd7f",
}
ROLES = ("writer", "editor", "memory")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fresh bundle is mandatory. Never silently replace experimental evidence.
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")


def inside(path: Path, boundary: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(boundary.resolve()):
        raise ValueError(f"Path must remain in {boundary}")
    return path


def pairs_from_reference(data: bytes) -> list[tuple[str, str]]:
    previous = json.loads(data)
    pairs = sorted(
        {(row["project_id"], row["chapter_id"]) for row in previous["trials_detail"]}
    )
    if len(pairs) != 24 or {pid for pid, _ in pairs} != OLD_PROJECTS:
        raise ValueError(
            "Reference must identify exactly the two old projects / 24 chapters"
        )
    if any(sum(pid == p for pid, _ in pairs) != 12 for p in OLD_PROJECTS):
        raise ValueError("Each old project must contribute exactly 12 chapters")
    if any(
        len(cid) != 32 or any(c not in "0123456789abcdef" for c in cid)
        for _, cid in pairs
    ):
        raise ValueError("Unexpected chapter identifier")
    return pairs


def load_mechanism(path: Path):
    # Register the isolated module for dataclasses; do not import the application.
    name = "context_replay_frozen_" + sha(path.read_bytes())[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the frozen context mechanism")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module.pack_context


def query_from_plan(plan: dict) -> str:
    return " ".join(
        [plan.get("goal", ""), *plan.get("characters", []), *plan.get("hooks", [])]
    )


def aggregate(trials: list[dict]) -> dict:
    original = sum(x["audit"]["original_bytes"] for x in trials)
    selected = sum(x["audit"]["serialized_bytes"] for x in trials)
    return {
        "chapters": len({x["chapter_id"] for x in trials}),
        "trials": len(trials),
        "all_within_budget": all(
            x["audit"]["serialized_bytes"] <= x["audit"]["max_bytes"]
            and x["audit"]["within_budget"]
            for x in trials
        ),
        "original_bytes_total": original,
        "selected_bytes_total": selected,
        "max_original_bytes": max(x["audit"]["original_bytes"] for x in trials),
        "max_selected_bytes": max(x["audit"]["serialized_bytes"] for x in trials),
        "aggregate_bytes_reduction": 1 - selected / original,
        "by_role": {
            role: {
                "original_bytes_total": sum(
                    x["audit"]["original_bytes"] for x in trials if x["role"] == role
                ),
                "selected_bytes_total": sum(
                    x["audit"]["serialized_bytes"] for x in trials if x["role"] == role
                ),
            }
            for role in ROLES
        },
    }


def snapshot(database: Path, output: Path, max_bytes: int) -> dict:
    database = inside(database, ROOT)
    output = inside(output, DESTINATION)
    if max_bytes <= 0:
        raise ValueError("max-bytes must be positive")
    reference_bytes = REFERENCE.read_bytes()
    pairs = pairs_from_reference(reference_bytes)
    source_bytes = MECHANISM.read_bytes()
    script_path = Path(__file__).resolve()
    script_bytes = script_path.read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    source_dir = output / "source"
    source_dir.mkdir()
    (source_dir / "context_budget.py").write_bytes(source_bytes)
    (source_dir / "replay_context_budget.py").write_bytes(script_bytes)
    (source_dir / "reference-context-saved-samples.json").write_bytes(reference_bytes)

    # Read-only URI plus query_only, with a single SQLite snapshot transaction.
    # No project list, manuscript column, settings table, or other project is read.
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = []
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        for project_id, chapter_id in pairs:
            row = connection.execute(
                "SELECT id,project_id,volume,number,plan,context FROM chapters "
                "WHERE id=? AND project_id=?",
                (chapter_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Missing allowlisted chapter {chapter_id}")
            rows.append(dict(row))
        connection.rollback()
    finally:
        connection.close()
    rows.sort(key=lambda row: (row["project_id"], row["volume"], row["number"]))
    pack_context = load_mechanism(source_dir / "context_budget.py")
    files = {}
    trials = []

    def save(relative, data):
        path = output / relative
        write_json(path, data)
        files[relative] = sha(path.read_bytes())

    for row in rows:
        context, plan = json.loads(row["context"]), json.loads(row["plan"])
        query = query_from_plan(plan)
        cid = row["id"]
        save(
            f"inputs/{cid}.json",
            {
                "project_id": row["project_id"],
                "chapter_id": cid,
                "volume": row["volume"],
                "number": row["number"],
                "stored_context_json": row["context"],
                "stored_context_sha256": sha(row["context"].encode("utf-8")),
                "stored_plan_json": row["plan"],
                "stored_plan_sha256": sha(row["plan"].encode("utf-8")),
                "query": query,
            },
        )
        for role in ROLES:
            view, audit = pack_context(
                context, role=role, query=query, max_bytes=max_bytes
            )
            save(f"outputs/{cid}/{role}.json", {"view": view, "audit": audit})
            trials.append(
                {
                    "project_id": row["project_id"],
                    "chapter_id": cid,
                    "role": role,
                    "audit": audit,
                }
            )
    source_after = MECHANISM.read_bytes()
    script_after = script_path.read_bytes()
    source_consistent = source_after == source_bytes and script_after == script_bytes
    summary = aggregate(trials)
    old = json.loads(reference_bytes)
    summary["reference_aggregate_bytes_reduction"] = old["aggregate_bytes_reduction"]
    summary["difference_from_reference"] = (
        summary["aggregate_bytes_reduction"] - old["aggregate_bytes_reduction"]
    )
    save("summary.json", summary)
    for path in source_dir.iterdir():
        if not path.is_file():
            continue
        files[str(path.relative_to(output))] = sha(path.read_bytes())
    manifest = {
        "experiment": "context-budget-replay-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "Two old projects / 24 stored contexts and current stored plans, offline replay only",
        "limitations": [
            "No historical author instruction is reconstructed or added.",
            "No new manuscript, LLM call, quality, provider-token or actual-cost measurement.",
            "The byte ceiling covers only the compact context view JSON, not the entire request.",
            "Saved contexts may contain historical previous-chapter text; no current manuscript columns are read.",
        ],
        "database_read": {
            "path": str(database.relative_to(ROOT)),
            "mode": "ro",
            "query_only": True,
            "consistent_transaction": True,
            "table": "chapters",
            "columns": ["id", "project_id", "volume", "number", "plan", "context"],
            "allowlist_source": "source/reference-context-saved-samples.json",
        },
        "pairs": [list(pair) for pair in pairs],
        "roles": list(ROLES),
        "max_bytes": max_bytes,
        "query_rule": "' '.join([plan.goal, *plan.characters, *plan.hooks])",
        "source_consistent": source_consistent,
        "source_sha256_before": sha(source_bytes),
        "source_sha256_after": sha(source_after),
        "script_sha256_before": sha(script_bytes),
        "script_sha256_after": sha(script_after),
        "files_sha256": files,
    }
    write_json(output / "manifest.json", manifest)
    if not source_consistent:
        raise RuntimeError(
            "Source changed during snapshot; bundle retained but not accepted"
        )
    return summary | {"bundle": str(output), "source_consistent": source_consistent}


def verify(bundle: Path) -> dict:
    bundle = inside(bundle, DESTINATION)
    manifest = json.loads((bundle / "manifest.json").read_text())
    if (
        manifest["experiment"] != "context-budget-replay-v1"
        or not manifest["source_consistent"]
    ):
        raise ValueError("Unrecognized or inconsistent source snapshot")
    for relative, expected in manifest["files_sha256"].items():
        path = inside(bundle / relative, bundle)
        if sha(path.read_bytes()) != expected:
            raise ValueError(f"Artifact SHA mismatch: {relative}")
    source = bundle / "source/context_budget.py"
    if sha(source.read_bytes()) != manifest["source_sha256_before"]:
        raise ValueError("Frozen mechanism fingerprint mismatch")
    if (
        sha((bundle / "source/replay_context_budget.py").read_bytes())
        != manifest["script_sha256_before"]
    ):
        raise ValueError("Frozen replay script fingerprint mismatch")
    pairs = pairs_from_reference(
        (bundle / "source/reference-context-saved-samples.json").read_bytes()
    )
    if [list(pair) for pair in pairs] != manifest["pairs"] or manifest["roles"] != list(
        ROLES
    ):
        raise ValueError("Input membership / roles do not match the frozen reference")
    required_files = {
        "summary.json",
        "source/context_budget.py",
        "source/replay_context_budget.py",
        "source/reference-context-saved-samples.json",
        *{f"inputs/{cid}.json" for _, cid in pairs},
        *{f"outputs/{cid}/{role}.json" for _, cid in pairs for role in ROLES},
    }
    if set(manifest["files_sha256"]) != required_files:
        raise ValueError("Manifest does not fingerprint every required evidence file")
    pack_context = load_mechanism(source)
    trials = []
    for project_id, cid in pairs:
        saved = json.loads((bundle / f"inputs/{cid}.json").read_text())
        if saved["project_id"] != project_id or saved["chapter_id"] != cid:
            raise ValueError("Input identity mismatch")
        for key in ("context", "plan"):
            if (
                sha(saved[f"stored_{key}_json"].encode("utf-8"))
                != saved[f"stored_{key}_sha256"]
            ):
                raise ValueError(f"Stored {key} fingerprint mismatch")
        context = json.loads(saved["stored_context_json"])
        plan = json.loads(saved["stored_plan_json"])
        query = query_from_plan(plan)
        if query != saved["query"]:
            raise ValueError("Query was not derived from the frozen plan")
        for role in ROLES:
            expected = json.loads((bundle / f"outputs/{cid}/{role}.json").read_text())
            original_bytes = encode(context)
            view, audit = pack_context(
                context, role=role, query=query, max_bytes=manifest["max_bytes"]
            )
            if {"view": view, "audit": audit} != expected:
                raise ValueError(f"Replay mismatch for {cid}/{role}")
            if encode(context) != original_bytes:
                raise ValueError("Mechanism mutated the saved input")
            if len(encode(view)) != audit["serialized_bytes"]:
                raise ValueError("Independent compact JSON byte measurement failed")
            trials.append(
                {
                    "project_id": project_id,
                    "chapter_id": cid,
                    "role": role,
                    "audit": audit,
                }
            )
    summary = json.loads((bundle / "summary.json").read_text())
    result = aggregate(trials)
    if any(summary[key] != value for key, value in result.items()):
        raise ValueError("Aggregate does not match independent replay")
    reference = json.loads(
        (bundle / "source/reference-context-saved-samples.json").read_text()
    )
    if (
        summary["reference_aggregate_bytes_reduction"]
        != reference["aggregate_bytes_reduction"]
    ):
        raise ValueError("Reference aggregate mismatch")
    if (
        summary["difference_from_reference"]
        != result["aggregate_bytes_reduction"] - reference["aggregate_bytes_reduction"]
    ):
        raise ValueError("Reference difference mismatch")
    return {
        "verified": True,
        "database_read": False,
        "model_calls": 0,
        "source": "frozen source in bundle",
        "verified_file_count": len(manifest["files_sha256"]),
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser(
        "snapshot", help="Read only the allowlisted old stored inputs"
    )
    snap.add_argument("--database", type=Path, default=ROOT / "data/studio.sqlite")
    snap.add_argument("--output", type=Path, default=DESTINATION / uuid4().hex)
    snap.add_argument("--max-bytes", type=int, default=60000)
    check = commands.add_parser(
        "verify", help="Replay solely from the saved artifact bundle"
    )
    check.add_argument("bundle", type=Path)
    args = parser.parse_args()
    result = (
        snapshot(args.database, args.output, args.max_bytes)
        if args.command == "snapshot"
        else verify(args.bundle)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
