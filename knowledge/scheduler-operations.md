# Scheduler Operations

## 등록되는 작업 두 개
<!-- akela: id=registered-tasks -->

| 작업 | 트리거 | 인수 | 역할 |
|---|---|---|---|
| `VXvue_SRS_Spec_Automation` | 매주 월요일 07:00 | `main.py` | 주간 정기 실행 |
| `VXvue_SRS_Spec_Automation_CatchUp` | PC 시작 5분 후 | `main.py --catch-up` | 놓친 주 만회 |

등록: `.\scripts\install_task.ps1` (python.exe는 PATH 자동 탐색, `-PythonExe`로 지정 가능, `-At "06:30"`으로 실행 시각 변경 가능. 동일 이름 작업이 있으면 제거 후 재등록). 해제: `.\scripts\uninstall_task.ps1` (두 작업 모두 제거).

## 놓친 실행 만회 규칙
<!-- akela: id=catchup-rules -->

두 겹의 보정 장치가 있다.

1. **`StartWhenAvailable`** — Windows가 놓친 예정 실행을 다음 가능 시점에 자동 실행한다. 다만 실행 시점이 OS 사정에 따라 늦어질 수 있다.
2. **부팅 시 `--catch-up` 작업** — PC 시작 5분 후 실행. `logs/last_run.json`에 이번 주(월요일 기준 ISO 주) 실행 기록이 있으면 즉시 종료하고 아무 것도 하지 않는다. 기록이 없으면 그 시점에 놓친 실행을 수행한다. 이 덕분에 매번 부팅해도 중복 실행되지 않는다.

**실패한 주는 자동으로 다시 돌리지 않는다.** 실패도 실행 기록으로 남기므로 부팅 시 재시도되지 않고, 실패 사실은 메일로만 알린다. `RestartCount`도 0으로 설정되어 있다 — 실패를 조용히 반복하는 대신 사람에게 알리는 쪽을 택한 설계.

## 스케줄러 작업 공통 설정과 이유
<!-- akela: id=task-settings -->

| 설정 | 값 | 이유 |
|---|---|---|
| `StartWhenAvailable` | True | 놓친 예정 실행을 다음 가능 시점에 실행 |
| `MultipleInstances` | IgnoreNew | 정기 실행과 부팅 만회 실행이 겹쳐도 중복 실행 방지 |
| `RunOnlyIfNetworkAvailable` | True | Polarion 접근 불가 상태에서 헛돌지 않게 |
| `ExecutionTimeLimit` | 2시간 | 무한 대기 방지 |
| `RestartCount` | 0 | 실패 시 자동 재시도 없음 — 메일로만 알림 |
| LogonType | S4U | 로그온 여부와 무관하게 실행, 비밀번호 미저장 |
| Priority | 4(보통) | Chromium 인쇄 성능에 직접 영향 (아래 참고) |

**Priority 4가 중요한 이유**: Task Scheduler 기본 우선순위(7, 낮음)에서는 Chromium 인쇄가 크게 느려져, 대화형 실행에서 17~36초에 끝나는 그룹이 90초 제한을 넘겨 실패하는 현상이 실측되었다. `-Priority 4`(보통)로 등록해야 대화형 실행과 비슷한 속도가 나온다. **스케줄러에서만 렌더링 타임아웃이 발생한다면 이 설정부터 확인.**

**`.ps1` 파일은 UTF-8 BOM으로 저장해야 한다.** Windows PowerShell 5.1이 BOM 없는 `.ps1`을 시스템 ANSI 코드페이지로 읽어서, 한글 주석/문자열이 깨지고 파싱 오류까지 발생할 수 있다.

## 인증과 S4U
<!-- akela: id=auth-s4u -->

Polarion 토큰은 환경변수 `POLARION_TOKEN`으로만 전달한다. S4U 로그온 방식에서도 사용자 환경변수를 읽을 수 있으며, 설정 로드 단계에서 토큰이 없으면 즉시 `ConfigError`로 종료되므로 **"스케줄러 실행이 설정 로드를 통과했다"는 사실 자체가 토큰이 정상 인식됐다는 증거**가 된다.

## 산출물 세대 보관 및 자동 삭제 규칙
<!-- akela: id=generation-retention -->

- 신규 반영 전, 직전 세대를 지식파일 폴더와 같은 위치의 `ORG/<YYMMDD>/`로 옮겨 보관한다.
- 다음 실행이 검증을 통과하면 그 ORG 보관본은 **자동 삭제**된다. 즉 ORG에는 항상 "직전 세대 하나만" 남는다.
- 오래 보관하고 싶은 세대가 있으면 다음 실행 전에 다른 위치로 직접 복사해 두어야 한다(자동 보존 대상 아님).
- 지식파일 폴더 반영 자체도 **검증을 모두 통과했을 때만** 수행되며, 하나라도 실패하면 기존 사양서를 교체하지 않고 종료 코드 1로 끝난다(`LastTaskResult = 1`).
- `LastTaskResult = 0`이면 성공, 지식파일 폴더가 갱신되었다는 뜻.

## 확인/운영 명령
<!-- akela: id=ops-commands -->

```powershell
Get-ScheduledTask -TaskName VXvue_SRS_Spec_Automation, VXvue_SRS_Spec_Automation_CatchUp | Format-List TaskName, State
Get-ScheduledTaskInfo -TaskName VXvue_SRS_Spec_Automation | Format-List LastRunTime, LastTaskResult, NextRunTime
Start-ScheduledTask -TaskName VXvue_SRS_Spec_Automation   # 즉시 1회 실행
```

## 자주 쓰는 실행 모드
<!-- akela: id=common-run-modes -->

```bash
python main.py                 # 전체 파이프라인
python main.py --dry-run       # 지식파일 폴더 반영 단계 생략(점검용)
python main.py --force         # 오늘자 Snapshot이 있어도 다시 수집
python main.py --export-only   # 저장된 스냅샷으로 HTML/PDF만 재생성 (Polarion 재조회 없음)
python main.py --diff-only     # 리포트만 재생성
python main.py --catch-up      # 이번 주 실행 기록 있으면 즉시 종료 (부팅 트리거 전용)
python main.py --since 2026-07-25   # 임의 기준일 이후 변경만 리포트 (PDF/배포/메일 없음, 순수 조회)
```
