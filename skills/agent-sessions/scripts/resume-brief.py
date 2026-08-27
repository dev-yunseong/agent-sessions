#!/usr/bin/env python3
"""Summarize where a session left off, to brief a takeover. Read-only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_sessions_lib import (  # noqa: E402
    LIVE_WINDOW_SECONDS, agy_prompts, is_preamble, iso, read_session,
    resolve_target,
)

TODO_HINTS = re.compile(
    r"(pending|in_progress|TODO|남은|다음 단계|next step|미완료|아직)", re.IGNORECASE)


def clip(text, limit):
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if limit and len(text) > limit:
        return text[:limit] + "\n… [%d chars truncated]" % (len(text) - limit)
    return text


def last_of(events, predicate):
    for event in reversed(events):
        if predicate(event):
            return event
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target",
                        help="absolute path to a session file, or session id / "
                             "conversation uuid")
    parser.add_argument("--tool-tail", type=int, default=5,
                        help="how many trailing tool calls to show")
    parser.add_argument("--chars", type=int, default=1600)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sess = resolve_target(args.target)
    events = read_session(sess, include_thinking=False)
    events = [e for e in events if not is_preamble(e)]

    user_events = [e for e in events
                   if e["role"].startswith("user") and e["kind"] in ("text", "queued")
                   and (e.get("text") or "").strip()]
    assistant_events = [e for e in events
                        if e["role"].startswith("assistant") and e["kind"] == "text"
                        and (e.get("text") or "").strip()]
    tool_calls = [e for e in events if e["kind"] == "tool_use"]
    tool_results = [e for e in events if e["kind"] == "tool_result"]

    last_user = user_events[-1] if user_events else None
    last_assistant = assistant_events[-1] if assistant_events else None
    last_tool = tool_calls[-1] if tool_calls else None
    todo = last_of(events, lambda e: e["kind"] == "tool_use"
                   and "todo" in (e.get("name") or "").lower())

    stale_seconds = time.time() - sess["updated"]
    dangling = len(tool_calls) - len(tool_results)

    if args.json:
        print(json.dumps({
            "session": sess,
            "counts": {"events": len(events), "user": len(user_events),
                       "assistant": len(assistant_events),
                       "tool_calls": len(tool_calls),
                       "tool_results": len(tool_results)},
            "last_user": last_user, "last_assistant": last_assistant,
            "last_tool_call": last_tool, "last_todo": todo,
            "dangling_tool_calls": dangling,
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 72)
    print("RESUME BRIEFING")
    print("=" * 72)
    print("agent   : %s" % sess["agent"])
    print("session : %s" % (sess["session_id"] or "(unknown)"))
    print("cwd     : %s" % (sess["cwd"] or "(unknown)"))
    print("updated : %s (%d min ago)" % (iso(sess["updated"]), stale_seconds // 60))
    print("path    : %s" % sess["path"])
    print("size    : %d events, %d user, %d assistant, %d tool calls"
          % (len(events), len(user_events), len(assistant_events), len(tool_calls)))
    if sess["live"] or stale_seconds < LIVE_WINDOW_SECONDS:
        print("\n*** WARNING: this session may still be alive (last update %d "
              "min ago). Taking over the same work WILL collide with it. Ask "
              "the user first. ***" % (stale_seconds // 60))
    if sess["agent"] == "agy":
        print("\nNOTE: the agy record is an approximation pulled from the "
              "printable strings of a protobuf blob. Long payloads were already "
              "truncated when they were stored.")

    prompts = agy_prompts(sess["session_id"]) if sess["agent"] == "agy" else []
    if prompts:
        print("\n--- verbatim user prompts from history.jsonl " + "-" * 27)
        for ts, display in prompts:
            print("  - %s  %s" % (iso(ts), display.replace("\n", " ")[:200]))

    print("\n--- last user request " + "-" * 49)
    if prompts:
        # agy step_payload extraction is approximate and mixes in protobuf
        # debris. history.jsonl holds the prompt verbatim, so trust it instead.
        print(clip(prompts[-1][1], args.chars))
    else:
        print(clip(last_user["text"], args.chars) if last_user else "(none)")

    print("\n--- last assistant message " + "-" * 44)
    print(clip(last_assistant["text"], args.chars) if last_assistant else "(none)")

    print("\n--- last %d tool calls " % args.tool_tail + "-" * 48)
    if not tool_calls:
        print("(none)")
    for event in tool_calls[-args.tool_tail:]:
        print("  * %s %s" % (event.get("name") or event["kind"],
                             clip(event.get("text"), 200).replace("\n", " ")))
    if dangling > 0:
        print("  tool calls with no result: %d (possibly cut off mid-run)" % dangling)

    print("\n--- unfinished work clues " + "-" * 45)
    if todo:
        print(clip(todo.get("text"), args.chars))
    else:
        hints = [e for e in (assistant_events[-3:] if assistant_events else [])
                 if TODO_HINTS.search(e.get("text") or "")]
        if hints:
            for event in hints:
                print(clip(event["text"], 600))
                print()
        else:
            print("(no todo tool record. judge from the last messages above)")


if __name__ == "__main__":
    main()
