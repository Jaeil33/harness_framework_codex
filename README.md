# Codex Harness

Codex CLI로 작은 Step을 순차 실행하고, 실행기가 검증 결과에 따라 상태와 Git 커밋을 기록하는 개발 하네스다.
Python 표준 라이브러리만 사용한다. `pytest`나 npm은 하네스 자체 실행에 필요하지 않다.

## 시작

필요한 도구: Python 3.10 이상, Git, `codex exec --json --output-schema`를 지원하는 Codex CLI, dcg.
이 환경에서는 Codex CLI 0.166.0의 명령 형식과 dcg 0.16.0을 확인했다.

```powershell
# 네트워크·모델 호출 없는 테스트와 환경 검사
python -B -m unittest discover -s scripts -p test_*.py
python scripts/setup_codex.py --check

# 로컬 터미널에서 1회: 이 저장소의 Codex 스킬과 훅 등록
python scripts/setup_codex.py
```

설치 후 이 폴더에서 Codex를 다시 열고 `/hooks`에서 두 훅의 내용을 검토·신뢰한다.
프로젝트 자체도 Codex에서 신뢰한 상태여야 프로젝트 훅이 로드된다.
설치기는 전역 설정이나 신뢰 기록을 변경하지 않는다. `.agents`와 `.codex`가 읽기 전용인 에이전트 환경에서는 로컬 터미널에서 설치해야 한다.

`CLAUDE.md`의 역할은 요청한 이름인 **`agent.md`**로 옮겼다.
Codex가 자동으로 찾는 **`AGENTS.md`**는 `agent.md`와 워크플로우 원본을 읽도록 연결한다.
따라서 설치 전에도 Codex에 이 저장소의 하네스 계획·실행·리뷰를 요청할 수 있다.
설치 후에는 `$harness`, `$harness-review` 스킬도 사용할 수 있다.

## 첫 실행

`0-smoke`는 `artifacts/smoke.txt` 한 파일을 만들고 검사하는 예제다. 자동으로 실행되지는 않는다.

```powershell
python scripts/execute.py 0-smoke --dry-run

# 변경 사항을 검토하고 커밋하여 작업 트리를 정리한 뒤 실행
python scripts/execute.py 0-smoke
```

실제 실행은 `feat-0-smoke` 브랜치를 만들고 Codex를 호출한다. Codex 인증이 준비되어 있어야 한다.
PowerShell에서 npm의 `codex.ps1`이 차단되면 로그인과 대화형 실행은 `codex.cmd login`, `codex.cmd`로 시작할 수 있다.
Python 실행기는 npm으로 설치된 Codex의 Node 진입점을 직접 실행하므로 PowerShell 실행 정책 변경이 필요 없다.

```powershell
python scripts/execute.py 0-smoke --resume       # 원인 해결 및 변경 사항 검토·커밋 후 재개
python scripts/execute.py 0-smoke --model MODEL # 선택 사항; 생략 시 Codex 설정 사용
python scripts/execute.py 0-smoke --push         # 사용자가 push까지 원하는 경우에만
```

`--dry-run`은 파일과 명세만 검사하며 모델 호출, 브랜치 변경, 커밋을 하지 않는다.
인증·모델 접근·훅 신뢰 여부는 실제 Codex 환경에서 별도 확인해야 한다.

## 구성

| 경로 | 역할 |
|---|---|
| `AGENTS.md`, `agent.md` | 자동 발견과 프로젝트 지침 |
| `skills/` | 계획·실행·리뷰 스킬 원본 |
| `harness.json` | 공통 검증 명령, 문맥 파일, 타임아웃, 시도 횟수 |
| `phases/` | 실행할 작업과 실행기가 관리하는 상태 |
| `schemas/step-result.schema.json` | Codex 최종 응답 계약 |
| `scripts/execute.py` | 실행, 재시도, 독립 검증, 커밋 |
| `scripts/hooks.py` | dcg 명령 가드와 종료 검사 |
| `scripts/setup_codex.py` | 프로젝트 로컬 스킬·훅 설치 |
| `docs/HARNESS.md` | 명세 형식과 복구 절차 |
| `docs/HOOKS.md` | 훅 설치·검증·신뢰 설정 |
| `legacy/claude/` | 전환 전 사용자 훅 설정과 설명의 보존본; 실행에 사용하지 않음 |

대상 제품의 기획 문서는 `docs/PRD.md`, `ARCHITECTURE.md`, `ADR.md`, `UI_GUIDE.md`에 채운다.
Node 제품이라면 `harness.json`의 `verification_commands`에 `["npm", "run", "lint"]`, `["npm", "run", "build"]`, `["npm", "test"]` 같은 실제 명령을 설정한다.
