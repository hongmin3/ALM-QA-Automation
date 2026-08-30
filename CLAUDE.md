# CLAUDE.md

Follow `akela/PROTOCOL.md` for every task.

## Project Root 탐색 규칙

`src/`, `tests/`, `scripts/`, `config/` 등 이 프로젝트 하위 어디서 작업하든 먼저 상위로 `akela.json`을 탐색해 가장 가까운 Project Root를 식별하고 그 Root의 `knowledge/`·`akela/PROTOCOL.md`를 사용한다. 하위 디렉터리에 별도 `akela.json`/`knowledge/`를 만들지 않는다.

`scripts/find-project-root.ps1`을 사용하면 이 탐색을 자동화할 수 있다.

## 참고

- Knowledge: `knowledge/`
- Protocol: `akela/PROTOCOL.md`
- 설정: `akela.json`
- 소스 코드(`src/`)는 이 저장소 작업 시 임의로 수정하지 않는다.
- `config/config.yaml`, `.env`, `IMPLEMENTATION_LOG.md`는 실사용 값/내부 전용 정보를 담고 있으므로 열람·인용하지 않는다(gitignore 대상).
