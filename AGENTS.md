# AGENTS.md

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

<!-- readme-guidance: start -->
## README 작성 기준

- 처음 보는 사람이 **무엇을 해주는 프로젝트인지, 왜 필요한지, 어떻게 시작하는지** 이해할 수 있게 쓴다.
- 첫 부분은 쉬운 한두 문장과 실제 사용 예로 설명한다. 전문 용어는 필요한 곳에서 풀어 쓴다.
- 준비 사항 → 설치 → 첫 실행 → 기대 결과 순서의 **빠른 시작**을 앞에 둔다. 확인한 명령만 적는다.
- 자세한 운영 규칙, 내부 구조, 검증 기록은 `docs/` 등 별도 문서에 두고 README에서 연결한다.
- README는 현재 기능과 사용법을 설명한다. 세션별 작업 경과나 수정 내역을 길게 나열하지 않는다.
- 구현과 README가 어긋나지 않게 함께 갱신한다. 미검증 기능·플랫폼·제한사항은 분명히 구분한다.
- 기존 프로젝트의 기능·설정·민감정보·고유 문서 체계를 보존한다. 문서 정리를 이유로 실행 동작을 바꾸지 않는다.
<!-- readme-guidance: end -->
