# Step 0: smoke-artifact

## 읽어야 할 파일

- `agent.md`
- `phases/0-smoke/index.json`

## 작업

Codex 실행과 검증 연결을 확인하는 작은 예제다.
`artifacts/` 디렉토리를 만들고 `artifacts/smoke.txt`를 UTF-8로 작성한다.
내용은 `Codex harness OK` 한 줄이며 끝에 LF 줄바꿈 하나를 붙인다.

## Acceptance Criteria

`index.json`에 정의된 파일 내용 검사와 `harness.json`의 공통 테스트가 통과해야 한다.
최종 응답은 status, summary, reason 필드의 JSON이다.

## 범위

제품 기능이나 추가 의존성을 만들지 않는다. Step 명세, 인덱스, 하네스 설정을 수정하거나 Git 커밋을 하지 않는다.
