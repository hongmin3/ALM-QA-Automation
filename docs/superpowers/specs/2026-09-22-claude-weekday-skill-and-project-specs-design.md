# Claude 평일 자동화 Skill 및 프로젝트별 SPEC 분리 설계

## 목표

기존의 결정론적 SRS·이슈 수집 및 연계 분석 코드는 유지하면서, Claude가 반복 실행 절차와 결과 판정을 일관되게 수행할 수 있는 프로젝트 로컬 Skill을 제공한다. Claude Desktop 예약 작업은 매주 월요일부터 금요일까지 오전 06:00(Asia/Seoul)에 이 Skill을 실행한다.

기존 평일 09:00 Windows 예약 작업은 새 Claude 예약 작업의 실제 수동 실행이 성공한 뒤 비활성화한다. 삭제하지 않으며, 새 경로가 실패하면 즉시 되돌릴 수 있게 보존한다.

현재 하나의 루트 `SPEC.md`에 섞여 있는 통합·SRS·이슈 요구사항은 책임 경계에 따라 나눈다. 루트 사양은 워크스페이스 통합 계약을, 각 앱 사양은 해당 앱의 독립 동작을 정의한다.

## 사용자 승인 사항

- 실행 주기: 월요일부터 금요일까지 매일 오전 06:00(Asia/Seoul).
- 전환 방식: 새 Claude 예약 작업으로 교체.
- 안전 조건: 새 경로의 성공 증거가 생기기 전에는 기존 예약 작업을 비활성화하지 않음.
- 시험 방식: 정규 실행 전에 메일을 발송하지 않는 실제 수집·분석 시험 수행.
- 사양 구조: 모든 기능을 한 문서에 유지하지 않고 프로젝트별 `SPEC.md`로 분리.

## 선택한 접근

### 프로젝트 로컬 Skill이 기존 실행기를 호출하는 방식

`.claude/skills/alm-qa-weekday/SKILL.md`는 새 수집 로직을 구현하지 않는다. 프로젝트 루트를 확인하고 기존 `automation.py` 또는 `python run.py auto ...` 진입점을 실행한 뒤, 종료 코드뿐 아니라 확정된 실행 manifest와 summary를 읽어 결과를 판정한다.

이 접근은 다음 대안보다 안전하다.

1. **선택: 기존 결정론적 코드를 Skill이 조정** — 기존 복구·중복 방지·보안 경계를 재사용하고, Skill은 절차와 결과 설명만 담당한다.
2. **배제: 수집·연계 판단을 프롬프트 안에 재구현** — 동작이 비결정적이고 기존 테스트와 오류 처리를 우회한다.
3. **배제: Windows Task Scheduler가 Claude CLI를 직접 호출** — 실행 안정성은 높지만 사용자가 요청한 Claude Desktop 예약 결과·세션 경험과 다르다.

Claude 예약 작업이 로컬 폴더와 명령을 사용하므로 이 작업은 로컬 실행이다. 해당 시각에 PC와 Claude의 로컬 실행 환경을 사용할 수 없으면 실행을 보장하지 않는다. 이 제한은 Skill 결과와 운영 문서에 명시하며, 성공하지 않은 실행을 자동으로 성공 처리하지 않는다.

## 구성 요소

| 구성 요소 | 책임 |
|---|---|
| `.claude/skills/alm-qa-weekday/SKILL.md` | 사전 점검, 안전한 실행 모드 선택, 실행, 결과 판정, 요약 |
| `SPEC.md` | 공통 진입점, 통합 오케스트레이션, 관찰, 예약 전환 및 전역 비기능 요구사항 |
| `apps/srs-spec/SPEC.md` | SRS 수집, 스냅샷, 렌더링, 비교, 검증·배포, 앱 자체 잠금 계약 |
| `apps/issue-export/SPEC.md` | 이슈 검색·수집, 문서 생성, 실행 manifest, 출력 보존 계약 |
| `AGENTS.md`, `.project-check/agents-spec-section.md` | 루트 통합 사양과 가장 가까운 앱 사양을 선택하는 작업 규칙 |
| `.project-check/project-readiness.js` | 루트와 선언된 하위 사양의 구조·ID·추적 경로를 함께 검사 |
| 관련 테스트 | Skill 명령 계약, 다중 SPEC 검증, 일정·전환 게이트 회귀 검사 |
| Claude Desktop 예약 작업 | 프로젝트 폴더에서 평일 06:00 KST에 Skill 호출, 실행 결과 세션 보존 |

Skill은 실제 `config.yaml`, `.env`, 운영 원문 또는 `IMPLEMENTATION_LOG.md`의 내용을 출력하거나 요약하지 않는다. 설정 확인은 기존 `--check-local` 결과와 필요한 파일의 존재 여부만 사용한다.

## 프로젝트별 SPEC 소유권

기존 Requirement와 Test ID는 의미를 바꾸거나 재사용하지 않고 그대로 이동한다.

### 루트 `SPEC.md`

- `REQ-CORE-001`, `REQ-CORE-002`: 공통 진입점과 메뉴.
- `REQ-OPS-001`: 앱 경계를 넘는 결과·알림 신뢰성.
- `REQ-OBS-001`: 저장 결과의 오프라인 관찰.
- `REQ-AUTO-001`~`REQ-AUTO-004`: 통합 수집, 연계, outbox, 예약 전환.
- `NFR-OPS-001`, `NFR-SEC-001`: 전체 저장소의 보존·보안 계약.
- 위 요구사항의 루트 테스트 정의와 추적성.
- 하위 사양 경로와 소유 Requirement 범위를 명시하는 사양 목록.

`REQ-AUTO-004`는 Claude Desktop 평일 06:00 예약과 안전한 전환으로 변경한다. Windows 09:00 작업은 새 경로 성공 후 비활성화되는 레거시 복구 경로가 된다.

### `apps/srs-spec/SPEC.md`

- `REQ-SRS-001`~`REQ-SRS-005`.
- `TEST-SRS-001`~`TEST-SRS-005`.
- SRS 구현·테스트 추적성과 앱에 한정된 오류·데이터 계약.

`REQ-SRS-005`의 앱 자체 주간 예약 설명은 통합 예약과 혼동하지 않도록 레거시/독립 실행 계약으로 한정한다. 통합 예약 주기와 전환 상태는 루트 `REQ-AUTO-004`만 정의한다.

### `apps/issue-export/SPEC.md`

- `REQ-ISSUE-001`, `REQ-ISSUE-002`, `REQ-OPS-002`.
- `TEST-ISSUE-001`, `TEST-OPS-004`.
- 이슈 구현·테스트 추적성과 앱에 한정된 오류·출력 계약.

`TEST-OPS-001`은 SRS와 이슈의 PDF 변환 경로를 함께 검증하는 교차 앱 Test ID이므로 루트 `SPEC.md`가 소유한다.

한 Requirement와 Test ID는 한 사양에서만 정의한다. 루트 사양은 하위 Requirement를 복제하지 않고 링크와 통합 선행·후행 조건으로만 참조한다. 교차 앱 테스트가 여러 계약을 검증하면 테스트 파일은 양쪽 추적표에서 참조할 수 있지만 Test ID의 정의는 한 문서에만 둔다.

## Skill 실행 계약

Skill은 두 모드를 제공한다.

### 시험 모드

1. 가장 가까운 `akela.json`으로 프로젝트 루트를 확인한다.
2. `python automation.py --check-local`을 실행해 외부 연결 전에 로컬 구성을 검사한다.
3. 새 `python run.py auto --verify-run`을 실행한다.
4. `--verify-run`은 실제 Polarion 읽기와 SRS·이슈 분석을 수행하되 SRS에는 `--dry-run --no-mail`을 전달하고, 검증 전용 상태·산출물을 `.automation/verification/<run-id>/`에 저장한다.
5. 검증 실행은 운영 `state.json`, outbox, 주간 마커, 배포 대상과 기존 운영 산출물을 갱신하지 않는다.
6. 새 검증 manifest와 summary를 찾고, 해당 호출에서 생성된 실행 ID와 일치하는지 확인한다.
7. SRS·이슈·분석 단계, 후보 수, 격리 상태, 종료 코드를 요약한다.
8. 메일 미발송, 실제 서버 조회, 배포 미수행, 운영 기준선 미변경을 각각 구분해 보고한다.

시험 모드는 읽기 전용 서버 조회와 격리된 로컬 검증 산출물 생성을 허용하지만 SMTP 발송, SRS 배포, 운영 기준선·outbox 변경은 하지 않는다. `--no-send`는 이 요건을 충족하지 않으므로 시험 모드로 사용하지 않는다. `--verify-run`은 기존 `--no-send`의 의미를 변경하지 않는 별도 옵션이다.

### 정규 모드

1. 같은 사전 점검을 수행한다.
2. 기존 정규 통합 진입점을 실행하여 현재 이메일 정책을 보존한다.
3. 종료 코드와 manifest 의미가 모두 성공일 때만 성공으로 보고한다.
4. 메일이 `PENDING`, `SENDING` 또는 `FAILED`이면 데이터 성공과 전달 상태를 분리한다.
5. 실패 시 무제한 재실행하거나 기준선을 임의 변경하지 않는다.

Skill은 shell 종료 코드 0만으로 성공을 선언하지 않는다. manifest 누락, 실행 ID 불일치, 필수 단계의 `PARTIAL`/`FAILED`, 확인되지 않은 메일 전달을 그대로 드러낸다.

## 예약 작업과 전환

Claude Desktop 예약 작업의 이름은 `ALM QA 평일 자동화`로 한다. 시간대는 `Asia/Seoul`, 일정은 월요일부터 금요일까지 06:00이다. 작업 폴더는 이 저장소 루트이며, 지시는 `$alm-qa-weekday`의 정규 모드를 실행하고 결과를 요약하도록 제한한다.

전환 순서는 다음과 같다.

1. 전체 로컬 자동 테스트와 프로젝트 readiness 검사를 통과시킨다.
2. Skill 시험 모드로 격리된 `--verify-run` 실제 실행을 수행하고 manifest를 확인한다.
3. Claude Desktop 예약 작업을 생성하되 기존 Windows 작업은 유지한다.
4. 새 예약 작업을 on-demand로 한 번 실행하고 해당 세션·manifest·summary를 대조한다.
5. 위 실행이 `dataComplete=true`, `resumedOutboxOnly=false`이고 필수 데이터 단계까지 성공한 경우에만 기존 `ALM_QA_Automation_Daily` Windows 작업 정의를 백업하고 비활성화한다. 기존 SRS 레거시 작업의 상태는 이 전환에서 변경하지 않는다.
6. 기존 작업은 삭제하지 않는다. Claude 예약 실행이 실패하면 다시 활성화할 수 있는 명령과 백업 위치를 결과에 남긴다.

Claude Desktop UI 또는 계정 정책이 예약 생성·수동 실행을 요구하면 그 상태를 완료 증거로 대체하지 않는다. 실제 등록 화면의 일정, 작업 폴더, 다음 실행 시각과 수동 실행 결과를 확인해야 한다.

## 오류 처리와 보안

- 로컬 사전 점검 실패 시 외부 수집을 시작하지 않는다.
- SRS 또는 이슈가 불완전하면 분석 기준선을 갱신하지 않는 기존 정책을 유지한다.
- 예약 실행 한도, 권한 요청, PC 오프라인 때문에 시작되지 않은 경우를 제품 데이터 실패와 구분한다.
- Skill과 예약 프롬프트에 토큰, 비밀번호, 운영 경로의 실제 값, 수집 원문을 포함하지 않는다.
- Hook 오류 진단은 별도 트랙으로 수행한다. 원인이 확인되기 전에는 이 기능의 설정 변경과 묶지 않는다.
- 기존 미커밋 파일과 운영 데이터는 수정·삭제하지 않는다.

## 검증 전략

### 자동 테스트

- 루트와 두 하위 SPEC의 Requirement ID가 전역에서 중복되지 않는다.
- 각 Requirement는 자신의 사양 추적표에서 구현·테스트 경로를 가진다.
- 루트가 선언한 하위 SPEC이 없거나 구조가 불완전하면 readiness가 실패한다.
- Skill이 `--check-local` 실패 뒤 실제 실행을 시작하지 않는다.
- 시험 모드는 `--verify-run`을 전달하고 SRS `--dry-run --no-mail`, 격리 상태 저장, 운영 기준선·outbox 불변을 보장한다.
- exit 0이지만 manifest가 없거나 필수 단계가 실패한 경우 Skill 결과가 실패다.
- 평일 06:00 KST 일정과 기존 작업 비활성화 성공 게이트를 검증한다.
- 기존 `python -m pytest tests apps/srs-spec/tests -q` 회귀를 통과한다.

### 대표 실행

- 네트워크·메일 없는 합성 fixture로 Skill 명령과 결과 요약을 먼저 확인한다.
- `python automation.py --check-local`의 실제 출력을 읽는다.
- 사용자가 요청한 실제 시험은 `python run.py auto --verify-run`으로 실행하고 격리된 manifest·summary와 운영 상태 불변을 확인한다.
- Claude 예약 작업을 on-demand로 실행해 로컬 Skill 발견, 작업 폴더, 승인 모드, 결과 세션을 확인한다.
- `node .project-check/project-readiness.js .`을 프로젝트 루트에서 실행한다.

테스트 통과는 실제 예약 등록이나 실제 수집 성공을 대신하지 않는다. 실제 메일은 정규 예약 실행 또는 별도로 승인된 메일 시험 전에는 전달 완료로 보고하지 않는다.

## 완료 조건

- 프로젝트 로컬 Skill이 기존 자동화의 시험·정규 모드를 재현 가능하게 실행하고 의미 기반 결과를 보고한다.
- 루트·SRS·이슈 `SPEC.md`가 중복 없이 책임을 나누며 전체 추적 검사를 통과한다.
- 배포·메일·운영 상태 변경 없는 실제 수집·분석 시험의 실행 ID, manifest, summary 결과를 확인한다.
- Claude Desktop 예약 작업이 평일 06:00 KST, 올바른 로컬 폴더와 Skill로 등록된다.
- 예약 작업의 on-demand 실행이 성공한 뒤에만 기존 평일 09:00 Windows 작업이 비활성화된다.
- 기존 작업 정의와 복구 경로가 남는다.
- 테스트, 대표 실행, readiness, diff 검사를 통과하고 관련 문서를 갱신한다.
- 실행하지 못한 운영 검증과 Hook 오류의 별도 진단 결과를 명확히 구분한다.
