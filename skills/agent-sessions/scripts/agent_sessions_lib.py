"""Shared library for the agent-sessions skill.

Sweeps the Claude Code / codex / agy (Google Antigravity CLI) session stores
read-only. SQLite is always opened through a mode=ro URI. Nothing in this file
writes or deletes a file.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import time

HOME = os.path.expanduser("~")

# ---------------------------------------------------------------- store locations

CLAUDE_PROJECTS_ROOT = os.path.join(HOME, ".claude", "projects")

# CODEX_HOME relocates the whole codex home, so the environment variable wins.
# The rest are fallbacks for a codex started somewhere else.
ORCA_CODEX_RUNTIME_SUFFIX = os.path.join("orca", "codex-runtime-home", "home")
CODEX_HOME_CANDIDATES = [
    os.environ.get("CODEX_HOME") or "",
    os.path.join(HOME, ".codex"),
    # The home Orca terminals swap CODEX_HOME to. Most real sessions live here.
    # Outside Orca the variable is unset, so name the per-OS defaults directly.
    os.path.join(HOME, ".local", "share", ORCA_CODEX_RUNTIME_SUFFIX),
    os.path.join(HOME, "Library", "Application Support", ORCA_CODEX_RUNTIME_SUFFIX),
]
# Windows-side codex homes as seen from Windows Subsystem for Linux.
# Drive letter and automount root differ per machine, so both are wildcards.
CODEX_HOME_GLOBS = [
    "/mnt/*/Users/*/.codex",
    "/media/*/Users/*/.codex",
]

AGY_ROOT = os.path.join(HOME, ".gemini", "antigravity-cli")
AGY_CONVERSATIONS = os.path.join(AGY_ROOT, "conversations")
AGY_HISTORY = os.path.join(AGY_ROOT, "history.jsonl")

# First user message of the sub-session codex spawns to assess an approval.
APPROVAL_MARKER = "The following is the Codex agent history whose request action you are assessing"

# System preamble codex pushes in front of the conversation as the user role.
CODEX_PREAMBLE_PREFIXES = (
    "# AGENTS.md instructions",
    "<user_instructions>",
    "<environment_context>",
    "<recommended_plugins>",
    "<INSTRUCTIONS>",
)

# System preamble Claude Code pushes in as the user role.
CLAUDE_PREAMBLE_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<system-reminder>",
    "Caveat: The messages below were generated",
    "<user-prompt-submit-hook>",
    "<task-notification>",
    "<bash-input>",
    "<bash-stdout>",
    "[Request interrupted",
    "API Error",
)

# Instructions injected into the model, not typed by a human: hidden by default.
PREAMBLE_PREFIXES = CODEX_PREAMBLE_PREFIXES + CLAUDE_PREAMBLE_PREFIXES + (
    "<skills_instructions>", "<multi_agent_mode>", "You are `/root`",
)

LIVE_WINDOW_SECONDS = 900


def is_preamble(event):
    if str(event.get("role", "")).startswith("developer"):
        return True
    if event.get("kind") not in ("text", "queued"):
        return False
    return (event.get("text") or "").lstrip().startswith(PREAMBLE_PREFIXES)


def codex_homes():
    """Existing codex home directories, deduplicated, order preserved."""
    out = []
    cands = list(CODEX_HOME_CANDIDATES)
    for pattern in CODEX_HOME_GLOBS:
        cands.extend(sorted(glob.glob(pattern)))
    for cand in cands:
        if not cand:
            continue
        cand = os.path.realpath(os.path.expanduser(cand))
        if cand in out:
            continue
        if os.path.isdir(os.path.join(cand, "sessions")):
            out.append(cand)
    return out


def project_slug(cwd):
    """Turn an absolute path into a Claude Code project directory name."""
    return os.path.abspath(os.path.expanduser(cwd)).replace("/", "-")


# ---------------------------------------------------------------- utilities

def _clean(text, limit=None):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def ro_connect(path):
    """Open SQLite read-only. Safe even on a running session's database."""
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def iso(epoch_seconds):
    if not epoch_seconds:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch_seconds))


# ---------------------------------------------------------------- session records

class Session(dict):
    """agent / session_id / cwd / updated / first_user_message / path / kind / live."""

    @property
    def path(self):
        return self["path"]


def _mk(agent, session_id, path, cwd, updated, first_user, kind="main",
        live=False, extra=None):
    rec = Session(
        agent=agent,
        session_id=session_id or "",
        path=path,
        cwd=cwd or "",
        updated=updated,
        first_user_message=_clean(first_user, 4000),
        kind=kind,
        live=bool(live),
    )
    if extra:
        rec.update(extra)
    return rec


# ---------------------------------------------------------------- Claude Code

def _claude_scan(path, live_window):
    """Read only the head to pull out cwd and the first user message."""
    cwd = ""
    session_id = os.path.splitext(os.path.basename(path))[0]
    first_user = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh):
                if lineno > 4000:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not cwd and rec.get("cwd"):
                    cwd = rec["cwd"]
                if not first_user:
                    text = _claude_user_text(rec)
                    if text:
                        first_user = text
                if cwd and first_user:
                    break
    except OSError:
        return None
    mtime = _mtime(path)
    return _mk(
        "claude", session_id, path, cwd, mtime, first_user,
        kind="main",
        live=(time.time() - mtime) < live_window,
    )


def _claude_user_text(rec):
    """Only what the user actually typed: no system preamble, no tool_result."""
    if rec.get("type") == "queue-operation" and rec.get("operation") == "enqueue":
        text = rec.get("content") or ""
    elif rec.get("type") == "user":
        if rec.get("isSidechain"):
            return ""
        message = rec.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n".join(p for p in parts if p)
        else:
            return ""
    else:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith(CLAUDE_PREAMBLE_PREFIXES):
        return ""
    return stripped


def _prefilter(paths, since_epoch, max_scan):
    """Narrow the candidates by mtime alone; parsing bodies is expensive."""
    pairs = [(p, _mtime(p)) for p in paths]
    if since_epoch:
        pairs = [pair for pair in pairs if pair[1] >= since_epoch]
    pairs.sort(key=lambda pair: pair[1], reverse=True)
    if max_scan:
        pairs = pairs[:max_scan]
    return [p for p, _ in pairs]


def list_claude_sessions(live_window=LIVE_WINDOW_SECONDS, cwd_filter=None,
                         since_epoch=None, max_scan=None):
    out = []
    if cwd_filter and cwd_filter.startswith(("/", "~")):
        dirs = [os.path.join(CLAUDE_PROJECTS_ROOT, project_slug(cwd_filter))]
        if not os.path.isdir(dirs[0]):
            dirs = sorted(glob.glob(os.path.join(CLAUDE_PROJECTS_ROOT, "*")))
    else:
        dirs = sorted(glob.glob(os.path.join(CLAUDE_PROJECTS_ROOT, "*")))
    paths = []
    for d in dirs:
        paths.extend(glob.glob(os.path.join(d, "*.jsonl")))
    for path in _prefilter(paths, since_epoch, max_scan):
        rec = _claude_scan(path, live_window)
        if rec:
            out.append(rec)
    return out


def read_claude_session(path, include_thinking=False):
    """Flatten a Claude Code session into an ordered event list."""
    events = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            kind = rec.get("type")
            ts = rec.get("timestamp") or ""
            side = " (sidechain)" if rec.get("isSidechain") else ""
            if kind == "user":
                message = rec.get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    events.append({"ts": ts, "role": "user", "kind": "text",
                                   "text": content})
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            events.append({"ts": ts, "role": "user",
                                           "kind": "text",
                                           "text": block.get("text", "")})
                        elif btype == "tool_result":
                            events.append({"ts": ts, "role": "tool",
                                           "kind": "tool_result",
                                           "text": _stringify(block.get("content"))})
                        elif btype == "image":
                            events.append({"ts": ts, "role": "user",
                                           "kind": "image", "text": "<image>"})
            elif kind == "assistant":
                message = rec.get("message") or {}
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        events.append({"ts": ts, "role": "assistant" + side,
                                       "kind": "text",
                                       "text": block.get("text", "")})
                    elif btype == "thinking" and include_thinking:
                        events.append({"ts": ts, "role": "assistant" + side,
                                       "kind": "thinking",
                                       "text": block.get("thinking", "")})
                    elif btype == "tool_use":
                        events.append({
                            "ts": ts, "role": "assistant" + side,
                            "kind": "tool_use",
                            "name": block.get("name", ""),
                            "text": json.dumps(block.get("input") or {},
                                               ensure_ascii=False),
                        })
            elif kind == "queue-operation" and rec.get("operation") == "enqueue":
                events.append({"ts": ts, "role": "user", "kind": "queued",
                               "text": rec.get("content") or ""})
    return events


def _stringify(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


# ---------------------------------------------------------------- codex

def _codex_rollout_paths(home):
    return glob.glob(os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl"))


def _codex_message_text(payload):
    return "".join(block.get("text", "") for block in payload.get("content") or []
                   if isinstance(block, dict))


def _codex_scan(path, live_window, home):
    cwd = ""
    session_id = ""
    cli_version = ""
    first_user = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh):
                if lineno > 4000:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                rtype = rec.get("type")
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue
                if rtype == "session_meta":
                    cwd = payload.get("cwd") or ""
                    session_id = payload.get("session_id") or payload.get("id") or ""
                    cli_version = payload.get("cli_version") or ""
                elif (rtype == "response_item"
                      and payload.get("type") == "message"
                      and payload.get("role") == "user"
                      and not first_user):
                    text = _codex_message_text(payload).strip()
                    if text and not text.startswith(CODEX_PREAMBLE_PREFIXES):
                        first_user = text
                if cwd and first_user:
                    break
    except OSError:
        return None
    if not session_id:
        match = re.search(r"rollout-[\dT:-]+Z?-([0-9a-f-]{36})\.jsonl$", path)
        session_id = match.group(1) if match else os.path.basename(path)
    mtime = _mtime(path)
    lock = os.path.join(home, "thread-writer-locks", session_id + ".lock")
    kind = "approval" if first_user.startswith(APPROVAL_MARKER) else "main"
    return _mk(
        "codex", session_id, path, cwd, mtime, first_user, kind=kind,
        live=(time.time() - mtime) < live_window,
        extra={"codex_home": home, "cli_version": cli_version,
               "writer_lock": os.path.exists(lock)},
    )


def list_codex_sessions(live_window=LIVE_WINDOW_SECONDS, cwd_filter=None,
                        since_epoch=None, max_scan=None):
    out = []
    for home in codex_homes():
        for path in _prefilter(_codex_rollout_paths(home), since_epoch, max_scan):
            rec = _codex_scan(path, live_window, home)
            if rec:
                out.append(rec)
    return out


def read_codex_session(path, include_thinking=False):
    events = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "response_item":
                continue
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            ts = rec.get("timestamp") or ""
            ptype = payload.get("type")
            if ptype == "message":
                text = _codex_message_text(payload)
                events.append({"ts": ts, "role": payload.get("role") or "?",
                               "kind": "text", "text": text})
            elif ptype == "reasoning" and include_thinking:
                parts = []
                for key in ("summary", "content"):
                    for block in payload.get(key) or []:
                        if isinstance(block, dict):
                            parts.append(block.get("text", ""))
                events.append({"ts": ts, "role": "assistant", "kind": "thinking",
                               "text": "\n".join(p for p in parts if p)})
            elif ptype in ("function_call", "custom_tool_call", "local_shell_call"):
                events.append({
                    "ts": ts, "role": "assistant", "kind": "tool_use",
                    "name": payload.get("name") or ptype,
                    "text": _stringify(payload.get("arguments")
                                       or payload.get("input")
                                       or payload.get("action")),
                })
            elif ptype in ("function_call_output", "custom_tool_call_output",
                           "local_shell_call_output"):
                events.append({"ts": ts, "role": "tool", "kind": "tool_result",
                               "text": _stringify(payload.get("output"))})
    return events


def codex_state_databases(home):
    """State index database paths held by one codex home.

    codex bumps the number in the file name whenever the schema changes
    (state_5 as observed, state_6 next). A hard-coded number would silently
    return nothing the day codex is upgraded, so glob for it instead.
    """
    found = []
    for pattern in ("state_*.sqlite", os.path.join("sqlite", "state_*.sqlite")):
        found.extend(glob.glob(os.path.join(home, pattern)))
    return sorted(found, reverse=True)


def codex_index_rows(limit=200):
    """threads table of every codex home state index. One index per home."""
    rows = []
    seen = set()
    for home in codex_homes():
        for db in codex_state_databases(home):
            if db in seen:
                continue
            seen.add(db)
            try:
                conn = ro_connect(db)
                query = ("select id, cwd, coalesce(first_user_message, title, ''), "
                         "rollout_path, updated_at from threads "
                         "order by updated_at desc limit ?")
                for row in conn.execute(query, (limit,)):
                    rows.append({"db": db, "id": row[0], "cwd": row[1],
                                 "first_user_message": row[2],
                                 "rollout_path": row[3], "updated_at": row[4]})
                conn.close()
            except sqlite3.Error:
                continue
    return rows


# ---------------------------------------------------------------- agy

AGY_PRINTABLE = re.compile(rb"[^\x00-\x08\x0b-\x1f\x7f]{20,}")

# Meaning per step_type, confirmed against real databases.
AGY_STEP_TYPES = {
    14: ("user", "text"),
    15: ("assistant", "text"),
    132: ("assistant", "tool_use"),
    101: ("tool", "tool_result"),
}

_AGY_UUID_ONLY = re.compile(
    r"^[^0-9A-Za-z]*(?:(?:bot-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}[^0-9A-Za-z]*)+$")


def agy_history_index():
    """Group history.jsonl by conversationId; primary agy session lookup."""
    index = {}
    if not os.path.exists(AGY_HISTORY):
        return index
    with open(AGY_HISTORY, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cid = rec.get("conversationId")
            if not cid:
                continue
            entry = index.setdefault(cid, {
                "conversation_id": cid,
                "workspace": rec.get("workspace") or "",
                "first_display": rec.get("display") or "",
                "last_display": "",
                "first_ts": rec.get("timestamp") or 0,
                "last_ts": 0,
                "prompt_count": 0,
            })
            entry["last_display"] = rec.get("display") or entry["last_display"]
            entry["last_ts"] = max(entry["last_ts"], rec.get("timestamp") or 0)
            entry["first_ts"] = min(entry["first_ts"] or 1 << 62,
                                    rec.get("timestamp") or 1 << 62)
            entry["prompt_count"] += 1
            if rec.get("workspace"):
                entry["workspace"] = rec["workspace"]
    return index


def agy_prompts(conversation_id):
    """Verbatim user prompts left in history.jsonl. Older lines carry no
    conversationId, so they may not show up here."""
    out = []
    if not os.path.exists(AGY_HISTORY):
        return out
    with open(AGY_HISTORY, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("conversationId") == conversation_id:
                out.append(((rec.get("timestamp") or 0) / 1000.0,
                            rec.get("display") or ""))
    return out


def list_agy_sessions(live_window=LIVE_WINDOW_SECONDS, cwd_filter=None,
                      since_epoch=None, max_scan=None):
    index = agy_history_index()
    out = []
    seen = set()
    for path in glob.glob(os.path.join(AGY_CONVERSATIONS, "*.db")):
        cid = os.path.splitext(os.path.basename(path))[0]
        seen.add(cid)
        meta = index.get(cid, {})
        mtime = _mtime(path)
        # A non-empty write-ahead log means a write is still in progress. -shm
        # is no such signal: the agy process holds every conversation open.
        wal = _size(path + "-wal")
        out.append(_mk(
            "agy", cid, path, meta.get("workspace", ""), mtime,
            meta.get("first_display", ""),
            live=(wal > 0 or (time.time() - mtime) < live_window),
            extra={"prompt_count": meta.get("prompt_count", 0),
                   "last_prompt": _clean(meta.get("last_display", ""), 200),
                   "wal_bytes": wal},
        ))
    # A deleted database still leaves a record if history.jsonl has the thread.
    for cid, meta in index.items():
        if cid in seen:
            continue
        out.append(_mk(
            "agy", cid, "", meta.get("workspace", ""),
            (meta.get("last_ts") or 0) / 1000.0, meta.get("first_display", ""),
            extra={"prompt_count": meta.get("prompt_count", 0),
                   "note": "conversation database missing"},
        ))
    return out


def agy_printable(blob, raw=False):
    """Pull only the human-readable strings out of a protobuf blob."""
    if not blob:
        return ""
    parts = [m.decode("utf-8", "replace") for m in AGY_PRINTABLE.findall(blob)]
    if raw:
        return "\n".join(parts)
    kept = []
    for part in parts:
        stripped = part.strip()
        if not _agy_keep(stripped) or stripped in kept:
            continue
        kept.append(stripped)
    if not kept:
        return ""
    # The real body is by far the longest fragment; drop the protobuf header
    # scraps around it.
    longest = max(len(k) for k in kept)
    floor = min(60, max(20, int(longest * 0.15)))
    body = "\n".join(k for k in kept if len(k) >= floor or longest < 60)
    return _strip_framing_lines(body)


# protobuf field separators come out glued to the same fragment as the body,
# because a newline is a printable byte and one fragment spans several lines.
# That debris only clings to the first and last line of a fragment, so strip
# from both ends only. The middle is body text and stays untouched.
_AGY_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")


def _is_framing_line(line):
    # Replacement characters come from protobuf field boundaries; drop them
    # before judging.
    text = line.replace("\ufffd", "").strip()
    if not text:
        return True
    if not _agy_keep(text):
        return True
    if _AGY_OPAQUE_TOKEN.match(text) and any(c.isupper() for c in text) \
            and any(c.isdigit() for c in text):
        return True
    return False


def _strip_framing_lines(text):
    lines = text.split("\n")
    while lines and _is_framing_line(lines[0]):
        lines.pop(0)
    while lines and _is_framing_line(lines[-1]):
        lines.pop()
    return "\n".join(lines)


_AGY_UUID_ANY = re.compile(
    r"(?:bot-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_AGY_SNAKE_TOKEN = re.compile(r"^[a-z0-9_]{3,60}$")


def _agy_keep(text):
    if not text:
        return False
    if text.count("�") / len(text) > 0.15:
        return False
    if _AGY_UUID_ONLY.match(text) or _AGY_SNAKE_TOKEN.match(text):
        return False
    if "sessionID" in text:
        return False
    residue = _AGY_UUID_ANY.sub("", text)
    residue = re.sub(r"[^0-9A-Za-z가-힣]", "", residue)
    return len(residue) >= 8


def read_agy_session(path, include_thinking=False):
    """Flatten an agy conversation into an event list.

    step_payload is a protobuf blob and no public parser exists. What comes out
    is an approximation built from the printable bytes alone, NOT a lossless
    transcript.
    """
    events = []
    conn = ro_connect(path)
    try:
        rows = conn.execute(
            "select idx, step_type, status, metadata, step_payload from steps order by idx"
        ).fetchall()
    finally:
        conn.close()
    for idx, step_type, status, metadata, payload in rows:
        role, kind = AGY_STEP_TYPES.get(
            step_type, ("unknown", "step_type_%s" % step_type))
        if kind == "tool_use":
            # Tool name and call id live in metadata, argument JSON in both.
            text = agy_printable(payload) or agy_printable(metadata)
            name = _agy_tool_name(metadata)
        else:
            text = agy_printable(payload)
            name = ""
        events.append({
            "ts": "", "role": role, "kind": kind, "idx": idx,
            "name": name, "status": status, "text": text,
        })
    return events


_AGY_TOOL_NAME = re.compile(rb"call_\d+\x12.([a-z_]{3,40})")


def _agy_tool_name(metadata):
    if not metadata:
        return ""
    match = _AGY_TOOL_NAME.search(metadata)
    return match.group(1).decode("ascii", "replace") if match else ""


def agy_conversation_summaries():
    db = os.path.join(AGY_ROOT, "conversation_summaries.db")
    if not os.path.exists(db):
        return []
    conn = ro_connect(db)
    try:
        cols = [r[1] for r in conn.execute("pragma table_info(conversation_summaries)")]
        rows = [dict(zip(cols, r)) for r in
                conn.execute("select * from conversation_summaries")]
    finally:
        conn.close()
    return rows


# ---------------------------------------------------------------- unified lookup

LISTERS = {
    "claude": list_claude_sessions,
    "codex": list_codex_sessions,
    "agy": list_agy_sessions,
}


def list_sessions(agents=None, cwd_filter=None, since_epoch=None,
                  include_approval=False, live_window=LIVE_WINDOW_SECONDS,
                  max_scan=400):
    agents = agents or list(LISTERS)
    out = []
    for agent in agents:
        lister = LISTERS.get(agent)
        if not lister:
            continue
        out.extend(lister(live_window=live_window, cwd_filter=cwd_filter,
                          since_epoch=since_epoch, max_scan=max_scan))
    if not include_approval:
        out = [s for s in out if s["kind"] != "approval"]
    if cwd_filter:
        needle = cwd_filter.rstrip("/")
        expanded = os.path.abspath(os.path.expanduser(needle)) if needle.startswith(("~", "/")) else needle
        out = [s for s in out
               if needle in (s["cwd"] or "") or expanded in (s["cwd"] or "")]
    if since_epoch:
        out = [s for s in out if s["updated"] >= since_epoch]
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out


READERS = {
    "claude": read_claude_session,
    "codex": read_codex_session,
    "agy": read_agy_session,
}


def detect_agent(path):
    path = os.path.abspath(path)
    if path.endswith(".db") and os.sep + "antigravity-cli" + os.sep in path:
        return "agy"
    if os.path.basename(path).startswith("rollout-"):
        return "codex"
    if os.sep + ".claude" + os.sep + "projects" + os.sep in path:
        return "claude"
    if path.endswith(".db"):
        return "agy"
    return "claude"


def resolve_target(target, include_approval=True):
    """Resolve a file path or session id / conversation uuid into a record."""
    if os.path.exists(target):
        agent = detect_agent(target)
        for sess in list_sessions(agents=[agent], include_approval=True):
            if os.path.abspath(sess["path"]) == os.path.abspath(target):
                return sess
        return _mk(agent, "", os.path.abspath(target), "", _mtime(target), "")
    matches = [s for s in list_sessions(include_approval=include_approval)
               if s["session_id"] == target
               or s["session_id"].startswith(target)
               or target in os.path.basename(s["path"] or "")]
    if not matches:
        raise SystemExit("No session found: %s" % target)
    matches.sort(key=lambda s: s["updated"], reverse=True)
    return matches[0]


def read_session(sess, include_thinking=False):
    reader = READERS[sess["agent"]]
    return reader(sess["path"], include_thinking=include_thinking)
