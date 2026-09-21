# ALM-QA-Automation 사양서

<!-- spec-template: v1 -->

| 항목 | 값 |
|---|---|
| Document Version | 1.0.0 |
| Project Version | 통합 기준 커밋 65f79cb (별도 제품 버전 미지정) |
| Last Updated | 2026-09-21 |
| Status | baseline — 구현·검증 범위와 알려진 불일치를 분리한 최초 사양 |
| Owner | 프로젝트 운영자 |

이 문서는 프로그램의 올바른 동작 기준이다. 향후 완전 자동화는 현재 제공 기능과 구분한다. 발견된 결함을 정상 사양으로 정당화하지 않는다. 워크스페이스 기준은 [automation-workspace의 사양 기반 개발 규칙](https://github.com/hongmin3/automation-workspace/blob/main/AGENTS.md)이며, 작업 절차는 `AGENTS.md`를 따른다.

## 1. 목적

QA 운영자가 하나의 프로그램에서 Polarion SRS 사양서와 변경 리포트를 생성하고, 선택한 이슈를 PDF/HTML/Markdown으로 내보내도록 한다. 성공 조건은 기존 두 기능과 이력을 유지하면서 단일 실행 메뉴·프로젝트·저장소에서 사용하고, 데이터 누락이나 실패를 확인할 수 있는 것이다.

## 2. 프로젝트 범위

### 포함

- 공통 Windows 실행 메뉴, Python CLI 및 앱별 작업 디렉터리 분리.
- SRS 수집·서식 정규화·스냅샷·안정적 분할·PDF·변경 비교·검증 후 배포.
- 주간 사양서 예약 실행, 부팅 시 만회 판단, 중복 실행 잠금, 설정된 결과 메일.
- 이슈 ID/검색어 기반 수집, 연결 항목·댓글·첨부 처리와 문서 출력.
- 운영 설정과 원문 데이터의 Git 제외, 요구사항·코드·테스트 추적.

### 제외

- 두 앱 설정 스키마와 모든 수집·렌더링 함수의 즉시 단일화.
- 이슈 자동 예약 수집, SRS·이슈 연계 검토 후보, 검토 상태 저장, 통합 알림 재시도.
- AI API 호출, 이슈 자동 수정, 서버 데이터 변경, 자동 검토 승인.
- 실제 환경을 검사하지 않고 무결점 운영 또는 메일 도착을 보장하는 행위.

## 3. 시스템 구성

| 구성 | 책임 | 경계 |
|---|---|---|
| `run.py` | 한국어 메뉴, 모드 분기, 자식 프로세스 실행·종료 코드 전달 | 수집 및 배포 로직을 복제하지 않음 |
| `run.ps1`, `실행하기.bat` | Windows 진입점; PowerShell은 JSON으로 인자 보존 | 따옴표·공백·한글·빈 인자 손상 방지 |
| `apps/srs-spec/` | 사양서 파이프라인과 기존 설정·데이터 | 앱 폴더가 상대 경로 기준 |
| `apps/issue-export/` | 이슈 수집·문서 출력 | 별도 설정 형식 유지 |
| Polarion REST API | 인증된 원본 항목·첨부 조회 | 외부 서버 가용성·권한에 의존 |
| Chromium / SMTP / Windows Task Scheduler | PDF 변환 / 선택적 알림 / 예약 | 개별 단계 검증과 운영 검증 구분 |
| Root Akela 컨텍스트 | AI 작업 판단 규칙 | SPEC의 제품 사양을 복제하지 않음 |

## 4. 전체 동작 흐름

1. 메뉴 또는 CLI에서 `srs`/`issues` 기능과 인자를 받는다.
2. 모드·메뉴 입력을 확인하고 현재 Python 실행기로 해당 앱을 앱 디렉터리에서 시작한다.
3. SRS: 설정·인증 → 실행 잠금/만회 판단 → 조회 또는 기존 스냅샷 → 분할·PDF → 이전 스냅샷과 비교 → 검증 → 허용된 경우 배포 → 실행 기록·선택적 알림.
4. 기간 리포트는 서버의 수정 항목 조회와 보관 스냅샷을 사용하며 PDF·배포·메일·주간 마커를 변경하지 않는다.
5. 이슈: 설정 → ID/쿼리 해석 → 검색·수집 → 항목별 데이터/첨부 → 통합 HTML/Markdown/PDF → 실행 manifest → 요청된 경우 문서 열기.
6. 실행기는 자식 종료 코드를 그대로 반환한다. 종료 코드 0만으로 이슈 수집·PDF의 완전 성공을 판정하지 않는다(9절 참조).

## 5. 기능 요구사항

### REQ-CORE-001 — 단일 진입점과 인자 보존

- 목적: 한 프로그램에서 두 기능을 선택한다.
- 입력: `python run.py srs ...`, `python run.py issues ...`, Windows 실행 메뉴.
- 선행 조건: Python과 기능별 의존성이 설치되어 있다.
- 동작: 앱별 스크립트와 작업 디렉터리를 고정하고 인자를 토큰 단위로 그대로 전달한다. PowerShell 인자는 일시적인 프로세스 환경변수의 JSON 배열을 통해 전달하고 복원한다.
- 기대 결과: 다른 디렉터리에서도 동일 실행; Python/PowerShell 직접 진입에서 따옴표·빈 문자열·한글·후행 역슬래시 보존; 자식 종료 코드 유지. BAT는 기본 메뉴 진입점이며 복잡한 인자는 Python CLI 사용을 권장한다.
- 예외 처리: Python 실행기의 알 수 없는 모드·잘못된 입력은 2, 시작 실패는 1, 인터럽트는 130. 자식 실행 전 도움말/종료는 0. PowerShell의 사전 인자 바인딩 오류는 Python 시작 전 비정상 종료(현재 1)한다.
- 관련 구현: `run.py`, `run.ps1`, `실행하기.bat`.
- 관련 테스트: TEST-CORE-001, `tests/test_launcher.py`.

### REQ-CORE-002 — 메뉴 입력과 결과 보존 기본값

- 목적: 사용자가 두 기능의 대표 작업을 쉽게 선택한다.
- 입력: 사양서/이슈/종료 선택, 작업 종류, 기준일 또는 대상.
- 선행 조건: 대화형 표준 입력 사용 가능.
- 동작: 사양서 하위 메뉴는 생성·배포·메일, 배포 없는 점검(빈 입력 기본), 기간 리포트를 제공한다. 이슈는 ID, 쿼리, 저장 조건, 실행 환경 점검을 제공한다.
- 기대 결과: 이슈 내보내기 메뉴는 `--timestamp --open`을 붙여 이전 결과를 보존한다. 점검은 `--check`만 전달하며 서버 조회를 포함한다.
- 예외 처리: 없는 메뉴·잘못된 날짜·빈 대상은 자식 실행 없이 2; 종료는 0. SRS dry-run은 배포 생략이지 오프라인/무메일 모드가 아니다.
- 관련 구현: `run.py`.
- 관련 테스트: TEST-CORE-001, `tests/test_launcher.py`.

### REQ-SRS-001 — 수집과 원본 스냅샷

- 목적: SRS를 안정적 비교가 가능한 구조로 보존한다.
- 입력: 프로젝트·쿼리·본문 필드 우선순위·PAT 환경변수.
- 선행 조건: 서버 조회 권한과 설정 존재.
- 동작: 항목과 관련 필드·이미지·댓글 등을 수집하고 SRS별 JSON을 저장한다. 취소선·밑줄·표 등 의미 있는 서식과 참조 링크를 유지하며 실행 가능한 스크립트는 제거한다.
- 기대 결과: 같은 항목에 안정적 식별자가 있고 스냅샷 왕복에서 데이터가 유지된다. 렌더링 fallback은 원본 스냅샷을 변형하지 않는다.
- 예외 처리: 실패한 이미지·참조를 표시하며 조용히 성공으로 위장하지 않는다. 오늘 스냅샷이 있으면 기본 재사용하고 `--force`로 재수집한다.
- 관련 구현: `apps/srs-spec/src/collector.py`, `apps/srs-spec/src/richtext.py`, `apps/srs-spec/src/snapshot_store.py`.
- 관련 테스트: TEST-SRS-001. 실제 서버 전체 수집은 운영 확인 대상.

### REQ-SRS-002 — 안정적 분할과 PDF 복구

- 목적: 항목 추가 시에도 기존 SRS의 문서 배치를 유지한다.
- 입력: 정규화된 SRS, 모듈별 그룹 매핑, 렌더링 시간 제한.
- 선행 조건: Chromium 설치.
- 동작: 그룹 매핑으로 HTML/PDF를 생성한다. PDF는 별도 프로세스에서 제한 시간 내 생성하고, 지연·폭주 항목은 재시도/격리와 서식 단순화로 처리한다.
- 기대 결과: 동일 SRS의 그룹 유지, fallback에서도 텍스트·이미지 보존, 문제 항목 표시; PDF 크기와 페이지 수 검사.
- 예외 처리: 미등록 모듈은 경고와 fallback 배정. 문제 캐시는 본문 및 렌더링 코드 해시 변경 시 무효화하며 재검사 옵션을 제공한다.
- 관련 구현: `apps/srs-spec/src/partition.py`, `apps/srs-spec/src/pdf.py`, `apps/srs-spec/src/render_recovery.py`.
- 관련 테스트: TEST-SRS-002, TEST-OPS-001.

### REQ-SRS-003 — 변경 비교와 기간 리포트

- 목적: 변경된 사양과 변경 근거를 식별한다.
- 입력: 현재/이전 스냅샷 또는 `--since YYYY-MM-DD`.
- 선행 조건: 비교할 보관 데이터; 기간 조회에는 서버 접근 필요.
- 동작: ID 기준 신규·삭제·변경·동일을 구분하고 상태·본문·서식·이미지·댓글·연결 항목 변화를 보고한다. 기간 리포트는 비교 가능 항목과 수정 시점만 확인 가능한 항목을 구분한다.
- 기대 결과: 변경 전후와 원문 링크 제공; 보관 이전 원문을 추정해 만들지 않는다.
- 예외 처리: 기준 스냅샷이 없으면 비교 불가를 표시한다. 기간 리포트는 PDF/배포/메일/마커에 영향을 주지 않는다.
- 관련 구현: `apps/srs-spec/src/diff.py`, `apps/srs-spec/src/report.py`, `apps/srs-spec/src/period_report.py`.
- 관련 테스트: TEST-SRS-003.

### REQ-SRS-004 — 검증 후 배포와 모드 분리

- 목적: 검증 실패가 기존 배포 사양서를 교체하지 않도록 한다.
- 입력: 수집 통계, 항목 ID, 생성 PDF, 배포 대상 및 실행 옵션.
- 선행 조건: 배포 대상은 운영 설정으로 결정하며 실제 값은 문서에 포함하지 않는다.
- 동작: 수집 기대 건수 일치, ID 중복·누락, PDF 수·크기·페이지를 검사한다. 통과한 PDF만 배포하고 직전 세대를 ORG에 보관한다. 기존 ORG 정리는 검증 통과 후 배포 시작 단계에 수행된다. 모든 수집 항목은 문서 배정 대상에 포함되어야 하며 배포 실패 시 복구 가능한 사본이 남아야 한다(9절 미충족 항목 참조).
- 기대 결과: 검증 실패 시 배포 생략. `--dry-run`은 배포와 주간 마커 갱신 생략; `--export-only`는 저장 스냅샷으로 재생성; `--diff-only`는 변경 리포트만 작성; `--crawl-only`는 수집·스냅샷까지만 수행.
- 예외 처리: 이미지 실패와 그룹 매핑 경고는 현재 경고 정책이며 필수 실패 여부는 13절 확인 사항이다. 배포 대상이 비어 있으면 배포를 건너뛴다.
- 관련 구현: `apps/srs-spec/main.py`, `apps/srs-spec/src/validate.py`, `apps/srs-spec/src/publish.py`.
- 관련 테스트: TEST-SRS-004. 모든 CLI 모드의 실제 운영 부작용까지 테스트 완료한 것은 아니다.

### REQ-SRS-005 — 예약·잠금·만회 실행

- 목적: 주간 실행을 유지하고 두 예약 트리거의 중복 실행을 방지한다.
- 입력: 주간 트리거, 부팅 트리거, ISO 주간 실행 기록.
- 선행 조건: Windows 예약 작업 등록, 해당 계정의 Python·네트워크·PAT 접근 가능.
- 동작: 설치 스크립트 기본은 월요일 07:00과 부팅 5분 후이다. 기존 작업 이름을 유지하고 앱 폴더에서 실행한다. 프로세스 잠금 안에서 만회 여부를 판단한다.
- 기대 결과: 같은 주 실행 기록이 있으면 성공/실패 여부와 관계없이 CatchUp 생략; 잠금 획득 실패는 중복 실행 없이 종료.
- 예외 처리: 손상/없는 마커는 미실행으로 취급; stale lock 회수. 기간 리포트는 잠금 대상에서 제외한다. 현재 잠금은 3시간 경과 시 생존 프로세스 확인 없이 회수하므로, 예약 작업 기본 제한 2시간 이내 운영을 전제로 하며 장시간 수동 실행의 배타성은 미검증이다.
- 관련 구현: `apps/srs-spec/scripts/install_task.ps1`, `apps/srs-spec/src/run_lock.py`, `apps/srs-spec/src/run_marker.py`.
- 관련 테스트: TEST-SRS-005. 설치 기본 시각과 실제 운영 설정은 구분한다.

### REQ-ISSUE-001 — 이슈 대상 지정과 수집

- 목적: 사용자가 선택한 항목과 연관 정보를 내보낸다.
- 입력: ID 목록 또는 쿼리, 프로젝트 설정, 선택적 건수 제한.
- 선행 조건: 서버 조회 권한.
- 동작: ID/쿼리를 상호 배타적으로 받고, CLI 대상은 저장 쿼리에 우선한다. 페이지 조회 후 본문·댓글·첨부·연결 항목을 설정에 따라 수집한다.
- 기대 결과: CLI 지정은 실사용 설정 파일을 수정하지 않는다. 필드 목록과 원시 JSON을 설정에 따라 저장한다.
- 예외 처리: 인증/권한 등 영구 오류를 일시적 오류와 구분한다. 첨부 오류 지속 여부는 설정을 따른다. 건수 제한·0건·부분 실패는 9절 개선 필요 항목이다.
- 관련 구현: `apps/issue-export/polarion_query_backup.py`.
- 관련 테스트: TEST-ISSUE-001; 현재는 검증 계획이며 서버 수집 회귀 테스트가 없다.

### REQ-ISSUE-002 — 문서 생성과 보존

- 목적: 사람이 읽을 문서와 외부 검토용 데이터를 제공한다.
- 입력: 수집된 이슈, 출력 경로·제목·출력 형식 설정.
- 선행 조건: 출력 디렉터리 쓰기 권한; PDF는 Chromium 필요.
- 동작: 통합 HTML과 선택적 Markdown/PDF, 항목별 HTML 및 첨부를 만든다. 화면 문서에는 요약·목차·항목 정보를 제공한다. `--timestamp`는 실행 시각별 디렉터리를 선택한다.
- 기대 결과: PDF 실패가 이미 생성된 HTML/Markdown까지 없애지 않는다. 메뉴 실행은 이전 산출물을 보존한다.
- 예외 처리: 직접 CLI에서 timestamp 미사용 시 기존 출력 폴더 삭제 동작이 있으므로 삭제 확인/출력 경로 보호를 따른다. 비대화형 자동화의 원자적 교체·동시 실행 보장은 아직 제공하지 않는다.
- 관련 구현: `apps/issue-export/polarion_query_backup.py`, `run.py`.
- 관련 테스트: TEST-ISSUE-001, TEST-OPS-001. PDF 변환 검증은 전체 이슈 수집 성공 증거가 아니다.

### REQ-OPS-001 — 결과 상태와 알림의 신뢰성

- 목적: 일부 실패를 완전 성공으로 오인하지 않도록 한다.
- 입력: 수집·렌더링·검증·배포 단계 결과와 알림 설정.
- 선행 조건: 필수 산출물과 경고 허용 정책이 정의되어 있다.
- 동작: 실패 단계와 누락을 확인할 수 있어야 하며, 필수 결과 실패는 완전 성공으로 전달하지 않아야 한다. 알림 실패는 이미 완료된 배포를 되돌리지 않아야 한다.
- 기대 결과: 수집 성공과 알림 성공을 분리해 해석할 수 있다. SRS 결과 메일은 설정 시에만 발송한다.
- 예외 처리: 현재 이슈 부분/PDF 실패의 정상 종료, manifest 상태 부족, SRS 조기 예외 알림 누락이 있어 이 요구사항은 미충족이다. 문서로 정당화하지 않고 9절 불일치로 관리한다.
- 관련 구현: `apps/issue-export/polarion_query_backup.py`, `apps/srs-spec/main.py`, `apps/srs-spec/src/notify.py`.
- 관련 테스트: TEST-OPS-002 (미구현 검증 계획).

## 6. 비기능 요구사항

### NFR-OPS-001 — 기존 기능·데이터·이력 보존

통합으로 앱 내부 소스 의미와 설정 형식을 임의 변경하지 않아야 한다. 두 Git 이력을 보존하고 데이터 복사 무결성을 확인한다. 실제 운영 확인 전 원본과 복구 자료를 보존한다. 관련 검증은 TEST-OPS-003이다.

### NFR-SEC-001 — 비밀값과 운영 데이터 분리

실제 설정·PAT·SMTP 비밀값·SRS 원문·수집 문서·로그는 Git에 포함하지 않는다. 허용된 예시만 제공하고 Rich Text의 실행 스크립트를 제거한다. 관련 검증은 TEST-SEC-001이다.

## 7. 데이터 사양

| 데이터 | 앱 기준 기본 위치 | 보존/해석 |
|---|---|---|
| SRS 스냅샷 | `snapshots/<date>/<project>/` | 항목별 JSON; 비교 근거; 원문과 렌더 fallback 구분 |
| SRS 생성물 | `output/<date>/html/`, `pdf/`, `reports/` | 중간 HTML, 최종 PDF, 변경 리포트 |
| 렌더 문제 상태 | `snapshots/render_problem_state.json` | 본문·파이프라인 해시 기반 캐시 |
| 실행 상태 | `logs/last_run.json`, 실행 잠금 및 일별 로그 | ISO 주·실행일·성공 여부; 운영 파일 |
| 이전 배포본 | 설정된 ORG 경로 | 직전 세대 보존, 다음 검증 통과 후 배포 시작 단계에서 기존 세대 정리; I/O 실패 원자성 미보장 |
| 이슈 생성물 | `polarion_backup/` 또는 지정 경로 | 통합 문서, 항목별 JSON/첨부, 필드 목록, manifest |
| 이슈 메뉴 실행 | 출력 기준 경로의 timestamp 하위 | 이전 결과 보존; 초 단위 충돌 가능성은 개선 대상 |

설정으로 경로가 바뀔 수 있다. 원본 서버·계정·실사용 쿼리·수신자는 문서에 적지 않는다. 이슈 manifest는 현재 PDF 성공/수집 완전성의 충분한 증거가 아니다.

## 8. Configuration 사양

실사용 파일은 열람하지 않았으며 아래는 예시 설정과 로더 계약이다. 예시값을 실제 적용값으로 단정하지 않는다.

| 항목 | 출처 | 기본/필수 정책 |
|---|---|---|
| SRS 설정 | 앱의 `config/config.yaml` | 파일 필수; 예시는 `apps/srs-spec/config/config.example.yaml` |
| 이슈 설정 | 앱의 `config.yaml`, CLI `--config` | 파일 필수; 예시는 `apps/issue-export/config.example.yaml` |
| 인증 | `polarion.token_env`가 가리키는 환경변수 | 기본 이름 `POLARION_TOKEN`; 토큰 필수; SRS는 앱 `.env`도 로드 |
| 서버·대상 | `polarion.host`, SRS `projects`, 이슈 `project_id`/`search.query` | 실제 값은 운영자가 지정 |
| 요청 제어 | `verify_ssl`, `timeout_seconds`, `request_interval_seconds`, `page_size` | 예시 true / 90초 / 0.15초 / 100 |
| 이슈 재시도 | `max_retries`, `retry_backoff_seconds` | 예시 3회 / 2초, 일시 오류에만 적용 |
| SRS 분할·출력 | `partition`, `output`, `render` | 그룹 매핑 필요; PDF 제한 예시 300초; 출력 기본 위치는 7절 |
| 이슈 출력 | `output.generate_pdf`, `generate_markdown`, `generate_per_issue_html` | 예시 모두 true; CLI 출력 경로·제목은 실행 시 덮어씀 |
| 건수 제한 | 이슈 `search.max_items`, CLI `--limit` | 설정 0은 무제한; 제한 여부를 전체 성공과 구분해야 함 |
| 메일 | SRS `mail` 및 SMTP 환경변수 | 예시 enabled=false; 필요할 때만 운영자가 활성화 |
| SRS 검증 옵션 | `validation.min_expected_srs_ratio`, `require_all_pdfs` | 예시 0.95 / true; 현재 실행 검사 연결 불일치는 9절 참조 |

새 공통 설정 파일이나 새 알림 채널을 이번 통합으로 도입하지 않는다. CLI 상세는 앱별 `--help`와 README를 참조한다.

## 9. 오류 처리 정책

- 설정/입력 오류는 수집 전에 종료하고 원인을 알린다. 공통 실행기는 앱 오류를 성공으로 바꾸지 않는다.
- SRS 파이프라인은 일반 실패 1, 설정 오류 2, API 접근 오류 3을 반환한다. 검증 실패 시 배포하지 않는다.
- PDF 시간 초과는 격리·복구 대상이며, 복구 실패를 검증 결과에 반영한다.
- SRS dry-run은 외부 게시만 생략하므로 네트워크·로컬 파일·설정된 결과 메일이 발생할 수 있다.
- `issues --check`는 서버 프로젝트 조회까지 수행한다. 오프라인 점검으로 안내하면 안 된다.
- 사용자 승인 없이 기존 실패 재실행 정책을 변경하거나 새 자동 알림을 활성화하지 않는다.

### SPEC / CODE MISMATCH — 실행 결과

- Requirement: REQ-OPS-001
- Specification: 필수 출력 실패와 불완전 수집은 완전 성공과 구분되어야 한다.
- Current Implementation: 이슈별/PDF 실패 후 정상 반환할 수 있고 PDF 상태·제한 수집 정보가 manifest에 충분히 기록되지 않는다. SRS 조기 예외는 결과 알림 블록을 건너뛸 수 있다.
- Difference: 종료 코드/manifest/알림만으로 완전 성공을 신뢰할 수 없다.
- Action: CODE 수정 대상. 이번 문서 보완에서 구현하지 않음. TEST-OPS-002로 검증할 계획.

### SPEC / CODE MISMATCH — 설정과 점검 안내

- Requirement: REQ-SRS-004, REQ-CORE-002
- Specification: 지원한다고 안내한 설정은 검사에 연결되어야 하며 점검의 네트워크 부작용을 정확히 안내해야 한다.
- Current Implementation: SRS 로더가 읽는 `min_expected_srs_ratio`와 `require_all_pdfs`가 `validate_run` 호출에 전달되지 않는다. 기존 이슈 앱 도움말/래퍼의 오프라인 표현과 `run_environment_check`의 서버 조회가 다르다.
- Difference: 일부 설정/안내가 실제 동작과 일치하지 않는다. 통합 README는 서버 조회를 안내하지만 기존 앱 문구는 남아 있다.
- Action: CODE/안내 수정 대상. 경고를 없애려고 올바른 사양을 변경하지 않음.

### SPEC / CODE MISMATCH — 문서 반영 완전성과 배포 복구

- Requirement: REQ-SRS-004
- Specification: 수집된 모든 대상은 문서에 배정되어야 하며 배포 실패 후 복구 가능한 사본이 남아야 한다.
- Current Implementation: 분할 설정에 없는 프로젝트는 경고 후 생략될 수 있으나 전체 수집 ID와 배정 ID를 대조하지 않는다. 배포 함수는 기존 ORG를 먼저 정리하고 파일을 이동·복사한다.
- Difference: 수집 건수 검사 통과가 모든 항목의 PDF 반영을 보장하지 않는다. 이동·복사 중 I/O 실패에서 원자적 보존이 보장되지 않는다.
- Action: CODE 수정 대상. 배정 ID 대조와 중간 실패 주입 검증을 TEST-SRS-004의 후속 검증으로 추가해야 한다. 이번 문서 작업에서 운영 로직을 변경하지 않음.

## 10. 보안 요구사항

PAT와 SMTP 자격증명은 환경변수 또는 Git 제외 운영 설정에서만 사용한다. 실제 설정·원문·출력·로그·마이그레이션 백업은 커밋하지 않는다. 문서와 테스트에는 합성 데이터를 사용한다. 외부 ALM은 조회 대상으로 취급하고 변경 API를 자동 호출하지 않는다. Rich Text를 정규화해 실행 가능한 코드를 제거하며, 이미지/첨부 실패를 숨기지 않는다. AI 외부 전송은 현재 범위 밖이다.

## 11. 테스트 사양

아래의 계획과 실행 증거를 구분한다. `verified`는 해당 검증 범위에서 실제 실행까지 확인했다는 뜻이며 전체 운영 보증이 아니다.

### TEST-CORE-001

- 검증 대상: REQ-CORE-001, REQ-CORE-002.
- 선행 조건: Python, pytest, Windows PowerShell.
- 절차: `tests/test_launcher.py` 실행; 메뉴 입력별 분기, 외부 cwd 도움말, 실제 PowerShell 인자·종료 코드 보존 확인. 실제 메뉴에 종료 입력을 전달한다.
- Expected Result: 올바른 앱/인자/cwd, 잘못된 입력은 자식 미실행, 종료 코드 유지. 특수 인자 검증은 Python/PowerShell 직접 진입에 한정하며 cmd→BAT→PowerShell의 특수 인자 보존은 별도 미검증이다.

### TEST-SRS-001

- 검증 대상: REQ-SRS-001, NFR-SEC-001.
- 선행 조건: 합성 Rich Text와 임시 스냅샷.
- 절차: `apps/srs-spec/tests/test_richtext.py`, `apps/srs-spec/tests/test_snapshot_store.py` 실행; 스냅샷 관련 추가 검증은 `apps/srs-spec/tests/test_diff_incremental_realdata.py`에서 읽기 전용 입력을 사용한다.
- Expected Result: 서식·링크·이미지 상태 보존, 스크립트 제거, 스냅샷 왕복 일치. 실데이터가 없는 환경의 해당 테스트 skip을 실패/성공과 구분한다.

### TEST-SRS-002

- 검증 대상: REQ-SRS-002.
- 선행 조건: 합성 항목, 시간 초과/복구를 재현하는 테스트 대역.
- 절차: `apps/srs-spec/tests/test_partition.py`, `apps/srs-spec/tests/test_render_recovery.py` 실행.
- Expected Result: 그룹 안정성, 문제 항목 격리, 원문 보존, 캐시 무효화, 복구 실패 보고.

### TEST-SRS-003

- 검증 대상: REQ-SRS-003.
- 절차: `apps/srs-spec/tests/test_diff.py`, `apps/srs-spec/tests/test_report.py`, `apps/srs-spec/tests/test_period_report.py` 실행.
- Expected Result: 변경 종류·전후 문장·원문 링크 일치; 없는 과거 기록을 생성하거나 비교 가능 건수로 세지 않음.

### TEST-SRS-004

- 검증 대상: REQ-SRS-004.
- 선행 조건: 임시 출력/배포 폴더, 합성 수집·PDF 결과.
- 절차: `apps/srs-spec/tests/test_validate.py`, `apps/srs-spec/tests/test_publish_org.py` 실행.
- Expected Result: 건수/빈 PDF 실패 검출, 정상 결과 통과, ORG 세대 교체 정책 유지. CLI 전체 단계 결합과 실제 배포는 별도 운영 검증. 분할에서 생략된 ID 대조 및 배포 중 I/O 실패 복구 테스트는 아직 없으며 9절 불일치의 후속 검증이다.

### TEST-SRS-005

- 검증 대상: REQ-SRS-005.
- 절차: `apps/srs-spec/tests/test_run_lock.py`, `apps/srs-spec/tests/test_run_marker.py` 실행. 예약 작업은 변경 전후 정의를 읽기 비교한다.
- Expected Result: 중복 잠금 차단, 해제·stale 회수, 주간 만회 판정; 통합에서 작업 디렉터리 이외 설정 보존.

### TEST-ISSUE-001

- 검증 대상: REQ-ISSUE-001, REQ-ISSUE-002.
- 상태: 계획. 이슈 전체 수집의 독립 회귀 테스트는 아직 없음.
- 선행 조건: 합성 API 응답 및 임시 폴더; 별도 승인된 운영 확인 시에만 실제 서버 사용.
- 절차: ID/쿼리 선택, 페이지 경계·중복·0건·제한, 첨부 실패, HTML/Markdown 링크와 이미지, 설정 미변경, 이전 결과 보존을 검증한다.
- Expected Result: 선택한 항목과 산출물 일치; 실패·제한을 명확히 기록. 검증 전 verified로 표기하지 않음.

### TEST-OPS-001

- 검증 대상: REQ-SRS-002, REQ-ISSUE-002의 PDF 변환 경로.
- 선행 조건: Chromium, pypdf, 합성 1페이지 HTML.
- 절차: 두 앱의 PDF 생성 함수를 호출하고 페이지 수 및 추출 텍스트 확인.
- Expected Result: 두 PDF 모두 1페이지이며 입력 식별 문구 포함. 통합 시 실제 실행 증거는 `docs/INTEGRATION_VALIDATION.md`에 기록.

### TEST-OPS-002

- 검증 대상: REQ-OPS-001.
- 상태: 계획. 실패 재현 통합 테스트 미구현.
- 절차: 항목 수집 실패, PDF 실패, 0건, 제한 수집, SMTP 실패, 조기 API 예외를 각각 주입한다.
- Expected Result: 필수 단계 실패는 완전 성공과 구분되고 원인이 남음; 알림 실패가 배포를 취소하지 않음.

### TEST-OPS-003

- 검증 대상: NFR-OPS-001.
- 절차: 원본/통합 파일의 해시 비교, 양쪽 Git HEAD의 조상 관계, 원격·로컬 커밋 일치 확인.
- Expected Result: 비교 대상 데이터 불일치 0, 두 이력 보존. 제외한 캐시/관리 파일 범위를 증거에 명시.

### TEST-SEC-001

- 검증 대상: NFR-SEC-001.
- 절차: Git 추적 목록과 ignore 규칙을 대조하고 실제 설정·원문·생성물 미포함 확인. Rich Text 테스트 실행.
- Expected Result: 보호 파일 미추적, 비밀값 출력 없음, 스크립트 제거. 운영 비밀값 내용을 열람하지 않음.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-CORE-001 | `run.py`, `run.ps1`, `실행하기.bat` | TEST-CORE-001 | implemented |
| REQ-CORE-002 | `run.py` | TEST-CORE-001 | verified |
| REQ-SRS-001 | `apps/srs-spec/src/collector.py`, `apps/srs-spec/src/richtext.py`, `apps/srs-spec/src/snapshot_store.py` | TEST-SRS-001 | implemented |
| REQ-SRS-002 | `apps/srs-spec/src/partition.py`, `apps/srs-spec/src/pdf.py`, `apps/srs-spec/src/render_recovery.py` | TEST-SRS-002, TEST-OPS-001 | implemented |
| REQ-SRS-003 | `apps/srs-spec/src/diff.py`, `apps/srs-spec/src/report.py`, `apps/srs-spec/src/period_report.py` | TEST-SRS-003 | implemented |
| REQ-SRS-004 | `apps/srs-spec/main.py`, `apps/srs-spec/src/validate.py`, `apps/srs-spec/src/publish.py` | TEST-SRS-004 | implemented (9절 불일치 있음) |
| REQ-SRS-005 | `apps/srs-spec/scripts/install_task.ps1`, `apps/srs-spec/src/run_lock.py`, `apps/srs-spec/src/run_marker.py` | TEST-SRS-005 | implemented |
| REQ-ISSUE-001 | `apps/issue-export/polarion_query_backup.py` | TEST-ISSUE-001 | implemented |
| REQ-ISSUE-002 | `apps/issue-export/polarion_query_backup.py`, `run.py` | TEST-ISSUE-001, TEST-OPS-001 | implemented |
| REQ-OPS-001 | `apps/issue-export/polarion_query_backup.py`, `apps/srs-spec/main.py`, `apps/srs-spec/src/notify.py` | TEST-OPS-002 | draft |
| NFR-OPS-001 | `run.py`, `.gitignore` | TEST-OPS-003 | verified |
| NFR-SEC-001 | `.gitignore`, `apps/srs-spec/.gitignore`, `apps/issue-export/.gitignore`, `apps/srs-spec/src/richtext.py` | TEST-SEC-001 | implemented |

verified의 범위는 11절과 검증 기록으로 한정한다.

## 13. 미확정 사항

- 실제 서버·출력 대상·수신자·예약 실행 시각은 보호된 운영 설정을 열람하지 않아 확인하지 않았다.
- 필수 이미지 실패의 중단 기준, 수집 감소 허용치, PDF 선택 출력의 성공 판정은 설정 불일치 정리 시 확정해야 한다.
- 전체 이슈 수집/실제 배포/메일 수신/장시간 예약 실행은 운영 검증이 남아 있다.
- 다음 자동화의 실행 주기, 대상 쿼리, 중요도 규칙, 알림 채널·수신자, AI 사용 여부는 미확정이다.

## 14. 향후 개선 후보

[자동화 개선안](docs/AUTOMATION_ROADMAP.md)에 우선순위를 둔다. 공통 단계별 상태와 실행 이력, 수집 완전성, 원자적 결과 확정, 알림 재시도·중복 방지, SRS/이슈 연계 검토 후보를 순서대로 검토한다. 현재 제공 기능으로 표시하거나 사용자 확인 없이 활성화하지 않는다.
