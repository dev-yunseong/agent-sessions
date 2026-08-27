#!/usr/bin/env python3
"""세션이 "어디까지 하다 멈췄나" 를 요약한다. 이어받기 전 브리핑용. 읽기 전용."""

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
        return text[:limit] + "\n… [%d자 잘림]" % (len(text) - limit)
    return text


def last_of(events, predicate):
    for event in reversed(events):
        if predicate(event):
            return event
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target",
                        help="세션 파일 절대 경로, 또는 session id / conversation uuid")
    parser.add_argument("--tool-tail", type=int, default=5,
                        help="마지막 도구 호출 몇 개를 보여줄지")
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
    print("이어받기 브리핑")
    print("=" * 72)
    print("agent   : %s" % sess["agent"])
    print("session : %s" % (sess["session_id"] or "(unknown)"))
    print("cwd     : %s" % (sess["cwd"] or "(unknown)"))
    print("updated : %s (%d분 전)" % (iso(sess["updated"]), stale_seconds // 60))
    print("path    : %s" % sess["path"])
    print("규모    : 이벤트 %d, 사용자 발화 %d, 어시스턴트 발화 %d, 도구 호출 %d"
          % (len(events), len(user_events), len(assistant_events), len(tool_calls)))
    if sess["live"] or stale_seconds < LIVE_WINDOW_SECONDS:
        print("\n*** 경고: 이 세션은 아직 살아 있을 수 있다 (마지막 갱신 %d분 전). "
              "같은 작업을 이어받으면 충돌한다. 먼저 사용자에게 확인해라. ***"
              % (stale_seconds // 60))
    if sess["agent"] == "agy":
        print("\n참고: agy 기록은 protobuf blob 에서 printable 문자열만 뽑은 근사치다. "
              "길면 저장 시점에 이미 잘려 있다.")

    prompts = agy_prompts(sess["session_id"]) if sess["agent"] == "agy" else []
    if prompts:
        print("\n--- history.jsonl 사용자 프롬프트 원문 " + "-" * 30)
        for ts, display in prompts:
            print("  - %s  %s" % (iso(ts), display.replace("\n", " ")[:200]))

    print("\n--- 마지막 사용자 요청 " + "-" * 48)
    if prompts:
        # agy 의 step_payload 추출은 근사치라 protobuf 부스러기가 섞인다.
        # history.jsonl 은 프롬프트 원문이므로 있으면 그쪽을 신뢰한다.
        print(clip(prompts[-1][1], args.chars))
    else:
        print(clip(last_user["text"], args.chars) if last_user else "(없음)")

    print("\n--- 마지막 어시스턴트 발화 " + "-" * 44)
    print(clip(last_assistant["text"], args.chars) if last_assistant else "(없음)")

    print("\n--- 마지막 도구 호출 %d개 " % args.tool_tail + "-" * 44)
    if not tool_calls:
        print("(없음)")
    for event in tool_calls[-args.tool_tail:]:
        print("  * %s %s" % (event.get("name") or event["kind"],
                             clip(event.get("text"), 200).replace("\n", " ")))
    if dangling > 0:
        print("  결과 없이 끝난 도구 호출: %d 개 (중간에 끊겼을 가능성)" % dangling)

    print("\n--- 미완료 작업 단서 " + "-" * 48)
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
            print("(todo 도구 기록 없음. 위 마지막 발화로 판단해라)")


if __name__ == "__main__":
    main()
