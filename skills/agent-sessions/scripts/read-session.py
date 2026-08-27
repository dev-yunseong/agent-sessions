#!/usr/bin/env python3
"""Read one session, unfolding the conversation in order. Read-only."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_sessions_lib import (  # noqa: E402
    agy_prompts, is_preamble, iso, read_session, resolve_target,
)


def clip(text, limit):
    text = text or ""
    if limit and len(text) > limit:
        return text[:limit] + "\n… [%d chars truncated]" % (len(text) - limit)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target",
                        help="absolute path to a session file, or session id / "
                             "conversation uuid")
    parser.add_argument("--thinking", action="store_true",
                        help="also print reasoning blocks")
    parser.add_argument("--no-tools", action="store_true",
                        help="hide tool calls and tool results")
    parser.add_argument("--tool-chars", type=int, default=400,
                        help="max characters per tool call or tool result")
    parser.add_argument("--text-chars", type=int, default=4000,
                        help="max characters per user or assistant message")
    parser.add_argument("--tail", type=int, default=0,
                        help="print only the last N events")
    parser.add_argument("--head", type=int, default=0,
                        help="print only the first N events")
    parser.add_argument("--grep",
                        help="print only events containing this string")
    parser.add_argument("--preamble", action="store_true",
                        help="also print injected instructions (developer role, "
                             "AGENTS.md and friends)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sess = resolve_target(args.target)
    events = read_session(sess, include_thinking=args.thinking)

    if not args.preamble:
        events = [e for e in events if not is_preamble(e)]
    if args.no_tools:
        events = [e for e in events if e["kind"] not in ("tool_use", "tool_result")]
    if args.grep:
        events = [e for e in events if args.grep in (e.get("text") or "")]
    if args.head:
        events = events[: args.head]
    if args.tail:
        events = events[-args.tail:]

    if args.json:
        print(json.dumps({"session": sess, "events": events},
                         ensure_ascii=False, indent=2))
        return

    print("agent   : %s" % sess["agent"])
    print("session : %s" % (sess["session_id"] or "(unknown)"))
    print("cwd     : %s" % (sess["cwd"] or "(unknown)"))
    print("updated : %s%s" % (iso(sess["updated"]), "  [LIVE]" if sess["live"] else ""))
    print("path    : %s" % sess["path"])
    if sess["agent"] == "agy":
        print("note    : agy step_payload is a protobuf blob, so this is an "
              "approximation pulled from the printable strings alone. NOT a "
              "lossless transcript.")
        prompts = agy_prompts(sess["session_id"])
        if prompts:
            print("prompts : %d verbatim in history.jsonl" % len(prompts))
            for ts, display in prompts:
                print("  - %s  %s" % (iso(ts), display.replace("\n", " ")[:160]))
    print("events  : %d" % len(events))
    print("-" * 72)

    for event in events:
        head = event.get("ts") or ("idx %s" % event.get("idx", ""))
        role = event.get("role", "?")
        kind = event.get("kind", "")
        if kind == "tool_use":
            label = "%s [tool_use %s]" % (role, event.get("name", ""))
            body = clip(event.get("text"), args.tool_chars)
        elif kind == "tool_result":
            label = "%s [tool_result]" % role
            body = clip(event.get("text"), args.tool_chars)
        elif kind == "thinking":
            label = "%s [thinking]" % role
            body = clip(event.get("text"), args.text_chars)
        else:
            label = "%s [%s]" % (role, kind)
            body = clip(event.get("text"), args.text_chars)
        if not (body or "").strip():
            continue
        print("\n### %s  %s" % (head, label))
        print(body)


if __name__ == "__main__":
    main()
