# VXvue SRS Spec Automation

Polarion ALM에 흩어져 있는 수백 건의 SRS(Software Requirements Specification) Work Item을 매주 자동으로 수집해, QA가 바로 검토할 수 있는 사양서 PDF와 "무엇이 바뀌었는지"를 알려주는 변경 리포트를 만들어내는 자동화 시스템입니다.

> 이 저장소는 사내 QA 자동화 도구를 일반화한 코드입니다. 실제 사내 ALM 서버 주소, 프로젝트 데이터, SRS 원문, 사양서 PDF는 포함되어 있지 않으며, 아래 예시는 모두 더미(dummy) 값입니다.

## TL;DR

| | |
|---|---|
| 무엇을 | Polarion ALM의 SRS(수백 건)를 매주 수집해 QA용 PDF 사양서(5~6개 분할)와 SRS 단위 변경 리포트를 자동 생성 |
| 왜 | 수동 Export의 언어 혼재, 대용량 단일 문서, 변경점 추적 어려움을 해결 |
| 어떻게 | Polarion REST API → 구조화 Snapshot(JSON) → HTML 렌더 → PDF → 이전 Snapshot과 구조적 Diff |
| 실행 | Windows Task Scheduler, 매주 월요일 09:00, 실패 시 기존 산출물 보호 |
| 규모 | 실제 운영 환경 기준 SRS 500건 이상, 이미지 수백 장, PDF 6개 |

## Problem

품질보증(QA) 팀은 매 릴리스마다 "연구소가 이번 주에 사양을 어떻게 바꿨는지"를 확인해야 합니다. 지금까지는 이 과정이 전부 수작업이었습니다.

- SRS Work Item이 수백 건 단위라 전체를 하나의 문서로 만들면 열기도 버거운 용량이 됩니다.
- Polarion의 HTML Export 결과가 항목마다 언어가 들쭉날쭉해서(영문/국문 라벨 혼재) 문서를 그대로 배포하기 어렵습니다.
- 이미지가 실제로 잘 포함됐는지, 링크가 깨지지 않았는지 매번 눈으로 확인해야 합니다.
- 지난주 사양서와 이번주 사양서를 나란히 놓고 어디가 바뀌었는지 찾는 데 시간이 오래 걸립니다.
- 연구소가 변경한 내용을 QA가 바로 알아채기 어려워, 회귀 검증 대상에서 누락되는 사양이 생깁니다.

## Solution

```text
Polarion ALM (REST API)
        │  SRS Work Item 수집 (계층/커스텀 필드/이미지/첨부/댓글)
        ▼
SRS Collector
        │  Rich Text 정규화 (이미지 로컬화, 참조 링크 복원, XSS 새니타이즈)
        ▼
Normalized Snapshot (SRS 1건당 JSON 파일 1개)
        │  안정적 분할(Stable Partitioning) - 같은 SRS는 항상 같은 파일에 남음
        ▼
HTML Renderer  →  PDF Generator (5~6개 문서로 분할)
        │
        ▼
Previous Snapshot Diff  →  Change Report (HTML / Markdown)
```

## 주요 기능

- **Polarion REST API 기반 SRS 자동 수집** - 브라우저 DOM 크롤링이 아니라 Personal Access Token 인증 REST API로 안정적으로 수집
- **이미지 / Rich Text 서식 보존** - 취소선, 밑줄, Bold, 표, 색상 등 사양 개정 판단에 필요한 서식을 절대 평탄화하지 않음
- **깨진 참조 자동 복원** - Polarion 내부 Work Item 참조는 원래 브라우저 JS가 채워주는 빈 placeholder인데, 대상 항목 제목을 조회해 실제 링크 텍스트로 복원
- **Stable Partitioning** - SRS 전체를 5~6개 PDF로 나누되, 매주 항목이 추가돼도 기존 SRS는 항상 같은 파일에 남도록 모듈(카테고리) 기준으로 고정 배정
- **SRS 단위 구조적 Diff** - PDF 텍스트 비교가 아니라 SRS ID를 Key로 신규/삭제/변경/동일을 구분하고, Status/Description/이미지/첨부/링크/댓글/서식 변경까지 세분화
- **주간 자동 실행 + 놓친 실행 보정** - Windows Task Scheduler로 매주 월요일 실행, PC가 꺼져 있었으면 다음 가능 시점에 자동 실행
- **실패 시 기존 파일 보호** - SRS 개수 불일치, 중복 ID, PDF 생성 실패 등 하나라도 있으면 기존 배포본을 교체하지 않음
- **버전 보존 후 자동 정리** - 신규 반영 전 직전 세대를 `ORG/<날짜>/`로 옮겨 두고, 다음 실행이 검증을 통과하면 자동 삭제. ORG에는 항상 직전 세대 하나만 남습니다
- **결과 메일 알림** - 성공/실패와 무관하게 매 실행 결과를 메일로 발송. 실패했는데 아무도 모르는 상황을 막습니다
- **임의 기간 지정 리포트** (`--since`) - 주간 리포트와 별도로, 임의의 과거 기준일부터 지금까지의 변경만 뽑아볼 수 있음. 배포/메일 없이 리포트만 생성

## Architecture

| 모듈 | 역할 |
|---|---|
| `src/polarion_client.py` | Polarion REST API v1 클라이언트 (PAT 인증, 페이지네이션, 첨부 다운로드) |
| `src/collector.py` | SRS Work Item 수집 및 정규화된 레코드 생성 |
| `src/richtext.py` | Rich Text 새니타이즈, 이미지 로컬화, Work Item 참조 링크 복원 |
| `src/snapshot_store.py` | SRS 1건당 JSON 파일로 Snapshot 저장/조회 (git-friendly) |
| `src/partition.py` | Stable Partitioning - 모듈(oldId) 기준 파일 그룹 배정 |
| `src/render.py` | 표준 HTML 렌더링 (시스템 라벨 영어 고정, SRS 본문은 원문 언어 유지) |
| `src/pdf.py` / `src/pdf_worker.py` | Playwright PDF 변환 - 별도 프로세스로 격리 실행 + 시간제한 |
| `src/render_recovery.py` | 렌더링 시간 초과 시 이분 탐색으로 문제 SRS를 자동 격리·복구, 느린 그룹은 시간제한 연장 재시도 (Engineering Highlight 참고) |
| `src/problem_state.py` | 렌더링 문제 SRS 캐시 - 본문 + 렌더링 파이프라인 해시 기반 무효화 |
| `src/diff.py` | Snapshot 간 SRS 단위 구조적 Diff |
| `src/report.py` | 변경 리포트(HTML/Markdown) 생성 |
| `src/validate.py` | 실행 성공 판정 (개수/중복/PDF 무결성 등) |
| `src/publish.py` | 검증 통과 시에만 직전 세대를 ORG로 보관하고 신규 반영, 지난 세대 자동 정리 |
| `src/notify.py` | 실행 결과 메일 알림 (발송 실패가 자동화를 실패시키지 않음) |
| `src/run_marker.py` | 주간 실행 기록 - 부팅 시 만회 실행의 중복 방지 |

## Change Detection 예시

```diff
SRS-EXAMPLE-001  Detector Auto-Reconnect
Change Type: changed
Detected changes: description, underline_added

- Detector shall reconnect automatically.
+ Detector shall reconnect automatically within 10 seconds.
```

리포트 상단에는 아래와 같은 요약이 함께 표시됩니다.

```text
Execution Date: 2026-08-24
Previous Snapshot: 2026-08-17
Current Snapshot: 2026-08-24

Total SRS: 500+
Changed: 18   New: 5   Deleted: 1   Unchanged: 480+
```

## Engineering Highlight — 원인을 못 찾은 버그를 "자동 격리"로 우회하기

실제 운영 데이터로 테스트하던 중, 특정 SRS 하나가 포함된 그룹만 PDF 생성에 20분 넘게 걸리고 **30,000페이지가 넘는 PDF**가 나오는 현상을 만났습니다(정상 범위는 100~200페이지). 다른 98건은 전혀 문제가 없었습니다.

**진단 과정**

1. 이미 생성된 HTML을 재사용해(Polarion 재수집 없이) 항목을 절반씩 나눠 렌더링 시간을 비교하는 방식으로 이분 탐색을 진행 → 정확히 SRS 1건으로 범위를 좁힘.
2. 해당 SRS의 Rich Text를 뜯어보니 스펙에 어긋나게 깊이 중첩된 목록 구조, 실제 크기와 다르게 강제 지정된 이미지 크기 등 의심 요소가 여럿 발견됨.
3. 의심 요소를 하나씩 제거하며 재테스트했지만, 어느 것도 단독 원인이 아니었음(각 테스트마다 최대 22분 소요).
4. **Chromium 인쇄 페이지네이션 엔진 내부의 특정 조합에서 발생하는 문제로 추정되나, 정확한 트리거 요소는 특정하지 못함.**

**"원인을 몰라도 안전하게 동작하게 만들기"**

원인 규명에 매달리는 대신, 다음 원칙으로 방향을 바꿨습니다: *"어떤 SRS가 문제를 일으키는지는 몰라도, 문제를 일으키는 SRS가 있다는 사실 자체는 렌더링 시간으로 감지할 수 있다."*

- PDF 렌더링을 별도 프로세스로 격리하고 시간제한(90초)을 둔다. 초과하면 프로세스 트리 전체를 강제 종료한다(자식 프로세스인 브라우저까지 확실히 정리).
- 그룹 전체 렌더링이 시간 초과되면, **수동으로 했던 이분 탐색을 그대로 자동화**해서 문제 SRS를 찾아낸다(레코드를 절반씩 나눠 재귀적으로 렌더링 재시도).
- 문제 SRS를 찾으면 **문서에서 제외하지 않는다.** 대신 렌더링을 멈추게 하는 것으로 추정되는 복잡한 서식(중첩 구조 등)만 제거하고, 본문 텍스트와 이미지는 그대로 보존한 안내 배너 포함 블록으로 대체한 뒤, 전체 그룹을 다시 정상 렌더링한다.
- 어떤 SRS가 격리되었는지는 로그와 변경 리포트에 명시적으로 남겨(조용히 넘어가지 않음), 필요하면 사람이 원본을 직접 확인할 수 있게 한다.

**같은 탐색을 매주 반복하지 않기 — 그러나 수정을 놓치지도 않기**

이분 탐색은 문제 SRS 1건을 찾는 데 약 10분이 걸립니다. 주간 자동 실행이므로, 원인을 이미 아는 SRS를 매주 다시 찾는 것은 순수한 낭비입니다. 반대로 단순히 "이 SRS는 문제니까 건너뛴다"고 목록에 넣어두면, **그 사이에 원본 SRS가 수정되어 정상 렌더링이 가능해졌더라도 계속 서식이 낮은 상태로 남는** 새로운 문제가 생깁니다.

그래서 목록 캐시가 아니라 **본문 해시 기반 캐시**로 만들었습니다(`src/problem_state.py`).

- 문제로 확인한 시점의 **본문 SHA-256**과 **렌더링 파이프라인 SHA-256**을 함께 저장한다.
- 다음 실행에서 둘 다 **같으면** 이분 탐색을 생략한다(약 10분 절약).
- 본문 해시가 **다르면** 캐시를 무시하고 정상 렌더링부터 다시 확인한다 — 수정으로 문제가 해소되었을 기회를 놓치지 않는다.
- 렌더링 파이프라인 해시가 **다르면**(HTML 템플릿/CSS 또는 Playwright 인쇄 옵션을 고친 경우) 역시 전부 재확인한다. 본문이 그대로여도 파이프라인 개선으로 폭주가 해소될 수 있는데, 본문 해시만 봤다면 그 개선이 영구히 반영되지 않는다.
- 재확인 결과 정상 렌더링되면 상태에서 자동으로 제거하고, 설정에서 빼도 된다고 로그로 안내한다.
- `--recheck-known-problems`로 언제든 캐시를 무시한 전체 재확인이 가능하다.

이때 SRS 본문의 수집·Snapshot·변경 리포트는 캐시와 **무관하게 항상** 수행됩니다. 캐시가 영향을 주는 범위는 "PDF 안에서 서식을 단순화할지 여부"로 한정되어 있습니다.

이 접근은 "완벽한 근본 원인 분석"이 항상 가능하거나 시간 대비 효율적이지 않을 때, **감지 → 격리 → 최소 손실 복구 → 캐시하되 무효화 조건을 명시**로 시스템을 견고하게 만드는 실용적인 패턴을 보여줍니다.

## 기간 지정 리포트 (--since)

주간 정기 실행의 변경 리포트는 항상 "바로 직전 실행 대비"만 보여줍니다. "지난 검증 회차(예: 7월 25일) 이후 뭐가 바뀌었나"처럼 임의의 과거 시점을 기준으로 보고 싶을 때는 별도 조회 모드를 씁니다.

```bash
python main.py --since 2026-07-25
```

- Polarion에 기간 내 변경 목록(`updated:[... TO ...]`)을 직접 조회하므로, 그 사이 스냅샷을 매일 쌓아두지 않아도 **어떤 SRS가 바뀌었는지**는 항상 정확합니다.
- **본문 비교**(무엇이 어떻게 바뀌었는지)는 요청한 기준일 이하로 보관된 스냅샷이 있을 때만 가능합니다. 자동화가 매일 도는 것이 아니라 주간 스냅샷만 쌓이기 때문입니다.
  - 기준일에 정확히 일치하는 스냅샷이 없으면 그 이하에서 가장 늦은 스냅샷을 기준으로 쓰고, 로그로 어떤 날짜를 썼는지 알립니다.
  - 기준일 이하 스냅샷이 전혀 없으면 본문 비교 없이 "변경된 SRS 목록"만 리포트하고, 어느 구간부터 비교 가능한지 명시합니다. 조용히 다른 기준으로 바꿔치기하지 않습니다.
- PDF 생성·지식파일 폴더 반영·메일 발송·주간 실행 마커 기록은 전혀 건드리지 않는 순수 조회 모드입니다.

## Scheduler

작업 **두 개**를 등록합니다.

| 작업 | 트리거 | 인수 | 역할 |
|---|---|---|---|
| `VXvue_SRS_Spec_Automation` | 매주 월요일 **07:00** | `main.py` | 주간 정기 실행 |
| `VXvue_SRS_Spec_Automation_CatchUp` | **PC 시작 5분 후** | `main.py --catch-up` | 놓친 주 만회 |

### 놓친 실행은 어떻게 만회되나

예정 시각에 PC가 꺼져 있으면 그 주 실행이 통째로 누락됩니다. 두 겹으로 막습니다.

1. **`StartWhenAvailable`** — Windows가 놓친 예정 실행을 다음 가능 시점에 실행합니다. 다만 실행 시점이 OS 사정에 따라 늦어질 수 있습니다.
2. **부팅 시 `--catch-up` 작업** — PC 시작 5분 후 실행됩니다. `logs/last_run.json`에 **이번 주(월요일 기준 ISO 주)** 실행 기록이 있으면 아무것도 하지 않고 즉시 종료하고, 없으면 놓친 실행을 그때 수행합니다. 그래서 매번 부팅해도 중복 실행되지 않습니다.

**실패한 주는 자동으로 다시 돌리지 않습니다.** 실패도 실행 기록으로 남기므로 부팅 시 재시도되지 않으며, 실패 사실은 메일로 알립니다. 자동 재시도(`RestartCount`)도 0입니다 — 실패를 조용히 반복하지 않고 사람에게 알리는 쪽을 택했습니다.

### 등록

```powershell
.\scripts\install_task.ps1
```

`python.exe`는 PATH에서 자동 탐색하며, `-PythonExe "C:\path\to\python.exe"`로 지정할 수 있습니다. 실행 시각은 `-At "06:30"`처럼 바꿀 수 있습니다. 이미 같은 이름의 작업이 있으면 제거 후 재등록하므로 설정 변경 시 그대로 다시 실행하면 됩니다.

공통 설정:

| 설정 | 값 | 이유 |
|---|---|---|
| `StartWhenAvailable` | True | 놓친 예정 실행을 다음 가능 시점에 실행 |
| `MultipleInstances` | IgnoreNew | 정기 실행과 부팅 만회 실행이 겹쳐도 중복 실행 안 함 |
| `RunOnlyIfNetworkAvailable` | True | Polarion 접근 불가 상태에서 헛돌지 않게 |
| `ExecutionTimeLimit` | 2시간 | 무한 대기 방지 |
| `RestartCount` | 0 | 실패 시 자동 재시도 없음 — 메일로 알림 |
| LogonType | **S4U** | 로그온 여부와 무관하게 실행되며 비밀번호를 저장하지 않음 |
| **Priority** | **4** | 아래 주의사항 참고 |

> **우선순위 4가 중요한 이유.** Task Scheduler는 작업을 기본 우선순위 7(낮음)로 실행합니다. 이 우선순위에서는 Chromium 인쇄가 크게 느려져, 대화형 실행에서 17~36초에 끝나는 그룹이 90초 제한을 넘겨 실패하는 것을 실제로 확인했습니다. `-Priority 4`(보통)로 등록하면 대화형 실행과 비슷한 속도가 나옵니다. 스케줄러에서만 타임아웃이 발생한다면 이 설정을 먼저 확인하세요.

> **`.ps1` 파일은 UTF-8 BOM으로 저장해야 합니다.** Windows PowerShell 5.1은 BOM이 없는 `.ps1`을 시스템 ANSI 코드페이지로 읽기 때문에, 한글 주석·문자열이 깨지고 파싱 오류까지 발생합니다.

### 확인

```powershell
Get-ScheduledTask -TaskName VXvue_SRS_Spec_Automation, VXvue_SRS_Spec_Automation_CatchUp | Format-List TaskName, State
Get-ScheduledTaskInfo -TaskName VXvue_SRS_Spec_Automation | Format-List LastRunTime, LastTaskResult, NextRunTime
```

`LastTaskResult = 0`이면 성공, `1`이면 검증 실패(이 경우 지식파일 폴더는 변경되지 않습니다). 스케줄러에서 즉시 1회 실행:

```powershell
Start-ScheduledTask -TaskName VXvue_SRS_Spec_Automation
```

### 해제

```powershell
.\scripts\uninstall_task.ps1
```

두 작업을 모두 제거합니다.

### 인증 정보와 S4U

Polarion 토큰은 환경변수 `POLARION_TOKEN`으로만 전달합니다. S4U 로그온 방식에서도 사용자 환경변수를 읽을 수 있으며, 설정 로드 단계에서 토큰이 없으면 즉시 `ConfigError`로 종료되므로 **스케줄러 실행이 설정 로드를 통과했다는 것 자체가 토큰이 정상 인식됐다는 증거**입니다.

## Notification

매 실행 후 결과를 메일로 보냅니다. 성공이든 실패든 보냅니다 — 주간 자동화에서 가장 위험한 상황은 "실패했는데 아무도 모르는 것"이기 때문입니다.

메일 본문에 담기는 내용:

- 성공/실패 배지, 실행 소요 시간
- 수집 건수 (프로젝트별 `실제/예상`)
- 변경 요약 (변경 / 신규 / 삭제 / 동일 건수, 비교 기준 스냅샷)
- 생성된 PDF 목록과 페이지 수·용량
- 배포 결과 (지식파일 반영 개수, ORG 보관 개수, ORG 자동 정리 결과)
- 실패한 검증 항목 (있을 때만)
- 확인이 필요한 사항 (서식이 단순화된 SRS, 이미지 다운로드 실패 등)
- 변경 리포트·실행 로그 경로

**SRS 원문은 본문에 담지 않습니다.** 변경 리포트 첨부(`mail.attach_report`)는 기본 꺼져 있습니다 — 켜면 SRS 제목이 메일로 나갑니다.

**메일 발송 실패는 자동화 실패가 아닙니다.** SMTP 오류는 경고 로그만 남기고 종료 코드에 영향을 주지 않습니다 — 사양서는 이미 정상 반영되었을 수 있기 때문입니다.

## Installation

```bash
git clone https://github.com/hongmin3/vxvue-srs-spec-automation.git
cd vxvue-srs-spec-automation
pip install -r requirements.txt
playwright install chromium

cp config/config.example.yaml config/config.yaml   # 값 채우기
cp .env.example .env                                # POLARION_TOKEN 입력
```

요구사항:

- Windows (Task Scheduler 등록과 `taskkill` 기반 프로세스 트리 종료가 Windows 전용입니다)
- Python 3.11+
- Playwright Chromium (`playwright install chromium`)
- Polarion Personal Access Token (REST API v1 접근 권한)

동작 확인:

```bash
python -m pytest tests/ -q      # 단위 테스트
python main.py --dry-run        # 지식파일 폴더를 건드리지 않고 전체 파이프라인 점검
```

## Configuration

- `.env` - Polarion Personal Access Token (`POLARION_TOKEN`). 저장소에는 절대 커밋되지 않습니다(`.gitignore`).
- `config/config.yaml` - Polarion 호스트/프로젝트 ID, 수집 쿼리, 본문 언어 우선순위, PDF 분할 그룹, 산출물 경로. 실사용 값이 들어가므로 이 파일도 커밋되지 않으며, `config/config.example.yaml`을 참고해 직접 채웁니다.

렌더링 관련 설정(`render:` 블록):

| 키 | 기본값 | 설명 |
|---|---|---|
| `pdf_timeout_seconds` | 300 | PDF 렌더링 1회 시도의 시간제한. 정상 그룹은 대화형 40~70초, 스케줄러(저우선순위)에서는 더 오래 걸립니다. 폭주 케이스는 16분 이상이므로 이 값으로 걸러집니다. |
| `known_problem_srs` | (빈 목록) | 렌더링 폭주가 확인된 SRS의 `프로젝트/ID`. 등록하면 이분 탐색을 생략합니다(무효화 조건은 Engineering Highlight 참고). |

배포 관련 설정(`output:` 블록):

| 키 | 기본값 | 설명 |
|---|---|---|
| `knowledge_folder` | (필수) | 최종 PDF를 반영할 폴더 |
| `org_folder` | (빈 값) | 교체된 직전 세대 보관 폴더. 비우면 `<knowledge_folder와 같은 위치>/ORG` |

메일 알림 설정(`mail:` 블록):

| 키 | 기본값 | 설명 |
|---|---|---|
| `enabled` | false | 메일 알림 사용 여부 |
| `to` | (빈 값) | 수신자. 쉼표로 여러 명 |
| `host` / `port` / `user` / `from` | - | SMTP 접속 정보 |
| `use_starttls` | true | STARTTLS 사용 |
| `attach_report` | false | 변경 리포트 첨부(켜면 SRS 제목이 메일로 나감) |
| `credentials_ini` | (빈 값) | 이미 쓰는 다른 자동화의 `config.ini`(`[email]` 섹션) 경로. 비밀값을 이 프로젝트로 복사하지 않고 재사용할 때 |

자격증명 우선순위는 **환경변수 > `credentials_ini` > `config.yaml`** 입니다. 비밀값은 `config.yaml`에 적지 말고 `.env`에 두세요.

```dotenv
POLARION_TOKEN=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
MAIL_FROM=
MAIL_TO=
```

## Usage

```bash
python main.py                 # 전체 파이프라인 (수집 -> PDF -> Diff -> 리포트 -> 반영)
python main.py --crawl-only    # Polarion 수집 + Snapshot 저장만
python main.py --export-only   # 이미 저장된 오늘자 Snapshot으로 HTML/PDF만 재생성
python main.py --diff-only     # 오늘자 vs 이전 Snapshot Diff/리포트만 재생성
python main.py --force         # 오늘자 Snapshot이 있어도 다시 수집
python main.py --dry-run       # 지식파일 폴더 반영(archive/copy) 단계 생략

python main.py --recheck-known-problems
                              # 이미 렌더링 문제로 등록된 SRS도 정상 렌더링이
                              # 가능해졌는지 캐시를 무시하고 재확인

python main.py --catch-up     # 이번 주에 이미 수행한 기록이 있으면 아무것도 하지 않고
                              # 종료. PC 시작 시 트리거가 쓰는 모드

python main.py --since 2026-07-25
                              # 지정한 기준일부터 지금까지의 변경만 리포트 (PDF/배포/메일 없음)
```

### 운영 기준: PDF만 사용합니다

최종 산출물과 검증 기준은 **PDF 6개**입니다. 지식파일 폴더에 함께 존재할 수 있는 `.txt` 변환본은 이 자동화의 대상이 아니며 생성·갱신하지 않습니다. 검증(페이지 수, 내용 보존, Sanity Check)도 모두 PDF를 대상으로 수행합니다.

### 산출물 위치

| 경로 | 내용 |
|---|---|
| `output/<YYYY-MM-DD>/pdf/` | 생성된 사양서 PDF 6개 |
| `output/<YYYY-MM-DD>/html/` | PDF 변환 전 중간 HTML (재현·디버깅용) |
| `output/<YYYY-MM-DD>/reports/` | 변경 리포트 (`.md` / `.html`), `--since` 사용 시 기간 리포트도 같은 폴더에 생성 |
| `snapshots/<YYYY-MM-DD>/<project>/` | SRS 1건당 JSON 스냅샷 (Diff 기준 데이터) |
| `snapshots/render_problem_state.json` | 렌더링 문제 SRS 캐시 상태 |
| `<지식파일 폴더와 같은 위치>/ORG/<YYMMDD>/` | 교체된 **직전 세대** 사양서. 다음 실행이 검증을 통과하면 자동 삭제 |
| `logs/automation_<YYYYMMDD>.log` | 실행 로그 |
| `logs/last_run.json` | 주간 실행 기록(부팅 시 만회 판단용) |

지식파일 폴더 반영은 **검증을 모두 통과했을 때만** 수행됩니다. 하나라도 실패하면 기존 사양서를 교체하지 않고 종료 코드 1로 끝냅니다.

## Troubleshooting — 실패 시 점검 순서

실행이 실패하면(종료 코드 0이 아니거나 `LastTaskResult != 0`) 아래 순서로 확인합니다. **검증에 실패하면 기존 지식파일은 교체되지 않으므로, 실패 상태에서도 이전 사양서는 그대로 남아 있습니다.**

1. **로그부터 확인** — `logs/automation_<YYYYMMDD>.log`의 마지막 실행 구간. 검증 실패 항목은 `[ERROR] 검증 실패 [항목명]` 형태로 남습니다.

2. **`ConfigError: 환경변수 POLARION_TOKEN 가 설정되어 있지 않습니다`**
   → 토큰 미설정. 대화형은 `.env`, 스케줄러는 **사용자 환경변수**에 설정되어 있어야 합니다.

3. **`Polarion 접근 실패` (종료 코드 3)**
   → 토큰 만료/권한, VPN·네트워크, 호스트 설정을 확인합니다. 4xx는 재시도하지 않으므로 즉시 실패합니다.

4. **`srs_count_match` 실패**
   → Polarion이 알려준 예상 건수와 실제 수집 건수가 다릅니다. 수집 중 페이지네이션이 끊긴 경우이니 재실행합니다. 이 검증이 실패하면 **불완전한 사양서가 반영되는 것을 막기 위해** 반영 단계를 건너뜁니다.

5. **`pdf_nonzero` / `pdf_has_pages` 실패 = 특정 그룹 PDF 생성 실패**
   → 로그에서 그 그룹의 처리 경로를 확인합니다.
   - `이분 탐색에서 개별 문제 SRS가 발견되지 않았습니다` → 폭주가 아니라 **느린 그룹**입니다. 시간제한을 3배로 늘려 자동 재시도하며, 성공하면 `render.pdf_timeout_seconds` 상향을 권고하는 로그가 남습니다. 그래도 실패하면 이 값을 직접 올리세요.
   - **스케줄러에서만 실패한다면 작업 우선순위를 먼저 확인하세요.** `-Priority 4`가 아니면 Chromium 인쇄가 크게 느려집니다(Scheduler 섹션 참고).
   - `문제 SRS로 격리됨(렌더링 시간 초과)` → 신규 폭주 SRS가 발견된 것입니다. 해당 SRS는 서식만 단순화되어 문서에 남습니다. 로그가 안내하는 대로 `render.known_problem_srs`에 추가하면 다음 실행에서 이분 탐색(약 10분)을 생략합니다.

6. **페이지 수가 비정상적으로 많다 (수천~수만 페이지)**
   → Chromium 인쇄 페이지네이션 폭주입니다. 시간제한에 걸려 자동 격리되어야 정상입니다. 격리 없이 통과했다면 `render.pdf_timeout_seconds`가 너무 큽니다.

7. **`이미지 다운로드 실패` (WARNING)**
   → Rich Text가 `workitemimg:`로 참조하는 파일이 그 Work Item의 첨부 목록에 없는 경우입니다. 실행 실패로 처리하지 않으며, 해당 이미지만 PDF에서 빠집니다. Polarion 원본 데이터 확인이 필요합니다.

8. **변경 리포트가 전부 `new`로 나온다**
   → 비교할 이전 스냅샷이 없는 첫 실행입니다(`Previous Snapshot: (none - first run)`). 정상이며, 두 번째 실행일부터 증분이 나옵니다.

9. **렌더링 템플릿을 고쳤는데 서식 단순화가 그대로다**
   → 파이프라인 해시가 캐시 키에 포함되므로 자동 재확인됩니다. 강제로 다시 확인하려면 `python main.py --recheck-known-problems`.

### 재실행 시 유용한 플래그

Polarion을 다시 긁지 않고 특정 단계만 반복할 수 있습니다. 폭주 디버깅 중에는 이 조합이 특히 유용합니다.

```bash
python main.py --export-only    # 저장된 스냅샷으로 HTML/PDF만 재생성
python main.py --diff-only      # 리포트만 재생성
python main.py --dry-run        # 지식파일 폴더를 건드리지 않고 전 과정 점검
```

## Security

- ID/Password/Token/Cookie는 소스코드에 하드코딩하지 않습니다. Polarion 인증은 Personal Access Token을 환경변수(`.env`)로만 전달합니다.
- `.env`, 실사용 `config/config.yaml`, 수집된 SRS 원문(`snapshots/`), 생성된 사양서 PDF(`output/`, `archive/`), 실행 로그(`logs/`)는 모두 `.gitignore` 처리되어 저장소에 올라가지 않습니다.
- 로그에는 토큰/비밀번호가 포함될 수 있는 메시지를 자동으로 마스킹하는 필터가 적용되어 있습니다.

## Folder Structure

```text
vxvue-srs-spec-automation/
├─ src/            # 수집/정규화/분할/렌더링/Diff/검증/배포 모듈
├─ config/         # config.example.yaml (실 설정은 gitignore)
├─ scripts/        # Windows Task Scheduler 등록/해제 스크립트
├─ tests/          # pytest 단위 테스트
├─ output/         # 실행별 산출물 (html/pdf/reports) - gitignore
├─ archive/        # 교체 전 기존 사양서 백업 - gitignore
├─ snapshots/       # SRS 단위 JSON Snapshot + 렌더링 문제 상태 - gitignore
├─ logs/           # 실행 로그 - gitignore
├─ .env.example
├─ requirements.txt
└─ main.py
```

## Tech Stack

- Python
- Requests (Polarion REST API v1 클라이언트)
- BeautifulSoup4 (Rich Text 파싱/새니타이즈)
- Playwright (HTML → PDF 변환)
- pypdf (PDF 페이지 수 검증)
- PyYAML / python-dotenv (설정 관리)
- pytest (단위 테스트)
- Windows Task Scheduler (주간 자동 실행)
- subprocess + Windows `taskkill` (PDF 렌더링 프로세스 격리/시간제한/강제 종료)
