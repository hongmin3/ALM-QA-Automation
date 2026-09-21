# 완전 자동화 검증 기록

검증일: 2026-09-21
대상: REQ-AUTO-001~004
범위: 합성 데이터, 로컬 CLI/PowerShell, 공개 설정 스키마. 실제 Polarion 조회와 실제 SMTP 발송은 수행하지 않았다.

## 자동 테스트

| 명령 | 결과 | 확인 범위 |
|---|---:|---|
| `python -m pytest tests apps/srs-spec/tests -q` | `199 passed, 4 skipped` | 통합 오케스트레이터, 상태·잠금, SRS `updated` 기간 연계, outbox, 런처, 예약 스크립트와 기존 SRS/이슈 회귀 |
| `python -m pytest tests/test_automation_orchestrator.py::test_synthetic_end_to_end_sends_one_candidate_once -q` | `1 passed` | 첫 기준선 0건, 다음 실행 후보 1건·합성 메일 1회 `SENT`, 동일 날짜 재실행 추가 수집/메일 0건 |

4개 skip은 저장소에 포함된 실데이터 선택 테스트로, 합성 회귀 실패가 아니다. 민감하지 않은 E2E 요약은 Git 제외 경로 `.migration/full-automation-smoke/summary.json`에 저장했다.

## 구조와 CLI 검증

| 명령 | 종료 코드 | 결과 |
|---|---:|---|
| `node tools/spec-lint.js ... full-automation` | 0 | SPEC 1개, Requirement 35개, 테스트 참조 38개, 구현 경로 27개; WARN/ERROR 없음 |
| `python automation.py --config automation.example.yaml --check-schema` | 0 | 공개 정책과 추적 진입점 확인; private config, 네트워크, SMTP 미사용 |
| `python run.py auto --help` | 0 | 공통 실행기가 Root `automation.py` 도움말로 라우팅 |
| `powershell -NoProfile -File scripts/install_automation_task.ps1 -PlanJson` | 0 | `ALM_QA_Automation_Daily`, 월~금, 09:00, Root `automation.py`, StartWhenAvailable, network required, IgnoreNew, 240분, priority 7 |
| `git diff --check` | 0 | 공백 오류 없음 |

## 검증한 안전 경계

- 두 수집 중 하나가 실패·부분 완료이면 분석 기준선과 후보를 갱신하지 않는다.
- 최초 완전 수집은 기준선만 만들고 기존 전체 항목을 신규 후보로 알리지 않는다.
- 정확한 Polarion ID와 linked work item만 연결하며 유사 제목은 연결하지 않는다.
- 변경 SRS 관련 이슈는 `(직전 SRS updated, 현재 SRS updated]`에 생성 또는 수정된 항목만 남기며, 충족 필드와 적용 기간을 후보·메일 근거에 기록한다.
- outbox는 발송 전 `SENDING`을 확정하고 성공 후 `SENT`, 확인된 실패는 최대 3회 뒤 `FAILED`로 남긴다.
- 중단된 `SENDING`은 자동 재발송하지 않으며 운영자가 수신 여부를 확인한 후 수동 재대기한다.
- 이메일 본문은 상태, 우선순위, 프로젝트/항목 ID, 이유, 연결 ID, 상태 변화, 로컬 summary 경로만 포함한다.
- 실패 manifest로 `-FinalizeTransition`을 실행하면 비정상 종료하고 기존 작업을 건드리지 않는다.
- 성공 manifest의 `-WhatIf`는 두 레거시 작업 이름을 보여 주지만 비활성화하거나 삭제하지 않는다.

## 운영 활성화 상태

- 최종 프로젝트 Root에 Git 제외 `automation.yaml`을 `automation.example.yaml`과 바이트 단위로 같게 생성했다.
- 격리 브랜치에서는 실제 설정 파일이 공유되지 않으므로 `--check-local`과 예약 작업 실제 등록을 실행하지 않았다. 구현 검토·통합 후 최종 Root에서 실행하고 이 절을 갱신한다.
- 레거시 `VXvue_SRS_Spec_Automation`, `VXvue_SRS_Spec_Automation_CatchUp`은 유지한다. 실제 통합 `SUCCESS`이며 `dataComplete=true`인 manifest와 메일 수신 증거 전에는 `-FinalizeTransition`을 실행하지 않는다.

## 남은 운영 검증

1. 보호된 실제 설정을 노출하지 않고 `python automation.py --check-local` 통과.
2. `-Install -WhatIf` 확인 후 통합 예약 작업 등록과 action/trigger/settings 재조회 일치.
3. 첫 실제 통합 실행의 SRS·이슈 건수, PDF, `dataComplete`, 후보 근거 확인.
4. 받은 편지함에서 실제 Message-ID 한 건 수신과 중복 없음 확인.
5. 성공 manifest를 사용한 레거시 작업 비활성화와 복구 가능성 확인.
6. 장시간 실행, 재부팅 후 StartWhenAvailable, 네트워크 복구, 강제 종료 후 `SENDING` 운영 절차 확인.

실제 메일 도착과 실제 서버 전체 수집은 이 기록에서 완료로 표시하지 않는다.
