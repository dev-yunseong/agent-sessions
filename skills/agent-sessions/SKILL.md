---
name: agent-sessions
description: >-
  다른 코딩 에이전트(Claude Code, codex, agy)의 과거·현재 세션 기록이 디스크
  어디에 있는지 찾아서 읽는다. 사용자가 "codex가 뭐 하던 중이었는지 봐줘",
  "agy 세션 읽어와", "다른 에이전트 대화 기록", "아까 하던 작업 이어받아",
  "어제 그 세션 찾아줘", "누가 이 디렉토리에서 뭘 했는지", "session log",
  "rollout jsonl", "conversation database", "이어받기", "handoff 받아서 계속"
  같은 요청을 할 때 사용한다. 세션 목록 조회, 세션 전문 읽기, 중단 지점 요약,
  진행 중인 세션 판별을 전부 다룬다.
metadata:
  short-description: 다른 코딩 에이전트의 세션 기록 찾아 읽기
---

# Agent Sessions

Claude Code / codex / agy 세 에이전트가 디스크에 남긴 세션 기록을 찾아서 읽는다.
주 용도는 **이어받기**다. 다른 에이전트가 어디까지 하다 멈췄는지 파악하고 그 자리에서
작업을 계속한다.

## 절대 규칙

- **읽기 전용이다.** 세션 파일을 쓰거나 지우거나 옮기지 마라. 아래 스크립트는 전부
  읽기만 한다.
- **이 머신에 `sqlite3` 명령줄 도구가 없다.** SQLite 는 `python3` 의 `sqlite3`
  모듈로 읽고, **반드시** 읽기 전용 URI 로 연다.

  ```python
  connection = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
  ```

  이렇게 열어야 지금 돌고 있는 세션의 데이터베이스도 안전하게 읽는다. 일반
  `sqlite3.connect(path)` 는 잠금을 잡고 쓰기 가능 상태로 열기 때문에 쓰면 안 된다.
- **세션 파일에는 자격증명, 토큰, 비공개 코드가 들어 있을 수 있다.** 통째로 덤프해서
  외부 서비스로 보내거나 공개 위치에 올리지 마라. 필요한 부분만 인용해라.

## 헬퍼 스크립트

세 개 다 `python3` 로 바로 실행한다. 인자 없이 실행하면 `--help` 를 볼 수 있다.

아래 경로는 모두 **이 스킬 디렉토리 기준 상대 경로**다. `npx skills add` 로 설치하면
설치 위치가 환경마다 다르므로, 실행하기 전에 이 `SKILL.md` 가 있는 디렉토리로 이동하거나
그 디렉토리를 앞에 붙여라.

| 스크립트 | 하는 일 |
| --- | --- |
| `scripts/list-sessions.py` | 세 에이전트 세션을 한 번에 훑어 최신순 목록 |
| `scripts/read-session.py` | 세션 하나의 대화를 순서대로 펼쳐 읽기 |
| `scripts/resume-brief.py` | "어디까지 하다 멈췄나" 요약 (이어받기 브리핑) |

공용 파싱 코드는 같은 디렉토리의 `agent_sessions_lib.py` 에 있다. 직접 파이썬을
짜야 하면 이 모듈을 import 해서 재사용해라.

## 1. 크로스 에이전트 목록

세 에이전트 세션을 한 번에 훑어 `시각 / 에이전트 / 살아있음 / cwd / 첫 사용자 메시지 /
파일경로` 로 최신순 출력한다.

```bash
python3 scripts/list-sessions.py --limit 20
```

프로젝트로 좁힐 때 (`--cwd` 는 절대 경로도, 부분 문자열도 받는다):

```bash
python3 scripts/list-sessions.py \
  --cwd ~/dev/my-project --since 2d --limit 20
```

자주 쓰는 옵션:

- `--agent claude,codex,agy` — 에이전트 선택. 기본은 셋 다.
- `--since 30m|6h|7d|2w` — 최근 갱신분만.
- `--live-only` — 지금 살아 있어 보이는 세션만.
- `--include-approval` — codex 승인 판정용 하위 세션까지 포함 (아래 함정 참고).
- `--max-scan 0` — 최근 파일만 열어 보는 상한을 해제한다. 오래된 세션을 찾을 때 쓴다.
  기본값은 에이전트별 150 개이고, `/mnt/c` 아래 Windows 쪽 저장소가 느려서 둔 상한이다.
- `--json` — 기계가 읽을 형태로.
- `--show-roots` — 실제로 뒤진 codex 홈 목록을 표준 오류로 먼저 찍는다.

## 2. 저장소 위치

### Claude Code

- 세션: `~/.claude/projects/<cwd 를 슬러그화한 이름>/<session_id>.jsonl`
- 슬러그 규칙은 절대 경로의 `/` 를 `-` 로 바꾼 것이다.
  `/home/alice/dev/my-project` → `-home-alice-dev-my-project`
- 프로젝트별 메모리: `~/.claude/projects/<슬러그>/memory/MEMORY.md` 와 그 옆의
  개별 메모 파일들. 세션을 읽기 전에 여기부터 보면 맥락을 빨리 잡는다.

```bash
ls -1t ~/.claude/projects/-home-alice-dev-my-project/*.jsonl | head
```

### codex — 홈이 여러 군데다. 이게 가장 큰 함정이다

codex 는 `CODEX_HOME` 환경 변수로 홈이 통째로 갈린다. 한 군데만 보면 세션을 통째로
놓친다. 이 머신에서 확인된 홈:

| 홈 | 언제 여기 쌓이나 | 확인된 rollout 개수 |
| --- | --- | --- |
| `$CODEX_HOME/sessions/YYYY/MM/DD/` | **환경 변수가 1순위다.** codex 를 띄운 셸에 이 변수가 있으면 무조건 여기다 | — |
| `~/.local/share/orca/codex-runtime-home/home/sessions/YYYY/MM/DD/` | **Orca 터미널에서 띄운 codex.** Orca 가 `CODEX_HOME` 을 갈아끼운다. 실무 세션 대부분이 여기다. macOS 는 `~/Library/Application Support/orca/codex-runtime-home/home` | 40 |
| `~/.codex/sessions/YYYY/MM/DD/` | 맨손으로 띄운 codex | 10 |
| `/mnt/<드라이브>/Users/<사용자>/.codex/sessions/YYYY/MM/DD/` | Windows 쪽에서 띄운 codex (Windows Subsystem for Linux 에서 보인다). 드라이브 문자와 automount 루트가 환경마다 달라서 `/mnt/*` 와 `/media/*` 를 둘 다 훑는다 | 106 |

`list-sessions.py` 는 세 홈을 전부 glob 한다. 직접 찾을 때는:

```bash
ls -1t ~/.local/share/orca/codex-runtime-home/home/sessions/*/*/*/rollout-*.jsonl | head
ls -1t ~/.codex/sessions/*/*/*/rollout-*.jsonl | head
ls -1t /mnt/*/Users/*/.codex/sessions/*/*/*/rollout-*.jsonl | head
```

**SQLite 색인은 홈마다 따로 논다.** 각 홈의 `state_<숫자>.sqlite` 에 `threads`
테이블(`id`, `rollout_path`, `created_at`, `updated_at`, `cwd`, `title`,
`first_user_message`, `cli_version`, `git_branch` 등)이 있고, `thread_history_<숫자>.sqlite`
에 `thread_items` / `thread_turns` 가 있다.

**파일 이름의 숫자는 스키마 버전이라 codex 가 올린다.** 확인 시점에는 `state_5`,
`thread_history_1`, `logs_2`, `goals_1`, `memories_1`, `queue_1` 이었다. 숫자를 고정하면
codex 가 올라간 날 조용히 빈 결과가 나오므로 `state_*.sqlite` 로 glob 해야 한다.
스크립트는 그렇게 한다.

한 홈이 색인을 두 군데 둘 수 있다. `<홈>/state_*.sqlite` 와 `<홈>/sqlite/state_*.sqlite`
를 둘 다 본다. 확인된 머신에서는 네 벌이 나왔다.

- `~/.codex/state_5.sqlite` — `~/.codex/sessions` 만 담는다 (threads 10 개)
- `~/.codex/sqlite/state_5.sqlite` — 최신 codex 가 쓰는 위치. Windows 쪽 rollout
  경로를 담고 있다 (threads 172 개)
- `~/.local/share/orca/codex-runtime-home/home/state_5.sqlite` — Orca 홈 전용 (threads 40 개)
- `/mnt/<드라이브>/Users/<사용자>/.codex/state_5.sqlite`

**색인 하나만 믿으면 세션을 놓친다.** 색인은 참고용이고, 확실한 조회는 모든 홈의
rollout 파일을 직접 glob 하는 것이다. 색인을 굳이 보고 싶으면 (기본 목록과 마찬가지로
승인 판정용 하위 세션은 걸러진다. `--include-approval` 로 꺼낸다):

```bash
python3 scripts/list-sessions.py --codex-index --limit 20
```

그 외:

- 프롬프트 이력: `~/.codex/history.jsonl` (`session_id`, `ts`, `text`). 홈마다 따로 있다.
- 진행 중 표시: `<홈>/thread-writer-locks/<session_id>.lock`. 종료 후에도 남는
  경우가 있어 단독으로는 못 믿는다.

### agy (Google Antigravity CLI, `~/.local/bin/agy`)

- 세션: `~/.gemini/antigravity-cli/conversations/<conversation_uuid>.db` — **SQLite**
- **1차 조회 수단은 `~/.gemini/antigravity-cli/history.jsonl` 이다.** 줄마다
  `{"display": "<사용자가 친 프롬프트>", "timestamp": <epoch 밀리초>,
  "workspace": "<cwd>", "conversationId": "<uuid>"}` 형태라서 `workspace` 로
  필터하면 프로젝트별 세션을 바로 찾는다.

```bash
grep -F '"workspace":"'"$PWD"'"' ~/.gemini/antigravity-cli/history.jsonl | tail -20
```

  오래된 줄에는 `conversationId` 가 없다 (이 머신 기준 6 줄). 그런 줄은 프롬프트
  원문만 남고 어느 대화인지는 `workspace` 와 `timestamp` 로 추정해야 한다.

- 그 외: `conversation_summaries.db` (`conversation_id`, `title`, `preview`,
  `step_count`, `workspace_uris`, `last_user_input_time` 등. 모든 대화가 들어 있지는
  않다), `brain/`, `knowledge/`, `log/cli-*.log`

## 3. 세션 하나 펼쳐 읽기

인자로 파일 절대 경로를 주거나, session id / conversation uuid 를 줘도 된다
(전체 저장소에서 찾아서 매칭한다).

```bash
python3 scripts/read-session.py \
  ~/.local/share/orca/codex-runtime-home/home/sessions/2026/08/27/rollout-2026-08-27T13-48-11-01a0418c-0a7c-7643-8451-070ea39daa3e.jsonl

python3 scripts/read-session.py 01a0418c-0a7c-7643
```

옵션:

- `--no-tools` — 도구 호출과 결과를 빼고 사람 대화만. 흐름 파악할 때 이게 제일 낫다.
- `--tail 30` / `--head 30` — 앞뒤 잘라 보기.
- `--grep <문자열>` — 그 문자열이 든 이벤트만.
- `--thinking` — 추론 블록도 포함.
- `--preamble` — 모델에 주입된 지시문(developer 역할, AGENTS.md 본문,
  skills 목록)까지 출력. 기본은 숨긴다.
- `--tool-chars` / `--text-chars` — 이벤트 하나당 최대 글자 수. 기본 400 / 4000.

세션이 크면 한 번에 다 읽지 말고 `--no-tools --tail 40` 으로 끝부분부터 보고,
필요한 구간을 `--grep` 으로 좁혀라.

## 4. "어디까지 하다 멈췄나" 요약 — 이어받기의 본 절차

```bash
python3 scripts/resume-brief.py 01a0418c-0a7c-7643
```

출력에 담기는 것:

- 에이전트, session id, cwd, 마지막 갱신 시각과 경과 시간, 파일 경로
- 이벤트 / 사용자 발화 / 어시스턴트 발화 / 도구 호출 개수
- **마지막 사용자 요청** — agy 세션은 `history.jsonl` 의 프롬프트 원문을 쓴다.
  protobuf 추출보다 정확하기 때문이다
- **마지막 어시스턴트 발화**
- **마지막 도구 호출 몇 개** (`--tool-tail N`)
- 결과 없이 끝난 도구 호출 개수 — 0 이 아니면 중간에 끊긴 것이다
- todo 도구 기록이 있으면 그 내용, 없으면 미완료 단서가 될 만한 마지막 발화

이어받기 순서:

1. `list-sessions.py --cwd <프로젝트>` 로 대상 세션을 고른다.
2. `resume-brief.py <세션>` 으로 중단 지점을 파악한다.
3. 살아 있는 세션이면 멈추고 사용자에게 먼저 물어본다 (아래).
4. 필요하면 `read-session.py --no-tools --tail 60` 으로 앞뒤 맥락을 더 읽는다.
5. 그 프로젝트의 `AGENTS.md` 와 `~/.claude/projects/<슬러그>/memory/` 를 읽고
   작업을 이어간다.

## 5. 진행 중인 세션 판별

- Claude Code / codex: 세션 파일 mtime 이 방금이면 살아 있다.
- agy / codex 색인: `-wal` 파일 크기가 0 이 아니면 아직 쓰는 중이다.
  agy 의 `-shm` 파일은 프로세스가 모든 대화를 열어 두기 때문에 살아 있는 신호가
  못 된다. 쓰지 마라.
- codex: `<홈>/thread-writer-locks/<session_id>.lock` 존재 여부는 보조 신호다.

`list-sessions.py` 는 기본 15 분(`--live-window` 로 조절) 안에 갱신된 세션을 `LIVE`
로 표시한다.

> **살아 있는 세션의 작업을 그냥 이어받으면 두 에이전트가 같은 파일을 동시에 고쳐
> 충돌한다. 이어받기 전에 반드시 사용자에게 "이 세션 아직 돌고 있는데 이어받을까요"
> 를 먼저 물어라.**

## 6. 원시 데이터 구조 레퍼런스

스크립트로 안 되는 걸 직접 파싱해야 할 때 쓴다.

### Claude Code 세션 JSONL

평문 JSONL. 줄마다 `type` 필드. 이 머신의 artel 프로젝트에서 실제로 관측된 타입과
빈도:

`assistant`, `attachment`, `user`, `last-prompt`, `queue-operation`, `ai-title`,
`pr-link`, `atis-latch`, `bridge-session`, `mode`, `system`, `permission-mode`,
`file-history-snapshot`, `cost-state`

읽을 때 실제로 필요한 것:

- `user` / `assistant` — `message.content` 가 문자열이거나 블록 배열이다. 블록
  `type` 은 `text`, `thinking`, `tool_use`, `tool_result`, `image`.
  `user` 레코드에 `cwd`, `gitBranch`, `version`, `sessionId`, `isSidechain` 이 붙는다.
- `queue-operation` (`operation == "enqueue"`) — 사용자가 큐에 넣은 입력이 `content` 에.
- `last-prompt` — 마지막 프롬프트 원문.
- `system` — hook 실행 요약 등.
- `bridge-session`, `atis-latch`, `mode`, `permission-mode`, `cost-state` — 대화
  내용이 아니라 세션 메타데이터다.

사람이 친 말만 뽑으려면 `<command-name>`, `<system-reminder>`, `<task-notification>`,
`<bash-input>`, `Caveat: The messages below were generated` 로 시작하는 사용자
텍스트는 걸러야 한다. 이건 주입된 것이지 사용자가 친 게 아니다.

### codex rollout JSONL

파일 이름 `rollout-<ISO 시각>-<session_id>.jsonl`. 평문 JSONL.

- 첫 줄이 `type == "session_meta"` 이고 `payload` 안에 `session_id`, `cwd`,
  `cli_version`, `model_provider`, `originator`, `base_instructions`.
- 대화는 `type == "response_item"` 줄이고 `payload.type` 이 다음 중 하나다:
  `message` (`role` 이 `user` / `assistant` / `developer`, 본문은
  `payload.content[].text` 를 이어 붙인다), `reasoning`, `custom_tool_call`,
  `custom_tool_call_output`, `function_call`, `function_call_output`,
  `local_shell_call`.
- `type == "event_msg"` 는 UI 이벤트(`token_count`, `item_completed`,
  `task_started`, `task_complete`)라 대화 재구성에는 필요 없다.
- 앞쪽 `user` 메시지 몇 개는 실제 사용자 입력이 아니라 주입된 지시문이다.
  `# AGENTS.md instructions`, `<user_instructions>`, `<environment_context>`,
  `<recommended_plugins>` 로 시작하면 건너뛰어라.

**승인 판정용 하위 세션을 걸러라.** codex 는 샌드박스 승인을 판정하려고 같은 시각에
같은 session id 접두사로 별도 rollout 을 여러 개 만든다. 첫 실제 사용자 메시지가
`The following is the Codex agent history whose request action you are assessing`
로 시작하면 그건 본 대화가 아니다. 기본으로 숨기고, 승인 판정 과정 자체를 봐야 할
때만 `--include-approval` 로 꺼내라.

### agy conversation SQLite

`~/.gemini/antigravity-cli/conversations/<conversation_uuid>.db` 의 테이블:
`trajectory_meta`, `steps`, `gen_metadata`, `executor_metadata`,
`parent_references`, `trajectory_metadata_blob`, `battle_mode_infos`.

`steps` 컬럼: `idx`, `step_type`, `status`, `has_subtrajectory`, `metadata`,
`error_details`, `permissions`, `task_details`, `render_info`, `step_payload`,
`step_format`.

확인된 `step_type` 의미:

| 값 | 의미 |
| --- | --- |
| `14` | 사용자 메시지 |
| `15` | 어시스턴트 출력 및 추론 |
| `132` | 도구 호출 (인자가 평문 JSON) |
| `101` | 도구 결과 |

`step_payload` 와 `metadata` 는 **protobuf blob 이고 공개 파서가 없다.** printable
바이트 시퀀스를 정규식으로 뽑으면 사람이 읽을 수 있다. 검증된 정규식:

```python
re.compile(rb'[^\x00-\x08\x0b-\x1f\x7f]{20,}')
```

`agent_sessions_lib.agy_printable()` 이 여기에 더해 protobuf 헤더 부스러기
(uuid 만 있는 조각, `sessionID`, snake_case 토큰 하나짜리 조각)를 걸러 준다.

**주의 두 가지:**

1. 이 추출은 **무손실 전사가 아니다.** 근사치다. 정확한 사용자 프롬프트가 필요하면
   `history.jsonl` 의 `display` 를 봐라 (`read-session.py` 와 `resume-brief.py` 가
   agy 세션에 대해 자동으로 같이 찍어 준다).
2. 도구 결과가 길면 **저장 시점에 이미** `<truncated 31 lines>` 식으로 잘려 있다.
   원본이 디스크에 없다.
