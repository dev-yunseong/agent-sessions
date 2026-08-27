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

## 1. Cross-agent listing

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
  below).
- `--max-scan 0` — lift the cap on how many recent files get opened. Use it when
  hunting an old session. The default is 150 per agent. The cap exists because
  repositories mounted under `/mnt/*` on Windows Subsystem for Linux are slow, so if no
  codex home sits on that path, lifting it costs little.
- `--json` — machine-readable form.
- `--show-roots` — print the codex homes actually searched to standard error first.

## 2. Storage locations

### Claude Code

- Sessions: `~/.claude/projects/<slugified cwd>/<session_id>.jsonl`
- The slug rule is the absolute path with `/` replaced by `-`.
  `/home/alice/dev/my-project` → `-home-alice-dev-my-project`
- Per-project memory: `~/.claude/projects/<slug>/memory/MEMORY.md` and the individual
  note files beside it. Reading these before the session itself gets you the context
  faster.

```bash
ls -1t ~/.claude/projects/-home-alice-dev-my-project/*.jsonl | head
```

### codex — there are several homes. This is the biggest trap

codex splits its home wholesale via the `CODEX_HOME` environment variable. Look in only
one place and you miss entire sessions. Known homes:

| Home | When things land here |
| --- | --- |
| `$CODEX_HOME/sessions/YYYY/MM/DD/` | **The environment variable wins.** If the shell that launched codex had this variable set, it is here, always |
| `~/.local/share/orca/codex-runtime-home/home/sessions/YYYY/MM/DD/` | **codex launched from an Orca terminal.** Orca swaps out `CODEX_HOME`. Most real-work sessions are here. On macOS it is `~/Library/Application Support/orca/codex-runtime-home/home` |
| `~/.codex/sessions/YYYY/MM/DD/` | codex launched bare |
| `/mnt/<drive>/Users/<user>/.codex/sessions/YYYY/MM/DD/` | codex launched on the Windows side (visible from Windows Subsystem for Linux). The drive letter and the automount root differ per machine, so sweep both `/mnt/*` and `/media/*` |

`list-sessions.py` globs all of these homes. To search by hand:

```bash
ls -1t ~/.local/share/orca/codex-runtime-home/home/sessions/*/*/*/rollout-*.jsonl | head
ls -1t ~/.codex/sessions/*/*/*/rollout-*.jsonl | head
ls -1t /mnt/*/Users/*/.codex/sessions/*/*/*/rollout-*.jsonl | head
```

**The SQLite indexes are separate per home.** Each home has a `state_<number>.sqlite`
holding a `threads` table (`id`, `rollout_path`, `created_at`, `updated_at`, `cwd`,
`title`, `first_user_message`, `cli_version`, `git_branch`, and more), plus a
`thread_history_<number>.sqlite` holding `thread_items` / `thread_turns`.

**The number in the filename is a schema version, and codex bumps it.** At the time of
checking it was `state_5`, `thread_history_1`, `logs_2`, `goals_1`, `memories_1`,
`queue_1`. Hardcode a number and you get silently empty results the day codex bumps it,
so glob for `state_*.sqlite`. The scripts do exactly that.

A single home can keep its index in two places. Look at both `<home>/state_*.sqlite` and
`<home>/sqlite/state_*.sqlite`. On a machine where codex has been launched several
different ways, more than four copies of the index can show up. Each index holds only
its own home's sessions and knows nothing about the others.

**Trust one index and you will miss sessions.** The index is a hint; the reliable query
is globbing the rollout files across every home directly. If you do want to look at the
index (approval-decision sub-sessions are filtered out here just as in the default
listing — pull them in with `--include-approval`):

```bash
python3 scripts/list-sessions.py --codex-index --limit 20
```

Also:

- Prompt history: `~/.codex/history.jsonl` (`session_id`, `ts`, `text`). Separate per
  home.
- In-progress marker: `<home>/thread-writer-locks/<session_id>.lock`. It sometimes
  survives shutdown, so do not trust it on its own.

### agy (Google Antigravity CLI, `~/.local/bin/agy`)

- Sessions: `~/.gemini/antigravity-cli/conversations/<conversation_uuid>.db` —
  **SQLite**
- **The primary lookup path is `~/.gemini/antigravity-cli/history.jsonl`.** Each line
  looks like `{"display": "<the prompt the user typed>", "timestamp": <epoch
  milliseconds>, "workspace": "<cwd>", "conversationId": "<uuid>"}`, so filtering on
  `workspace` finds a project's sessions immediately.

```bash
grep -F '"workspace":"'"$PWD"'"' ~/.gemini/antigravity-cli/history.jsonl | tail -20
```

  Older lines have no `conversationId`. For those, only the prompt text survives, and
  which conversation it belongs to has to be inferred from `workspace` and `timestamp`.

- Also: `conversation_summaries.db` (`conversation_id`, `title`, `preview`,
  `step_count`, `workspace_uris`, `last_user_input_time`, and more — it does not hold
  every conversation), `brain/`, `knowledge/`, `log/cli-*.log`

## 3. Reading one session in full

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

## 4. "Where did it stop" summary — the main resume procedure

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

## 5. Detecting live sessions

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

## 6. Raw data structure reference

Use this when you have to parse something the scripts do not cover.

### Claude Code session JSONL

Plain JSONL. Every line has a `type` field. Types observed in real sessions:

`assistant`, `attachment`, `user`, `last-prompt`, `queue-operation`, `ai-title`,
`pr-link`, `atis-latch`, `bridge-session`, `mode`, `system`, `permission-mode`,
`file-history-snapshot`, `cost-state`

What you actually need when reading:

- `user` / `assistant` — `message.content` is either a string or an array of blocks.
  Block `type` is one of `text`, `thinking`, `tool_use`, `tool_result`, `image`.
  `user` records carry `cwd`, `gitBranch`, `version`, `sessionId`, `isSidechain`.
- `queue-operation` (`operation == "enqueue"`) — input the user queued up, in `content`.
- `last-prompt` — the text of the last prompt.
- `system` — hook execution summaries and the like.
- `bridge-session`, `atis-latch`, `mode`, `permission-mode`, `cost-state` — session
  metadata, not conversation content.

To pull out only what a human typed, filter out user text starting with
`<command-name>`, `<system-reminder>`, `<task-notification>`, `<bash-input>`, or
`Caveat: The messages below were generated`. That text was injected; the user did not
type it.

### codex rollout JSONL

Filename `rollout-<ISO timestamp>-<session_id>.jsonl`. Plain JSONL.

- The first line is `type == "session_meta"`, and inside `payload` are `session_id`,
  `cwd`, `cli_version`, `model_provider`, `originator`, `base_instructions`.
- The conversation is on `type == "response_item"` lines, where `payload.type` is one
  of: `message` (`role` is `user` / `assistant` / `developer`, with the body assembled
  by concatenating `payload.content[].text`), `reasoning`, `custom_tool_call`,
  `custom_tool_call_output`, `function_call`, `function_call_output`,
  `local_shell_call`.
- `type == "event_msg"` is a UI event (`token_count`, `item_completed`,
  `task_started`, `task_complete`) and is not needed to reconstruct the conversation.
- The first few `user` messages are injected instructions, not real user input. Skip
  anything starting with `# AGENTS.md instructions`, `<user_instructions>`,
  `<environment_context>`, or `<recommended_plugins>`.

**Filter out approval-decision sub-sessions.** To decide sandbox approvals, codex
creates several separate rollouts at the same moment under the same session id prefix.
If the first real user message starts with
`The following is the Codex agent history whose request action you are assessing`,
that is not the main conversation. Hide them by default, and pull them out with
`--include-approval` only when you need to look at the approval decision process
itself.

### agy conversation SQLite

Tables in `~/.gemini/antigravity-cli/conversations/<conversation_uuid>.db`:
`trajectory_meta`, `steps`, `gen_metadata`, `executor_metadata`,
`parent_references`, `trajectory_metadata_blob`, `battle_mode_infos`.

`steps` columns: `idx`, `step_type`, `status`, `has_subtrajectory`, `metadata`,
`error_details`, `permissions`, `task_details`, `render_info`, `step_payload`,
`step_format`.

Confirmed `step_type` meanings:

| Value | Meaning |
| --- | --- |
| `14` | User message |
| `15` | Assistant output and reasoning |
| `132` | Tool call (arguments are plain JSON) |
| `101` | Tool result |

`step_payload` and `metadata` are **protobuf blobs, and there is no public parser.**
Pulling printable byte sequences out with a regular expression makes them human
readable. The verified expression:

```python
re.compile(rb'[^\x00-\x08\x0b-\x1f\x7f]{20,}')
```

`agent_sessions_lib.agy_printable()` adds to that by filtering out protobuf header
debris (fragments that are only a uuid, `sessionID`, single snake_case token
fragments).

**Two warnings:**

1. This extraction is **not a lossless transcript.** It is an approximation. When you
   need the exact user prompt, look at `display` in `history.jsonl` (`read-session.py`
   and `resume-brief.py` print it alongside automatically for agy sessions).
2. When a tool result is long, it is **already truncated at write time**, as something
   like `<truncated 31 lines>`. The original is not on disk.
