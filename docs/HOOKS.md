# Codex 훅

훅 구현은 `scripts/hooks.py`, 등록은 `scripts/setup_codex.py`가 담당한다.

| 이벤트 | 동작 |
|---|---|
| `PreToolUse` | stdin JSON의 셸 명령을 dcg에 전달하고 거부·실행 오류를 명시적인 deny 응답으로 변환 |
| `Stop` | `harness.json`의 공통 검증 명령을 실행하고 실패 시 수정 요청 |

## 설치와 신뢰

```powershell
python scripts/setup_codex.py --check
python scripts/setup_codex.py
```

설치기는 프로젝트의 `.codex/hooks.json`과 `.agents/skills/`에만 쓴다.
Codex 전역 설정·권한·훅 신뢰 기록은 바꾸지 않는다. 원본은 `skills/`와 Python 스크립트에 있다.
기존의 다른 훅은 유지하며, 등록된 스킬 내용이 원본과 다르면 덮어쓰지 않고 중단한다.
차이를 검토하고 원본으로 갱신하려면 `--update-skills`를 사용한다.

등록 후 새 Codex 세션에서 프로젝트를 신뢰하고 `/hooks`로 두 훅을 검토·신뢰한다.
등록만으로 훅이 실행되는 것은 아니다. 정의나 스크립트가 바뀌면 다시 검토해야 할 수 있다.
조직 정책이 프로젝트 훅을 제한하면 해당 정책이 우선한다.

`.agents`나 `.codex`가 읽기 전용인 에이전트 샌드박스에서는 로컬 터미널에서 설치한다.
등록 파일은 머신별 절대 경로를 포함하므로 Git에서 제외한다. 다른 PC나 경로로 옮긴 후에는 설치기를 다시 실행한다.

## Windows와 dcg

dcg는 명령을 **검사만** 하며 hook의 대상 명령을 직접 실행하지 않는다.
설치기는 PATH와 Windows 사용자 설치 위치에서 바이너리를 찾고 절대 경로를 등록한다.
사용자 지정 위치라면 다음과 같이 지정한다.

```powershell
python scripts/setup_codex.py --dcg 'C:\tools\dcg\dcg.exe'
```

Windows 훅은 명시적인 Python 경로와 PowerShell 프로세스를 사용한다. 경로 인용을 보존하기 위해 생성된 PowerShell 호출은 UTF-16LE `EncodedCommand` 형태로 등록된다.
경로와 실행 내용은 `setup_codex.py`의 `build_hooks`에서 확인할 수 있다. PowerShell 실행 정책 변경은 필요하지 않다.
가드는 JSON을 stdin으로 전달하므로 명령 문자열이 셸 코드로 재해석되지 않는다.

dcg가 없거나 설정 로딩·실행에 실패하면 해당 도구 호출을 거부한다.
이전 설정에서 사용하던 dcg의 팩·자가복구 설정은 변경하지 않는다. 데이터베이스 등 추가 팩의 활성화는 dcg의 로컬 설정을 따른다.
도구별 훅은 모든 실행 경로를 포괄하는 샌드박스가 아니다. Codex 자체의 권한·샌드박스 정책도 유지한다.

## 종료 검사

성공하면 `{}`를 반환한다. 실패하면 `decision: block`과 오류를 반환하여 한 차례 수정을 요청한다.
이미 Stop 훅 때문에 계속된 턴에서도 실패하면 `continue: false`와 실패 사유를 반환하여 끝없는 수정 반복을 막는다.
구조화된 `blocked` 결과에는 관련 없는 검증을 추가하지 않고 종료를 허용한다.
부모 실행기는 이와 별개로 종료 코드와 AC를 확인하므로 훅이 반복을 멈췄다고 성공으로 기록하지 않는다.

이전의 npm 고정 검사와 환경변수 기반 grep 훅은 이식하지 않았다. 실제 검증 명령은 `harness.json`에서 관리한다.
전환 전 설정과 문서는 `legacy/claude/`에 원문 그대로 보존했다.

공식 규약: [Codex Hooks](https://learn.chatgpt.com/docs/hooks), [Agent Skills](https://learn.chatgpt.com/docs/build-skills).
