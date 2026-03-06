#!/usr/bin/env python3
"""Regression suite for hit-first memory retrieval across recall and MCP."""

from __future__ import annotations

import importlib.util
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RECALL_PATH = Path("/Users/dunova/.agents/skills/recall/scripts/recall.py")
MCP_PATH = Path("/Users/dunova/.codex/skills/openviking-memory-sync/scripts/openviking_mcp.py")
OPENVIKING_PYTHON = Path("/Users/dunova/.openviking_env/bin/python")
RECALL_DB = Path.home() / ".recall.db"
RANDOM_SEED = 20260306


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    elapsed_sec: float


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def run_cmd(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_recall_cli(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    return run_cmd([sys.executable, str(RECALL_PATH), *args], timeout=timeout)


def run_mcp_python(code: str, timeout: int = 30) -> tuple[int, str, str]:
    python_bin = str(OPENVIKING_PYTHON if OPENVIKING_PYTHON.exists() else Path(sys.executable))
    return run_cmd([python_bin, "-c", code], timeout=timeout)


def choose_anchor(text: str, variants_fn) -> str:
    for item in variants_fn(text):
        candidate = item.strip().strip('"')
        if not candidate:
            continue
        if len(candidate) < 3 or len(candidate) > 40:
            continue
        if " AND " in candidate:
            continue
        if re.fullmatch(r"\d{8}", candidate):
            continue
        if "/" in candidate:
            candidate = Path(candidate).name or candidate
        if any(ch in candidate for ch in "._-"):
            pieces = [part for part in re.split(r"[._/-]+", candidate) if len(part) >= 4]
            if pieces:
                candidate = pieces[0]
        if candidate.isdigit():
            continue
        if re.search(r"[\u4e00-\u9fff]", candidate) and len(candidate) > 6:
            continue
        return candidate
    return "NotebookLM"


def load_random_cases(limit: int = 6) -> list[dict[str, Any]]:
    if not RECALL_DB.exists():
        return []

    conn = sqlite3.connect(str(RECALL_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT s.session_id, s.project, s.file_path, s.slug, s.timestamp, m.text
            FROM sessions s
            JOIN messages m ON m.session_id = s.session_id
            WHERE s.source = 'codex'
              AND s.timestamp > strftime('%s','now','-10 days') * 1000
              AND length(m.text) > 30
            ORDER BY s.timestamp DESC, m.rowid DESC
            LIMIT 80
            """
        ).fetchall()
    finally:
        conn.close()

    random.seed(RANDOM_SEED)
    sample = list(rows)
    random.shuffle(sample)
    chosen = []
    seen = set()
    for row in sample:
        key = row["session_id"]
        if key in seen:
            continue
        seen.add(key)
        chosen.append(dict(row))
        if len(chosen) >= limit:
            break
    return chosen


def check_query_variant_order(recall_mod) -> Check:
    t0 = time.time()
    query = "继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案"
    variants = recall_mod._build_recall_query_variants(query)
    passed = bool(variants) and variants[0].lower() in {"notebooklm", "github"}
    detail = f"variants={variants[:5]}"
    return Check("查询改写优先级", passed, detail, time.time() - t0)


def check_recall_fixed_cases() -> list[Check]:
    cases: list[Check] = []

    fixed_inputs = [
        ("recall-long-hybrid", ["继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案", "--backend", "hybrid", "--type", "all", "--source", "codex", "--days", "1", "--limit", "5"], "019cc215"),
        ("recall-keyword", ["NotebookLM", "--backend", "recall", "--type", "all", "--source", "codex", "--days", "1", "--limit", "5"], "NotebookLM"),
        ("recall-date", ["2026-03-06", "--backend", "hybrid", "--type", "all", "--source", "codex", "--days", "1", "--limit", "5"], "2026-03-06"),
        ("recall-content", ["NotebookLM", "--backend", "hybrid", "--type", "content", "--source", "codex", "--days", "7", "--limit", "5", "--no-regex"], "role="),
        ("recall-aline", ["NotebookLM", "--backend", "aline", "--type", "all", "--limit", "5", "--no-regex"], "NOTEBOOKLM".lower()),
        ("recall-health", ["--health"], '"recall_db_exists": true'),
        ("recall-index-only", ["--index-only"], "Index refresh done"),
    ]

    for name, args, marker in fixed_inputs:
        t0 = time.time()
        rc, out, err = run_recall_cli(*args, timeout=40)
        text = (out + "\n" + err).lower()
        passed = rc == 0 and marker.lower() in text
        cases.append(Check(name, passed, f"rc={rc}, marker={marker}, tail={(out or err)[-220:]}", time.time() - t0))

    return cases


def check_mcp_fixed_cases() -> list[Check]:
    cases: list[Check] = []

    snippets = {
        "mcp-health": """
import importlib.util, json
spec = importlib.util.spec_from_file_location('ovm', r'%s')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.context_system_health())
""" % MCP_PATH,
        "mcp-long-query": """
import importlib.util
spec = importlib.util.spec_from_file_location('ovm', r'%s')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.search_onecontext_history('继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案', 'all', 5, False))
""" % MCP_PATH,
        "mcp-keyword": """
import importlib.util
spec = importlib.util.spec_from_file_location('ovm', r'%s')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.search_onecontext_history('NotebookLM', 'all', 5, True))
""" % MCP_PATH,
        "mcp-memory-save-query": """
import importlib.util, time
spec = importlib.util.spec_from_file_location('ovm', r'%s')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
marker = 'regression-marker-' + str(int(time.time()))
print(m.save_conversation_memory('Regression ' + marker, marker, ['regression']))
print('---')
print(m.query_viking_memory(marker, 3))
""" % MCP_PATH,
    }
    expected = {
        "mcp-health": '"all_ok": true',
        "mcp-long-query": "019cc215",
        "mcp-keyword": "NotebookLM".lower(),
        "mcp-memory-save-query": "regression-marker-",
    }

    for name, code in snippets.items():
        t0 = time.time()
        rc, out, err = run_mcp_python(code, timeout=50)
        text = (out + "\n" + err).lower()
        passed = rc == 0 and expected[name].lower() in text
        cases.append(Check(name, passed, f"rc={rc}, tail={(out or err)[-260:]}", time.time() - t0))

    return cases


def check_random_tasks(recall_mod) -> list[Check]:
    checks: list[Check] = []
    rows = load_random_cases(limit=6)
    for idx, row in enumerate(rows, 1):
        anchor = choose_anchor(row["text"], recall_mod._build_recall_query_variants)
        long_query = f"继续找和 {anchor} 相关的那个 session 和实现方案"
        t0 = time.time()
        rc, out, err = run_recall_cli(
            long_query,
            "--backend",
            "hybrid",
            "--type",
            "all",
            "--source",
            "codex",
            "--days",
            "10",
            "--limit",
            "5",
        )
        haystack = (out + "\n" + err).lower()
        passed = rc == 0 and (
            row["session_id"].lower() in haystack
            or Path(row["project"] or "").name.lower() in haystack
            or anchor.lower() in haystack
        )
        detail = f"anchor={anchor}, session={row['session_id']}, rc={rc}"
        checks.append(Check(f"random-task-{idx}", passed, detail, time.time() - t0))
    return checks


def main() -> int:
    recall_mod = load_module(RECALL_PATH, "recall_regression")
    checks: list[Check] = []
    checks.append(check_query_variant_order(recall_mod))
    checks.extend(check_recall_fixed_cases())
    checks.extend(check_mcp_fixed_cases())
    checks.extend(check_random_tasks(recall_mod))

    passed = sum(1 for item in checks if item.passed)
    failed = len(checks) - passed

    print(json.dumps(
        {
            "seed": RANDOM_SEED,
            "passed": passed,
            "failed": failed,
            "checks": [
                {
                    "name": item.name,
                    "passed": item.passed,
                    "detail": item.detail,
                    "elapsed_sec": round(item.elapsed_sec, 3),
                }
                for item in checks
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
