#!/usr/bin/env python3
"""Import memory observations from JSON with dedup."""

from __future__ import annotations

from datetime import datetime
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

try:
    from memory_index import ensure_index_db, strip_private_blocks, sync_index_from_storage
except Exception:  # pragma: no cover
    from .memory_index import ensure_index_db, strip_private_blocks, sync_index_from_storage  # type: ignore[import-not-found]


SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def _sanitize_text(text: str) -> str:
    out = strip_private_blocks(text or "")
    for pat in SECRET_PATTERNS:
        out = pat.sub("***REDACTED***", out)
    return out.strip()


def _norm_obs(raw: dict) -> dict:
    raw_tags = raw.get("tags") or []
    if not isinstance(raw_tags, list):
        raw_tags = [raw_tags]
    clean_tags = []
    for tag in raw_tags:
        t = _sanitize_text(str(tag))
        if t:
            clean_tags.append(t[:80])

    raw_path = _sanitize_text(str(raw.get("file_path") or "import://json"))[:300]
    if raw_path.startswith("/") or raw_path.startswith("~"):
        raw_path = "import://local-path-redacted"

    title = _sanitize_text(str(raw.get("title") or "imported memory"))[:240]
    content = _sanitize_text(str(raw.get("content") or ""))
    created_at_epoch = int(raw.get("created_at_epoch") or int(datetime.now().timestamp()))
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint and content:
        fingerprint = hashlib.sha256(
            f"{raw.get('source_type') or 'import'}|{raw.get('session_id') or 'imported'}|{title}|{content}|{created_at_epoch}".encode(
                "utf-8"
            )
        ).hexdigest()
    return {
        "fingerprint": fingerprint,
        "source_type": str(raw.get("source_type") or "import"),
        "session_id": str(raw.get("session_id") or "imported"),
        "title": title,
        "content": content,
        "tags_json": json.dumps(clean_tags, ensure_ascii=False),
        "file_path": raw_path,
        "created_at": str(raw.get("created_at") or datetime.now().isoformat()),
        "created_at_epoch": created_at_epoch,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Context Mesh memories.")
    parser.add_argument("input", help="Input JSON path exported by export_memories.py")
    parser.add_argument("--no-sync", action="store_true", help="Skip sync_index_from_storage after import.")
    args = parser.parse_args(argv)

    input_path = Path(args.input).expanduser()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    observations = data.get("observations") or []
    if not isinstance(observations, list):
        raise SystemExit("invalid payload: observations must be list")

    db_path = ensure_index_db()
    conn = sqlite3.connect(db_path)
    inserted = 0
    skipped = 0
    now_epoch = int(datetime.now().timestamp())
    try:
        for raw in observations:
            if not isinstance(raw, dict):
                continue
            obs = _norm_obs(raw)
            if not obs["fingerprint"] or not obs["content"].strip():
                skipped += 1
                continue
            exists = conn.execute(
                "SELECT id FROM observations WHERE fingerprint = ?",
                (obs["fingerprint"],),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO observations(
                    fingerprint, source_type, session_id, title, content, tags_json,
                    file_path, created_at, created_at_epoch, updated_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs["fingerprint"],
                    obs["source_type"],
                    obs["session_id"],
                    obs["title"],
                    obs["content"],
                    obs["tags_json"],
                    obs["file_path"],
                    obs["created_at"],
                    obs["created_at_epoch"],
                    now_epoch,
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()

    if not args.no_sync:
        sync_index_from_storage()
    print(f"import done inserted={inserted} skipped={skipped} db={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
