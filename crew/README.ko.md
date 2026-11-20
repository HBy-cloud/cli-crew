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

대상 프로젝트에서 `crew init`을 실행합니다. 루트에 지원되는 지침 파일이 없을 때만 기본 `AGENTS.md`를 만들고, 스킬을 통한 첫 사용 시 메인이 확인한 프로젝트 정보를 보완합니다.

## 위임 절차

작업할 프로젝트에서 실행합니다. 계약 파일에는 목표, 대상 경로, 완료 조건을 작성합니다. 수락 전에는 실행 결과와 근거를 확인하세요.

```
crew detect --paths src/
crew contract new T-01 --agent codex --mode read
# edit .agent-runs/contracts/T-01.md
crew run --contract .agent-runs/contracts/T-01.md --with-result
# inspect status, boundaries and RESULT; verify the evidence before acceptance
crew accept T-01            # or: crew reject T-01 --reason "..."
crew handoff --agent claude --task my-task --status Done
```

agy는 읽기 작업에만 사용할 수 있습니다. 민감한 데이터 공유에는 `--approve-data-sharing`, 높은 추론 수준이나 승인 대상 모델에는 `--approve-cost`가 필요합니다. 승인 옵션은 사용자 확인 후 적용합니다. 워커 환경에서는 다른 제공업체의 인증 변수 등 자격 증명으로 분류된 변수를 제거합니다.

상태는 `crew status`, 기록 재검사는 `crew check <id> --recheck`로 확인합니다. 작업 취소는 `crew cancel <id> [--yes]`, 기록 정리는 `crew clean [--sessions] [--runs] [--older-than N] [--yes]`를 사용합니다.

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
