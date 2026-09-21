# Changelog

현재 사양은 `SPEC.md`, 현재 진행 상태는 `progress.md`를 기준으로 한다.

## [Unreleased]

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
