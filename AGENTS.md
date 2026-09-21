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
