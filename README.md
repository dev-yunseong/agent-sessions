# Agent Sessions

Agent Sessions is an agent skill that finds and reads the session records other coding agents leave on disk.

Claude Code, codex, and agy (Google Antigravity CLI) each persist every conversation locally, in three different formats and in several different places. This skill knows where those records live, how to parse them, and how to summarize where another agent stopped — so a handoff does not need the other agent to still be running.

## What it does

- **List** sessions from all three agents in one table, newest first, filtered by working directory or time window.
- **Read** any single session as an ordered transcript, with or without tool calls.
- **Brief** you on where a session stopped: last user request, last assistant message, last tool calls, unfinished work.
- **Detect** sessions that are still live, so you do not pick up work another agent is still doing.

Everything is read-only. SQLite stores are opened with a `mode=ro` URI so a running session is never disturbed.

## Install

From a local checkout:

```sh
npx skills add . --skill agent-sessions
```

From GitHub:

```sh
npx skills add dev-yunseong/agent-sessions --skill agent-sessions
```

## Use

```text
Find what codex was doing in this directory and pick it up.
```

Or run the scripts directly from the skill directory:

```sh
python3 scripts/list-sessions.py --cwd ~/dev/my-project --since 2d
python3 scripts/resume-brief.py <session-id>
python3 scripts/read-session.py <session-id> --no-tools --tail 40
```

## Requirements

- Python 3, standard library only. No third-party packages.
- The `sqlite3` command line tool is **not** required.

## Storage locations it knows about

| Agent | Format | Location |
| --- | --- | --- |
| Claude Code | JSONL, one record per line | `~/.claude/projects/<slugified-cwd>/<session-id>.jsonl` |
| codex | JSONL rollout, one event per line | `$CODEX_HOME`, `~/.codex`, the Orca runtime home, and Windows homes visible under `/mnt/*` |
| agy | SQLite with protobuf payloads | `~/.gemini/antigravity-cli/conversations/<uuid>.db` |

codex splits its history across several homes depending on how it was launched, and each home keeps its own SQLite index. Trusting one index silently loses sessions, so the skill globs the rollout files across every home. `SKILL.md` documents the full layout, including the record shapes needed to parse each format by hand.

## Caveats

- agy stores its steps as protobuf blobs with no public parser. The skill recovers text by extracting printable runs, which is an approximation, not a lossless transcript. Exact user prompts come from `history.jsonl` instead. Long tool output is already truncated on disk by agy itself.
- Record types and step type numbers were derived by observation. A schema change in any of the three agents can require re-deriving them.
- Session files can contain credentials, tokens, and private code. Quote what you need; do not dump them to external services.
