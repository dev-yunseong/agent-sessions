#!/usr/bin/env python3
"""Claude Code / codex / agy 세션을 한 번에 훑어 최신순으로 나열한다. 읽기 전용."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_sessions_lib import (  # noqa: E402
    APPROVAL_MARKER, LIVE_WINDOW_SECONDS, codex_homes, codex_index_rows, iso,
    list_sessions,
)

SINCE = re.compile(r"^(\d+)([smhdw])$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(text):
    if not text:
        return None
    match = SINCE.match(text.strip())
    if not match:
        raise SystemExit("--since 형식: 30m, 6h, 7d, 2w")
    return time.time() - int(match.group(1)) * UNITS[match.group(2)]


def print_codex_index(limit, include_approval):
    """codex state 색인 덤프. 기본 목록과 같은 머리글과 같은 필터를 쓴다."""
    rows = codex_index_rows(limit=limit)
    if not include_approval:
        rows = [r for r in rows
                if not (r["first_user_message"] or "").lstrip()
                .startswith(APPROVAL_MARKER)]
    rows.sort(key=lambda r: r["updated_at"] or 0, reverse=True)
    rows = rows[:limit]
    if not rows:
        print("색인에 조건에 맞는 세션이 없다.")
        return
    header = "%-16s %-34s %-40s %s"
    print(header % ("UPDATED", "CWD", "FIRST USER MESSAGE", "ROLLOUT PATH"))
    for row in rows:
        print(header % (
            iso(row["updated_at"]), (row["cwd"] or "")[-34:],
            (row["first_user_message"] or "").replace("\n", " ")[:40],
            row["rollout_path"]))
    print("\n색인 데이터베이스: %s" % ", ".join(sorted({r["db"] for r in rows})))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="claude,codex,agy",
                        help="쉼표로 구분. 기본 claude,codex,agy")
    parser.add_argument("--cwd", help="작업 디렉토리 절대 경로 또는 부분 문자열")
    parser.add_argument("--since", help="30m / 6h / 7d / 2w")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--include-approval", action="store_true",
                        help="codex 승인 판정용 하위 세션까지 포함")
    parser.add_argument("--live-only", action="store_true",
                        help="지금 살아 있어 보이는 세션만")
    parser.add_argument("--live-window", type=int, default=LIVE_WINDOW_SECONDS,
                        help="이 초 안에 갱신되었으면 진행 중으로 본다")
    parser.add_argument("--max-scan", type=int, default=150,
                        help="에이전트별로 최근 몇 개 파일까지 본문을 열어 볼지. "
                             "0 이면 전부. cwd 로 좁혀 오래된 세션까지 찾을 때 0 으로 둔다")
    parser.add_argument("--message-width", type=int, default=64)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-roots", action="store_true",
                        help="탐색한 저장소 루트를 먼저 출력")
    parser.add_argument("--codex-index", action="store_true",
                        help="codex state 색인 threads 테이블만 덤프한다. "
                             "색인은 홈마다 따로 놀아서 rollout glob 을 대체하지 못한다")
    args = parser.parse_args()

    if args.codex_index:
        print_codex_index(limit=args.limit,
                          include_approval=args.include_approval)
        return

    agents = [a.strip() for a in args.agent.split(",") if a.strip()]
    sessions = list_sessions(
        agents=agents,
        cwd_filter=args.cwd,
        since_epoch=parse_since(args.since),
        include_approval=args.include_approval,
        live_window=args.live_window,
        max_scan=args.max_scan or None,
    )
    if args.live_only:
        sessions = [s for s in sessions if s["live"]]
    sessions = sessions[: args.limit]

    if args.show_roots:
        print("codex homes: " + (", ".join(codex_homes()) or "(none)"),
              file=sys.stderr)

    if args.json:
        print(json.dumps(sessions, ensure_ascii=False, indent=2))
        return

    if not sessions:
        print("조건에 맞는 세션이 없다.")
        return

    width = args.message_width
    print("%-16s %-6s %-4s %-34s %-*s %s" %
          ("UPDATED", "AGENT", "LIVE", "CWD", width, "FIRST USER MESSAGE", "PATH"))
    for sess in sessions:
        message = sess["first_user_message"] or "(없음)"
        if len(message) > width:
            message = message[: width - 1] + "…"
        cwd = sess["cwd"] or "(unknown)"
        if len(cwd) > 34:
            cwd = "…" + cwd[-33:]
        print("%-16s %-6s %-4s %-34s %-*s %s" % (
            iso(sess["updated"]), sess["agent"],
            "LIVE" if sess["live"] else "",
            cwd, width, message, sess["path"] or "(no file)",
        ))
    live = [s for s in sessions if s["live"]]
    if live:
        print("\n주의: LIVE 표시 세션 %d 개는 지금 돌고 있을 수 있다. "
              "이어받기 전에 사용자에게 먼저 확인해라." % len(live))


if __name__ == "__main__":
    main()
