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
}


def menu_arguments() -> list[str] | None:
    print("\nALM-QA-Automation\n  1. 사양서 자동화\n  2. 이슈 내보내기\n  0. 종료")
    choice = input("선택: ").strip()
    if choice == "0":
        return None
    if choice == "1":
        print("1. 생성·배포·결과 메일  2. 배포 없는 점검  3. 기간별 변경 리포트")
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
        print("1. 이슈 ID  2. 검색 쿼리  3. 저장된 검색 조건  4. 실행 환경 점검")
        action = input("선택 [1]: ").strip() or "1"
        args = ["issues"]
        if action in ("1", "2"):
            value = input("이슈 ID (쉼표로 구분): " if action == "1" else "검색 쿼리: ").strip()
            if not value:
                raise ValueError("이슈 ID 또는 검색 쿼리를 입력해 주세요.")
            args += ["-id" if action == "1" else "-query", value]
        elif action == "4":
            return args + ["--check"]
        elif action != "3":
            raise ValueError("이슈 메뉴 번호가 올바르지 않습니다.")
        return args + ["--timestamp", "--open"]
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
        print("Usage: python run.py {srs|issues} [application arguments]")
        print("  python run.py srs --help")
        print("  python run.py issues --help")
        print("Relative application paths are resolved in apps/srs-spec or apps/issue-export.")
        return 0
    mode = args.pop(0)
    if mode not in APPLICATIONS:
        print(f"Unknown application: {mode}. Choose srs or issues.", file=sys.stderr)
        return 2
    directory, script = APPLICATIONS[mode]
    try:
        return subprocess.call([sys.executable, str(directory / script), *args], cwd=directory)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"Cannot start {mode}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
