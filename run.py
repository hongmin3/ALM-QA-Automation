"""Run either ALM application in its own working directory."""
from __future__ import annotations

import subprocess
import sys
import os
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APPLICATIONS = {
    "srs": (ROOT / "apps" / "srs-spec", "main.py"),
    "issues": (ROOT / "apps" / "issue-export", "polarion_query_backup.py"),
    "observe": (ROOT, "observation.py"),
}


def menu_arguments() -> list[str] | None:
    print("\n" + "=" * 64)
    print("  ALM-QA-Automation  |  QA 작업 시작")
    print("=" * 64)
    print("  [1] 사양서 자동화     최신 사양서 / 변경 리포트")
    print("  [2] 이슈 내보내기     검색 결과를 PDF · HTML · Markdown으로")
    print("  [3] 관찰 분석         저장된 결과 비교 / 검토 후보 기록")
    print("  [0] 종료")
    print("-" * 64)
    print("  관찰 분석은 서버 접속·배포·메일 발송을 하지 않습니다.")
    choice = input("  실행할 작업 번호 > ").strip()
    if choice == "0":
        return None
    if choice == "1":
        print("\n  사양서 자동화")
        print("  [1] 최신 사양서 생성  | 배포·설정된 결과 메일 포함")
        print("  [2] 배포 없는 점검   | 서버 조회·로컬 저장·설정된 메일 가능")
        print("  [3] 기간 변경 리포트 | 서버 조회 포함, 배포·메일 없음")
        action = input("선택 [2]: ").strip() or "2"
        if action == "1":
            return ["srs"]
        if action == "2":
            return ["srs", "--dry-run"]
        if action == "3":
            since = input("기준일 (YYYY-MM-DD): ").strip()
            datetime.strptime(since, "%Y-%m-%d")
            return ["srs", "--since", since]
        raise ValueError("사양서 메뉴 번호가 올바르지 않습니다.")
    if choice == "2":
        print("\n  이슈 내보내기 — 실행별 폴더에 저장합니다.")
        print("  [1] 이슈 ID 입력")
        print("  [2] 검색 쿼리 입력")
        print("  [3] 저장된 검색 조건")
        print("  [4] 서버 연결 포함 점검")
        print("  [5] 로컬 환경만 점검 (서버 접속 없음)")
        action = input("선택 [1]: ").strip() or "1"
        args = ["issues"]
        if action in ("1", "2"):
            value = input("이슈 ID (쉼표로 구분): " if action == "1" else "검색 쿼리: ").strip()
            if not value:
                raise ValueError("이슈 ID 또는 검색 쿼리를 입력해 주세요.")
            args += ["-id" if action == "1" else "-query", value]
        elif action == "4":
            return args + ["--check"]
        elif action == "5":
            return args + ["--check-local"]
        elif action != "3":
            raise ValueError("이슈 메뉴 번호가 올바르지 않습니다.")
        return args + ["--timestamp", "--open"]
    if choice == "3":
        print("\n  관찰 분석 — 기존 수집 파일을 읽고 검토 후보만 저장합니다.")
        print("  하나 이상의 입력이 필요합니다. 불완전 수집 결과는 분석하지 않습니다.")
        current = input("  현재 SRS 날짜 폴더 (없으면 Enter) > ").strip().strip('"')
        previous = input("  이전 SRS 날짜 폴더 (없으면 Enter) > ").strip().strip('"') if current else ""
        issues = input("  이슈 manifest.json 경로 (없으면 Enter) > ").strip().strip('"')
        if not current and not issues:
            raise ValueError("SRS 폴더 또는 이슈 manifest 경로가 필요합니다.")
        args = ["observe"]
        for flag, value in (("--srs-current", current), ("--srs-previous", previous), ("--issues", issues)):
            if value:
                args += [flag, value]
        return args
    raise ValueError("메뉴 번호가 올바르지 않습니다.")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--launcher-env"]:
        try:
            args = json.loads(os.environ.pop("ALM_QA_LAUNCH_ARGS"))
            if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                raise ValueError("Expected a string argument list")
        except (KeyError, ValueError) as exc:
            print(f"Invalid launcher arguments: {exc}", file=sys.stderr)
            return 2
    if args == ["--menu"]:
        try:
            args = menu_arguments()
        except (ValueError, EOFError) as exc:
            print(f"입력 오류: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130
        if args is None:
            return 0
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python run.py {srs|issues|observe} [application arguments]")
        print("  python run.py srs --help")
        print("  python run.py issues --help")
        print("  python run.py observe --help")
        print("Relative application paths are resolved in apps/srs-spec or apps/issue-export.")
        print("Observation paths are resolved from the project root.")
        return 0
    mode = args.pop(0)
    if mode not in APPLICATIONS:
        print(f"Unknown application: {mode}. Choose srs, issues or observe.", file=sys.stderr)
        return 2
    directory, script = APPLICATIONS[mode]
    print("\n" + "-" * 64, flush=True)
    print(f"  실행 작업 : {mode}\n  작업 폴더 : {directory}", flush=True)
    print("-" * 64, flush=True)
    try:
        code = subprocess.call([sys.executable, str(directory / script), *args], cwd=directory)
        labels = {0: "정상 종료", 1: "실패 — 상세 오류를 확인해 주세요", 2: "입력 또는 설정 확인 필요", 3: "서버 접근 실패", 4: "부분 완료 — 누락·실패 항목 확인 필요", 130: "사용자 중단"}
        print("\n" + "=" * 64)
        print(f"  결과 : {labels.get(code, '비정상 종료')}  (종료 코드 {code})")
        print("  주의 : 종료 코드만으로 메일 도착이나 전체 수집을 보장하지 않습니다.")
        print("=" * 64)
        return code
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"Cannot start {mode}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
