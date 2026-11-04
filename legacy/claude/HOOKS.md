# Hooks

이 프로젝트의 Claude Code 훅은 `.claude/settings.json`에 정의된다.

| 이벤트 | matcher | 하는 일 |
|---|---|---|
| `PreToolUse` | `Bash\|PowerShell\|Monitor` | **dcg** — 파괴적 명령을 실행 전에 차단 |
| `PreToolUse` | `Bash` | 인라인 grep 가드 (`rm -rf`, `git push --force`, `git reset --hard`, `DROP TABLE`) |
| `Stop` | — | `npm run lint && npm run build && npm run test` |

## dcg (destructive_command_guard)

[Dicklesworthstone/destructive_command_guard](https://github.com/Dicklesworthstone/destructive_command_guard) —
Rust로 작성된 안전 훅. 에이전트가 셸 명령을 실행하기 **전에** stdin으로 받은 명령을
평가해서 파괴적이면 거부(exit 비정상 + 사유)한다. 현재 장착 버전: **v0.16.0**.

차단 대상은 단순 문자열 매칭이 아니라 팩(pack) 단위 규칙이다. 설정 파일이 없는
Windows 기본 상태에서 켜지는 팩:

- `core.filesystem` — `rm -rf /`·`~`, `Remove-Item -Recurse`, .NET 재귀 삭제 API
- `core.git` — 28개 규칙: `reset-hard`, `push-force-*`, `checkout-discard`, `clean-force`, `stash-clear`, `filter-branch`, `update-ref-delete` 등
- `system.disk` — `dd`, 파티션/디스크 파괴
- `windows.filesystem` — `del /s`, `rd /s`, `format <drive>:`, `Clear-RecycleBin`
- `windows.system` — `vssadmin delete shadows`, `wbadmin delete`, `diskpart`, `bcdedit /delete`

heredoc과 인라인 스크립트(`python -c`, `bash -c` 등) 안의 명령도 스캔한다.
DB/K8s/Docker/클라우드/Terraform 등 50개 이상의 추가 팩은 opt-in이다.

### 설치 (각 머신에서 1회)

`.claude/settings.json`의 훅 커맨드는 **머신별 절대 경로**다. 이 repo를 클론하면
dcg를 설치한 뒤 `dcg install --project`로 경로를 다시 생성해야 한다.

> dcg는 훅 커맨드가 해석된 절대 경로여야 한다고 명시한다 (`dcg --help`:
> "Hook commands must use the resolved absolute dcg executable path").
> PATH에 의지하는 `"command": "dcg"` 형태로 바꾸지 말 것.

```powershell
# 1) 서명 검증 도구
winget install --id jedisct1.minisign --exact --scope user

# 2) dcg 설치 — 공식 검증 레시피 (install.ps1의 Minisign 서명 + 고정 SHA256 확인 후 실행)
#    https://github.com/Dicklesworthstone/destructive_command_guard/blob/main/docs/windows.md#verified-release-installation

# 3) 이 repo의 .claude/settings.json에 훅 등록
dcg install --project
```

Unix는 [Quick Install (Verified Release)](https://github.com/Dicklesworthstone/destructive_command_guard#quick-install-verified-release) 참고.
릴리스 아카이브를 직접 받는 경우 pinned 공개키(키 ID `69B3955C8D2E62A8`)로
`.minisig`를 검증하고 SHA256까지 맞춰본 뒤에만 압축을 풀 것. 아카이브 옆에 있는
체크섬만으로는 배포자가 인증되지 않는다.

### 동작 확인

```bash
dcg doctor            # 바이너리/훅 배선/설정 점검
dcg test 'git status' # 설정 파일이 정상 로드되는지 (ALLOWED 가 떠야 정상)
```

> 설정 파일(`config.toml`)이 로드에 실패하면 dcg는 **모든 셸 명령을 차단**한다.
> 설정을 손본 뒤에는 반드시 `dcg test 'git status'`를 한 번 돌려볼 것.

### 거부를 넘기는 방법

```bash
dcg allow-once <code>   # 거부 메시지에 찍힌 코드로 그 명령 1회만 허용
DCG_BYPASS=1 <command>  # dcg 전체 우회 (최소한으로만)
```

### 실제로 겪은 false positive (장착 당시)

dcg는 heredoc과 인터프리터 입력까지 정적으로 읽기 때문에, **실행이 아닌 텍스트**도
막힐 수 있다. 장착 과정에서 두 건이 걸렸다.

| 걸린 명령 | 규칙 | 원인 |
|---|---|---|
| `dcg test '<위험 명령>'` | `core.filesystem:rm-rf-root-home` | 테스트하려고 인수로 넘긴 위험 문자열을 실행 의도로 판정 |
| `python -I - <<'PY' … PY` | `core.git:reset-hard` | Python heredoc **안의 설명용 문자열**을 명령으로 판정 |
| `cat > x.py <<'PY' … PY && python -I x.py` | `core.filesystem:stdin-unverified` | heredoc으로 쓴 파일을 인터프리터가 실행하는 패턴 자체를 거부 (stdin 우회 차단) |

대응:

- 위험 문자열을 **명령줄에 두지 말고** stdin으로 데이터로 넘긴다: `dcg test --stdin --explain`
- 문서/스크립트 편집은 셸 heredoc 대신 에디터 도구나 미리 존재하는 파일 수정으로 처리한다
- 그 한 번만 통과시킬 때는 거부 메시지의 `dcg allow-once <code>`

`scripts/execute.py`가 phase 프롬프트를 셸로 넘기는 구조라, phase 본문에 위험 명령
예시를 문자열로 적으면 같은 방식으로 막힐 수 있다. 예시는 설명문으로 쓰거나
별도 파일에 두는 편이 안전하다.

### 범위: 프로젝트 전용

dcg는 기본적으로 user 설정(`~/.claude/settings.json`)에 훅을 심고, 매 호출마다
없으면 다시 심는다(`self_heal_hook`). 이 프로젝트는 훅을 repo 안에만 두기로 해서
user 설정 쪽은 제거하고 자가복구를 껐다 — `%APPDATA%\dcg\config.toml`:

```toml
[general]
self_heal_hook = false
```

이 PC 전체를 보호하려면 자가복구를 되돌리고(`self_heal_hook = true` 또는 해당 줄 주석 처리)
`dcg install`을 실행하면 된다.

### 인라인 grep 가드와의 중복

기존 `PreToolUse`/`Bash` grep 가드가 잡는 4개 패턴은 dcg의 `core.filesystem` /
`core.git` 팩이 모두 포함한다. 둘 다 돌아가도 문제는 없지만(dcg가 먼저 거부),
정리하려면 grep 가드 쪽을 지우면 된다.
