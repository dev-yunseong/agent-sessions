# Agent Sessions

Agent Sessions 는 다른 coding `agent` 가 디스크에 남긴 `session` 기록을 찾아 읽는 `agent` `skill` 이다.

Claude Code, codex, agy (Google Antigravity CLI) 는 각각 모든 대화를 로컬에 남긴다. 형식은 셋 다 다르고, 저장 위치도 여러 군데다. 이 `skill` 은 그 기록이 어디에 있는지, 어떻게 parsing 하는지, 다른 `agent` 가 어디서 멈췄는지 어떻게 요약하는지 안다 — 그래서 인계받을 때 상대 `agent` 가 계속 돌고 있을 필요가 없다.

## 하는 일

- **List** — 세 `agent` 의 `session` 을 표 하나에 최신순으로 모은다. 작업 디렉토리나 기간으로 거를 수 있다.
- **Read** — `session` 하나를 순서대로 된 transcript 로 읽는다. tool call 은 넣거나 뺄 수 있다.
- **Brief** — `session` 이 어디서 멈췄는지 알려준다. 마지막 사용자 요청, 마지막 assistant 메시지, 마지막 tool call, 끝내지 못한 일.
- **Detect** — 아직 살아 있는 `session` 을 가려낸다. 다른 `agent` 가 지금 하고 있는 일을 가로채지 않도록.

전부 읽기 전용이다. SQLite 저장소는 `mode=ro` URI 로 열기 때문에 돌아가는 `session` 을 건드리는 일이 없다.

## 설치

로컬 checkout 에서:

```sh
npx skills add . --skill agent-sessions
```

GitHub 에서:

```sh
npx skills add dev-yunseong/agent-sessions --skill agent-sessions
```

## 사용

```text
Find what codex was doing in this directory and pick it up.
```

또는 `skill` 디렉토리에서 script 를 직접 실행한다:

```sh
python3 scripts/list-sessions.py --cwd ~/dev/my-project --since 2d
python3 scripts/resume-brief.py <session-id>
python3 scripts/read-session.py <session-id> --no-tools --tail 40
```

## 요구 사항

- Python 3, 표준 라이브러리만 쓴다. 서드파티 패키지는 없다.
- `sqlite3` 명령줄 도구는 **필요 없다**.

## 알고 있는 저장 위치

| Agent | 형식 | 위치 |
| --- | --- | --- |
| Claude Code | JSONL, 한 줄에 record 하나 | `~/.claude/projects/<slugified-cwd>/<session-id>.jsonl` |
| codex | JSONL `rollout`, 한 줄에 event 하나 | `$CODEX_HOME`, `~/.codex`, Orca runtime home, 그리고 `/mnt/*` 아래 보이는 Windows home |
| agy | protobuf payload 를 담은 SQLite | `~/.gemini/antigravity-cli/conversations/<uuid>.db` |

codex 는 실행 방식에 따라 기록을 여러 home 에 나눠 쌓고, home 마다 SQLite `index` 를 따로 둔다. `index` 하나만 믿으면 `session` 을 소리 없이 통째로 놓치기 때문에, 이 `skill` 은 모든 home 을 훑어 `rollout` 파일을 glob 한다. `SKILL.md` 에 전체 배치가 정리되어 있다. 각 형식을 손으로 parsing 할 때 필요한 record 모양까지 들어 있다.

## 주의할 점

- agy 는 각 단계를 `protobuf` blob 으로 저장하는데 공개 parser 가 없다. 이 `skill` 은 출력 가능한 문자 구간을 뽑아 텍스트를 복원한다. 근사치이지 무손실 transcript 가 아니다. 정확한 사용자 prompt 는 `history.jsonl` 에서 가져온다. 긴 tool 출력은 agy 가 디스크에 쓸 때 이미 잘라 놓는다.
- record 종류와 step type 번호는 관찰로 알아낸 것이다. 세 `agent` 중 하나라도 schema 가 바뀌면 다시 알아내야 할 수 있다.
- `session` 파일에는 자격 증명, token, 비공개 코드가 들어 있을 수 있다. 필요한 부분만 인용하고, 외부 서비스로 넘기지 마라.
