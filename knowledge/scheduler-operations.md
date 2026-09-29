# Scheduler Operations

## 등록된 예약 작업
<!-- akela: id=registered-tasks -->

| 작업 | 상태 | 실행 시점 | 실행 | 역할 |
|---|---|---|---|---|
| `ALM_QA_Automation_Daily` | 사용 중 | 평일(월~금) 09:00 | 저장소 Root 에서 `automation.py` | SRS 수집·사양서 PDF·이슈 Export·알림을 한 번에 도는 통합 실행 |
| `VXvue_SRS_Spec_Automation` | Disabled (2026-09-28) | 매주 월요일 07:00 | `apps/srs-spec` 에서 `main.py` | 통합 전 주간 실행. 지우지 않고 꺼 두었다 |
| `VXvue_SRS_Spec_Automation_CatchUp` | Disabled (2026-09-28) | PC 시작 5분 후 | `apps/srs-spec` 에서 `main.py --catch-up` | 통합 전 부팅 만회 실행 |

등록: `.\scripts\install_automation_task.ps1 -Install` (먼저 `-PlanJson` 또는 `-Install -WhatIf` 로 계획을 본다). 옛 두 작업은 첫 통합 실행이 성공한 뒤 `-FinalizeTransition` 으로 끄기만 하고 지우지 않는다.

- 꺼 둔 옛 작업을 다시 켜거나 수동 실행하지 않는다. 통합 작업과 같은 수집을 한 번 더 한다(2026-09-28 08:03 과 09:00 에 두 번 수집됐다).
- `apps/srs-spec/scripts/install_task.ps1` 은 통합 전 방식의 등록 스크립트다. 새로 등록할 때 쓰지 않는다.

## 놓친 실행 만회 규칙
<!-- akela: id=catchup-rules -->

- 통합 작업은 `StartWhenAvailable` 로만 만회한다. PC 가 09:00 에 꺼져 있었으면 다음에 켜졌을 때 Windows 가 한 번 실행한다. 시점은 OS 사정에 따라 늦어질 수 있다.
- 부팅 시 만회 작업(`--catch-up`, `logs/last_run.json` 의 주간 실행 기록 확인)은 옛 주간 작업의 장치다. 2026-09-28 부터 꺼져 있다.

**실패한 실행은 자동으로 다시 돌리지 않는다.** `RestartCount` 가 0 이다. 실패를 조용히 되풀이하는 대신 사람이 알아채게 하려는 설계다.

## 스케줄러 작업 공통 설정과 이유
<!-- akela: id=task-settings -->

`ALM_QA_Automation_Daily` 의 값이다. 설치 스크립트(`scripts/install_automation_task.ps1`)가 이 값으로 등록하고, 등록 뒤 다시 읽어 다르면 실패시킨다.

| 설정 | 값 | 이유 |
|---|---|---|
| `StartWhenAvailable` | True | 놓친 예정 실행을 다음 가능 시점에 실행 |
| `MultipleInstances` | IgnoreNew | 수동 실행과 예약 실행이 겹쳐도 한 번만 돈다 |
| `RunOnlyIfNetworkAvailable` | True | Polarion 에 닿지 않는 상태에서 헛돌지 않게 |
| `ExecutionTimeLimit` | 4시간 | 무한 대기 방지 |
| `RestartCount` | 0 | 실패 시 자동 재시도 없음 |
| LogonType | S4U | 로그온 여부와 상관없이 실행, 비밀번호를 저장하지 않음 |
| Priority | 7 (Task Scheduler 기본값) | 아래 참고 |

**우선순위와 PDF 렌더링**: 통합 전 주간 작업은 우선순위 4(보통)로 등록했다. 기본값 7 에서 Chromium 인쇄가 느려져 90초 제한을 넘긴 일이 있었기 때문이다. 통합 작업은 7 로 돌고, 2026-09-29 실행에서 사양서 그룹마다 16~22초에 끝났다. 스케줄러에서만 렌더링 시간 초과가 늘어나면 우선순위를 4 로 올려 비교해 본다.

> **참고** `VP-1277` 이 들어간 그룹은 2026-09-07 부터 우선순위 4·7 과 상관없이 매번 300초 시간 초과로 끊기고, 복구 경로(`render_problem_state.json`)로 사양서가 만들어진다. 우선순위 문제로 보지 않는다.

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
Get-ScheduledTask -TaskName ALM_QA_Automation_Daily | Format-List TaskName, State
Get-ScheduledTaskInfo -TaskName ALM_QA_Automation_Daily | Format-List LastRunTime, LastTaskResult, NextRunTime
Start-ScheduledTask -TaskName ALM_QA_Automation_Daily   # 즉시 1회 실행. 오늘 이미 돌았으면 같은 수집을 한 번 더 한다
python automation.py --check-local                      # 로컬 설정·경로만 확인
```

옛 작업(`VXvue_SRS_Spec_Automation`, `_CatchUp`)은 Disabled 상태가 맞다. 상태를 볼 때만 이름을 쓴다.

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
