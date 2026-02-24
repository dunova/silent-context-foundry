import atexit
from datetime import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("OpenViking Global Memory Server")

OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:8090/api/v1")

# Security: require HTTPS for non-localhost URLs to prevent MITM
_ov_host = OPENVIKING_URL.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
if _ov_host not in ("127.0.0.1", "localhost", "::1") and not OPENVIKING_URL.startswith("https://"):
    raise SystemExit(f"Remote OPENVIKING_URL must use https://. Got: {OPENVIKING_URL}")

LOCAL_STORAGE_ROOT = os.path.expanduser(
    os.environ.get("UNIFIED_CONTEXT_STORAGE_ROOT", os.environ.get("OPENVIKING_STORAGE_ROOT", "~/.unified_context_data"))
)
ALINE_DB_PATH = os.path.expanduser("~/.aline/db/aline.db")
VALID_SEARCH_TYPES = {"all", "event", "session", "turn", "content"}
HTTP_TIMEOUT_SEC = max(int(os.environ.get("OPENVIKING_HTTP_TIMEOUT_SEC", "20")), 3)
HTTP_CLIENT = httpx.Client(timeout=HTTP_TIMEOUT_SEC, trust_env=False, follow_redirects=False)
atexit.register(HTTP_CLIENT.close)
OPENVIKING_ROOT_URL = OPENVIKING_URL.split("/api/", 1)[0].rstrip("/")


def _resolve_search_type(search_type: str) -> str:
    if search_type in VALID_SEARCH_TYPES:
        return search_type
    return "all"


def _onecontext_no_match(result_text: str) -> bool:
    if not result_text:
        return True
    markers = [
        "Found 0 matches",
        "No matches found",
    ]
    return any(marker in result_text for marker in markers)


def _looks_like_identifier_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if q.startswith("ctx-"):
        return True
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", q, re.IGNORECASE):
        return True
    if re.search(r"\d{8}", q) and ("-" in q or "_" in q):
        return True
    # opaque IDs / tags are often dominated by digits + separators and are poor semantic queries
    alnum = sum(ch.isalnum() for ch in q)
    punct = sum(ch in "-_:" for ch in q)
    return len(q) >= 12 and punct >= 2 and alnum >= 6


def _normalize_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    if isinstance(tags, str):
        raw = tags.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except Exception:
            pass
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(tags).strip()]


def _safe_filename(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip().lower())
    s = s.strip("._-")
    return (s or "memory")[:120]


def _secure_write_text(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _local_exact_resource_matches(query: str, limit: int = 3) -> list[dict[str, Any]]:
    root = os.path.join(LOCAL_STORAGE_ROOT, "resources", "shared")
    if not os.path.isdir(root):
        return []

    files: list[str] = []
    for base, _, names in os.walk(root):
        for name in names:
            if name.startswith("."):
                continue
            if name.lower().endswith((".md", ".txt", ".json", ".jsonl", ".log")):
                files.append(os.path.join(base, name))

    if not files:
        return []

    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    matches: list[dict[str, Any]] = []
    ql = query.lower()

    for path in files[:400]:
        hit_in = None
        snippet = ""
        rel_path = os.path.relpath(path, root)
        if ql in rel_path.lower():
            hit_in = "path"
            snippet = rel_path
        else:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(120_000)
            except Exception:
                continue
            idx = text.lower().find(ql)
            if idx >= 0:
                hit_in = "content"
                start = max(0, idx - 120)
                end = min(len(text), idx + len(query) + 120)
                snippet = re.sub(r"\s+", " ", text[start:end]).strip()

        if hit_in:
            matches.append(
                {
                    "uri_hint": f"viking://resources/{rel_path.replace(os.sep, '/')}",
                    "file_path": path,
                    "matched_in": hit_in,
                    "mtime": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
                    "snippet": snippet,
                }
            )
        if len(matches) >= limit:
            break

    return matches


def _build_snippet(text: str, query: str, use_regex: bool, radius: int = 80) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""

    if use_regex and len(query) <= 200:
        try:
            pattern = re.compile(query, re.IGNORECASE)
            match = pattern.search(compact)
        except re.error:
            match = None
    else:
        idx = compact.lower().find(query.lower())
        if idx < 0:
            match = None
        else:
            _s, _e = idx, idx + len(query)
            match = type("_Span", (), {"start": staticmethod(lambda _s=_s: _s), "end": staticmethod(lambda _e=_e: _e)})()

    if not match:
        return compact[: radius * 2]

    start = max(0, match.start() - radius)
    end = min(len(compact), match.end() + radius)
    return compact[start:end]


def _try_cli_search(query: str, search_type: str, limit: int, no_regex: bool) -> str:
    candidates = [
        os.environ.get("ONECONTEXT_BIN", ""),
        "onecontext",
        "aline",
        os.path.expanduser("~/.local/bin/aline"),
        os.path.expanduser("~/.npm-global/bin/onecontext"),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        if "/" in candidate:
            cmd_path = candidate if os.path.exists(candidate) else None
        else:
            cmd_path = shutil.which(candidate)
        if not cmd_path:
            continue

        cmd = [cmd_path, "search", query, "-t", search_type, "-l", str(limit)]
        if no_regex:
            cmd.append("--no-regex")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception:
            continue

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        # newer aline help may hide subcommands but search still works; non-zero with no useful output -> try fallback
        if result.returncode == 0 and stdout:
            return f"--- ONECONTEXT SEARCH RESULTS (cli: {os.path.basename(cmd_path)}) ---\n{stdout}"

        unknown_cmd_markers = [
            "No such command",
            "Unknown command",
            "Usage:",
        ]
        if any(m in stderr for m in unknown_cmd_markers):
            continue

        if stdout:
            return f"--- ONECONTEXT SEARCH RESULTS (cli: {os.path.basename(cmd_path)}) ---\n{stdout}"

    return ""


def _sqlite_search(query: str, search_type: str, limit: int, no_regex: bool) -> str:
    if not os.path.exists(ALINE_DB_PATH):
        return f"OneContext fallback failed: DB not found at {ALINE_DB_PATH}"

    use_regex = not no_regex
    regex_obj = None
    if use_regex:
        # Guard against ReDoS: reject overly complex patterns
        if len(query) > 200:
            use_regex = False
        else:
            try:
                regex_obj = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                return f"OneContext fallback failed: invalid regex `{query}` ({exc})"

    def _matched(text: str) -> bool:
        if not text:
            return False
        if use_regex:
            return bool(regex_obj.search(text))
        return query.lower() in text.lower()

    results: list[dict[str, Any]] = []
    hard_limit = max(limit, 1) * 8

    conn = None
    try:
        conn = sqlite3.connect(ALINE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if search_type in ("all", "event"):
            rows = cur.execute(
                """
                SELECT id, title, description, created_at
                FROM events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (hard_limit,),
            ).fetchall()
            for r in rows:
                text = f"{r['title'] or ''}\n{r['description'] or ''}"
                if _matched(text):
                    results.append(
                        {
                            "type": "event",
                            "id": r["id"],
                            "title": r["title"] or "",
                            "snippet": _build_snippet(text, query, use_regex),
                        }
                    )

        if search_type in ("all", "session"):
            rows = cur.execute(
                """
                SELECT id, session_type, session_title, session_summary, created_at
                FROM sessions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (hard_limit,),
            ).fetchall()
            for r in rows:
                text = f"{r['session_title'] or ''}\n{r['session_summary'] or ''}"
                if _matched(text):
                    results.append(
                        {
                            "type": "session",
                            "id": r["id"],
                            "title": f"{r['session_type']} | {(r['session_title'] or '').strip()}",
                            "snippet": _build_snippet(text, query, use_regex),
                        }
                    )

        if search_type in ("all", "turn"):
            rows = cur.execute(
                """
                SELECT t.id, t.session_id, t.turn_number, t.llm_title, t.user_message, t.assistant_summary
                FROM turns t
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (hard_limit * 2,),
            ).fetchall()
            for r in rows:
                text = f"{r['llm_title'] or ''}\n{r['user_message'] or ''}\n{r['assistant_summary'] or ''}"
                if _matched(text):
                    results.append(
                        {
                            "type": "turn",
                            "id": r["id"],
                            "title": f"session={r['session_id']} turn={r['turn_number']}",
                            "snippet": _build_snippet(text, query, use_regex),
                        }
                    )

        if search_type in ("all", "content"):
            rows = cur.execute(
                """
                SELECT tc.turn_id, t.session_id, t.turn_number, substr(tc.content, 1, 60000) AS content_excerpt
                FROM turn_content tc
                JOIN turns t ON t.id = tc.turn_id
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (max(hard_limit, 40),),
            ).fetchall()
            for r in rows:
                text = r["content_excerpt"] or ""
                if _matched(text):
                    results.append(
                        {
                            "type": "content",
                            "id": r["turn_id"],
                            "title": f"session={r['session_id']} turn={r['turn_number']}",
                            "snippet": _build_snippet(text, query, use_regex, radius=120),
                        }
                    )
    except Exception as exc:
        return f"OneContext fallback failed: sqlite query error ({exc})"
    finally:
        if conn:
            conn.close()

    if not results:
        return "No matches found in OneContext history (sqlite fallback)."

    lines = [f"--- ONECONTEXT SEARCH RESULTS (sqlite fallback: {ALINE_DB_PATH}) ---"]
    for item in results[:limit]:
        lines.append(f"[{item['type']}] {item['id']} | {item['title']}")
        lines.append(f"  {item['snippet']}")
    lines.append(f"\nFound {len(results)} matches (showing up to {limit}).")
    return "\n".join(lines)


@mcp.tool()
def save_conversation_memory(title: str, content: str, tags: list[str] | str | None = None) -> str:
    """
    Save a generalized conversation summary or key conclusions to OpenViking.
    """
    title = (title or "").strip()
    content = (content or "").strip()
    if not title:
        return "Failed to save memory: title cannot be empty."
    if not content:
        return "Failed to save memory: content cannot be empty."
    tags = _normalize_tags(tags)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_title = _safe_filename(title)
    uri = f"viking://resources/shared/conversations/{timestamp}_{filename_title}.md"

    local_path = os.path.join(LOCAL_STORAGE_ROOT, "resources", "shared", "conversations")
    os.makedirs(local_path, exist_ok=True)
    try:
        os.chmod(local_path, 0o700)
    except OSError:
        pass
    file_path = os.path.join(local_path, f"{timestamp}_{filename_title}.md")

    formatted_content = f"# {title}\n\nTags: {', '.join(tags)}\nDate: {datetime.now().isoformat()}\n\n{content}\n"
    _secure_write_text(file_path, formatted_content)

    target = uri.rsplit("/", 1)[0]
    payload = {
        "path": file_path,
        "target": target,
        "reason": "save_conversation",
        "instruction": f"Index global conversation memory: {title}",
    }

    try:
        response = HTTP_CLIENT.post(f"{OPENVIKING_URL}/resources", json=payload)
        response.raise_for_status()
        return f"Successfully saved memory to OpenViking: {uri}"
    except Exception as e:
        return f"Saved to local file {file_path}, but failed to index in OpenViking: {str(e)}"


@mcp.tool()
def query_viking_memory(query: str, limit: int = 3) -> str:
    """
    Search OpenViking global memory for relevant context.
    """
    safe_limit = max(1, min(int(limit), 50))
    payload = {
        "query": query,
        "target_uri": "viking://resources",
        "limit": safe_limit,
    }

    try:
        output = []

        # Hybrid retrieval: exact local scan for opaque IDs/tags, then semantic API.
        if _looks_like_identifier_query(query):
            exact_matches = _local_exact_resource_matches(query, limit=max(1, safe_limit))
            if exact_matches:
                output.append("--- EXACT LOCAL RESOURCE MATCHES (ID/TAG fallback) ---")
                for item in exact_matches:
                    output.append(json.dumps(item, ensure_ascii=False, indent=2))

        response = HTTP_CLIENT.post(f"{OPENVIKING_URL}/search/find", json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "ok":
            resources = data.get("result", {}).get("resources", [])
            memories = data.get("result", {}).get("memories", [])
            if resources:
                output.append("--- FOUND RESOURCES ---")
                for r in resources:
                    output.append(json.dumps(r, ensure_ascii=False, indent=2))
            if memories:
                output.append("--- FOUND MEMORIES ---")
                for m in memories:
                    output.append(json.dumps(m, ensure_ascii=False, indent=2))
            if output:
                return "\n".join(output)
            return "No relevant context found in OpenViking."
        return f"API returned non-ok status: {data}"
    except Exception as e:
        return f"Failed to query OpenViking: {str(e)}"


@mcp.tool()
def search_onecontext_history(query: str, search_type: str = "all", limit: int = 10, no_regex: bool = False) -> str:
    """
    Search OneContext history. CLI-first, sqlite fallback for compatibility.
    """
    normalized_type = _resolve_search_type(search_type)
    safe_limit = max(1, min(int(limit), 100))

    cli_result = _try_cli_search(query, normalized_type, safe_limit, no_regex)
    if cli_result and not (_onecontext_no_match(cli_result) and normalized_type == "all" and _looks_like_identifier_query(query)):
        return cli_result

    if normalized_type == "all" and _looks_like_identifier_query(query):
        cli_content_result = _try_cli_search(query, "content", safe_limit, no_regex)
        if cli_content_result and not _onecontext_no_match(cli_content_result):
            return (
                "Note: auto-fallback to OneContext content search for ID/tag query after `all` returned no matches.\n"
                + cli_content_result
            )

    sqlite_result = _sqlite_search(query, normalized_type, safe_limit, no_regex)
    if not (_onecontext_no_match(sqlite_result) and normalized_type == "all" and _looks_like_identifier_query(query)):
        return sqlite_result

    if normalized_type == "all" and _looks_like_identifier_query(query):
        sqlite_content = _sqlite_search(query, "content", safe_limit, no_regex)
        if not _onecontext_no_match(sqlite_content):
            return (
                "Note: auto-fallback to OneContext content search for ID/tag query after `all` returned no matches.\n"
                + sqlite_content
            )

    return sqlite_result


@mcp.tool()
def context_system_health() -> str:
    """
    Unified health snapshot for OneContext + OpenViking + daemon.
    """
    report: dict[str, Any] = {
        "checked_at": datetime.now().isoformat(),
        "openviking": {"ok": False},
        "onecontext": {"ok": False},
        "daemon": {"ok": False},
    }

    try:
        resp = HTTP_CLIENT.get(f"{OPENVIKING_ROOT_URL}/health", timeout=5)
        if resp.status_code == 200:
            report["openviking"] = {"ok": True, "status_code": 200, "probe": "GET /health"}
        else:
            deep = HTTP_CLIENT.post(
                f"{OPENVIKING_URL}/search/find",
                json={"query": "__healthcheck__", "target_uri": "viking://resources", "limit": 1},
                timeout=8,
            )
            report["openviking"] = {
                "ok": deep.status_code == 200,
                "status_code": deep.status_code,
                "probe": "POST /api/v1/search/find",
            }
    except Exception as exc:
        report["openviking"] = {"ok": False, "error": str(exc)}

    cli_result = _try_cli_search("__healthcheck__", "all", 1, True)
    report["onecontext"] = {"ok": bool(cli_result), "mode": "cli" if cli_result else "sqlite_fallback"}
    if not cli_result:
        fallback = _sqlite_search("__healthcheck__", "all", 1, True)
        report["onecontext"]["fallback_probe"] = "ok" if "sqlite fallback" in fallback.lower() else "no_match"

    try:
        daemon = subprocess.run(["pgrep", "-f", "viking_daemon.py"], capture_output=True, text=True, timeout=5)
        pids = [x.strip() for x in daemon.stdout.splitlines() if x.strip()]
        report["daemon"] = {"ok": bool(pids), "pids": pids[:5]}
    except Exception as exc:
        report["daemon"] = {"ok": False, "error": str(exc)}

    report["all_ok"] = bool(report["openviking"]["ok"] and report["onecontext"]["ok"] and report["daemon"]["ok"])
    return json.dumps(report, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
