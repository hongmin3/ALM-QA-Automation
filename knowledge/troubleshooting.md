# Troubleshooting

실행이 실패하면(종료 코드 0이 아니거나 `LastTaskResult != 0`) 아래 순서로 확인한다. **검증에 실패하면 기존 지식파일은 교체되지 않으므로, 실패 상태에서도 이전 사양서는 그대로 남아 있다** — 급하게 대응하지 않아도 된다.

## 점검 순서
<!-- akela: id=check-order -->

1. **로그부터 확인** — `logs/automation_<YYYYMMDD>.log`의 마지막 실행 구간. 검증 실패 항목은 `[ERROR] 검증 실패 [항목명]` 형태로 남는다.
2. **`ConfigError: 환경변수 POLARION_TOKEN 가 설정되어 있지 않습니다`** → 토큰 미설정. 대화형 실행은 `.env`, 스케줄러 실행은 **사용자 환경변수**에 설정되어 있어야 한다.
3. **`Polarion 접근 실패` (종료 코드 3)** → 토큰 만료/권한, VPN/네트워크, 호스트 설정을 확인한다. 4xx 응답은 재시도하지 않고 즉시 실패한다.
4. **`srs_count_match` 실패** → Polarion이 알려준 예상 건수와 실제 수집 건수가 다른 경우. 수집 중 페이지네이션이 끊긴 것이므로 재실행한다. 이 검증이 실패하면 불완전한 사양서 반영을 막기 위해 반영 단계를 건너뛴다.
5. **`pdf_nonzero` / `pdf_has_pages` 실패 (특정 그룹 PDF 생성 실패)** → 로그에서 해당 그룹의 처리 경로를 확인.
   - `이분 탐색에서 개별 문제 SRS가 발견되지 않았습니다` → 폭주가 아니라 단순히 **느린 그룹**. 시간제한을 3배로 늘려 자동 재시도하며, 성공하면 `render.pdf_timeout_seconds` 상향을 권고하는 로그가 남는다. 그래도 실패하면 이 값을 직접 올린다.
   - **스케줄러에서만 실패한다면 작업 우선순위를 먼저 확인** (`-Priority 4`가 아니면 Chromium 인쇄가 느려짐 — `scheduler-operations.md` 참고).
   - `문제 SRS로 격리됨(렌더링 시간 초과)` → 신규 폭주 SRS 발견. 해당 SRS는 서식만 단순화되어 문서에 남는다. 로그 안내대로 `render.known_problem_srs`에 추가하면 다음 실행에서 이분 탐색(약 10분)을 생략한다.
6. **페이지 수가 비정상적으로 많다 (수천~수만 페이지)** → Chromium 인쇄 페이지네이션 폭주. 시간제한에 걸려 자동 격리되어야 정상. 격리 없이 통과했다면 `render.pdf_timeout_seconds`가 너무 크다는 뜻.
7. **`이미지 다운로드 실패` (WARNING)** → Rich Text가 `workitemimg:`로 참조하는 파일이 해당 Work Item의 첨부 목록에 없는 경우. 실행 실패로 처리하지 않으며 해당 이미지만 PDF에서 빠진다. Polarion 원본 데이터 확인이 필요하다.
8. **변경 리포트가 전부 `new`로 나온다** → 비교할 이전 스냅샷이 없는 첫 실행(`Previous Snapshot: (none - first run)`). 정상이며, 두 번째 실행일부터 증분이 나온다.
9. **렌더링 템플릿을 고쳤는데 서식 단순화가 그대로다** → 파이프라인 해시가 캐시 키에 포함되므로 보통은 자동 재확인된다. 강제로 다시 확인하려면 `python main.py --recheck-known-problems`.

## 재실행 시 유용한 플래그
<!-- akela: id=useful-rerun-flags -->

Polarion을 다시 긁지 않고 특정 단계만 반복할 수 있다. 폭주 디버깅 중 특히 유용.

```bash
python main.py --export-only    # 저장된 스냅샷으로 HTML/PDF만 재생성
python main.py --diff-only      # 리포트만 재생성
python main.py --dry-run        # 지식파일 폴더를 건드리지 않고 전 과정 점검
```

## 자주 묻는 상황 (FAQ)
<!-- akela: id=faq -->

**결과 메일이 안 왔다** → 메일 발송 실패는 자동화를 실패시키지 않으므로 사양서는 정상 생성됐을 수 있다. `logs/automation_<날짜>.log`에서 `메일 발송 실패` 문자열을 찾는다.

**월요일에 PC가 꺼져 있었다** → 그대로 두면 된다. PC를 켜고 5분 뒤 만회 실행이 자동으로 돈다. 이미 그 주에 실행했다면 아무 일도 일어나지 않는다.

**메일에 "실패"라고 왔다** → 기존 사양서는 교체되지 않았으므로 당장 급하지 않다. 메일의 "실패한 검증 항목"을 보고 위 점검 순서대로 확인한다. 대부분 `python main.py` 재실행으로 해결된다.

**이전 버전 사양서를 다시 보고 싶다** → 지식파일 폴더와 같은 위치의 `ORG/<날짜>/`에 직전 세대가 있다. 다음 실행이 성공하면 자동 삭제되므로, 오래 보관하려면 다른 곳으로 복사해 둔다.

**특정 기간(예: 2차 검증 종료일) 이후 뭐가 바뀌었는지 확인하고 싶다** → `python main.py --since <기준일>` 실행 후 `output/<오늘>/reports/SRS_Period_Report_*.html`을 브라우저로 연다. 변경된 SRS 목록과 ALM 바로가기 링크가 있다.

**특정 SRS의 서식이 깨져 보인다 (사양서5가 이상하다 등)** → 렌더링 폭주를 일으키는 SRS는 내용·이미지는 보존한 채 서식만 단순화해서 넣는다. 어떤 SRS가 그랬는지는 메일의 "확인이 필요한 사항"과 변경 리포트에 명시된다. 원인은 `known-issues.md` 참고.

## Security 관련 체크
<!-- akela: id=security-checks -->

- ID/Password/Token/Cookie는 소스코드에 하드코딩하지 않는다. Polarion 인증은 PAT를 `.env` 환경변수로만 전달.
- `.env`, 실사용 `config/config.yaml`, 수집된 SRS 원문(`snapshots/`), 생성된 PDF(`output/`, `archive/`), 실행 로그(`logs/`)는 모두 gitignore 대상.
- 로그에는 토큰/비밀번호가 포함될 수 있는 메시지를 자동 마스킹하는 필터가 적용되어 있다.
