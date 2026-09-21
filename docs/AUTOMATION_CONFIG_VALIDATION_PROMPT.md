# 통합 자동화 설정 검증 개선 요청 프롬프트

아래 내용을 `ALM-QA-Automation` 프로젝트의 새 작업에 그대로 전달한다.

---

`ALM-QA-Automation`의 통합 자동화 설정 검증에서 남아 있는 낮은 위험도 결함 두 가지를 구현·검증해줘. 분석이나 제안으로 끝내지 말고, 현재 SPEC·코드·테스트를 확인한 뒤 테스트 우선으로 수정하고 전체 회귀 검증까지 완료해줘.

## 목표

잘못된 `automation.yaml`이 로드 단계에서 즉시, 명확하고 안전하게 거부되도록 다음 두 계약을 고정한다.

1. `email.max_attempts`와 `email.retry_minutes`의 항목 수가 정확히 일치해야 한다.
2. `priority.notify_priorities`는 YAML 목록만 허용하며 문자열을 포함한 다른 타입은 거부해야 한다.

현재 공개 기본 설정은 다음과 같으며 정상 입력으로 계속 통과해야 한다.

```yaml
email:
  max_attempts: 3
  retry_minutes: [15, 60, 240]

priority:
  notify_priorities: ["CRITICAL", "HIGH", "MEDIUM"]
```

## 현재 확인된 문제

- `max_attempts: 4`와 `retry_minutes: [15, 60, 240]` 조합이 설정 로더를 통과한 뒤 메일 처리 시점에 실패할 수 있다. 외부 수집·상태 변경·SMTP 시도 전에 설정 로더가 거부해야 한다.
- `notify_priorities: "HIGH"`가 문자열의 각 문자를 순회해 문자 집합처럼 변환될 수 있다. 문자열을 목록처럼 해석하지 말고 설정 오류로 처리해야 한다.

## 구현 요구사항

1. 먼저 `SPEC.md`의 REQ-AUTO-003과 관련 추적성, `automation_core/config.py`, `automation_core/email.py`, `automation.example.yaml`, 기존 설정 테스트를 확인해 이 작업이 기존 사양 미충족인지 사양 보완인지 판정해줘.
2. 합성 임시 YAML을 사용하는 실패 테스트를 먼저 추가하고 RED를 확인한 뒤 구현해줘. 실사용 `automation.yaml`, 각 앱의 `config.yaml`, `.env`, `IMPLEMENTATION_LOG.md`는 열람하거나 출력하지 마.
3. `email.retry_minutes`는 YAML 목록이어야 하며 각 값은 양의 정수여야 한다. `email.max_attempts`는 양의 정수여야 하고 `len(retry_minutes) == max_attempts`가 아니면 `ValueError`로 거부해줘. 불리언을 정수로 허용하거나 실수·숫자 문자열을 조용히 정수로 바꾸지 마.
4. `priority.notify_priorities`는 YAML 목록만 허용해줘. 문자열, 매핑, 숫자 등은 `ValueError`로 거부하고, 목록 항목의 기존 대문자 정규화와 중복 제거 동작은 유지해줘. 빈 목록이 현재 의도된 “후보 알림 없음” 설정이라면 계속 허용하고, 코드·사양 근거가 다르면 임의로 결정하지 말고 불일치를 보고해줘.
5. 오류 메시지에는 `email.max_attempts`, `email.retry_minutes`, `priority.notify_priorities`처럼 잘못된 설정 경로가 드러나야 한다. 설정값 전체, 토큰, 주소, 비밀번호는 출력하지 마.
6. `automation.py --check-schema`와 일반 실행이 외부 프로세스나 SMTP보다 먼저 같은 설정 오류를 반환하는 기존 경계를 유지해줘. `automation_core/email.py`의 방어적 검사는 제거하거나 약화하지 마.
7. 관련 없는 수집·연계·렌더링·스케줄러 동작, 공개 기본값, 종료 코드, 설정 파일 경로는 변경하지 마.

## 필수 테스트

최소한 다음 사례를 독립적으로 검증해줘.

- `max_attempts: 3`, `retry_minutes: [15, 60, 240]` → 성공.
- `max_attempts: 4`, 재시도 값 3개 → 로드 단계 실패.
- `max_attempts: 2`, 재시도 값 3개 → 로드 단계 실패.
- `retry_minutes`가 문자열·매핑·빈 목록이거나 0/음수/불리언/실수 항목을 포함 → 로드 단계 실패.
- `max_attempts`가 0/음수/불리언/실수/숫자 문자열 → 로드 단계 실패.
- `notify_priorities: ["critical", "HIGH", "HIGH"]` → `{"CRITICAL", "HIGH"}`로 정규화.
- `notify_priorities: []` → 기존 의도와 일치하는 결과.
- `notify_priorities: "HIGH"`, 숫자, 매핑 → 로드 단계 실패.
- 잘못된 설정으로 `automation.py --check-schema` 실행 → 종료 코드 2, 민감값 비노출, 자식 프로세스·SMTP 호출 없음.

가능하면 설정 전용 테스트 파일 `tests/test_automation_config.py`에 계약을 모으고, 기존 테스트 구조가 더 적절하면 그 근거를 완료 보고에 적어줘.

## 문서와 검증

- 동작 계약이 SPEC에 충분히 명시되지 않았다면 REQ-AUTO-003의 의미를 바꾸지 않는 범위에서 검증 규칙을 보완하고 `CHANGELOG.md`를 갱신해줘.
- 공개 예시는 정상 계약과 계속 일치해야 한다.
- 관련 테스트를 먼저 실행한 뒤 전체 `python -m pytest tests apps/srs-spec/tests -q`를 실행해줘.
- `node C:\Users\2024980\Documents\자동화\tools\spec-lint.js --root=C:\Users\2024980\Documents\자동화`와 `git diff --check`도 확인해줘.
- 실제 Polarion 조회, 문서 배포, SMTP 발송, 예약 작업 실행은 수행하지 마.

## 완료 조건

- 두 잘못된 설정이 모두 설정 로드 단계에서 재현 가능하게 차단된다.
- 공개 기본 설정과 기존 정상 사용자 설정의 의미가 유지된다.
- 관련 테스트와 전체 회귀 테스트가 통과한다.
- SPEC·CHANGELOG·테스트·구현의 추적성이 맞는다.
- 최종 보고에 변경 파일, RED→GREEN 증거, 전체 테스트 결과, 미검증 운영 항목을 간결하게 남긴다.

저장소에서 확인 가능한 내용은 먼저 조사하고, 결과에 중요한 모호성만 질문해줘.
