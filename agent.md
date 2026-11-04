# Codex 프로젝트 지침

이 저장소는 Python 표준 라이브러리와 Codex CLI를 사용하는 단계별 개발 하네스다.
`AGENTS.md`는 자동 발견용 진입 파일이며 실제 지침은 이 파일에 둔다.

## 프로젝트 문맥

- 운영 방법과 실행 계약은 `docs/HARNESS.md`, 훅은 `docs/HOOKS.md`를 참조한다.
- `docs/PRD.md`, `ARCHITECTURE.md`, `ADR.md`, `UI_GUIDE.md`는 대상 제품의 기획 템플릿이다. 중괄호 자리표시자는 확정된 요구사항으로 취급하지 않는다.
- 제품을 시작할 때 실제 목표·스택·검증 명령을 문서와 `harness.json`에 반영한다.
- 하네스 자체는 Python 3.10 이상, Git, `codex exec`를 사용한다. npm은 대상 제품이 사용할 때만 필요하다.

## 개발 규칙

- 기능과 오류 수정에는 실패 상황을 재현하는 테스트를 먼저 작성한다. 기존 테스트를 약화시키지 않는다.
- 텍스트와 JSON은 UTF-8로 읽고 쓴다. JSON 상태 저장은 원자적으로 교체한다.
- 모델의 완료 응답만으로 성공 처리하지 않는다. 실행기가 종료 코드·이벤트·구조화된 결과·검증 명령을 확인한다.
- 자동 실행 중 Git 커밋, 브랜치와 phase 상태는 부모 실행기만 변경한다. 작업 Codex는 제품 코드와 테스트만 수정한다.
- 작업의 `checks`, Step 명세, `harness.json`을 바꿔 검증을 통과시키지 않는다. 계약 변경이 필요하면 사유를 보고한다.
- 커밋 메시지는 conventional commits 형식을 따른다. push는 사용자가 요청한 경우에만 한다.
- 인증이나 권한이 부족하면 구체적인 이유를 남긴다. 샌드박스·훅 신뢰를 우회하는 옵션을 자동으로 추가하지 않는다.

## 명령어

```text
python -B -m unittest discover -s scripts -p test_*.py
python scripts/setup_codex.py --check
python scripts/execute.py 0-smoke --dry-run
python scripts/execute.py 0-smoke
```

실행 명세의 커맨드는 문자열 셸 명령이 아닌 인수 배열로 작성한다. `python`은 실행기의 Python으로 해석된다.
재사용할 명령은 `harness.json`의 `verification_commands`, Step 전용 AC는 `index.json`의 `steps[].checks`에 둔다.
