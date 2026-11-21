# cli-crew

[![tests](https://github.com/HBy-cloud/cli-crew/actions/workflows/test.yml/badge.svg)](https://github.com/HBy-cloud/cli-crew/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4.svg)
![Version 0.1.0](https://img.shields.io/badge/version-0.1.0-informational.svg)

[English](README.md) · **한국어**

**AI 코딩 CLI 사이에서 범위가 정해진 작업을 맡기고, 결과를 검증하고, 근거를 프로젝트에 남깁니다.**

cli-crew를 쓰면 지금 사용 중인 AI CLI(*메인*)가 다른 비대화형 CLI(*워커*)에 범위가 정해진 작업을 맡길 수 있습니다. 워커는 Codex, Claude Code, Antigravity를 지원합니다. 스킬은 메인에게 언제 위임할 가치가 있는지와 결과를 어떻게 검증할지 알려 줍니다. 외부 의존성이 없는 Python CLI `crew`는 기계적인 일을 맡습니다. 워커를 안전 모드로 실행하고, 시간 제한과 승인 조건을 지키게 하고, 파일 경계를 검사하고, 모든 실행을 기록합니다. 최종 판단은 언제나 메인이 합니다.

![메인이 계약을 정하고 워커가 실행한 뒤 메인이 결과를 검증하는 흐름](docs/assets/workflow.ko.svg)

[주요 기능](#주요-기능) · [설치](#설치) · [빠른 시작](#빠른-시작) · [명령어](#명령어) · [실행 결과 확인](#실행-결과-확인) · [지원 워커](#지원-워커) · [안전 모델](#안전-모델과-한계) · [다른 도구와 비교](#다른-오케스트레이터와의-차이) · [업데이트](#업데이트) · [제거](#제거) · [CLI 명령 안내](crew/README.ko.md)

## 주요 기능

- **목표 중심 위임.** 사용자는 원하는 결과만 말하면 됩니다. 메인이 직접 할 일, 자기 서브에이전트에 맡길 일, 워커에 맡길 일, 그리고 다른 모델의 독립 리뷰가 추가 호출만큼의 가치가 있는지를 판단합니다.
- **여러 단계 목표의 계획.** 메인은 단계마다 실행 주체(메인 자신, 메인의 내장 서브에이전트, crew 워커), 의존 단계, 검증 방법을 적은 계획을 씁니다. 실행 기록이 단계에 연결되므로 인수인계 뒤에도 진행 상황이 남습니다.
- **명시적인 작업 계약.** 위임하는 작업마다 Markdown 계약을 씁니다. 계약에는 목표, 참고할 파일 경로, 완료 조건이 들어가고, 수정 작업이면 워커가 바꿀 수 있는 경로도 들어갑니다.
- **수락 전 검증.** 모든 실행은 `DONE`, `DONE_UNVERIFIED`, `PARTIAL`, `FAILED` 등으로 분류됩니다. 범위 밖 파일 변경과 서브에이전트 사용도 검사합니다. 메인은 이유를 기록해 수락하거나 반려해야 합니다.
- **기본 안전장치.** 기본은 읽기 전용입니다. 쓰기 작업은 한 번에 하나만 실행하고, 우회 플래그는 거부합니다. 워커 프로세스에서는 자격 증명처럼 보이는 환경 변수를 제거합니다. 비용이 큰 실행과 데이터를 공유하는 실행은 사용자 승인이 필요합니다.
- **로컬에서 확인할 수 있는 기록.** 계약, 프롬프트, 로그, 결과, 판정이 프로젝트의 `.agent-runs/`에 남습니다. `crew handoff`로 인수인계 초안을 만들면 다른 CLI가 작업을 이어받을 수 있습니다.

## 동작 방식

1. 메인이 프로젝트 지침을 읽고 `crew detect`로 사용할 수 있는 워커를 확인합니다.
2. 분리할 수 있는 작업이면 계약을 쓰고(`crew contract new`) 실행합니다(`crew run`).
3. `crew`는 워커를 안전 모드로 실행하고 시간 제한을 적용합니다. 결과는 CLI의 종료 코드와 진단 출력, 워커의 결과 블록으로 분류합니다.
4. `crew`는 실행 전후의 프로젝트 스냅샷을 계약에서 허용한 경로와 비교합니다.
5. 메인은 워커의 주장을 소스와 대조하고 테스트를 직접 실행한 뒤 `crew accept` 또는 `crew reject`로 판정을 기록합니다.

## 설치

현재 cli-crew는 **Windows 11**에서만 검증했습니다.

**필요한 것**

- Git, PowerShell 7(`pwsh`), Python 3.12 이상, [uv](https://docs.astral.sh/uv/)
- 설치하고 로그인한 워커 CLI 하나 이상: [Codex CLI](https://github.com/openai/codex), Claude Code, Antigravity(`agy`)

**설치 명령**

```powershell
git clone https://github.com/HBy-cloud/cli-crew.git
cd cli-crew
uv tool install ./crew
$env:Path = "$(uv tool dir --bin);$env:Path"
pwsh -File scripts/install-skill.ps1
crew --version
```

설치 스크립트는 Claude Code, Codex, agy에 스킬을 등록합니다. 일부만 고르려면 `-Agents`를 지정하세요. 예: `pwsh -File scripts/install-skill.ps1 -Agents claude,codex`. Claude Code와 Codex는 복제한 폴더로 연결되는 디렉터리 정션을 쓰고, agy는 `~/.gemini/config/skills.json`에 스킬 경로를 등록합니다. 그래서 복제한 폴더는 같은 위치에 그대로 두어야 합니다. 새 터미널에서 `crew`를 찾지 못하면 `uv tool update-shell`을 실행하고 터미널을 다시 여세요.

## 빠른 시작

**작업할 프로젝트**에서 새 AI CLI 세션을 열고 원하는 결과를 말하세요.

> CSV를 내보내면 마지막 행이 빠져. 원인을 재현하고 수정한 뒤 회귀 테스트까지 추가해줘.

작은 버그라면 메인이 직접 고칠 수 있습니다. 독립 확인이 도움이 된다면 "파일 끝에 줄바꿈이 없을 때 내보내기가 어떻게 동작하는지"처럼 질문 하나를 읽기 전용 워커에 맡길 수 있습니다. 이때 수정과 회귀 테스트는 메인이 직접 맡습니다. 워커에게는 대화 전체가 아니라 질문, 관련 경로, 완료 조건을 담은 계약만 전달됩니다.

스킬을 직접 호출하려면 Claude Code에서는 `/cli-crew`, Codex에서는 `$cli-crew`를 쓰거나 요청에 `cli-crew`를 포함하세요. 프로젝트에서 `crew detect`를 실행하면 사용할 수 있는 워커가 표시됩니다.

**프로젝트 지침.** 프로젝트 설정을 요청하거나, 파일 수정이 허용된 요청에서 스킬을 이름으로 직접 호출하면 메인이 `crew init`을 실행합니다. 루트에 `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`가 있으면 그대로 씁니다. 없으면 기본 `AGENTS.md`를 만들고, 메인이 저장소에서 확인한 목적, 작업 규칙, 테스트 명령을 채웁니다. 메인이 다른 작업 중에 스스로 스킬을 불러왔거나 읽기 전용 요청이면 지침 파일을 만들지 않습니다. 대신 최종 보고에서 지침이 없다는 사실을 한 번 알리고 설정을 제안합니다.

## 명령어

보통은 메인이 대신 실행하지만, PowerShell에서 직접 써도 됩니다. 먼저 `crew --help`부터 보세요.

| 단계 | 명령 |
| --- | --- |
| 프로젝트 준비 | `crew init`, `crew detect` |
| 여러 단계 계획 | `crew plan new`, `crew plan check`, `crew plan show` |
| 워커 선택 | `crew routing`, `crew compare` |
| 계약 작성·검사 | `crew contract new`, `crew contract check` |
| 실행·조회·취소 | `crew run`, `crew status`, `crew cancel` |
| 검증·판정 | `crew result`, `crew check`, `crew accept`, `crew reject` |
| 인수인계·정리 | `crew handoff`, `crew clean` |

전체 문법과 옵션은 [명령어 안내](crew/README.ko.md#전체-명령어)에 있습니다.

## 실행 결과 확인

메인이 리뷰를 `T-01` 작업으로 실행했다면, 같은 프로젝트에서 다음 명령으로 확인합니다.

```powershell
crew status
crew result T-01
crew check T-01 --recheck
```

| 확인할 내용 | 확인 방법 |
| --- | --- |
| 무엇을 맡겼나? | `.agent-runs/contracts/T-01.md`에 범위와 완료 조건이 있고, 쓰기 작업이면 허용 경로도 있습니다. |
| 워커가 뭐라고 보고했나? | `crew result T-01`로 저장된 결과를 봅니다. 응답 전문은 `--raw`로 봅니다. |
| 범위를 지켰나? | `crew check T-01 --recheck`가 저장된 근거로 경계 검사와 서브에이전트 검사를 다시 계산합니다. |

예를 들어 워커가 성공을 보고해도 마지막 파일 경계 검사가 실패할 수 있습니다. 그러면 `crew`는 `DONE_UNVERIFIED`로 기록하고, 메인이 직접 확인한 내용을 남기기 전에는 수락할 수 없습니다. [회귀 테스트](crew/tests/test_crew.py)가 이 경우를 검사합니다. 읽기 전용 워커가 파일을 수정한 경우와 워커가 시작한 직후에 취소된 경우도 함께 검사합니다.

## 지원 워커

| 워커 | 조사·리뷰 | 파일 수정 | 읽기 범위 |
| --- | --- | --- | --- |
| Codex (`codex`) | 지원 | 계약에 지정한 경로 | 사용자가 읽을 수 있는 모든 파일 |
| Claude Code (`claude`) | 지원 | 계약에 지정한 경로 | 프로젝트와 지정한 디렉터리만 |
| Antigravity (`agy`) | 지원 | 실행 전 차단 | 사용자가 읽을 수 있는 모든 파일 |

어댑터는 codex-cli 0.171.0, agy 1.5.13, Claude Code 2.1.329에서 검증했습니다. CLI를 업그레이드하면 다시 확인하세요. 호출 방식과 알려진 특이사항은 [워커 어댑터](skill/cli-crew/references/adapters.md)에 있습니다.

## 안전 모델과 한계

- **동시 실행.** 서로 독립적인 읽기 작업은 최대 3개까지 동시에 실행합니다. 쓰기 작업은 항상 단독으로 실행합니다.
- **워커에는 셸이 없습니다.** 워커는 셸을 쓸 수 없는 안전 모드로 실행됩니다. 명령, 빌드, 테스트는 메인이 실행합니다.
- **경계 검사는 격리가 아니라 탐지입니다.** `crew`는 프로젝트 안에서 감지할 수 있는 변경만 보고합니다. 운영체제 수준의 샌드박스는 제공하지 않고, 프로젝트 밖의 쓰기는 감지하지 못합니다. agy는 쓰기 경계를 강제할 수 없어서 쓰기 작업을 거부합니다.
- **데이터가 외부로 전송됩니다.** 워커는 각자의 계정을 쓰므로, 위임한 내용은 OpenAI, Anthropic, Google로 전송됩니다. 프로젝트 지침에 민감한 데이터가 언급돼 있으면 `crew run`은 명시적인 승인을 요구합니다. 지침이 외부 에이전트 사용을 제한하면 메인이 위임하기 전에 묻습니다.
- **비용 승인.** 높은 추론 수준이나 `~/.crew/config.toml`에 등록한 모델은 실행 전에 승인이 필요합니다.
- **자동 되돌리기 없음.** `crew`는 위반을 보고하지만 변경을 되돌리지는 않습니다.

전체 규칙은 [검증·승인 규칙](skill/cli-crew/references/protocol.md)에 있습니다.

## 다른 오케스트레이터와의 차이

Claude Code에서 Codex, Gemini, Antigravity를 부르는 도구는 이미 여럿 있습니다. 2차 의견만 받는 단순한 스킬부터 [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) 같은 본격적인 멀티 에이전트 오케스트레이터까지 있습니다. 이런 도구들은 자동화를 우선하고, cli-crew는 통제를 우선합니다.

| | 자동화 우선 오케스트레이터 (예: oh-my-claudecode) | cli-crew |
| --- | --- | --- |
| 목표 | 목표가 통과될 때까지 여러 에이전트를 팀으로 실행 | 이득이 있을 때만 위임하고, 작은 일은 메인이 직접 처리 |
| 지휘자(main) | Claude Code | Claude Code, Codex, Antigravity 중 아무거나 |
| 워커 권한 | 사람 없이 돌 수 있도록 자동 승인 플래그로 실행할 수 있음 (해당 SECURITY.md에 Codex `--dangerously-bypass-approvals-and-sandbox`, Gemini `--approval-mode yolo`가 나옴) | 워커는 안전 모드로만 실행하고, 우회 플래그는 거부 |
| 에이전트 간 경계 | 에이전트가 파일 시스템을 공유 (해당 SECURITY.md의 알려진 한계) | 쓰기 작업마다 허용 경로를 정하고, 그 밖에서 보이는 변경을 보고 (무시된 파일과 제외 폴더는 검사하지 않음) |
| 완료 판정 | 테스트·빌드·린트가 통과할 때까지 자동 검증·수정 루프 | 깨끗한 DONE이 아니면 메인이 검증 내용을 기록해야 수락 가능. 스킬은 모든 수락에 검증 기록을 요구 |

최대한의 자동화가 목적이라면 자동화 우선 오케스트레이터가 더 잘 맞습니다. 워커가 승인을 건너뛰지 못하게 하고, 누가 무엇을 바꿨고 어떻게 확인했는지 기록을 남기고 싶다면 cli-crew를 고르세요. 이 비교는 2026-11-21에 읽은 oh-my-claudecode의 README와 SECURITY.md를 기준으로 합니다.

## 다른 CLI에서 이어서 작업하기

세션을 마치기 전에 프로젝트에서 다음 명령을 실행하세요. `codex`와 `csv-export`는 지금 쓰는 에이전트와 작업 이름으로 바꾸세요.

```powershell
crew handoff --agent codex --task csv-export --status "In progress" --lang ko
```

이 명령은 `handoffs/` 아래에 날짜별 Markdown 초안을 만듭니다. 이미 `handoff/` 폴더를 쓰고 있다면 그 폴더에 만듭니다. 초안에는 선택한 실행 기록과 메인이 남긴 검증 내용이 들어갑니다. 남은 작업과 다음 단계를 채운 뒤, 다음 CLI에서 **같은 작업 폴더**를 열고 최신 인수인계 기록을 읽어 현재 Git 상태와 대조하라고 요청하세요. 지금은 인수인계를 수동으로 합니다. 메인 에이전트 자동 전환은 후속 과제입니다([명세 T-06](docs/SPEC.md)).

## 업데이트

복제한 cli-crew 폴더에서 실행하세요.

```powershell
git pull --ff-only && uv tool install --reinstall ./crew && crew --version
```

버전 번호가 출력되면 업데이트가 끝난 것입니다. 바뀐 스킬은 새 AI CLI 세션부터 적용됩니다.

> **팁:** 파일 탐색기에서 복제한 폴더(`crew`와 `scripts`가 들어 있는 폴더)를 열고 주소창에 `pwsh`를 입력한 뒤 Enter를 누르면 그 위치에서 PowerShell이 열립니다.

## 제거

처음 복제한 폴더에서 스킬 등록을 먼저 해제한 뒤 명령을 제거하세요. 패키지 이름은 `cli-crew`이고, 실행 명령 이름은 `crew`입니다.

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall
uv tool uninstall cli-crew
```

한 클라이언트에서만 제거하려면 대상을 지정하세요. 다음은 Codex에서만 제거하는 예입니다.

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall -Agents codex
```

스크립트는 이 복제본을 가리키는 정션과 등록 항목만 제거합니다. agy의 다른 설정과 백업은 그대로 둡니다. 프로젝트 파일(`AGENTS.md`, `.agent-runs/`, 인수인계 기록)과 복제한 폴더도 그대로 남습니다. 전체 제거 후 `uv tool list`에 `cli-crew`가 나오지 않아야 합니다. 그래도 `crew`가 실행되면 `Get-Command crew -All`로 PowerShell이 어떤 실행 파일을 찾는지 확인하세요.

## 프로젝트 상태

cli-crew는 초기 릴리스(0.1.0)입니다. Windows에서 설치, 재설치, 제거를 임시 홈 디렉터리로 테스트했습니다. macOS와 Linux는 아직 검증하지 않았습니다.

## 개발

`crew`는 Python 표준 라이브러리만 씁니다. 테스트는 로컬 가짜 워커를 쓰고 제공업체를 호출하지 않습니다. 다음 명령으로 실행합니다.

```powershell
cd crew
py -3 -m unittest discover -s tests
```

GitHub Actions가 `main` 푸시와 모든 PR마다 Windows의 Python 3.12·3.13에서 같은 테스트를 실행합니다([워크플로](.github/workflows/test.yml)).

| 문서 | 내용 |
| --- | --- |
| [crew/README.ko.md](crew/README.ko.md) | CLI 명령 안내 |
| [skill/cli-crew/SKILL.md](skill/cli-crew/SKILL.md) | 메인이 따르는 스킬 지침 |
| [docs/SPEC.en.md](docs/SPEC.en.md) | 구조 개요 (영어) |
| [docs/SPEC.md](docs/SPEC.md) | 전체 기술 명세 |

이 프로젝트는 Claude Code, Codex, agy의 도움을 받아 개발했습니다. 명세 초안 작성, 구현, 교차 리뷰, 테스트, 문서 작성에 AI를 활용했습니다.

## 라이선스

[MIT 라이선스](LICENSE)로 배포합니다.

cli-crew는 독립 프로젝트이며 Anthropic, OpenAI, Google과 제휴하거나 그들의 보증·후원을 받지 않았습니다. Claude, Claude Code, Codex, Antigravity는 각 소유자의 상표이며, 여기서는 cli-crew가 연동하는 도구를 가리키는 용도로만 사용합니다.
