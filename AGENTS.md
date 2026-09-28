# AGENTS.md

Follow `akela/PROTOCOL.md` for every task.

## Project Root 탐색 규칙

`src/`, `tests/`, `scripts/`, `config/` 등 이 프로젝트 하위 어디서 작업하든 먼저 상위로 `akela.json`을 탐색해 가장 가까운 Project Root를 식별하고 그 Root의 `knowledge/`·`akela/PROTOCOL.md`를 사용한다. 하위 디렉터리에 별도 `akela.json`/`knowledge/`를 만들지 않는다.

`scripts/find-project-root.ps1`을 사용하면 이 탐색을 자동화할 수 있다.

## 참고

- Knowledge: `knowledge/`
- Protocol: `akela/PROTOCOL.md`
- 설정: `akela.json`
- 사양서 소스 코드(`apps/srs-spec/src/`)는 이 저장소 작업 시 임의로 수정하지 않는다.
- 모든 앱의 `config/config.yaml`, `config.yaml`, `.env`, `IMPLEMENTATION_LOG.md`는 실사용 값/내부 전용 정보를 담고 있으므로 열람·인용하지 않는다(gitignore 대상).

## 통합 레이아웃

- 사양서 앱: `apps/srs-spec/`, 이슈 앱: `apps/issue-export/`.
- 두 앱 모두 Root의 단일 `akela.json`과 컨텍스트를 사용한다. 하위 앱에 별도 Akela Root를 만들지 않는다.
- 기존 compiled slice의 사양서 앱 상대 경로는 `apps/srs-spec/` 기준이다. Knowledge는 직접 편집하지 않는다.
- 공통 실행기는 `python run.py srs ...`와 `python run.py issues ...`이며 각 앱 작업 디렉터리에서 실행한다.
- 검증: `python -m pytest tests apps/srs-spec/tests -q`. 통합 검증 중 실수집·배포·메일을 실행하지 않는다.

## 사양 기반 개발 (SPEC)

<!-- spec-workflow: v1 -->

`SPEC.md`가 이 프로젝트가 **어떻게 동작해야 하는가**의 기준이다. 코드의 현재 동작은 사양이
아니다. 아래는 상위 기준이고, 실제 절차(계획·테스트·검증)는 기존 Skill을 그대로 쓴다.

**작업 시작 전** — 기능 추가·변경·버그 수정이면 먼저 `SPEC.md`에서 ① 관련 Requirement,
② 그 Requirement의 구현, ③ 관련 테스트, ④ 변경 영향 범위를 확인한다. 해당 Requirement가
없는 신규 기능은 **구현 전에** SPEC에 추가한다. typo, 주석, 문서만 바꾸는 작업은 예외다.

**구현** — 사양에 없는 기능을 임의로 추가하지 않는다. 요구사항이 불충분하거나 모순되면
추측으로 확정하지 말고 SPEC의 "13. 미확정 사항"에 `(TBD)` 또는 `확인 필요`로 남기고
사용자에게 알린다.

**변경 요청 판별** — 요청을 받으면 먼저 현재 SPEC · 요청 · 기존 구현 · 변경 의도를 대조해
둘 중 무엇인지 정한다.

- **사양 변경**: `SPEC.md` 수정 → 테스트 수정·추가 → 구현 → 검증 → `CHANGELOG.md`
- **기존 사양 미충족(버그)**: SPEC 유지 → 재현 테스트 → 코드 수정 → regression 확인 → `CHANGELOG.md`

버그를 고치려고 올바른 기존 사양을 바꾸지 않는다.

**완료 전** — `Requirement → 구현 → 테스트 → 실제 실행 결과`를 모두 확인한다. 테스트 PASS는
실제 동작 검증을 대체하지 않는다. 실행 가능한 프로젝트는 대표 실행 경로를 실제로 돌리고
출력을 읽는다.

**SPEC / CODE 불일치** — 조용히 맞추지 말고 다음 형식으로 보고한다.

```text
SPEC / CODE MISMATCH

Requirement:
Specification:
Current Implementation:
Difference:
Action: SPEC 수정 / CODE 수정 / 사용자 확인 필요
```

코드의 현재 상태를 정당화하려고 SPEC을 고치지 않는다.

**ID 규칙** — `REQ-<CATEGORY>-NNN`, `NFR-<CATEGORY>-NNN`, `TEST-<CATEGORY>-NNN`.
CATEGORY는 대문자·숫자, NNN은 세 자리. 한 번 부여한 ID는 재사용하거나 의미를 바꾸지 않고,
삭제한 ID를 다른 기능에 돌려쓰지 않는다. 추적은 테스트 쪽에 남긴다(예: 테스트 위에
`Validates: REQ-QUERY-001`). 검색용 주석을 모든 함수에 강제로 달지 않는다.

**문서 경계** — 같은 내용을 두 곳에 두지 않는다.

| 파일 | 담는 것 |
|---|---|
| `SPEC.md` | 현재 시스템이 어떻게 동작해야 하는가 |
| `CHANGELOG.md` | 무엇이 변경되었는가 |
| `progress.md` | 현재 작업이 어디까지 진행됐는가 |
| `knowledge/` | AI가 작업할 때 필요한 판단 규칙·맥락 |
| `README.md` | 사람이 설치하고 사용하는 방법 |
| `AGENTS.md` | AI가 따라야 하는 작업 규칙 |

SPEC 내용을 `knowledge/`에 복제하지 않는다. knowledge는 Requirement ID를 **참조**만 한다
(예: "REQ-EXPORT-001을 고칠 때 한글 파일명 encoding 회귀를 항상 확인한다").
