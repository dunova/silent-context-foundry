#!/usr/bin/env python3
"""
OpenViking Real-time Context Sync Daemon (Hardened v3.0)

Goals:
- Global terminal coverage on one machine (CLI tools + shell history)
- Zero-touch background operation via launchd
- Safe long-running behavior (bounded memory, rotating logs, retries)
"""

import glob
import hashlib
import json
import logging
import logging.handlers
import os
import re
import resource
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:8090/api/v1")
LOCAL_STORAGE_ROOT = Path(
    os.environ.get(
        "UNIFIED_CONTEXT_STORAGE_ROOT",
        os.environ.get("OPENVIKING_STORAGE_ROOT", str(Path.home() / ".unified_context_data")),
    )
).expanduser()
PENDING_DIR = LOCAL_STORAGE_ROOT / "resources" / "shared" / "history" / ".pending"
LOG_DIR = Path.home() / ".context_system" / "logs"

CODEX_SESSIONS = str(Path.home() / ".codex" / "sessions")
ANTIGRAVITY_BRAIN = str(Path.home() / ".gemini" / "antigravity" / "brain")

ENABLE_SHELL_MONITOR = os.environ.get("VIKING_ENABLE_SHELL_MONITOR", "1") == "1"
IDLE_TIMEOUT_SEC = int(os.environ.get("VIKING_IDLE_TIMEOUT_SEC", "300"))
POLL_INTERVAL_SEC = int(os.environ.get("VIKING_POLL_INTERVAL_SEC", "30"))
HEARTBEAT_INTERVAL_SEC = int(os.environ.get("VIKING_HEARTBEAT_INTERVAL_SEC", "600"))
FAST_POLL_INTERVAL_SEC = max(1, int(os.environ.get("VIKING_FAST_POLL_INTERVAL_SEC", "3")))
PENDING_RETRY_INTERVAL_SEC = max(5, int(os.environ.get("VIKING_PENDING_RETRY_INTERVAL_SEC", "60")))
MAX_TRACKED_SESSIONS = int(os.environ.get("VIKING_MAX_TRACKED_SESSIONS", "240"))
MAX_FILE_CURSORS = int(os.environ.get("VIKING_MAX_FILE_CURSORS", "800"))
SESSION_TTL_SEC = int(os.environ.get("VIKING_SESSION_TTL_SEC", "7200"))
MAX_MESSAGES_PER_SESSION = int(os.environ.get("VIKING_MAX_MESSAGES_PER_SESSION", "500"))
EXPORT_HTTP_TIMEOUT_SEC = max(5, int(os.environ.get("VIKING_EXPORT_HTTP_TIMEOUT_SEC", "30")))
PENDING_HTTP_TIMEOUT_SEC = max(5, int(os.environ.get("VIKING_PENDING_HTTP_TIMEOUT_SEC", "15")))

JSONL_SOURCES: dict[str, list[dict[str, Any]]] = {
    "claude_code": [
        {
            "path": str(Path.home() / ".claude" / "history.jsonl"),
            "sid_keys": ["sessionId", "session_id"],
            "text_keys": ["display", "text", "input", "prompt"],
        }
    ],
    "codex_history": [
        {
            "path": str(Path.home() / ".codex" / "history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["text", "input", "prompt"],
        }
    ],
    "opencode": [
        {
            "path": str(Path.home() / ".local" / "state" / "opencode" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
        {
            "path": str(Path.home() / ".config" / "opencode" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
        {
            "path": str(Path.home() / ".opencode" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
    ],
    "kilo": [
        {
            "path": str(Path.home() / ".local" / "state" / "kilo" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
        {
            "path": str(Path.home() / ".config" / "kilo" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
    ],
}

SHELL_SOURCES: dict[str, list[str]] = {
    "shell_zsh": [
        str(Path.home() / ".zsh_history"),
    ],
    "shell_bash": [
        str(Path.home() / ".bash_history"),
    ],
}

SHELL_LINE_RE = re.compile(r"^:\s*(\d+):\d+;(.*)$")
SECRET_REPLACEMENTS = [
    (re.compile(r"(api[_-]?key\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(token\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(password\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(--api-key\s+)([^\s]+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(--token\s+)([^\s]+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"), "sk-***"),
]

IGNORE_SHELL_CMD_PREFIXES = (
    "history",
    "fc ",
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / "viking_daemon.log"

logger = logging.getLogger("viking_daemon")
logger.setLevel(logging.INFO)

_rfh = logging.handlers.RotatingFileHandler(
    str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_rfh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_rfh)

_sh = logging.StreamHandler(sys.stderr)
_sh.setLevel(logging.WARNING)
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_sh)

# ---------------------------------------------------------------------------
# Lazy httpx import
# ---------------------------------------------------------------------------
try:
    import httpx

    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False
    logger.warning("httpx not installed; will only write local files.")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    logger.info("Received signal %s, shutting down.", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


class SessionTracker:
    def __init__(self):
        self.sessions: dict[str, dict[str, Any]] = {}
        self.file_cursors: dict[str, int] = {}
        self.antigravity_sessions: dict[str, dict[str, Any]] = {}
        self.active_jsonl: dict[str, dict[str, Any]] = {}
        self.active_shell: dict[str, str] = {}

        self._last_heartbeat = time.time()
        self._last_source_refresh = 0.0
        self._last_pending_retry = 0.0
        self._export_count = 0
        self._error_count = 0
        self._last_activity_ts = 0.0
        self._http_client = None

        if _HTTPX_OK:
            try:
                self._http_client = httpx.Client(trust_env=False, follow_redirects=True)
            except Exception as exc:
                logger.warning("Failed to initialize httpx client: %s", exc)
                self._http_client = None

        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        self.refresh_sources(force=True)

    # -- source discovery -------------------------------------------------
    def refresh_sources(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_source_refresh < 120:
            return
        self._last_source_refresh = now

        # JSONL AI sources: pick first existing candidate per source.
        for source_name, candidates in JSONL_SOURCES.items():
            picked = None
            for candidate in candidates:
                p = candidate["path"]
                if os.path.exists(p):
                    picked = candidate
                    break
            prev = self.active_jsonl.get(source_name)
            if picked:
                self.active_jsonl[source_name] = picked
                if not prev or prev["path"] != picked["path"]:
                    cursor_key = self._cursor_key("jsonl", source_name, picked["path"])
                    self.file_cursors[cursor_key] = os.path.getsize(picked["path"])
                    logger.info("Source active: %s -> %s", source_name, picked["path"])
            elif source_name in self.active_jsonl:
                logger.info("Source offline: %s", source_name)
                del self.active_jsonl[source_name]

        # Shell sources
        if ENABLE_SHELL_MONITOR:
            for source_name, paths in SHELL_SOURCES.items():
                picked_path = ""
                for p in paths:
                    if os.path.exists(p):
                        picked_path = p
                        break
                prev = self.active_shell.get(source_name, "")
                if picked_path:
                    self.active_shell[source_name] = picked_path
                    if prev != picked_path:
                        cursor_key = self._cursor_key("shell", source_name, picked_path)
                        self.file_cursors[cursor_key] = os.path.getsize(picked_path)
                        logger.info("Source active: %s -> %s", source_name, picked_path)
                elif source_name in self.active_shell:
                    logger.info("Source offline: %s", source_name)
                    del self.active_shell[source_name]

    def _cursor_key(self, kind: str, source_name: str, path: str) -> str:
        digest = hashlib.md5(path.encode("utf-8")).hexdigest()[:10]
        return f"{kind}:{source_name}:{digest}"

    # -- polling ----------------------------------------------------------
    def poll_jsonl_sources(self):
        now = time.time()
        for source_name, source in self.active_jsonl.items():
            path = source["path"]
            cursor_key = self._cursor_key("jsonl", source_name, path)
            self._poll_jsonl_file(source_name, path, source, cursor_key, now)

    def _poll_jsonl_file(self, source_name: str, path: str, source: dict[str, Any], cursor_key: str, now: float):
        try:
            cur_size = os.path.getsize(path)
        except OSError:
            return

        last = self.file_cursors.get(cursor_key, cur_size)
        if cur_size < last:
            last = 0
        if cur_size <= last:
            self.file_cursors[cursor_key] = cur_size
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    sid = self._extract_sid(data, source.get("sid_keys", []), source_name)
                    text = self._extract_text(data, source.get("text_keys", []))
                    text = self._sanitize_text(text)
                    if not text:
                        continue

                    self._upsert_session(sid, source_name, text, now)

            self.file_cursors[cursor_key] = cur_size
        except Exception as exc:
            self._error_count += 1
            logger.error("poll_jsonl_sources(%s): %s", source_name, exc)

    def poll_shell_sources(self):
        if not ENABLE_SHELL_MONITOR:
            return

        now = time.time()
        for source_name, path in self.active_shell.items():
            cursor_key = self._cursor_key("shell", source_name, path)
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                continue

            last = self.file_cursors.get(cursor_key, cur_size)
            if cur_size < last:
                last = 0
            if cur_size <= last:
                self.file_cursors[cursor_key] = cur_size
                continue

            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last)
                    for line in f:
                        parsed = self._parse_shell_line(source_name, line)
                        if not parsed:
                            continue
                        sid, text = parsed
                        self._upsert_session(sid, source_name, text, now)
                self.file_cursors[cursor_key] = cur_size
            except Exception as exc:
                self._error_count += 1
                logger.error("poll_shell_sources(%s): %s", source_name, exc)

    def poll_codex_sessions(self):
        if not os.path.isdir(CODEX_SESSIONS):
            return

        now = time.time()
        try:
            session_files = glob.glob(os.path.join(CODEX_SESSIONS, "**", "*.jsonl"), recursive=True)
        except OSError as exc:
            logger.error("glob codex sessions: %s", exc)
            return

        for path in session_files:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < now - 3600:
                continue

            cursor_key = self._cursor_key("codex_session", "codex_session", path)
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                continue

            last = self.file_cursors.get(cursor_key, cur_size)
            if cur_size < last:
                last = 0
            if cur_size <= last:
                self.file_cursors[cursor_key] = cur_size
                continue

            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if data.get("type") != "response_item":
                            continue
                        payload = data.get("payload", {})
                        ptype = payload.get("type")
                        text = ""
                        if ptype == "message":
                            texts = [
                                c.get("text", "")
                                for c in payload.get("content", [])
                                if c.get("type") == "output_text"
                            ]
                            text = "\n".join(t for t in texts if t)
                        elif ptype == "reasoning":
                            text = payload.get("text", "")

                        text = self._sanitize_text(text)
                        if text:
                            sid = os.path.basename(path)
                            self._upsert_session(sid, "codex_session", text, now)

                self.file_cursors[cursor_key] = cur_size
            except Exception as exc:
                self._error_count += 1
                logger.error("poll_codex_sessions(%s): %s", path, exc)

    def poll_antigravity(self):
        if not os.path.isdir(ANTIGRAVITY_BRAIN):
            return

        now = time.time()
        try:
            dirs = glob.glob(os.path.join(ANTIGRAVITY_BRAIN, "*-*-*-*-*"))
        except OSError:
            return

        for sdir in dirs:
            sid = os.path.basename(sdir)
            wt = os.path.join(sdir, "walkthrough.md")
            if not os.path.exists(wt):
                continue

            try:
                mtime = os.path.getmtime(wt)
            except OSError:
                continue

            # First sighting: establish baseline and skip to avoid replay storm
            # after daemon restart.
            if sid not in self.antigravity_sessions:
                self.antigravity_sessions[sid] = {"mtime": mtime, "path": wt}
                continue

            prev = self.antigravity_sessions[sid].get("mtime", 0)
            if mtime <= prev:
                continue

            try:
                with open(wt, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(50_000)
                content = self._sanitize_text(content)
                if content:
                    data = {
                        "source": "antigravity",
                        "messages": [content],
                        "last_seen": now,
                    }
                    self._export(sid, data, title_prefix="Antigravity Walkthrough")
                    self.antigravity_sessions[sid] = {"mtime": mtime, "path": wt}
            except Exception as exc:
                self._error_count += 1
                logger.error("poll_antigravity(%s): %s", sid, exc)

    # -- parsing helpers ---------------------------------------------------
    def _extract_sid(self, data: dict[str, Any], sid_keys: list[str], source_name: str) -> str:
        for key in sid_keys:
            val = data.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                return str(val)
        return f"{source_name}_default"

    def _extract_text(self, data: dict[str, Any], text_keys: list[str]) -> str:
        for key in text_keys:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        # fallback for structures like opencode parts
        parts = data.get("parts")
        if isinstance(parts, list):
            text_parts: list[str] = []
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    ptext = part.get("text")
                    if isinstance(ptext, str) and ptext.strip():
                        text_parts.append(ptext.strip())
            if text_parts:
                prefix = data.get("input") if isinstance(data.get("input"), str) else ""
                if prefix.strip():
                    return prefix.strip() + "\n" + "\n".join(text_parts)
                return "\n".join(text_parts)
        return ""

    def _parse_shell_line(self, source_name: str, raw_line: str):
        line = raw_line.strip()
        if not line:
            return None

        ts = int(time.time())
        cmd = line

        match = SHELL_LINE_RE.match(line)
        if match:
            ts = int(match.group(1))
            cmd = match.group(2).strip()

        if not cmd:
            return None

        low = cmd.lower()
        if low.startswith(IGNORE_SHELL_CMD_PREFIXES):
            return None

        cmd = self._sanitize_text(cmd)
        if not cmd:
            return None

        day = datetime.fromtimestamp(ts).strftime("%Y%m%d")
        sid = f"{source_name}_{day}"
        return sid, cmd

    def _sanitize_text(self, text: str) -> str:
        if not text:
            return ""
        out = text.strip()
        for pattern, repl in SECRET_REPLACEMENTS:
            out = pattern.sub(repl, out)
        if len(out) > 4000:
            out = out[:4000]
        return out

    # -- session management -----------------------------------------------
    def _upsert_session(self, sid: str, source: str, text: str, now: float):
        if sid not in self.sessions:
            if len(self.sessions) >= MAX_TRACKED_SESSIONS:
                self._evict_oldest()
            self.sessions[sid] = {
                "last_seen": now,
                "messages": [],
                "exported": False,
                "source": source,
                "created": now,
                "last_hash": "",
            }

        sess = self.sessions[sid]
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()
        if digest == sess.get("last_hash"):
            return

        sess["messages"].append(text)
        sess["last_hash"] = digest
        sess["last_seen"] = now
        self._last_activity_ts = now

        if len(sess["messages"]) > MAX_MESSAGES_PER_SESSION:
            sess["messages"] = sess["messages"][-200:]

    def _evict_oldest(self):
        exported = [(k, v) for k, v in self.sessions.items() if v["exported"]]
        if exported:
            oldest_k = min(exported, key=lambda x: x[1]["last_seen"])[0]
            del self.sessions[oldest_k]
            return
        oldest_k = min(self.sessions, key=lambda k: self.sessions[k]["last_seen"])
        del self.sessions[oldest_k]

    def check_and_export_idle(self):
        now = time.time()
        to_remove = []

        for sid, data in self.sessions.items():
            if data["exported"]:
                if now - data["last_seen"] > SESSION_TTL_SEC:
                    to_remove.append(sid)
                continue

            if now - data["last_seen"] <= IDLE_TIMEOUT_SEC:
                continue

            source = data["source"]
            min_messages = 4 if source.startswith("shell_") else 2
            if len(data["messages"]) >= min_messages:
                self._export(sid, data)

            data["exported"] = True

        for sid in to_remove:
            del self.sessions[sid]

    def cleanup_cursors(self):
        if len(self.file_cursors) <= MAX_FILE_CURSORS:
            return
        keys = sorted(self.file_cursors.keys())
        remove_n = max(1, len(keys) // 3)
        for key in keys[:remove_n]:
            del self.file_cursors[key]
        logger.info("Cleaned %d file cursors.", remove_n)

    # -- export -----------------------------------------------------------
    def _export(self, sid: str, data: dict[str, Any], title_prefix: str = ""):
        source = data["source"]
        messages = data["messages"]
        content = "\n- ".join(msg[:2000] for msg in messages[-60:])

        prefix = title_prefix or f"Live {source} Session"
        title = f"{prefix} {sid[:12]}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        local_dir = LOCAL_STORAGE_ROOT / "resources" / "shared" / "history"
        local_dir.mkdir(parents=True, exist_ok=True)
        file_path = local_dir / f"{source}_{ts}_{sid[:12]}.md"

        formatted = (
            f"# {title}\n\n"
            f"Tags: {source}, live_sync, unified_context\n"
            f"Date: {datetime.now().isoformat()}\n\n"
            f"## Content\n- {content}\n"
        )

        try:
            file_path.write_text(formatted, encoding="utf-8")
            os.chmod(file_path, 0o600)
        except OSError as exc:
            logger.error("Failed to write local file %s: %s", file_path, exc)
            return False

        if self._http_client:
            payload = {
                "path": str(file_path),
                "target": "viking://resources/shared/history",
                "reason": f"Real-time sync of {source} session",
                "instruction": f"Index real-time completed {source} conversation: {title}",
            }
            try:
                resp = self._http_client.post(
                    f"{OPENVIKING_URL}/resources",
                    json=payload,
                    timeout=EXPORT_HTTP_TIMEOUT_SEC,
                )
                if resp.status_code < 300:
                    self._export_count += 1
                    logger.info("Synced %s session %s to Viking.", source, sid[:12])
                    self._retry_pending()
                    return True
                logger.warning("Viking HTTP %d for %s %s", resp.status_code, source, sid[:12])
            except Exception as exc:
                logger.warning("Viking offline, queue pending: %s", exc)

        pending_path = PENDING_DIR / file_path.name
        try:
            pending_path.write_text(formatted, encoding="utf-8")
            os.chmod(pending_path, 0o600)
            logger.info("Queued pending sync: %s", pending_path.name)
        except OSError as exc:
            logger.error("Failed pending write: %s", exc)
        return False

    def _retry_pending(self):
        if not self._http_client:
            return

        pending = sorted(PENDING_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime)
        if not pending:
            return

        self._last_pending_retry = time.time()
        for pf in pending[:8]:
            try:
                payload = {
                    "path": str(pf),
                    "target": "viking://resources/shared/history",
                    "reason": "Retry pending sync",
                    "instruction": f"Index pending conversation: {pf.stem}",
                }
                resp = self._http_client.post(
                    f"{OPENVIKING_URL}/resources",
                    json=payload,
                    timeout=PENDING_HTTP_TIMEOUT_SEC,
                )
                if resp.status_code < 300:
                    pf.unlink(missing_ok=True)
                    logger.info("Retried pending OK: %s", pf.name)
            except Exception:
                break

    def maybe_retry_pending(self):
        if not PENDING_DIR.exists():
            return
        try:
            has_pending = any(PENDING_DIR.glob("*.md"))
        except Exception:
            has_pending = False
        if not has_pending:
            return
        now = time.time()
        if now - self._last_pending_retry < PENDING_RETRY_INTERVAL_SEC:
            return
        self._retry_pending()

    def next_sleep_interval(self) -> int:
        """Adaptive polling: faster near idle-export boundary, quiet when idle."""
        sleep_s = max(1, POLL_INTERVAL_SEC)

        try:
            if PENDING_DIR.exists() and any(PENDING_DIR.glob("*.md")):
                sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)
        except Exception:
            pass

        now = time.time()
        nearest_due = None
        for data in self.sessions.values():
            if data.get("exported"):
                continue
            remaining = IDLE_TIMEOUT_SEC - (now - data.get("last_seen", now))
            if nearest_due is None or remaining < nearest_due:
                nearest_due = remaining

        if nearest_due is not None:
            if nearest_due <= FAST_POLL_INTERVAL_SEC:
                sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)
            elif nearest_due < sleep_s:
                sleep_s = min(sleep_s, max(1, int(nearest_due)))

        if self._last_activity_ts and (now - self._last_activity_ts) < max(15, FAST_POLL_INTERVAL_SEC * 4):
            sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)

        return max(1, sleep_s)

    # -- heartbeat --------------------------------------------------------
    def heartbeat(self):
        now = time.time()
        if now - self._last_heartbeat < HEARTBEAT_INTERVAL_SEC:
            return
        self._last_heartbeat = now

        try:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # macOS reports bytes; Linux reports kilobytes
            if sys.platform == "darwin":
                mem_mb = rss / (1024 * 1024)
            else:
                mem_mb = rss / 1024
        except Exception:
            mem_mb = -1

        pending_count = len(list(PENDING_DIR.glob("*.md"))) if PENDING_DIR.exists() else 0

        active_sources = list(self.active_jsonl.keys()) + list(self.active_shell.keys())
        logger.info(
            "♥ sessions=%d cursors=%d exported=%d errors=%d pending=%d mem=%.1fMB active_sources=%s",
            len(self.sessions),
            len(self.file_cursors),
            self._export_count,
            self._error_count,
            pending_count,
            mem_mb,
            ",".join(active_sources) if active_sources else "none",
        )


def main():
    os.umask(0o077)
    logger.info("Starting OpenViking Hardened Daemon v3.0")
    logger.info("OpenViking URL: %s", OPENVIKING_URL)
    logger.info("Codex sessions path: %s", CODEX_SESSIONS)
    logger.info("Antigravity brain path: %s", ANTIGRAVITY_BRAIN)
    logger.info(
        "Idle=%ds Poll=%ds FastPoll=%ds PendingRetry=%ds Heartbeat=%ds ShellMonitor=%s",
        IDLE_TIMEOUT_SEC,
        POLL_INTERVAL_SEC,
        FAST_POLL_INTERVAL_SEC,
        PENDING_RETRY_INTERVAL_SEC,
        HEARTBEAT_INTERVAL_SEC,
        "on" if ENABLE_SHELL_MONITOR else "off",
    )

    tracker = SessionTracker()
    cycle = 0

    while not _shutdown:
        try:
            tracker.refresh_sources()
            tracker.poll_jsonl_sources()
            tracker.poll_shell_sources()
            tracker.poll_codex_sessions()
            tracker.poll_antigravity()
            tracker.check_and_export_idle()
            tracker.maybe_retry_pending()
            tracker.heartbeat()

            cycle += 1
            if cycle % 60 == 0:
                tracker.cleanup_cursors()
                tracker.maybe_retry_pending()

        except Exception as exc:
            logger.exception("Unhandled error in main loop: %s", exc)

        time.sleep(tracker.next_sleep_interval())

    logger.info("Daemon shutdown complete. Exported %d sessions total.", tracker._export_count)


if __name__ == "__main__":
    main()
