# crew

[English](README.md) · **한국어**

`crew`는 현재 AI CLI가 다른 CLI에 작업을 맡길 때 쓰는 Python 명령입니다. 워커를 실행하고 종료 상태, 파일 변경, 작업 결과를 기록합니다. 작업 분할과 결과 수락은 현재 AI가 담당합니다.

[워커 프로토콜](../skill/cli-crew/references/protocol.md) · [스킬 지침](../skill/cli-crew/SKILL.md) · [Windows 설치](../README.ko.md)

## 개발용 설치

저장소의 `crew/` 폴더에서 실행합니다.

```
uv tool install -e .
crew --version
```

### 개발용 설치 업데이트와 복구

개발용 편집 가능 설치는 복제본의 로컬 변경까지 읽습니다. 최신 변경을 받은 뒤 스킬도 다시 읽으려면 새 AI CLI 세션을 여세요. 공유 Codex 세션을 추가하려면 저장소 루트에서 `uv tool install --reinstall -e "./crew[terminal]"`을 실행합니다.

Windows에서 재설치 중 접근 오류가 나서 `crew`가 실행되지 않더라도 기존 uv 환경의 Python 실행 파일이 남아 있으면 해당 환경을 복구할 수 있습니다. 저장소 루트에서 실행합니다.

```powershell
$crewPython = Join-Path (uv tool dir) 'cli-crew/Scripts/python.exe'
uv pip install --python $crewPython -e "./crew[terminal]"
crew --version
crew trace view --help
```

기본 설치를 복구하려면 두 번째 명령에 `-e ./crew`를 사용합니다. `terminal` 옵션은 `pywinpty`를 설치합니다.

## 프로젝트 지침 초기화

대상 프로젝트에서 `crew init`을 실행합니다. 루트에 지원되는 지침 파일이 없을 때만 기본 `AGENTS.md`를 만들고, 스킬을 통해 설정을 요청하면 메인이 확인한 프로젝트 정보를 보완합니다.

## 위임 절차

작업할 프로젝트에서 실행합니다. 계약 파일에는 목표, 대상 경로, 완료 조건을 작성합니다. 수락 전에는 실행 결과와 근거를 확인하세요.

```
crew detect --paths src/
crew routing --kind review --mode read
crew contract new T-01 --agent codex --kind review --mode read
# edit .agent-runs/contracts/T-01.md
crew run --contract .agent-runs/contracts/T-01.md --with-result
# inspect status, boundaries and RESULT; verify the evidence before acceptance
crew accept T-01 --verified "<확인한 내용>"  # 또는: crew reject T-01 --reason "..."
crew handoff --agent claude --task my-task --status Done
```

agy 쓰기 작업은 세션별 `--settings` 파일로 프로젝트 안에만 쓰게 제한하며, 프로젝트 안의 새 파일은 경계 검사가 보고합니다(Git 프로젝트에서 git이 무시하는 파일은 비밀 파일 이름이 아니면 검사하지 않습니다). 민감한 데이터 공유에는 `--approve-data-sharing`, 높은 추론 수준이나 승인 대상 모델에는 `--approve-cost`가 필요합니다. 승인 옵션은 사용자 확인 후 적용합니다. 워커 환경에서는 다른 제공업체의 인증 변수 등 자격 증명으로 분류된 변수를 제거합니다.

작은 수정은 `--agent copilot --mode write --write <파일>`로 지정할 수 있습니다. 이미 존재하는 파일 하나와 `effort: low`를 사용합니다. [Copilot 전용 프로필 설정](../skill/cli-crew/references/adapters.md#github-copilot-cli-setup)

## 전체 명령어

작업 대상 프로젝트에서 실행합니다. 대문자는 실제 값으로 바꿉니다. `crew --help`, `crew contract --help`, `crew run --help`로 단계별 도움말을, `crew --version`으로 설치 버전을 확인할 수 있습니다.

| 사용법 | 설명 |
| --- | --- |
| `crew init` | 기존 지침을 재사용하고 없으면 프로젝트 지침을 만듭니다. |
| `crew detect [--paths PATH ...] [--json]` | 사용 가능한 워커와 적용할 지침, 실행 중인 작업을 확인합니다. |
| `crew plan new ID` | 여러 단계 목표를 위한 계획 초안을 `.agent-runs/plans/`에 만듭니다. |
| `crew plan check ID [--json]` | 계획의 단계 ID, 실행 주체, 의존 관계, 검증 항목을 검사합니다. |
| `crew plan show [ID] [--json]` | 계획 목록을 보거나, 한 계획의 연결된 실행·시작 가능한 단계·상태 불일치를 봅니다. |
| `crew contract new ID [--agent CLI] [--mode MODE] [--kind KIND] [--write PATH ...] [--plan ID --step STEP]` | 계약 초안을 만듭니다. 계획의 단계에 연결할 수 있습니다. 실행 전에 목표·맥락·완료 조건을 채웁니다. |
| `crew routing --kind KIND --mode MODE [--json]` | 프로젝트에서 같은 종류·모드로 종료된 최근 워커 실행을 최대 10개 표시합니다. 종류는 investigate, implement, review, document, other입니다. |
| `crew compare ID... [--json]` | 완료된 작업들의 검증 근거, 위반, 워커 소요 시간과 공급자별 사용량을 비교합니다. |
| `crew contract check FILE [--agent CLI] [--effort LEVEL] [--model MODEL] [--json]` | 워커를 시작하기 전에 계약만 검사합니다. |
| `crew run --contract FILE [--agent CLI] [--effort LEVEL] [--model MODEL] [--timeout MIN] [--with-result] [--json] [--approve-cost] [--approve-data-sharing]` | 워커를 실행하고 완료를 기다립니다. 타임아웃 단위는 분입니다. 기존 --allow-unbounded-write 옵션은 폐기되어 무시됩니다. |
| `crew status [--all] [--json]` | 현재·최근 작업 상태를 봅니다. 이전 작업까지 보려면 --all을 사용합니다. |
| `crew result ID [--raw]` | 저장된 워커 결과를 읽습니다. --raw는 응답 전문을 표시합니다. |
| `crew check ID [--recheck] [--json]` | 실행 검사 결과를 봅니다. --recheck는 저장된 근거로 다시 검사합니다. |
| `crew accept ID [--verified TEXT] [--override --reason TEXT]` | 수락을 기록합니다. 부분 완료 또는 미검증 결과는 직접 확인한 내용을 적어야 합니다. |
| `crew reject ID --reason TEXT` | 반려하고 이유를 남깁니다. |
| `crew cancel ID [--target IDENTITY] [--yes]` | 종료 대상을 확인하고, 사용자 승인 뒤 미리보기가 출력한 명령을 실행합니다. |
| `crew trace view [--port PORT] [--interactive codex]` | Crew Runs를 로컬 주소에 엽니다. Windows에서는 선택 옵션으로 새 Codex 세션을 공유할 수 있습니다. |
| `crew handoff --agent NAME --task SLUG --status STATUS [--since ISO_OR_LAST] [--tasks ID,ID] [--lang LANG]` | 다음 세션용 인계를 만듭니다. 언어는 en 또는 ko, 상태는 Done, "In progress", Blocked 중 하나입니다. |
| `crew clean [--sessions] [--runs] [--older-than DAYS] [--yes] [--json]` | 정리할 기록을 미리 봅니다. 기본 기간은 7일이며 --yes를 붙이면 실제 정리를 수행합니다. `--runs`는 `history.jsonl`, `locks/`, `contracts/<task_id>.md`를 남깁니다. |

## 선택 설정

`~/.crew/config.toml`에서 동시 실행 수, 타임아웃, 기본 추론 수준과 승인 대상 모델을 설정할 수 있습니다.

```toml
max_concurrent = 3
default_timeout_min = 20
default_effort = "low"
approval_models = []        # model ids that need --approve-cost
agy_prompt_mode = "file"    # or "inline"
env_passthrough = []        # extra credential-like env var names to pass to workers
```

API 키 변수(`OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`)가 설정돼 있으면 워커가 구독 대신 API로 과금될 수 있으므로 `crew detect`가 경고합니다. `config.toml`이나 명령이 다루는 작업의 상태 파일을 읽을 수 없으면 `crew`는 traceback 대신 해당 파일 경로를 출력하고 코드 2로 종료합니다. 그 파일을 고치거나 다른 곳으로 옮기세요. `crew status` 같은 목록 명령은 읽을 수 없는 상태 파일을 건너뜁니다.

## 실시간 트레이스 뷰어

프로젝트에서 `crew trace view`를 실행한 뒤 표시된 `http://127.0.0.1:…/` 주소를 엽니다. `--port 8765`로 포트를 고정할 수 있습니다. 여러 탭은 프로젝트 감시를 공유하고, 로컬 서버는 변경을 SSE로 보냅니다. 다시 연결하면 최근 변경을 이어 받습니다. 해당 변경 기록이 이미 정리됐다면 `.agent-runs/`에 남아 있는 최신 상태와 타임라인을 다시 불러옵니다. 워커 상태, 경과 시간, 재시도·취소, 리뷰 결과와 메인의 판정을 표시합니다.

왼쪽 목록에서 작업을 고릅니다. **Timeline**은 실행, 시도, 서브에이전트 활동을 표시합니다. **Output**의 **Load result**와 **Verification**의 **Run check**는 선택한 작업 ID와 CLI 기본 옵션을 사용합니다. **Verification**에는 기록된 리뷰 판정과 메인 판정도 표시됩니다. **Project status**는 표시된 프로젝트에서 `crew status`를 실행합니다. 다른 프로젝트를 조회하려면 해당 프로젝트에서 뷰어를 엽니다. **Command history**에는 최근 명령 20개의 표준 출력·오류와 종료 코드가 표시됩니다. 명령당 최대 32,768자를 `.agent-runs/`의 로컬 `.viewer-command` 기록에 저장합니다.

**New run**에서 기존 계약을 고르고 **Preview run**을 눌러 작업, 워커, 실행 모드를 확인한 뒤 실행합니다. 승인이 필요하거나 위임 지침을 검토해야 하는 계약은 CLI에서 실행합니다. 기존 예약, 검사, 결과 확인 절차가 적용되며 뷰어를 종료해도 실행 컨트롤러는 계속 동작합니다. 실행 중인 작업 상세에서 **Cancel run**을 누르면 워커 PID와 생성 시각을 확인할 수 있습니다. 확인한 대상을 `crew cancel`에 전달하므로 워커가 바뀌면 다시 미리 봐야 합니다.

Codex와 agy는 구조화된 출력에서 하위 에이전트 ID가 확인될 때 실시간으로 표시합니다. 저장된 증거로 종료 후 하위 에이전트가 확인될 수도 있습니다. Claude 워커는 현재 실행 방식에서 종료 시 JSON을 내보내므로 서브에이전트 표시는 최종 증거에 따릅니다. Copilot은 워커 활동을 실시간으로 표시하며, 서브에이전트 사용 여부만 확인되는 경우가 있습니다. 타임라인의 워커 활동 시각은 `crew`가 이벤트를 관측한 시점입니다. 확인할 수 없는 하위 상태는 미확인으로 표시합니다.

Claude Code **메인 세션**을 포함하려면 프로젝트의 `.claude/settings.local.json`에 아래 명령 훅을 추가하고, Claude Code에서 요구하는 경우 세션을 다시 시작합니다. 두 명령에 `--plan P-example`을 붙이면 해당 cplan에 묶입니다. 훅은 ID와 시작·종료 이벤트, 시각을 `.agent-runs/`에 기록하며 대화 기록과 메시지 필드는 읽어 저장하지 않습니다.

```json
{
  "hooks": {
    "SubagentStart": [{"hooks": [{"type": "command", "command": "crew trace claude-hook"}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "crew trace claude-hook"}]}]
  }
}
```

### 칸반

프로젝트에서 `crew trace view`를 실행하고 표시된 주소를 연 뒤 **List** 옆의 **Kanban**을 누릅니다. **Plan**에서 cplan을 선택해 필터링합니다. 카드 위치는 Crew의 상태 기록에 따라 실시간으로 갱신되며, 보기를 전환해도 선택한 작업과 상세 탭이 유지됩니다.

| 열 | 표시되는 작업과 할 수 있는 일 |
| --- | --- |
| **Not run** | 작업 ID가 한 번도 등록되지 않은 저장 계약입니다. 카드를 눌러 계약을 미리 보고 **Confirm run**으로 시작합니다. |
| **Running** | 재시도를 포함해 현재 실행 중인 작업입니다. 카드를 눌러 최신 시도를 보거나 **Cancel run**에서 취소할 워커를 확인합니다. |
| **Needs review** | 수용 대기, 반려, 고아 또는 오래된 실행처럼 확인이 필요한 작업입니다. **Output**과 **Verification**에서 결과와 기록된 근거를 확인합니다. |
| **Complete** | 메인 에이전트가 수용한 작업입니다. 실패한 실행을 예외로 수용한 경우에도 카드에는 원래 실행 상태가 표시됩니다. |

수용과 반려는 CLI에서 `crew accept <task_id> --verified "<확인한 내용>"` 또는 `crew reject <task_id> --reason "<사유>"`로 기록합니다. 판정이 저장되면 칸반에 반영됩니다. 승인이나 위임 지침 검토가 필요한 계약은 기존 CLI 절차를 따릅니다.

### CLI 세션 공유

Windows에서 `terminal` 옵션으로 `pywinpty`를 설치하고 `codex.exe`를 PATH에 둡니다. 브라우저 파일은 Crew 패키지에 포함됩니다. 첫 명령은 Crew 저장소 루트에서, 둘째 명령은 작업할 프로젝트의 대화형 Windows 터미널에서 실행합니다.

```powershell
uv tool install --reinstall "./crew[terminal]"
crew trace view --port 8765 --interactive codex
```

`http://127.0.0.1:8765/`에서 **CLI session**을 누르거나 터미널에 표시된 세션 주소를 직접 엽니다. 양쪽에서 같은 Codex 출력을 보고 입력할 수 있습니다. 화면 크기는 로컬 터미널을 따릅니다. 브라우저 탭을 닫아도 Codex는 계속 실행됩니다. 탭을 다시 열면 기존 출력을 복원한 뒤 입력할 수 있습니다. 출력은 메모리에 보관하고 길어지면 임시 파일을 사용합니다. Codex가 끝나면 세션과 뷰어가 닫히고 임시 파일이 삭제됩니다. 실행할 때마다 새 세션이 시작됩니다.

## 테스트

`crew/` 폴더에서 실행합니다.

```
py -3 -m unittest discover -s tests
```

테스트는 `CREW_BIN_<AGENT>`로 테스트 워커(`tests/fake_worker.py`)를 선택하며, 임시 Git 저장소는 Git에서 제외되는 `tests/_work/`에 생성합니다.
