# crew

[English](README.md) · **한국어**

`crew`는 현재 AI CLI가 다른 CLI에 작업을 맡길 때 쓰는 Python 명령입니다. 워커를 실행하고 종료 상태, 파일 변경, 작업 결과를 기록합니다. 작업 분할과 결과 수락은 현재 AI가 담당합니다.

[기술 명세](../docs/SPEC.md) · [스킬 지침](../skill/cli-crew/SKILL.md) · [Windows 설치](../README.ko.md)

## 개발용 설치

저장소의 `crew/` 폴더에서 실행합니다.

```
uv tool install -e .
crew --version
```

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
crew accept T-01            # or: crew reject T-01 --reason "..."
crew handoff --agent claude --task my-task --status Done
```

agy는 읽기 작업에만 사용할 수 있습니다. 민감한 데이터 공유에는 `--approve-data-sharing`, 높은 추론 수준이나 승인 대상 모델에는 `--approve-cost`가 필요합니다. 승인 옵션은 사용자 확인 후 적용합니다. 워커 환경에서는 다른 제공업체의 인증 변수 등 자격 증명으로 분류된 변수를 제거합니다.

## 전체 명령어

작업 대상 프로젝트에서 실행합니다. 대문자는 실제 값으로 바꿉니다. `crew --help`, `crew contract --help`, `crew run --help`로 단계별 도움말을, `crew --version`으로 설치 버전을 확인할 수 있습니다.

| 사용법 | 설명 |
| --- | --- |
| `crew init` | 기존 지침을 재사용하고 없으면 프로젝트 지침을 만듭니다. |
| `crew detect [--paths PATH ...] [--json]` | 사용 가능한 워커와 적용할 지침, 실행 중인 작업을 확인합니다. |
| `crew contract new ID [--agent CLI] [--mode MODE] [--kind KIND] [--write PATH ...]` | 계약 초안을 만듭니다. 실행 전에 목표·맥락·완료 조건을 채웁니다. |
| `crew routing --kind KIND --mode MODE [--json]` | 프로젝트에서 같은 종류·모드로 종료된 최근 워커 실행을 최대 10개 표시합니다. 종류는 investigate, implement, review, document, other입니다. |
| `crew compare ID... [--json]` | 완료된 작업들의 검증 근거, 위반, 워커 소요 시간과 공급자별 사용량을 비교합니다. |
| `crew contract check FILE [--agent CLI] [--effort LEVEL] [--model MODEL] [--json]` | 워커를 시작하기 전에 계약만 검사합니다. |
| `crew run --contract FILE [--agent CLI] [--effort LEVEL] [--model MODEL] [--timeout MIN] [--with-result] [--json] [--approve-cost] [--approve-data-sharing]` | 워커를 실행하고 완료를 기다립니다. 타임아웃 단위는 분입니다. 기존 --allow-unbounded-write 옵션을 넣어도 agy 쓰기는 거부됩니다. |
| `crew status [--all] [--json]` | 현재·최근 작업 상태를 봅니다. 이전 작업까지 보려면 --all을 사용합니다. |
| `crew result ID [--raw]` | 저장된 워커 결과를 읽습니다. --raw는 응답 전문을 표시합니다. |
| `crew check ID [--recheck] [--json]` | 실행 검사 결과를 봅니다. --recheck는 저장된 근거로 다시 검사합니다. |
| `crew accept ID [--verified TEXT] [--override --reason TEXT]` | 수락을 기록합니다. 부분 완료 또는 미검증 결과는 직접 확인한 내용을 적어야 합니다. |
| `crew reject ID --reason TEXT` | 반려하고 이유를 남깁니다. |
| `crew cancel ID [--target IDENTITY] [--yes]` | 종료 대상을 확인하고, 사용자 승인 뒤 미리보기가 출력한 명령을 실행합니다. |
| `crew handoff --agent NAME --task SLUG --status STATUS [--since ISO_OR_LAST] [--tasks ID,ID] [--lang LANG]` | 다음 세션용 인계를 만듭니다. 언어는 en 또는 ko, 상태는 Done, "In progress", Blocked 중 하나입니다. |
| `crew clean [--sessions] [--runs] [--older-than DAYS] [--yes] [--json]` | 정리할 기록을 미리 봅니다. 기본 기간은 7일이며 --yes를 붙이면 실제 정리를 수행합니다. |

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

## 테스트

`crew/` 폴더에서 실행합니다.

```
py -3 -m unittest discover -s tests
```

테스트는 `CREW_BIN_<AGENT>`로 테스트 워커(`tests/fake_worker.py`)를 선택하며, 임시 Git 저장소는 Git에서 제외되는 `tests/_work/`에 생성합니다.
