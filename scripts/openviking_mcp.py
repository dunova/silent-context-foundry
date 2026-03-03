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
try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - optional runtime dependency
    FastMCP = None  # type: ignore[assignment]
try:
    from memory_index import (
        get_observations_by_ids,
        index_stats,
        search_index,
        strip_private_blocks,
        sync_index_from_storage,
        timeline_index,
    )
except Exception:  # pragma: no cover - module import path compatibility
    from .memory_index import (  # type: ignore[import-not-found]
        get_observations_by_ids,
        index_stats,
        search_index,
        strip_private_blocks,
        sync_index_from_storage,
        timeline_index,
    )

ALLOW_NOOP_MCP = os.environ.get("ALLOW_NOOP_MCP", "0") == "1"
_MCP_INIT_ERROR: str = ""


class _NoopMCP:
    """Fallback MCP object used in test environments with mocked FastMCP."""

    @staticmethod
    def tool(*_args, **_kwargs):
        def _decorator(func):
            return func

        return _decorator

    @staticmethod
    def run(*_args, **_kwargs):
        return None


def _create_mcp_server():
    """Create MCP server; degrade to no-op when FastMCP is mocked/unavailable."""
    global _MCP_INIT_ERROR
    try:
        if FastMCP is None:
            raise RuntimeError("FastMCP is unavailable")
        if getattr(FastMCP, "__module__", "").startswith("unittest.mock"):
            raise RuntimeError("FastMCP is mocked")
        server = FastMCP("OpenViking Global Memory Server")
        if getattr(type(server), "__module__", "").startswith("unittest.mock"):
            raise RuntimeError("FastMCP server is mocked")
        return server
    except Exception as exc:
        _MCP_INIT_ERROR = str(exc)
        return _NoopMCP()


mcp = _create_mcp_server()

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
ONECONTEXT_CLI_TIMEOUT_SEC = max(int(os.environ.get("ONECONTEXT_CLI_TIMEOUT_SEC", "12")), 3)
_ONECONTEXT_CLI_CANDIDATES: list[str] | None = None

# ─── Intent Pre-filter (memU-inspired, zero network dependency) ───────────────
# Exact no-retrieve token set – kept deliberately tight so we never drop a
# real query; false-positives here mean wasted DB calls, not missing data.
_NO_RETRIEVE_EXACT: frozenset[str] = frozenset({
    "hi", "hello", "hey", "yo", "ok", "okay", "k", "kk", "sure",
    "thanks", "thank you", "thx", "ty", "np", "nice", "lgtm",
    "got it", "understood", "noted", "great", "good", "cool", "👍",
    "bye", "goodbye", "see you", "later", "exit", "quit", "stop",
    "yes", "no", "yep", "nope", "nah", "uh huh", "uh uh",
})
# Prefix patterns for social openers (do not immediately skip if meaningful tail exists)
_NO_RETRIEVE_PREFIXES = re.compile(
    r"^(hi\b|hello\b|hey\b|yo\b|good\s*(morning|afternoon|evening|night)\b"
    r"|morning\b|evening\b|午安|早安|晚安|你好|嗨|哈喽|谢谢|thanks?\b|thank you\b|再见|拜拜)",
    re.IGNORECASE
)
_SOCIAL_PREFIXES = re.compile(
    r"^(hi|hello|hey|yo|你好|嗨|哈喽|您好|谢谢|thanks?|thank you|ok|okay|好的|收到|明白|请问|麻烦)[,\s，。.!！？?、]*",
    re.IGNORECASE,
)
_RETRIEVE_INTENT_HINTS = re.compile(
    r"(\?|？|\b(what|which|who|when|where|why|how|recap|review|remember|history|decision|decisions|project|projects|preference|preferences|context|summary|worked|did we|yesterday|last week)\b"
    r"|回顾|总结|检索|查询|历史|记录|决策|偏好|项目|上下文|记忆|昨天|上周|之前|为什么|怎么|如何|哪些|什么|谁|请帮)",
    re.IGNORECASE,
)
_SOCIAL_ONLY_TAIL = re.compile(
    r"^(good\s*(morning|afternoon|evening|night)|morning|afternoon|evening|night|你好|嗨|哈喽|thanks?|thank you|谢谢|再见|拜拜|好的|ok|okay)[!,.，。！？?\s]*$",
    re.IGNORECASE,
)


def _strip_social_prefixes(query: str) -> str:
    current = (query or "").strip()
    for _ in range(3):
        nxt = _SOCIAL_PREFIXES.sub("", current, count=1).strip()
        if nxt == current:
            break
        current = nxt
    return current

def _decide_retrieval_intent(query: str) -> bool:
    """Zero-latency, zero-network intent gate (memU-inspired).

    Returns False (skip retrieval) only when the query is demonstrably a greeting
    or pure social noise. Falls back to True (allow retrieval) for anything else,
    including ambiguous cases.
    """
    q = (query or "").strip()
    if not q:
        return False   # empty query – nothing to retrieve
    q_lower = q.lower().rstrip("!.,。？?！")
    # 1. Exact membership check (O(1))
    if q_lower in _NO_RETRIEVE_EXACT:
        return False
    # 2. Always keep identifier-like queries retrievable
    if _looks_like_identifier_query(q):
        return True
    # 3. Obvious retrieval intent (question marks, history/decision keywords)
    if _RETRIEVE_INTENT_HINTS.search(q):
        return True
    # 4. Pure social opener without meaningful tail should be skipped
    if _NO_RETRIEVE_PREFIXES.match(q_lower):
        tail = _strip_social_prefixes(q)
        if not tail:
            return False
        if _looks_like_identifier_query(tail) or _RETRIEVE_INTENT_HINTS.search(tail):
            return True
        if _SOCIAL_ONLY_TAIL.match(tail):
            return False
        token_count = len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", tail))
        if len(tail) <= 8 and token_count <= 3:
            return False
        return True
    # 5. Ambiguous cases default to retrieve (safe by design)
    return True


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
    if any(marker in result_text for marker in markers):
        return True
    # 兼容仅返回标题/检索语句的空命中输出，避免误判为“有结果”。
    compact_lines = [ln.strip() for ln in result_text.splitlines() if ln.strip()]
    if len(compact_lines) <= 3 and any(
        ln.startswith("Regex Search:") or ln.startswith("Search Results for:") for ln in compact_lines
    ):
        return True
    return False


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


def _to_day_start_epoch(day: str) -> int | None:
    raw = (day or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return int(dt.timestamp())
    except Exception:
        return None


def _to_day_end_epoch(day: str) -> int | None:
    start = _to_day_start_epoch(day)
    if start is None:
        return None
    return start + 86399


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


def _discover_onecontext_cli_candidates() -> list[str]:
    global _ONECONTEXT_CLI_CANDIDATES
    if _ONECONTEXT_CLI_CANDIDATES is not None:
        return _ONECONTEXT_CLI_CANDIDATES

    raw_candidates = [
        os.environ.get("ONECONTEXT_BIN", ""),
        "onecontext",
        "aline",
        os.path.expanduser("~/.local/bin/aline"),
        os.path.expanduser("~/.npm-global/bin/onecontext"),
    ]

    resolved: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        if not candidate:
            continue
        if "/" in candidate:
            cmd_path = candidate if os.path.exists(candidate) else None
        else:
            cmd_path = shutil.which(candidate)
        if not cmd_path:
            continue
        real = os.path.realpath(cmd_path)
        if real in seen:
            continue
        seen.add(real)
        resolved.append(real)

    _ONECONTEXT_CLI_CANDIDATES = resolved
    return resolved


def _try_cli_search(query: str, search_type: str, limit: int, no_regex: bool) -> str:
    for cmd_path in _discover_onecontext_cli_candidates():
        cmd = [cmd_path, "search", query, "-t", search_type, "-l", str(limit)]
        if no_regex:
            cmd.append("--no-regex")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=ONECONTEXT_CLI_TIMEOUT_SEC)
        except Exception:
            continue

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        mixed = f"{stdout}\n{stderr}".strip()

        # 排除帮助页/命令说明误判，避免把“无效调用”当成检索结果。
        if "Track and version AI agent chat sessions" in mixed and "Usage:" in mixed and "Commands:" in mixed:
            continue

        # newer aline help may hide subcommands but search still works; non-zero with no useful output -> try fallback
        if result.returncode == 0 and stdout:
            return f"--- ONECONTEXT SEARCH RESULTS (cli: {os.path.basename(cmd_path)}) ---\n{stdout}"

        unknown_cmd_markers = [
            "No such command",
            "Unknown command",
            "Usage:",
        ]
        if any(m in mixed for m in unknown_cmd_markers):
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
def workflow_important() -> str:
    """
    3-layer retrieval workflow guide for token-efficient memory search.
    """
    return (
        "3-layer workflow (always follow):\n"
        "1) search(query) -> get compact index IDs\n"
        "2) timeline(anchor=<id>) -> get chronological context\n"
        "3) get_observations(ids=[...]) -> fetch full details only for filtered IDs\n"
        "Never fetch all details before filtering."
    )


@mcp.tool()
def search(
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    source_type: str = "all",
    date_start: str = "",
    date_end: str = "",
) -> str:
    """
    Layer-1 search: returns compact observation index with IDs.
    """
    sync_info = sync_index_from_storage()
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    start_epoch = _to_day_start_epoch(date_start)
    end_epoch = _to_day_end_epoch(date_end)
    results = search_index(
        query=strip_private_blocks(query),
        limit=safe_limit,
        offset=safe_offset,
        source_type=source_type or "all",
        date_start_epoch=start_epoch,
        date_end_epoch=end_epoch,
    )

    payload = {
        "workflow": "search -> timeline -> get_observations",
        "sync": sync_info,
        "count": len(results),
        "results": [
            {
                "id": r["id"],
                "time": r["created_at"],
                "title": r["title"],
                "source_type": r["source_type"],
                "session_id": r["session_id"],
                "tags": r["tags"],
            }
            for r in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def timeline(anchor: int = 0, query: str = "", depth_before: int = 3, depth_after: int = 3) -> str:
    """
    Layer-2 timeline: returns chronological context around anchor observation.
    """
    sync_info = sync_index_from_storage()
    anchor_id = int(anchor or 0)
    if anchor_id <= 0 and query.strip():
        found = search_index(strip_private_blocks(query), limit=1, offset=0, source_type="all")
        if found:
            anchor_id = int(found[0]["id"])
    if anchor_id <= 0:
        return json.dumps({"error": "anchor not found. provide anchor id or query.", "sync": sync_info}, ensure_ascii=False)

    rows = timeline_index(
        anchor_id=anchor_id,
        depth_before=max(0, min(int(depth_before), 20)),
        depth_after=max(0, min(int(depth_after), 20)),
    )
    payload = {
        "anchor": anchor_id,
        "sync": sync_info,
        "count": len(rows),
        "timeline": [
            {
                "id": r["id"],
                "time": r["created_at"],
                "title": r["title"],
                "source_type": r["source_type"],
                "session_id": r["session_id"],
            }
            for r in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def get_observations(ids: list[int], limit: int = 100) -> str:
    """
    Layer-3 detail fetch: returns full observation details by IDs.
    """
    sync_info = sync_index_from_storage()
    rows = get_observations_by_ids(ids=[int(x) for x in ids], limit=max(1, min(int(limit), 300)))
    payload = {
        "sync": sync_info,
        "count": len(rows),
        "observations": rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def save_conversation_memory(title: str, content: str, tags: list[str] | str | None = None) -> str:
    """
    Save a generalized conversation summary or key conclusions to OpenViking.
    """
    title = strip_private_blocks((title or "").strip())
    content = strip_private_blocks((content or "").strip())
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
    try:
        sync_index_from_storage()
    except Exception:
        pass

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
    safe_query = strip_private_blocks((query or "").strip())
    if not safe_query:
        return "Failed to query OpenViking: query is empty after private-block sanitization."
    if not _decide_retrieval_intent(safe_query):
        return "Intent Check: Query categorized as common affirmation/chat. Skipped memory retrieval to save context."

    safe_limit = max(1, min(int(limit), 50))
    payload = {
        "query": safe_query,
        "target_uri": "viking://resources",
        "limit": safe_limit,
    }

    try:
        output = []

        # Hybrid retrieval: exact local scan for opaque IDs/tags, then semantic API.
        if _looks_like_identifier_query(safe_query):
            exact_matches = _local_exact_resource_matches(safe_query, limit=max(1, safe_limit))
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
    safe_query = strip_private_blocks((query or "").strip())
    if not safe_query:
        return "Intent Check: Empty query after private-block sanitization. Skipped retrieval."
    if not _decide_retrieval_intent(safe_query):
        return "Intent Check: Query categorized as common affirmation/chat. Skipped sqlite DB retrieval to save context."

    normalized_type = _resolve_search_type(search_type)
    safe_limit = max(1, min(int(limit), 100))

    cli_result = _try_cli_search(safe_query, normalized_type, safe_limit, no_regex)
    if cli_result and not (_onecontext_no_match(cli_result) and normalized_type == "all" and _looks_like_identifier_query(safe_query)):
        return cli_result

    if normalized_type == "all" and _looks_like_identifier_query(safe_query):
        cli_content_result = _try_cli_search(safe_query, "content", safe_limit, no_regex)
        if cli_content_result and not _onecontext_no_match(cli_content_result):
            return (
                "Note: auto-fallback to OneContext content search for ID/tag query after `all` returned no matches.\n"
                + cli_content_result
            )

    sqlite_result = _sqlite_search(safe_query, normalized_type, safe_limit, no_regex)
    if not (_onecontext_no_match(sqlite_result) and normalized_type == "all" and _looks_like_identifier_query(safe_query)):
        return sqlite_result

    if normalized_type == "all" and _looks_like_identifier_query(safe_query):
        sqlite_content = _sqlite_search(safe_query, "content", safe_limit, no_regex)
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

    try:
        report["memory_index"] = {"ok": True, **index_stats()}
    except Exception as exc:
        report["memory_index"] = {"ok": False, "error": str(exc)}

    report["all_ok"] = bool(
        report["openviking"]["ok"]
        and report["onecontext"]["ok"]
        and report["daemon"]["ok"]
        and report["memory_index"]["ok"]
    )
    return json.dumps(report, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    if isinstance(mcp, _NoopMCP) and not ALLOW_NOOP_MCP:
        raise SystemExit(
            "FastMCP is unavailable; refusing to run in no-op mode. "
            f"reason={_MCP_INIT_ERROR or 'unknown'}. "
            "Install mcp.server.fastmcp or set ALLOW_NOOP_MCP=1 for test-only execution."
        )
    mcp.run(transport="stdio")
