# Session Storage and Record Formats

## Storage locations

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

## Raw data formats

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
