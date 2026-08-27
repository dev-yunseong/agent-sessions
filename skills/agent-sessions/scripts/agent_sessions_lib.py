"""agent-sessions skill 공용 라이브러리.

Claude Code / codex / agy (Google Antigravity CLI) 세션 저장소를 읽기 전용으로 훑는다.
SQLite 는 반드시 mode=ro URI 로만 연다. 파일을 쓰거나 지우는 코드는 이 파일에 없다.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import time

HOME = os.path.expanduser("~")

# ---------------------------------------------------------------- 저장소 위치

CLAUDE_PROJECTS_ROOT = os.path.join(HOME, ".claude", "projects")

# codex 는 CODEX_HOME 환경 변수로 홈이 통째로 바뀐다. 환경 변수가 1순위이고,
# 나머지는 codex 를 다른 곳에서 띄웠을 때를 대비한 후보다.
ORCA_CODEX_RUNTIME_SUFFIX = os.path.join("orca", "codex-runtime-home", "home")
CODEX_HOME_CANDIDATES = [
    os.environ.get("CODEX_HOME") or "",
    os.path.join(HOME, ".codex"),
    # Orca 터미널이 CODEX_HOME 을 갈아끼운 홈. 실무 세션 대부분이 여기 있다.
    # Orca 밖에서 돌리면 환경 변수가 없으므로 운영체제별 기본 위치를 직접 짚는다.
    os.path.join(HOME, ".local", "share", ORCA_CODEX_RUNTIME_SUFFIX),
    os.path.join(HOME, "Library", "Application Support", ORCA_CODEX_RUNTIME_SUFFIX),
]
# Windows Subsystem for Linux 에서 보이는 Windows 쪽 codex 홈.
# 드라이브 문자와 automount 루트가 환경마다 달라서 둘 다 와일드카드로 둔다.
CODEX_HOME_GLOBS = [
    "/mnt/*/Users/*/.codex",
    "/media/*/Users/*/.codex",
]

AGY_ROOT = os.path.join(HOME, ".gemini", "antigravity-cli")
AGY_CONVERSATIONS = os.path.join(AGY_ROOT, "conversations")
AGY_HISTORY = os.path.join(AGY_ROOT, "history.jsonl")

# codex 가 승인 판정용으로 띄우는 하위 세션의 첫 사용자 메시지.
APPROVAL_MARKER = "The following is the Codex agent history whose request action you are assessing"

# codex 가 대화 앞에 사용자 역할로 밀어 넣는 시스템 preamble.
CODEX_PREAMBLE_PREFIXES = (
    "# AGENTS.md instructions",
    "<user_instructions>",
    "<environment_context>",
    "<recommended_plugins>",
    "<INSTRUCTIONS>",
)

# Claude Code 가 사용자 역할로 밀어 넣는 시스템 preamble.
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

# 모델에 주입된 지시문. 사람이 친 말이 아니라 기본 출력에서 뺀다.
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
    """존재하는 codex 홈 디렉토리 목록. 중복 제거, 순서 유지."""
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
    """절대 경로를 Claude Code 프로젝트 디렉토리 이름으로 바꾼다."""
    return os.path.abspath(os.path.expanduser(cwd)).replace("/", "-")


# ---------------------------------------------------------------- 유틸리티

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
    """SQLite 를 읽기 전용으로 연다. 실행 중인 세션의 데이터베이스도 안전하다."""
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def iso(epoch_seconds):
    if not epoch_seconds:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch_seconds))


# ---------------------------------------------------------------- 세션 레코드

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
    """헤드만 읽어 cwd 와 첫 사용자 메시지를 뽑는다."""
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
    """사용자가 실제로 친 문장만 돌려준다. 시스템 preamble 과 tool_result 는 뺀다."""
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
    """mtime 만 보고 후보를 줄인다. 본문 파싱이 비싸서 먼저 자른다."""
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
    """Claude Code 세션을 순서대로 이벤트 목록으로 편다."""
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
    """codex 홈 하나가 가진 state 색인 데이터베이스 경로.

    codex 는 스키마를 바꿀 때 파일 이름의 숫자를 올린다 (state_5 다음은 state_6).
    숫자를 고정하면 codex 가 올라간 날 조용히 빈 결과가 나오므로 glob 으로 찾는다.
    """
    found = []
    for pattern in ("state_*.sqlite", os.path.join("sqlite", "state_*.sqlite")):
        found.extend(glob.glob(os.path.join(home, pattern)))
    return sorted(found, reverse=True)


def codex_index_rows(limit=200):
    """각 codex 홈의 state 색인 threads 테이블. 홈마다 색인이 따로 논다."""
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

# step_type 별 의미. 실제 데이터베이스에서 확인한 값이다.
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
    """history.jsonl 을 conversationId 로 묶는다. agy 세션 1차 조회 수단."""
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
    """history.jsonl 에 남은 사용자 프롬프트 원문. 오래된 줄에는
    conversationId 가 없어 여기 안 잡힐 수 있다."""
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
        # write-ahead log 에 내용이 남아 있으면 아직 쓰는 중이다. -shm 은
        # agy 프로세스가 모든 대화를 열어 두기 때문에 살아 있는 신호가 못 된다.
        wal = _size(path + "-wal")
        out.append(_mk(
            "agy", cid, path, meta.get("workspace", ""), mtime,
            meta.get("first_display", ""),
            live=(wal > 0 or (time.time() - mtime) < live_window),
            extra={"prompt_count": meta.get("prompt_count", 0),
                   "last_prompt": _clean(meta.get("last_display", ""), 200),
                   "wal_bytes": wal},
        ))
    # 데이터베이스가 지워졌어도 history.jsonl 에 남은 대화는 기록으로 남긴다.
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
    """protobuf blob 에서 사람이 읽을 수 있는 문자열만 뽑는다."""
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
    # 진짜 본문은 압도적으로 긴 조각이다. 나머지 protobuf 헤더 부스러기는 버린다.
    longest = max(len(k) for k in kept)
    floor = min(60, max(20, int(longest * 0.15)))
    body = "\n".join(k for k in kept if len(k) >= floor or longest < 60)
    return _strip_framing_lines(body)


# protobuf 필드 구분자가 본문과 같은 조각에 붙어 나온다. 줄바꿈이 printable
# 문자라서 하나의 조각이 여러 줄에 걸치기 때문이다. 그 부스러기는 조각의 맨 앞과
# 맨 뒤에만 붙으므로 양 끝에서만 걷어낸다. 가운데는 본문이라 건드리지 않는다.
_AGY_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")


def _is_framing_line(line):
    # 디코딩 실패 표시는 protobuf 필드 경계에서 나온다. 판정 전에 걷어낸다.
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
    """agy 대화를 이벤트 목록으로 편다.

    step_payload 는 protobuf blob 이고 공개 파서가 없다. printable 바이트만
    뽑아 사람이 읽을 수 있게 만든 근사치이므로 무손실 전사가 아니다.
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
            # 도구 이름과 call id 는 metadata 에, 인자 JSON 은 양쪽 모두에 있다.
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


# ---------------------------------------------------------------- 통합 조회

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
    """파일 경로 또는 session id / conversation uuid 를 세션 레코드로 바꾼다."""
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
        raise SystemExit("세션을 찾지 못했다: %s" % target)
    matches.sort(key=lambda s: s["updated"], reverse=True)
    return matches[0]


def read_session(sess, include_thinking=False):
    reader = READERS[sess["agent"]]
    return reader(sess["path"], include_thinking=include_thinking)
