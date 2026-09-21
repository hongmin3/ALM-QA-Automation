# ALM-QA-Automation

Polarion ALM 사양서 자동화와 이슈 내보내기를 하나의 프로젝트에서 관리합니다.

## 실행

`실행하기.bat`을 더블클릭하면 사양서 생성, 배포 없는 점검, 기간별 변경 리포트, 이슈 내보내기를 선택할 수 있습니다. 사양서 생성은 기존 설정에 따라 배포와 결과 메일 발송까지 수행합니다.

메뉴에서 이슈 내보내기는 실행 시각별 폴더로 저장해 이전 결과를 보존합니다. 이슈의 실행 환경 점검은 서버 연결 확인을 포함합니다.

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py srs --help
python run.py srs --since 2026-09-01
python run.py issues --help
python run.py issues -id SAMPLE-101 --timestamp --open
```

공통 실행기는 인자를 각 기능에 그대로 전달하고 종료 코드를 반환합니다. 상대 경로 인자는 해당 앱 폴더 기준입니다. 프로젝트 밖에서 실행할 때도 `run.py`의 전체 경로를 지정하면 됩니다.

## 구조와 설정

| 경로 | 역할 |
| --- | --- |
| `apps/srs-spec/` | 사양서 수집, 스냅샷 비교, PDF 생성, 검증 후 배포 |
| `apps/issue-export/` | 검색 조건 또는 ID 기반 PDF/HTML/Markdown 내보내기 |
| `run.py`, `run.ps1`, `실행하기.bat` | 공통 실행기와 메뉴 |
| `akela.json`, `akela/`, `knowledge/` | 통합 프로젝트의 단일 Akela 컨텍스트 |
| `tests/` | 통합 실행기 테스트 |

두 앱의 설정 형식은 유지합니다. SRS는 `apps/srs-spec/config/config.yaml`과 앱 폴더의 `.env`를 사용합니다. 이슈 내보내기는 `apps/issue-export/config.yaml`을 사용합니다. 인증은 기존 `POLARION_TOKEN` 환경변수를 사용하며, 실제 설정과 산출물은 Git에 포함하지 않습니다.

- [사양서 상세 사용법](apps/srs-spec/README.md)
- [이슈 내보내기 상세 사용법](apps/issue-export/README.md)
- [통합 검증 결과와 검증 범위](docs/INTEGRATION_VALIDATION.md)
- [완전 자동화 제안 및 기존 기능 개선점](docs/AUTOMATION_ROADMAP.md)

상세 문서의 앱 상대 경로와 기존 실행 명령은 해당 앱 폴더에서 사용합니다. 사양서 문서의 이전 최상위 폴더명은 `ALM-QA-Automation/apps/srs-spec`으로 대체해서 읽습니다.

## 예약 실행

기존 `VXvue_SRS_Spec_Automation` 및 `_CatchUp` 작업 이름과 실행 정책을 유지합니다. 작업 디렉터리는 통합 폴더의 `apps/srs-spec`입니다. 신규 설치는 앱의 `scripts/install_task.ps1`을 사용합니다. 이슈 내보내기는 수동 실행입니다.

## 테스트

```powershell
python -m pytest tests apps/srs-spec/tests -q
```

통합은 기존 두 저장소의 커밋을 다시 쓰지 않고 병합했습니다. 이전 폴더와 Git bundle은 복구용으로 보존하며, 이후 변경은 이 통합 폴더에서만 수행합니다. 운영 환경의 실데이터와 설정은 로컬에만 유지됩니다.
