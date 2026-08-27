#!/usr/bin/env python3
"""List Claude Code / codex / agy sessions in one sweep, newest first. Read-only."""

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
        raise SystemExit("--since format: 30m, 6h, 7d, 2w")
    return time.time() - int(match.group(1)) * UNITS[match.group(2)]


def print_codex_index(limit, include_approval):
    """Dump the codex state index, same header and filters as the main list."""
    rows = codex_index_rows(limit=limit)
    if not include_approval:
        rows = [r for r in rows
                if not (r["first_user_message"] or "").lstrip()
                .startswith(APPROVAL_MARKER)]
    rows.sort(key=lambda r: r["updated_at"] or 0, reverse=True)
    rows = rows[:limit]
    if not rows:
        print("No session in the index matches.")
        return
    header = "%-16s %-34s %-40s %s"
    print(header % ("UPDATED", "CWD", "FIRST USER MESSAGE", "ROLLOUT PATH"))
    for row in rows:
        print(header % (
            iso(row["updated_at"]), (row["cwd"] or "")[-34:],
            (row["first_user_message"] or "").replace("\n", " ")[:40],
            row["rollout_path"]))
    print("\nIndex databases: %s" % ", ".join(sorted({r["db"] for r in rows})))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="claude,codex,agy",
                        help="comma separated. default claude,codex,agy")
    parser.add_argument("--cwd",
                        help="working directory absolute path or substring")
    parser.add_argument("--since", help="30m / 6h / 7d / 2w")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--include-approval", action="store_true",
                        help="include codex approval-assessment sub-sessions")
    parser.add_argument("--live-only", action="store_true",
                        help="only sessions that look alive right now")
    parser.add_argument("--live-window", type=int, default=LIVE_WINDOW_SECONDS,
                        help="updated within this many seconds counts as running")
    parser.add_argument("--max-scan", type=int, default=150,
                        help="how many recent files per agent to open and parse. "
                             "0 means all. use 0 when narrowing by cwd to reach "
                             "older sessions")
    parser.add_argument("--message-width", type=int, default=64)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-roots", action="store_true",
                        help="print the scanned store roots first")
    parser.add_argument("--codex-index", action="store_true",
                        help="dump only the threads table of the codex state "
                             "index. each home keeps its own index, so this does "
                             "not replace the rollout glob")
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
        print("No session matches.")
        return

    width = args.message_width
    print("%-16s %-6s %-4s %-34s %-*s %s" %
          ("UPDATED", "AGENT", "LIVE", "CWD", width, "FIRST USER MESSAGE", "PATH"))
    for sess in sessions:
        message = sess["first_user_message"] or "(none)"
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
        print("\nWARNING: %d session(s) marked LIVE may be running right now. "
              "Ask the user before taking any of them over." % len(live))


if __name__ == "__main__":
    main()
