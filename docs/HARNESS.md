# Codex 하네스 운영

## 실행 구조

1. 기획·코드를 읽고 자기완결적인 Step 명세와 검증 커맨드를 만든다.
2. 실행기는 명세를 검사하고 깨끗한 Git 작업 트리에서 `feat-{phase}` 브랜치를 준비한다.
3. 프로젝트 지침, 완료된 Step 요약, 현재 Step을 stdin으로 `codex exec`에 전달한다.
4. Codex는 제품 코드와 테스트를 변경하고 구조화된 결과를 반환한다.
5. 실행기는 프로세스 종료 코드, JSONL 이벤트, 결과 스키마를 확인한다.
6. 실행기가 Step AC와 공통 검증 명령을 직접 실행한다. 모두 통과하면 상태와 커밋을 기록한다.
7. 실패하면 실제 오류를 다음 시도에 전달한다. 사용자 개입이 필요하면 `blocked`로 중단한다.

각 Step은 별도 Codex 세션이다. 이전 대화의 기억에 의존하지 않는다.
기본값은 최초 실행을 포함해 최대 3회 시도, Codex 호출당 1,800초, 검증 명령당 300초다.

## Phase 파일

`phases/<phase>/index.json`의 `phase`는 디렉토리명과 같아야 한다. 이름은 소문자·숫자·하이픈 slug다.

```json
{
  "project": "My Project",
  "phase": "1-api",
  "steps": [
    {
      "step": 0,
      "name": "api-types",
      "status": "pending",
      "checks": [["npm", "run", "typecheck"], ["npm", "test"]]
    }
  ]
}
```

- `step`은 0부터 시작하는 연속 번호다. 빈 Phase, 중복 번호, 미완료 Step 뒤의 완료 Step은 거부한다.
- `checks`는 최소 한 개의 **인수 배열**이다. `"npm test && npm run build"` 같은 셸 문자열은 허용하지 않는다.
- 각 Step의 `stepN.md`에는 읽을 파일, 작업 범위, 인터페이스·불변조건, AC 설명을 쓴다.
- `python`과 `python3`은 현재 실행기의 Python 경로로 해석한다. Windows의 npm/npx/Codex shim은 Node 진입점으로 해석한다.
- 다른 셸 기능이 필요하면 `bash`, `powershell.exe` 등의 실행 파일과 인수를 명시한다. 이 커맨드들은 검토된 프로젝트 계약이다.
- `phases/index.json`은 전체 Phase 상태를 담는다. 실행기는 선택한 Phase만 갱신하며 없으면 추가한다.

## 응답과 상태 소유권

Codex의 최종 응답:

```json
{"status": "completed", "summary": "src/api.ts와 해당 테스트 구현", "reason": ""}
```

`completed`에는 비어 있지 않은 `summary`가 필요하고, `error` 또는 `blocked`에는 구체적인 `reason`이 필요하다.
이 응답은 완료 요청이다. **검증 명령을 통과한 뒤 실행기가 최종 상태를 결정한다.**

| 상태 | 의미 |
|---|---|
| `pending` | 아직 실행하지 않았거나 재개할 작업 |
| `running` | 현재 시도 중; 강제 종료 후에도 남을 수 있음 |
| `completed` | 결과와 검증을 확인하고 Step 커밋까지 성공 |
| `error` | 시도 한도 초과, 검증 실패, 실행 계약 위반 또는 커밋 실패 |
| `blocked` | 인증·권한·외부 설정 등 사용자 개입 필요 |

실행기가 `created_at`, `started_at`, `completed_at`, `failed_at`, `blocked_at`, `attempts`를 기록한다.
타임스탬프는 UTC의 ISO 8601 형식이다. JSON은 UTF-8로 임시 파일에 쓴 뒤 원자적으로 교체한다.
작업 Codex가 인덱스·검증 설정·Step 명세·결과 스키마를 수정하거나 Git HEAD/브랜치를 바꾸면 실행을 중단한다.

## 검증과 Git

- 각 Step에서 `steps[].checks`와 `harness.json`의 `verification_commands`를 실행한다. 동일한 명령은 한 번만 실행한다.
- `Stop` 훅 유무와 무관하게 부모 실행기는 직접 검증한다.
- 실행 시작 시 기존 수정 사항이나 untracked 파일이 있으면 중단한다. 먼저 사용자가 변경 사항을 검토하고 커밋해야 한다.
- 작업 중 같은 checkout에서 다른 편집·실행을 병행하지 않는다. `.harness-run.lock`은 같은 checkout의 하네스 중복 실행을 막는다.
- 코드 변경은 `feat(...)`, 상태 메타데이터는 `chore(...)`로 구분한다. Codex 작업 세션 자체는 커밋하지 않는다.
- 검증에 실패한 코드는 자동 커밋하지 않는다. 커밋 실패도 성공으로 보고하지 않는다.
- 모든 Step이 완료되어야 Phase를 완료한다. `--push`가 없으면 원격으로 보내지 않는다.
- 하네스는 실행 조율 도구다. 모델의 파일 쓰기를 보안 경계로 완전히 격리하지는 않는다. 검증 결과와 diff를 함께 리뷰한다.

## 로그와 복구

각 시도의 `stepN-attemptM-output.json`에는 종료 코드, JSONL 원문·이벤트, stderr, 결과와 실행 시간이 남는다.
`stepN-attemptM-result.json`은 Codex 최종 응답, `stepN-attemptM-checks.json`은 검증 결과다.
새 시도는 기존 번호 다음을 사용하므로 재시작해도 로그를 덮어쓰지 않는다. 로그는 Git에서 제외된다.

실패나 중단 후:

1. 인덱스의 사유와 해당 시도의 로그를 확인한다.
2. 인증·권한·코드 문제를 해결하고 남아 있는 변경 사항을 검토한다.
3. 검토한 변경 사항을 커밋하여 작업 트리를 정리한다.
4. `python scripts/execute.py <phase> --resume`을 실행한다.

`--resume`은 미완료 상태를 다시 시도하며 이미 완료된 Step은 건너뛴다. 오래된 오류·차단 필드를 정리한다.
타임아웃 또는 Ctrl+C 시 실행기가 생성한 프로세스 트리를 종료한다.
비정상적인 강제 종료로 lock이 남으면 기록된 PID의 프로세스가 더 이상 실행 중이 아닌지 확인한 뒤 해당 lock 파일만 제거한다.

종료 코드는 성공 0, 실패 1, 사용자 개입 필요 2, 사용자 중단 130이다.

## 테스트

```text
python -B -m unittest discover -s scripts -p test_*.py
```

테스트는 표준 라이브러리만 사용한다. 실제 프로세스·UTF-8·JSONL·Git 커밋 흐름은 임시 저장소와 모의 Codex 프로그램으로 검증한다.
실제 계정 인증과 모델 호출 성공을 대신 증명하지는 않는다. `0-smoke`를 실행하면 계정 환경을 포함한 확인을 할 수 있다.

공식 실행 인터페이스: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
