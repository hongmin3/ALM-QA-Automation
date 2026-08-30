# Architecture

Polarion ALM의 SRS(Software Requirements Specification) Work Item을 주 단위로 수집해 QA용 PDF 사양서와 변경 리포트를 생성하는 파이프라인. 규모: SRS 500건 이상, PDF 6개로 분할.

## 전체 흐름
<!-- akela: id=pipeline-flow -->

```
Polarion ALM (REST API)
  -> SRS Collector (계층/커스텀 필드/이미지/첨부/댓글 수집)
  -> Rich Text 정규화 (이미지 로컬화, 참조 링크 복원, XSS 새니타이즈)
  -> Normalized Snapshot (SRS 1건당 JSON 파일 1개)
  -> Stable Partitioning (같은 SRS는 항상 같은 파일에 남도록 고정 배정)
  -> HTML Renderer -> PDF Generator (5~6개 문서로 분할)
  -> Previous Snapshot Diff -> Change Report (HTML/Markdown)
```

## 모듈별 역할 (src/)
<!-- akela: id=module-roles -->

- `polarion_client.py` — Polarion REST API v1 클라이언트. Personal Access Token 인증, 페이지네이션, 첨부 다운로드 담당. 4xx 응답은 재시도하지 않고 즉시 실패 처리.
- `collector.py` — SRS Work Item 수집 및 정규화된 레코드 생성.
- `richtext.py` — Rich Text 새니타이즈, 이미지 로컬화, Work Item 참조 링크 복원. Polarion 내부 참조는 원래 브라우저 JS가 채우는 빈 placeholder라서, 대상 항목 제목을 조회해 실제 링크 텍스트로 복원해야 함.
- `snapshot_store.py` — SRS 1건당 JSON 파일로 Snapshot 저장/조회 (git-friendly 포맷).
- `partition.py` — Stable Partitioning. 모듈(oldId) 기준으로 파일 그룹을 고정 배정해, 매주 항목이 추가돼도 기존 SRS는 항상 같은 PDF 파일에 남는다.
- `render.py` — 표준 HTML 렌더링. 시스템 라벨은 영어로 고정하고 SRS 본문은 원문 언어를 유지한다(언어 혼재 방지).
- `pdf.py` / `pdf_worker.py` — Playwright 기반 PDF 변환. 별도 프로세스로 격리 실행하며 시간제한을 둔다.
- `render_recovery.py` — 렌더링 시간 초과 시 이분 탐색으로 문제 SRS를 자동 격리·복구하고, 단순히 느린 그룹은 시간제한을 늘려 재시도한다. (자세한 배경은 `known-issues.md` 참고)
- `problem_state.py` — 렌더링 문제 SRS 캐시. 본문 해시 + 렌더링 파이프라인 해시 기반으로 무효화 조건을 관리한다.
- `diff.py` — Snapshot 간 SRS 단위 구조적 Diff. PDF 텍스트 비교가 아니라 SRS ID를 Key로 신규/삭제/변경/동일을 구분한다.
- `report.py` — 변경 리포트(HTML/Markdown) 생성.
- `validate.py` — 실행 성공 판정 (수집 개수 일치, 중복 ID 없음, PDF 무결성 등).
- `publish.py` — 검증을 모두 통과했을 때만 직전 세대를 ORG로 보관하고 신규 산출물을 반영. 지난 세대 자동 정리도 담당.
- `notify.py` — 실행 결과 메일 알림. 발송 실패는 자동화 실패로 간주하지 않는다.
- `run_marker.py` — 주간 실행 기록. 부팅 시 만회 실행(catch-up)의 중복 실행 방지에 사용.

## 핵심 설계 원칙
<!-- akela: id=design-principles -->

- **서식 보존** — 취소선/밑줄/Bold/표/색상 등 사양 개정 판단에 필요한 서식은 평탄화하지 않는다.
- **구조적 Diff** — SRS ID를 Key로 신규/삭제/변경/동일을 구분하고, Status/Description/이미지/첨부/링크/댓글/서식 변경까지 세분화한다.
- **검증 통과 시에만 반영** — SRS 개수 불일치, 중복 ID, PDF 생성 실패 등 하나라도 있으면 기존 배포본을 교체하지 않는다.
- **PDF만이 운영 기준 산출물** — 지식파일 폴더에 함께 있을 수 있는 `.txt` 변환본은 이 자동화의 생성·검증 대상이 아니다.

## 산출물 위치
<!-- akela: id=output-locations -->

| 경로 | 내용 |
|---|---|
| `output/<YYYY-MM-DD>/pdf/` | 생성된 사양서 PDF 6개 |
| `output/<YYYY-MM-DD>/html/` | PDF 변환 전 중간 HTML (재현·디버깅용) |
| `output/<YYYY-MM-DD>/reports/` | 변경 리포트(.md/.html), `--since` 기간 리포트도 동일 폴더 |
| `snapshots/<YYYY-MM-DD>/<project>/` | SRS 1건당 JSON 스냅샷 (Diff 기준 데이터) |
| `snapshots/render_problem_state.json` | 렌더링 문제 SRS 캐시 상태 |
| `<지식파일 폴더와 같은 위치>/ORG/<YYMMDD>/` | 교체된 직전 세대 사양서 |
| `logs/automation_<YYYYMMDD>.log` | 실행 로그 |
| `logs/last_run.json` | 주간 실행 기록 (부팅 시 만회 판단용) |

## Tech Stack
<!-- akela: id=tech-stack -->

Python, Requests(Polarion REST API v1), BeautifulSoup4(Rich Text 파싱), Playwright(HTML→PDF), pypdf(페이지 수 검증), PyYAML/python-dotenv(설정), pytest, Windows Task Scheduler, subprocess + Windows `taskkill`(프로세스 트리 강제 종료).
