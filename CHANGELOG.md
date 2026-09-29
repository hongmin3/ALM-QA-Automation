# Changelog

현재 사양은 `SPEC.md`, 현재 진행 상태는 `progress.md`를 기준으로 한다.

## [Unreleased]

### Fixed

- REQ-AUTO-002: 본문 그림 주소에 스냅샷 날짜 폴더가 들어 있어, 그림이 있는 사양이 내용이 같아도 날마다 "변경"으로 잡히던 결함을 고쳤다. 2026-09-29 메일의 검토 후보 330건 가운데 실제 변경은 10건이었다. 이제 비교할 때 그림 주소 앞부분을 뺀다.

### Changed

- REQ-AUTO-003: 검토 후보 메일을 한국어 요약으로 바꿨다. 제목에 우선 확인 건수를, 본문에 중요도별 사양 번호·제목·바뀐 곳·연결 이슈·Polarion 링크를 적는다. 원자료 JSON은 첨부 파일 `alm_qa_candidates.json`으로만 붙인다. 실행 실패 메일도 한국어로 바꿨다.

### Removed

- 통합 후 참조가 없는 완료 계획서 `docs/superpowers/plans/2026-09-21-integration.md` 제거. 실제 이행 근거는 `docs/INTEGRATION_VALIDATION.md`로 유지.
- 루트와 SHA-256이 같은 `apps/srs-spec/scripts/find-project-root.ps1` 중복 사본 제거. 공통 `scripts/find-project-root.ps1` 유지.
- 미참조 표지 이미지 `apps/issue-export/docs/screenshots/01_cover.png`, 비어 있지 않은 계획 폴더의 `docs/plans/.gitkeep` 제거.

### Added

- REQ-AUTO-001~004: SRS→이슈→분석→outbox→메일 통합 실행, 평일 09:00 예약 계획과 안전한 전환.
- 정확한 Polarion ID 연계, CRITICAL/HIGH/MEDIUM 후보, 최초 기준선과 검토 상태 보존.
- 기존 SRS SMTP 전송부를 재사용하는 PENDING/SENDING/SENT/FAILED 대기함, 내용 중복 방지와 수동 재대기.
- `automation.py --check-schema/--check-local/--no-send/--retry-email`, 공통 메뉴의 완전 자동화 모드.
- REQ-OBS-001: 오프라인 관찰 분석, 변경 전후 근거·후보·처리 상태와 실행 이력 저장.
- REQ-OPS-002: 이슈 실행 잠금과 staging/previous/failed 출력, 결과 상태와 건수·PDF·이미지 실패 기록.
- 합성 실패 주입·실제 renderer·동시 실행·관찰 후보 검증 테스트.

### Fixed

- REQ-SRS-004: 수집/배정 UID 대조와 검증 옵션 연결, 배포 I/O 오류 롤백·복구 사본, 성공 후 ORG 정리.
- 이슈 출력 파일명 충돌·경로 이탈·Git 메타데이터 보호, 본문 이미지 누락의 부분 완료 처리.
- REQ-CORE-002: 실행 메뉴 가독성과 결과 표시, 서버/로컬 점검 구분.

### Changed

- 예약 작업 운영 지식(`knowledge/scheduler-operations.md`) 4개 절을 통합 작업 `ALM_QA_Automation_Daily` 기준으로 다시 썼다: 등록 작업 표, 놓친 실행 만회, 작업 설정 값(우선순위 7·4시간), 확인 명령. 옛 주간 작업 두 개는 Disabled 로 적었다. 이유: 옛 작업 기준 문장을 따라 수동 실행하면 같은 수집이 두 번 돈다(2026-09-28).

- REQ-SRS-004 / REQ-OPS-002 / REQ-OBS-001 / REQ-AUTO-001 / REQ-AUTO-002 / REQ-AUTO-003 / REQ-AUTO-004: 추적성 표 Status 칸에는 값(`verified`·`implemented`)만 남기고, 함께 적혀 있던 검증 범위 설명은 각 요구사항 절의 `참고` 상자로 옮겼다(공통 키트 workflow v6). 사양 내용은 바꾸지 않았다.
- 다른 저장소(옛 vxvue-srs-spec-automation 사본)에서 잘못 push된 병합이 들여온 `tests/test_run_lock.py`와 옛 Akela 기록 줄을 되돌렸다.
- README에 제품 사용 흐름과 AI 활용 개발 역량을 구분해 소개하고 SPEC/진행 상태/개선안을 동기화.
- 관찰 상태 기본 위치를 `.automation/`으로 통합하고 기존 `.observation` 상태는 원본 보존 마이그레이션.
- SRS 통합 실행은 `--no-mail`로 개별 메일을 생략하고 통합 요약 대기함만 사용.
- 변경 SRS의 관련 이슈는 직전·현재 SRS `updated` 구간 안에서 생성 또는 수정된 항목만 우선순위와 알림 근거에 반영.
- 최종 Root 로컬 점검 후 평일 09:00 통합 예약 작업을 등록하고 실제 Windows 설정을 재조회 검증.

## 2026-09-21 — 최초 기준 사양 (bce46c6)

### Added

- REQ-CORE-001, REQ-CORE-002: 통합 실행기의 입력·메뉴·작업 디렉터리·종료 코드 계약을 SPEC에 명시.
- REQ-SRS-001 ~ REQ-SRS-005: SRS 수집·PDF·변경 비교·배포·예약 실행 요구사항과 검증 연결.
- REQ-ISSUE-001, REQ-ISSUE-002: 이슈 수집·출력 요구사항과 테스트 미비 범위 명시.
- REQ-OPS-001: 결과 상태와 알림의 기대 동작, 현재 구현 불일치 기록.
- NFR-OPS-001, NFR-SEC-001: 데이터·이력 보존과 비밀값 분리 요구사항.
- 공식 워크스페이스 migration에 따른 AGENTS SPEC workflow, 변경 이력·진행 상태·사양 이력 안내.

### Changed

- README는 사용법과 기준 문서 링크 중심으로 유지하고 작업 경과는 검증 문서로 분리.
- 프로그램 로직·운영 설정·데이터·예약 작업은 이번 문서 보완에서 변경하지 않음.

## 2026-09-21 — 통합 구현 (65f79cb)

### Added

- 단일 프로젝트/저장소에 사양서·이슈 앱 배치와 공통 Python/Windows 메뉴 추가.
- 원본 두 저장소 이력 및 미커밋 개선사항 보존, 사양서 예약 작업의 실행 경로 전환.

### Fixed

- PowerShell에서 따옴표 포함 검색어가 손상되던 공통 실행 경로를 JSON 인자 전달로 보완.
