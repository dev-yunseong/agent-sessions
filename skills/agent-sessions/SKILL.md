---
name: agent-sessions
description: >-
  Locate and read the past and current session records that other coding agents
  (Claude Code, codex, agy) leave on disk. Use it for requests like "what was
  codex working on", "read the agy session", "another agent's conversation log",
  "pick up where that session left off", "find yesterday's session", "who did what
  in this directory", "session log", "rollout jsonl", "conversation database",
  "resume", "continue from the handoff", "codex가 뭐 하던 중이었는지 봐줘",
  "agy 세션 읽어와", "아까 하던 작업 이어받아", "이어받기". Covers listing sessions,
  reading a session in full, summarizing where it stopped, and identifying
  sessions still in progress.
metadata:
  short-description: Find and read other coding agents' session records
---

# Agent Sessions

Find and read the session records that three agents — Claude Code, codex, and agy —
leave on disk. The main use is **resuming**: work out how far another agent got before
it stopped, and continue from there.

## Absolute rules

- **This is read-only.** Never write, delete, or move a session file. Every script
  below only reads.
- **Always open SQLite through a read-only URI.** The scripts below use only the
  `python3` standard library. The `sqlite3` command line tool is not needed, and is
  often absent. When you have to query directly, use `python3`'s `sqlite3` module and
  open it like this:

  ```python
  connection = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
  ```

  Opening it this way is what makes it safe to read the database of a session that is
  running right now. A plain `sqlite3.connect(path)` takes a lock and opens writable,
  so never use it.
- **Session files can contain credentials, tokens, and private code.** Do not dump one
  wholesale to an external service or upload it to a public location. Quote only the
  part you need.

## Helper scripts

All three run directly under `python3`. Run one with no arguments to see `--help`.

Every path below is **relative to this skill directory**. Installing through
`npx skills add` puts the skill in a different place on every machine, so before you
run anything, move into the directory holding this `SKILL.md` or prefix that directory
onto the path.

| Script | What it does |
| --- | --- |
| `scripts/list-sessions.py` | Sweeps all three agents at once, newest first |
| `scripts/read-session.py` | Unrolls one session's conversation in order |
| `scripts/resume-brief.py` | "Where did it stop" summary (the resume briefing) |

Shared parsing code lives in `agent_sessions_lib.py` in the same directory. If you have
to write Python yourself, import that module and reuse it.

## List sessions

Sweeps all three agents at once and prints
`time / agent / live / cwd / first user message / file path`, newest first.

```bash
python3 scripts/list-sessions.py --limit 20
```

Narrowing to one project (`--cwd` accepts an absolute path or a substring):

```bash
python3 scripts/list-sessions.py \
  --cwd ~/dev/my-project --since 2d --limit 20
```

Options you will reach for:

- `--agent claude,codex,agy` — pick agents. Defaults to all three.
- `--since 30m|6h|7d|2w` — only recently updated ones.
- `--live-only` — only sessions that appear to be alive right now.
- `--include-approval` — include codex approval-decision sub-sessions (see the trap
  in [session storage](references/session-storage.md#storage-locations)).
- `--max-scan 0` — lift the cap on how many recent files get opened. Use it when
  hunting an old session. The default is 150 per agent. The cap exists because
  repositories mounted under `/mnt/*` on Windows Subsystem for Linux are slow, so if no
  codex home sits on that path, lifting it costs little.
- `--json` — machine-readable form.
- `--show-roots` — print the codex homes actually searched to standard error first.

## Read a session

Pass an absolute file path as the argument, or a session id / conversation uuid
(it is matched by searching the whole set of stores).

```bash
python3 scripts/read-session.py \
  ~/.local/share/orca/codex-runtime-home/home/sessions/2026/08/27/rollout-2026-08-27T13-48-11-01a0418c-0a7c-7643-8451-070ea39daa3e.jsonl

python3 scripts/read-session.py 01a0418c-0a7c-7643
```

Options:

- `--no-tools` — drop tool calls and results, keep only the human conversation. This is
  the best way to grasp the flow.
- `--tail 30` / `--head 30` — trim to one end.
- `--grep <string>` — only events containing that string.
- `--thinking` — include reasoning blocks.
- `--preamble` — also print the instructions injected into the model (developer role,
  AGENTS.md body, skill list). Hidden by default.
- `--tool-chars` / `--text-chars` — maximum characters per event. Defaults 400 / 4000.

For a large session, do not read it all at once: start from the end with
`--no-tools --tail 40`, then narrow to the stretch you need with `--grep`.

## Resume a session

```bash
python3 scripts/resume-brief.py 01a0418c-0a7c-7643
```

What the output carries:

- Agent, session id, cwd, last update time and elapsed time, file path
- Counts of events / user turns / assistant turns / tool calls
- **The last user request** — for agy sessions this uses the prompt text from
  `history.jsonl`, because it is more accurate than the protobuf extraction
- **The last assistant turn**
- **The last few tool calls** (`--tool-tail N`)
- The number of tool calls that ended without a result — anything other than 0 means it
  was cut off mid-flight
- The contents of the todo tool record if there is one, otherwise the last turns that
  might hint at what is unfinished

Resume order:

1. Pick the target session with `list-sessions.py --cwd <project>`.
2. Work out the stopping point with `resume-brief.py <session>`.
3. If the session is live, stop and ask the user first (see below).
4. If you need more, read surrounding context with
   `read-session.py --no-tools --tail 60`.
5. Read that project's `AGENTS.md` and `~/.claude/projects/<slug>/memory/`, then carry
   the work on.

## Detect live sessions

- Claude Code / codex: if the session file's mtime is just now, it is alive.
- agy / codex index: if the `-wal` file size is not 0, it is still being written to.
  agy's `-shm` file is not a liveness signal, because the process keeps every
  conversation open. Do not use it.
- codex: whether `<home>/thread-writer-locks/<session_id>.lock` exists is a secondary
  signal.

`list-sessions.py` marks a session `LIVE` when it was updated within the last 15 minutes
by default (tune with `--live-window`).

> **If you simply take over the work of a live session, two agents edit the same files
> at once and collide. Before resuming, you must first ask the user "this session is
> still running — should I take it over?"**

## Reference

Read [session storage and record formats](references/session-storage.md) when
troubleshooting discovery or parsing records the helper scripts cannot read.
