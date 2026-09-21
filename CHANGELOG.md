# Changelog

현재 사양은 `SPEC.md`, 현재 진행 상태는 `progress.md`를 기준으로 한다.

## [Unreleased]

### Planned

- REQ-AUTO-001~004: 평일 09:00 통합 수집, 명시 링크 기반 영향 분석·우선순위, 기존 SMTP 기반 영속 알림 대기함, 안전한 예약 작업 전환 설계를 승인 사양으로 추가.

### Removed

- 통합 후 참조가 없는 완료 계획서 `docs/superpowers/plans/2026-09-21-integration.md` 제거. 실제 이행 근거는 `docs/INTEGRATION_VALIDATION.md`로 유지.
- 루트와 SHA-256이 같은 `apps/srs-spec/scripts/find-project-root.ps1` 중복 사본 제거. 공통 `scripts/find-project-root.ps1` 유지.
- 미참조 표지 이미지 `apps/issue-export/docs/screenshots/01_cover.png`, 비어 있지 않은 계획 폴더의 `docs/plans/.gitkeep` 제거.

### Added

- REQ-OBS-001: 오프라인 관찰 분석, 변경 전후 근거·후보·처리 상태와 실행 이력 저장.
- REQ-OPS-002: 이슈 실행 잠금과 staging/previous/failed 출력, 결과 상태와 건수·PDF·이미지 실패 기록.
- 합성 실패 주입·실제 renderer·동시 실행·관찰 후보 검증 테스트.

### Fixed

- REQ-SRS-004: 수집/배정 UID 대조와 검증 옵션 연결, 배포 I/O 오류 롤백·복구 사본, 성공 후 ORG 정리.
- 이슈 출력 파일명 충돌·경로 이탈·Git 메타데이터 보호, 본문 이미지 누락의 부분 완료 처리.
- REQ-CORE-002: 실행 메뉴 가독성과 결과 표시, 서버/로컬 점검 구분.

### Changed

- README에 제품 사용 흐름과 AI 활용 개발 역량을 구분해 소개하고 SPEC/진행 상태/개선안을 동기화.

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
