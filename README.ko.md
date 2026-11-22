# cli-crew

[![tests](https://github.com/HBy-cloud/cli-crew/actions/workflows/test.yml/badge.svg)](https://github.com/HBy-cloud/cli-crew/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4.svg)
![Version 0.1.0](https://img.shields.io/badge/version-0.1.0-informational.svg)

[English](README.md) · **한국어**

**AI 코딩 CLI 사이에서 범위가 정해진 작업을 맡기고, 결과를 검증하고, 근거를 프로젝트에 남깁니다.**

cli-crew를 쓰면 지금 사용 중인 AI CLI(*메인*)가 Codex, Claude Code, Antigravity에 범위가 정해진 작업을 맡길 수 있습니다. 작은 단일 파일 수정에는 GitHub Copilot CLI를 워커로 지정할 수 있습니다. 스킬은 메인에게 언제 위임할 가치가 있는지와 결과를 어떻게 검증할지 알려 줍니다. 외부 의존성이 없는 Python CLI `crew`는 워커를 안전 모드로 실행하고, 시간 제한과 승인 조건을 지키게 하고, 파일 경계를 검사하고, 모든 실행을 기록합니다. 최종 판단은 언제나 메인이 합니다.

![메인이 계약을 정하고 워커가 실행한 뒤 메인이 결과를 검증하는 흐름](docs/assets/workflow.ko.svg)

[주요 기능](#주요-기능) · [설치](#설치) · [빠른 시작](#빠른-시작) · [명령어](#명령어) · [실행 결과 확인](#실행-결과-확인) · [지원 워커](#지원-워커) · [안전 모델](#안전-모델과-한계) · [다른 도구와 비교](#다른-오케스트레이터와의-차이) · [업데이트](#업데이트) · [제거](#제거) · [CLI 명령 안내](crew/README.ko.md)

## 주요 기능

- **목표 중심 위임.** 사용자는 원하는 결과만 말하면 됩니다. 메인이 직접 할 일, 자기 서브에이전트에 맡길 일, 워커에 맡길 일, 그리고 다른 모델의 독립 리뷰가 추가 호출만큼의 가치가 있는지를 판단합니다.
- **여러 단계 목표의 계획.** 메인은 시작하기 전에 단계마다 실행 주체(메인 자신, 메인의 내장 서브에이전트, crew 워커), 의존 단계, 검증 방법을 적은 계획(*cplan*)을 씁니다. 근거가 바뀌어 계획을 고치면 변경 내용과 이유를 기록합니다. 실행 기록이 단계에 연결되므로 인수인계 뒤에도 진행 상황이 남습니다.
- **독립 검증.** 산출물마다 저자와 다른 모델이 검증하는 단계를 둡니다. 검증자는 완료 조건별 근거와 함께 PASS, FAIL, PARTIAL 판정을 내고, `crew`가 이를 기록합니다.
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
- 설치하고 로그인한 워커 CLI 하나 이상. [Codex CLI](https://github.com/openai/codex), Claude Code, Antigravity(`agy`) 또는 단일 파일 수정용 GitHub Copilot CLI 전용 프로필을 사용합니다.

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

### 스킬 사용법

**cli-crew를 쓰고 싶을 때는 이름으로 직접 호출하세요.** 스킬은 요청이 스킬 설명과 맞을 때만 저절로 로드됩니다. 예를 들어 "다른 모델의 독립 리뷰를 받아 줘" 같은 요청입니다. 테스트에서 "이 버그 고치고 테스트 추가해 줘" 같은 평범한 요청은 한 번도 로드하지 않았고(6번 중 0번), 다른 모델의 리뷰를 요청한 경우는 모두 로드했습니다(4번 중 4번).

| 메인 CLI | 호출 방법 | 검증 상태 |
| --- | --- | --- |
| Claude Code | `/cli-crew <목표>` | 대화형·헤드리스 세션에서 검증 |
| Codex | `$cli-crew <목표>` | 스킬 로드는 검증, Codex를 메인으로 쓴 사례는 적음 |
| Antigravity (`agy`) | 요청에 `cli-crew`를 포함 | 스킬 로드는 검증, agy를 메인으로 쓴 사례는 적음 |

예: `/cli-crew CSV 내보내기 버그를 고치고, 수정한 내용은 다른 모델에게 리뷰받아 줘.`

직접 호출해도 무엇을 맡길지는 메인이 판단합니다. 작은 작업은 대개 직접 처리합니다. 프로젝트에서 `crew detect`를 실행하면 사용할 수 있는 워커가 표시됩니다. cli-crew를 업데이트한 뒤에는 새 CLI 세션을 여세요. 이미 열려 있는 세션은 시작할 때 불러온 스킬 내용을 계속 씁니다.

**프로젝트 지침.** 프로젝트 설정을 요청하거나, 파일 수정이 허용된 요청에서 스킬을 이름으로 직접 호출하면 메인이 `crew init`을 실행합니다. 루트에 `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`가 있으면 그대로 씁니다. 없으면 기본 `AGENTS.md`를 만들고, 메인이 저장소에서 확인한 목적, 작업 규칙, 테스트 명령을 채웁니다. 메인이 다른 작업 중에 스스로 스킬을 불러왔거나 읽기 전용 요청이면 지침 파일을 만들지 않습니다. 대신 최종 보고에서 지침이 없다는 사실을 한 번 알리고 설정을 제안합니다.

### 작은 수정에 GitHub Copilot CLI 쓰기

PowerShell에서 대상 프로젝트 밖에 Copilot 전용 프로필을 지정합니다. 이 프로필에 한 번 로그인하고 훅을 끕니다.

```powershell
$env:COPILOT_HOME = Join-Path $env:LOCALAPPDATA 'crew-copilot-profile'
copilot login
copilot config disableAllHooks true
```

프로젝트 디렉터리에서 계약을 만듭니다. `src/example.py`를 실제 수정할 기존 파일 하나로 바꾸고, 계약의 `effort: low`를 사용합니다. 비밀 파일은 지정할 수 없습니다.

```powershell
crew contract new T-EDIT --agent copilot --kind implement --mode write --write src/example.py
```

생성된 계약의 **Objective**, **Context**, **Done when**을 채운 뒤 같은 터미널에서 검사하고 실행합니다.

```powershell
crew contract check .agent-runs/contracts/T-EDIT.md
crew run --contract .agent-runs/contracts/T-EDIT.md --with-result
```

수정 결과를 수락하기 전에 diff를 확인하고 관련 테스트를 실행합니다. [Copilot 프로필 제한과 파일 권한](skill/cli-crew/references/adapters.md#github-copilot-cli-setup)

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
| Antigravity (`agy`) | 지원 | 계약에 지정한 경로(프로젝트 안으로 제한, 새 파일은 경계 검사가 보고) | 사용자가 읽을 수 있는 모든 파일 |
| GitHub Copilot CLI (`copilot`) | — | 기존 비밀 아닌 파일 하나 | 프로젝트 안의 파일 |

어댑터는 codex-cli 0.171.0, agy 1.5.14, Claude Code 2.1.330, GitHub Copilot CLI 1.0.112에서 검증했습니다. CLI를 업그레이드하면 다시 확인하세요. [워커 어댑터 및 Copilot 설정](skill/cli-crew/references/adapters.md#github-copilot-cli-setup)

## 안전 모델과 한계

- **동시 실행.** 서로 독립적인 읽기 작업은 최대 3개까지 동시에 실행합니다. 쓰기 작업은 항상 단독으로 실행합니다.
- **워커에는 셸이 없습니다.** 워커는 셸을 쓸 수 없는 안전 모드로 실행됩니다. 명령, 빌드, 테스트는 메인이 실행합니다.
- **경계 검사는 격리가 아니라 탐지입니다.** `crew`는 프로젝트 안에서 감지할 수 있는 변경만 보고합니다. 운영체제 수준의 샌드박스는 제공하지 않고, 프로젝트 밖의 쓰기는 감지하지 못합니다. agy 쓰기 작업은 세션별 설정 파일로 실행해 프로젝트 밖 쓰기와 허용되지 않은 파일 덮어쓰기를 막습니다. 프로젝트 안의 새 파일 생성은 막지 못하며 경계 검사가 보고합니다.
- **웹 사용.** 워커는 공개 웹을 검색하고 읽을 수 있지만, 프로젝트 내용·비밀·개인정보를 보내는 것은 지침으로 금지합니다. `crew`가 네트워크를 차단하지는 않습니다. 각 CLI가 실제로 어디까지 가져올 수 있는지는 다시 검증하지 않았습니다.
- **데이터가 외부로 전송됩니다.** 워커는 각자의 계정을 쓰므로 위임한 내용은 OpenAI, Anthropic, Google 또는 GitHub Copilot으로 전송됩니다. 프로젝트 지침에 민감한 데이터가 언급돼 있으면 `crew run`은 명시적인 승인을 요구합니다. 지침이 외부 에이전트 사용을 제한하면 메인이 위임하기 전에 묻습니다.
- **비용 승인.** 높은 추론 수준이나 `~/.crew/config.toml`에 등록한 모델은 실행 전에 승인이 필요합니다.
- **자동 되돌리기 없음.** `crew`는 위반을 보고하지만 변경을 되돌리지는 않습니다.

전체 규칙은 [검증·승인 규칙](skill/cli-crew/references/protocol.md)에 있습니다.

## 다른 오케스트레이터와의 차이

내장 서브에이전트·네이티브 스웜(Claude Code 서브에이전트, Antigravity `/swarm` 등)이나 [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) 같은 자율형 멀티 에이전트 오케스트레이터는 **구현 속도와 처리량**을 높이는 데 맞춰져 있습니다. 반면 cli-crew는 **통제, 이종 모델 교차 검증, 검증 가능한 기록**에 초점을 맞춘 **교차 모델 감사·인수인계 하네스(Harness)**입니다.

| | 내장 서브에이전트 / 네이티브 스웜 | 자동화 우선 오케스트레이터 (예: oh-my-claudecode) | cli-crew |
| --- | --- | --- | --- |
| 핵심 목적 | 한 CLI 안에서 빠른 병렬 탐색과 구현 | 테스트·빌드가 통과할 때까지 에이전트 팀을 자율 실행 | 타사 모델 교차 감사, 범위가 제한된 위임, CLI 간 작업 인수인계 |
| 제공업체·모델 | 현재 CLI의 모델 생태계 내부 | Claude Code가 외부 CLI를 호출 | Claude Code, Codex, Antigravity를 메인·워커로 사용. GitHub Copilot CLI는 작은 수정용 워커 |
| 워커 권한 | 세션 도구를 공유하거나 격리된 브랜치에서 실행 | 무인 실행을 위해 자동 승인 플래그를 쓸 수 있음 (해당 SECURITY.md에 Codex `--dangerously-bypass-approvals-and-sandbox`, Gemini `--approval-mode yolo`가 나옴) | 안전 모드로만 실행. 우회 플래그는 코드에서 거부하며 워커에는 셸이 없음 |
| 수정·테스트 루프 | 워커가 직접 파일을 고치고 테스트까지 실행 가능 | 테스트·빌드·린트가 통과할 때까지 자동 검증·수정 루프 | 워커는 셸·테스트를 실행할 수 없고 쓰기는 직렬(한 번에 1개)로만 실행. 테스트·빌드는 메인이 직접 실행 |
| 소통 방식 | 다회성 대화 또는 공유 메시지 보드 | 자동화된 다단계 루프 | 계약서 기반 단발성 실행 (`codex exec`, `claude -p`, `agy --print`, `copilot -p`) |
| 감사·경계 확인 | 세션 대화 로그 중심 | 에이전트가 파일 시스템을 공유 (해당 SECURITY.md의 알려진 한계) | 작업별 `write_paths`, 실행 전후 스냅샷 비교, `## REVIEW` 판정, `.agent-runs/` 아래 수락 근거 기록 |

**솔직한 장단점 (언제 무엇을 쓸 것인가):**
- **일상적인 코딩과 구현 속도:** 내장 서브에이전트, `/swarm`, 또는 메인 단독 실행이 더 빠릅니다. cli-crew 워커는 셸이 없는 안전 모드에서 단발성으로 돌고, 스스로 테스트를 실행하지 못하며, 쓰기 작업이 직렬로 제한되고 계획·계약·검증 절차를 거칩니다. 따라서 메인 혼자 풀 수 있는 평범한 과제에 쓰면 점수 향상 없이 시간과 토큰만 더 듭니다(10개 과제 벤치마크에서도 최신 메인 모델 단독으로 이미 만점에 도달함).
- **이종(Cross-Provider) 모델 코드 리뷰와 결함 검출:** cli-crew가 가장 큰 값을 하는 영역입니다. 저자와 다른 회사의 모델(`codex`, `claude`, `agy`)이 저자의 주장을 믿지 않고 완료 조건별로 직접 근거를 대조(`## REVIEW`)하므로, 단일 모델 자기 리뷰가 놓치는 맹점을 잡아냅니다.
- **엄격한 권한 통제와 멀티 CLI 인수인계:** `--yolo` 같은 우회 플래그를 허용할 수 없거나, 자격 증명 환경변수 제거와 파일 경계 스냅샷 검사가 필요할 때, 또는 사용량 한도 때문에 Claude Code ↔ Codex ↔ Antigravity 사이에서 진행 중인 계획(`cplan` + `handoffs/`)을 그대로 넘겨받아야 할 때 적합합니다.

oh-my-claudecode 비교는 2026-11-21에 읽은 README와 SECURITY.md를 기준으로 합니다.

## 다른 CLI에서 이어서 작업하기

세션을 마치기 전에 프로젝트에서 다음 명령을 실행하세요. `codex`와 `csv-export`는 지금 쓰는 에이전트와 작업 이름으로 바꾸세요.

```powershell
crew handoff --agent codex --task csv-export --status "In progress" --lang ko
```

이 명령은 `handoffs/` 아래에 날짜별 Markdown 초안을 만듭니다. 이미 `handoff/` 폴더를 쓰고 있다면 그 폴더에 만듭니다. 초안에는 선택한 실행 기록과 메인이 남긴 검증 내용이 들어갑니다. 남은 작업과 다음 단계를 채운 뒤, 다음 CLI에서 **같은 작업 폴더**를 열고 최신 인수인계 기록을 읽어 현재 Git 상태와 대조하라고 요청하세요. 지금은 인수인계를 수동으로 합니다. 메인 에이전트 자동 전환은 후속 과제입니다.

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

### 로컬 파일 안전하게 삭제하기

저장소 루트에서 [`scripts/trash.py`](scripts/trash.py)로 이동 계획을 미리 봅니다. 표시된 대상과 이동 위치를 확인한 뒤 `--yes`를 붙여 실행합니다.

```powershell
py -3 scripts/trash.py path\to\file
py -3 scripts/trash.py path\to\file --yes
```

Windows에서 해당 항목을 휴지통으로 보낼 수 있다고 확인되면 휴지통을 사용합니다. 그 외에는 같은 볼륨에 날짜별 백업 폴더를 만들어 옮깁니다. 기본 백업 위치는 `<볼륨>\cli-crew-trash` 아래입니다. 이동 전 검사에서 경로 하나라도 거부되면 아무 항목도 옮기지 않습니다. 예외는 [저장소의 삭제 규칙](AGENTS.md)을 참고하세요.

### 테스트 실행

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

이 프로젝트는 Claude Code, Codex, agy의 도움을 받아 개발했습니다. 명세 초안 작성, 구현, 교차 리뷰, 테스트, 문서 작성에 AI를 활용했습니다.

## 라이선스

[MIT 라이선스](LICENSE)로 배포합니다.

cli-crew는 독립 프로젝트이며 Anthropic, OpenAI, Google, GitHub와 제휴하거나 그들의 보증·후원을 받지 않았습니다. Claude, Claude Code, Codex, Antigravity, GitHub Copilot은 각 소유자의 상표이며, 여기서는 연동 도구를 가리킵니다.
