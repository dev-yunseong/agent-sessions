#!/usr/bin/env python3
"""세션 하나를 골라 대화를 순서대로 펼쳐 읽는다. 읽기 전용."""

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
        return text[:limit] + "\n… [%d자 잘림]" % (len(text) - limit)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target",
                        help="세션 파일 절대 경로, 또는 session id / conversation uuid")
    parser.add_argument("--thinking", action="store_true", help="추론 블록도 출력")
    parser.add_argument("--no-tools", action="store_true",
                        help="도구 호출과 도구 결과를 숨긴다")
    parser.add_argument("--tool-chars", type=int, default=400,
                        help="도구 호출·결과 하나당 최대 글자 수")
    parser.add_argument("--text-chars", type=int, default=4000,
                        help="사용자·어시스턴트 발화 하나당 최대 글자 수")
    parser.add_argument("--tail", type=int, default=0,
                        help="마지막 N 개 이벤트만 출력")
    parser.add_argument("--head", type=int, default=0,
                        help="처음 N 개 이벤트만 출력")
    parser.add_argument("--grep", help="이 문자열을 포함한 이벤트만 출력")
    parser.add_argument("--preamble", action="store_true",
                        help="주입된 지시문(developer 역할, AGENTS.md 등)도 출력")
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
        print("note    : agy step_payload 는 protobuf blob 이라 printable 문자열만 "
              "뽑은 근사치다. 무손실 전사가 아니다.")
        prompts = agy_prompts(sess["session_id"])
        if prompts:
            print("prompts : history.jsonl 원문 %d 개" % len(prompts))
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
