# cli-crew

[English](README.md) · **한국어**

**지금 쓰는 AI CLI에서 다른 CLI에 일을 맡기고, 검증 근거를 프로젝트에 남기세요.**

사용자는 목표를 전달합니다. 현재 대화 중인 AI(메인)는 cli-crew 스킬을 읽고, 직접 끝낼 일과 다른 CLI(워커)에 맡길 일을 정합니다. Python 명령 `crew`는 워커를 실행하고 결과를 기록합니다. 결과를 코드와 대조하고 수락할지는 메인이 판단합니다.

[Windows 설치](#windows-설치) · [첫 작업](#첫-작업) · [명령어](#명령어) · [실행 결과 확인](#실행-결과-확인) · [업데이트](#업데이트) · [제거](#제거) · [CLI 명령 안내](crew/README.ko.md)

![메인이 범위를 정하고 워커가 실행한 뒤 메인이 결과를 검증하는 흐름](docs/assets/workflow.ko.svg)

## 첫 작업

설치 후, **실제로 수정할 프로젝트**에서 새 AI CLI 세션을 열고 평소처럼 요청하세요.

> CSV를 내보내면 마지막 행이 빠져. 원인을 재현하고 수정한 뒤 회귀 테스트까지 추가해줘.

작은 결함이면 메인이 직접 재현하고 수정할 수 있습니다. 독립적인 검토가 유용하다면 “파일 끝에 줄바꿈이 없을 때 내보내기 로직이 마지막 행을 처리하는지 확인해 달라”고 읽기 전용 워커에게 맡길 수도 있습니다. 메인은 수정과 회귀 테스트를 담당합니다. 워커에게 전달하는 작업 계약에는 전체 대화 대신 확인할 질문, 관련 경로, 완료 조건을 담습니다.

**처음 사용하는 프로젝트**라면 파일 수정이 허용된 요청에서 메인이 `crew init`을 실행합니다. 루트에 `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` 중 하나가 있으면 그대로 사용합니다. 모두 없으면 기본 `AGENTS.md`를 만들고 메인이 저장소에서 확인한 프로젝트 목적·작업 규칙·테스트 명령을 보완합니다. 읽기 전용 요청에서는 지침 파일을 만들거나 수정하지 않습니다.

## 명령어

Codex 대화에서는 `$cli-crew`와 함께 목표를 적으세요. PowerShell에서는 `crew`로 개별 단계를 실행하거나 결과를 확인합니다. 전체 목록은 `crew --help`로 볼 수 있습니다.

| 목적 | 명령 |
| --- | --- |
| 프로젝트 준비 | `crew init`, `crew detect` |
| 계약 작성·검사 | `crew contract new`, `crew contract check` |
| 실행·상태 조회·취소 | `crew run`, `crew status`, `crew cancel` |
| 응답·검증·판정 | `crew result`, `crew check`, `crew accept`, `crew reject` |
| 인수인계·기록 정리 | `crew handoff`, `crew clean` |

[명령별 인자와 옵션 전체 보기](crew/README.ko.md#전체-명령어)

## 실행 결과 확인

메인이 CSV 리뷰에 `T-01`이라는 작업 ID를 붙였다고 가정하면, 같은 프로젝트에서 다음 명령으로 확인할 수 있습니다.

```powershell
crew status
crew result T-01
crew check T-01 --recheck
```

| 확인할 내용 | 확인 방법 |
| --- | --- |
| 무엇을 맡겼나? | `.agent-runs/contracts/T-01.md`에 작업 범위와 완료 조건이 있습니다. 쓰기 계약에는 수정 허용 경로도 기록합니다. |
| 워커가 내린 결론은? | `crew result T-01`로 저장된 결과를 봅니다. 응답 전문은 `--raw`로 확인합니다. |
| 허용한 범위 안에서 작업했나? | `crew check T-01 --recheck`가 저장된 근거로 프로젝트 내 변경과 서브에이전트 사용 여부를 다시 검사합니다. 메인은 워커의 주장을 파일과 대조하고 관련 테스트를 실행한 뒤 수락 여부를 기록합니다. |

**실제로 대비하는 실패 사례가 있습니다.** 워커가 성공을 보고했어도 마지막 파일 경계 검사가 실패하면 `crew`는 `DONE_UNVERIFIED`로 기록합니다. 이 결과를 수락하려면 메인이 직접 확인한 내용을 남겨야 합니다. 읽기 전용 워커가 파일을 수정한 경우와 워커 프로세스를 시작한 직후 취소된 경우도 회귀 테스트로 검사합니다. [회귀 테스트](crew/tests/test_crew.py)

## 지원 워커와 실행 범위

| 워커 | 조사·리뷰 | 파일 수정 |
| --- | --- | --- |
| Codex (`codex`) | 지원 | 계약에 지정한 경로 |
| Claude Code (`claude`) | 지원 | 계약에 지정한 경로 |
| Antigravity (`agy`) | 지원 | 실행 전 차단 |

서로 독립적인 읽기 작업은 최대 3개까지 동시에 실행합니다. 쓰기 작업은 한 번에 하나만 실행하며, 셸 명령·빌드·테스트는 메인이 담당합니다. 파일 경계 검사는 **프로젝트 안에서 감지할 수 있는 변경**을 보고합니다. 운영체제 수준의 격리를 제공하지 않으므로, 쓰기 범위를 강제할 수 없는 agy에는 수정 작업을 맡기지 않습니다.

위임에는 워커에 설정된 계정을 사용하며 프로젝트 내용이 해당 제공업체에 전달될 수 있습니다. 메인은 위임 전에 프로젝트 지침을 읽고 데이터 공유와 비용에 관한 승인 조건을 확인합니다. [워커별 동작](skill/cli-crew/references/adapters.md) · [검증·승인 규칙](skill/cli-crew/references/protocol.md)

## 다른 CLI에서 작업 이어가기

세션을 마치기 전에 작업 중인 프로젝트에서 실행합니다. `codex`와 `csv-export`는 현재 메인 에이전트와 작업 이름으로 바꾸세요.

```powershell
crew handoff --agent codex --task csv-export --status "In progress" --lang ko
```

이 명령은 `handoffs/` 아래에 날짜별 Markdown 초안을 만듭니다. 기존에 `handoff/` 폴더를 사용 중이라면 그 위치를 따릅니다. 초안에는 선택된 실행 기록과 메인이 기록한 검증 내용이 들어갑니다. 남은 작업과 다음 세션에서 처음 확인할 사항을 적어 마무리하세요.

다음 CLI에서 **같은 작업 폴더**를 열고 “최신 인수인계 기록을 읽고 현재 Git 상태와 대조한 뒤 이어서 작업해줘”라고 요청하세요. 현재 제공하는 방식은 이처럼 기록을 이용한 수동 인수인계입니다. 메인 에이전트 자동 전환과 다른 CLI에서의 재개 검증은 [명세의 후속 항목 T-06](docs/SPEC.md)에 있습니다. 이 저장소는 `handoffs/`와 `.agent-runs/`를 Git에서 제외하므로 새로 복제하면 해당 로컬 기록은 따라오지 않습니다.

## Windows 설치

Git, PowerShell 7(`pwsh`), Python 3.12 이상, [uv](https://docs.astral.sh/uv/)가 필요합니다. 작업을 위임하려면 워커 CLI를 하나 이상 설치하고 로그인하세요. PowerShell에서 다음 명령을 실행합니다.

```powershell
git clone https://github.com/HBy-cloud/cli-crew.git
cd cli-crew
uv tool install ./crew
$env:Path = "$(uv tool dir --bin);$env:Path"
pwsh -File scripts/install-skill.ps1
crew --version
```

기본 설치는 세 클라이언트에 스킬을 등록합니다. 일부만 선택하려면 `pwsh -File scripts/install-skill.ps1 -Agents claude,codex`를 실행하세요. Claude Code와 Codex는 디렉터리 정션을 사용하고, agy는 `~/.gemini/config/skills.json`에 스킬 경로를 등록합니다. 설치 후에도 복제한 폴더를 같은 위치에 두세요. 새 터미널에서 `crew`를 찾지 못하면 `uv tool update-shell`을 실행하고 터미널을 다시 여세요.

작업할 프로젝트에서 새 AI CLI 세션을 시작하세요. 해당 폴더에서 `crew detect`를 실행하면 사용 가능한 워커를 확인할 수 있습니다. 스킬을 명시적으로 사용하려면 요청에 `cli-crew`를 포함하세요.

## 업데이트

**처음 설치할 때 복제한 cli-crew 폴더**에서 PowerShell을 여세요. `crew/`와 `scripts/`가 함께 있는 곳입니다. 스킬은 이 폴더의 파일을 가리키고, 기본 설치 방식에서 `crew` 실행 파일은 uv가 별도로 설치합니다.

1. 최신 소스를 받습니다. Git이 충돌을 보고하거나 fast-forward할 수 없다고 하면, 재설치 전에 그 문제부터 해결하세요.

   ```powershell
   git pull --ff-only
   ```

2. 내려받은 소스로 명령을 다시 설치하고 실행되는지 확인합니다.

   ```powershell
   uv tool install --reinstall ./crew
   crew --version
   ```

3. 실제 작업할 프로젝트에서 **새 AI CLI 세션**을 여세요. 기존 스킬 등록은 이미 업데이트된 파일을 가리킵니다. 등록이 사라진 경우에만 해당 클라이언트를 다시 등록하세요. 예를 들어 Codex만 복구하려면 `pwsh -File scripts/install-skill.ps1 -Agents codex`를 실행합니다. `-Agents`를 생략하면 세 클라이언트 모두 등록됩니다.

## 제거

**스킬과 명령을 모두 제거하려면** 처음 복제한 폴더에서 스킬 등록을 먼저 해제한 뒤 공용 명령을 제거하세요. uv의 패키지 이름은 `cli-crew`, 실행 명령 이름은 `crew`입니다.

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall
uv tool uninstall cli-crew
```

**한 클라이언트에서만 제거하려면** 아래처럼 대상을 지정하세요. 이 예시는 Codex의 스킬 등록만 해제하며 Claude Code, agy, 공용 `crew` 명령은 계속 사용할 수 있습니다.

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall -Agents codex
```

스크립트 출력에서 클라이언트별로 어떤 정션이나 등록 항목이 제거됐는지 확인하고 새 CLI 세션을 여세요. **전체 제거 후** `uv tool list` 목록에서 `cli-crew`가 사라졌으면 패키지 제거가 완료된 것입니다. 그래도 `crew`가 실행된다면 `Get-Command crew -All`로 PowerShell이 찾은 명령 경로를 확인하세요.

**제거 후에도 남는 것:** 복제한 소스 폴더와 작업 프로젝트의 파일(`AGENTS.md`, `.agent-runs/`, 인수인계 기록 포함)은 유지됩니다. 스크립트는 현재 복제본을 가리키는 등록만 해제하고, agy의 다른 설정·설정 파일·백업도 보존합니다. 소스 폴더까지 직접 삭제하려면 스킬 등록부터 해제하세요.

## 개발 및 AI 활용

이 프로젝트는 Claude Code, Codex, agy의 도움을 받아 개발했습니다. 명세 초안 작성, 구현, 교차 리뷰, 테스트, 문서 작성에 AI를 활용했습니다.

Windows 임시 홈에서 설치·재설치·제거를 검증했습니다. `crew/` 폴더에서 `py -3 -m unittest discover -s tests`를 실행하면 로컬 테스트 워커로 Python 테스트를 수행합니다. macOS와 Linux는 아직 검증하지 않았습니다.

[CLI 명령 안내](crew/README.ko.md) · [스킬 지침](skill/cli-crew/SKILL.md) · [기술 명세](docs/SPEC.md)

## 라이선스

이 프로젝트는 [MIT 라이선스](LICENSE)로 배포합니다.
