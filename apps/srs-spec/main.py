#!/usr/bin/env python
"""VXvue / License Manager SRS 사양서 자동 최신화.

사용 예:
    python main.py                 # 전체 파이프라인 (수집 -> PDF -> Diff -> 리포트 -> 반영)
    python main.py --crawl-only    # Polarion 수집 + Snapshot 저장만
    python main.py --export-only   # 이미 저장된 오늘자 Snapshot으로 HTML/PDF만 재생성
    python main.py --diff-only     # 오늘자 vs 이전 Snapshot Diff + 리포트만 재생성
    python main.py --force         # 오늘자 Snapshot이 이미 있어도 다시 수집
    python main.py --dry-run       # 지식파일 폴더 반영(ORG 보관/복사) 단계만 생략
    python main.py --recheck-known-problems
                                   # 이미 문제로 등록된 SRS도 정상 렌더링 가능해졌는지 재확인
    python main.py --catch-up      # 이번 주에 이미 수행했으면 아무것도 하지 않고 종료
    python main.py --since 2026-07-25
                                   # 기준일부터 지금까지의 변경만 리포트 (PDF/배포/메일 없음)
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from time import monotonic

from src.collector import CollectStats, collect_project
from src.config import PROJECT_ROOT, ConfigError, load_config
from src.diff import diff_snapshots
from src.logging_setup import setup_logging
from src.notify import load_mail_settings, send_run_report
from src.partition import assign_file_groups
from src.period_report import build_period_report, fetch_changed_in_period, save_period_report
from src.polarion_client import PolarionApiError, PolarionClient
from src.problem_state import ProblemState
from src.publish import PublishResult, archive_and_publish
from src.render_recovery import render_group_pdf_with_recovery
from src.report import save_reports
from src.run_lock import LockHeldError, RunLock, lock_path
from src.run_marker import already_ran_this_week, write_run
from src.snapshot_store import (
    find_baseline_snapshot_date,
    find_previous_snapshot_date,
    list_snapshot_dates,
    load_snapshot,
    save_manifest,
    save_snapshot,
)
from src.util import build_pdf_filename, ensure_dir
from src.validate import validate_run


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VXvue/License Manager SRS 사양서 자동 최신화")
    p.add_argument("--crawl-only", action="store_true", help="Polarion 수집 + Snapshot 저장만 수행")
    p.add_argument("--export-only", action="store_true", help="오늘자 Snapshot으로 HTML/PDF만 재생성")
    p.add_argument("--diff-only", action="store_true", help="오늘자 vs 이전 Snapshot Diff/리포트만 재생성")
    p.add_argument("--force", action="store_true", help="오늘자 Snapshot이 있어도 다시 수집")
    p.add_argument("--dry-run", action="store_true", help="지식파일 폴더 반영 단계를 생략(ORG 보관/복사 안 함)")
    p.add_argument(
        "--no-mail",
        action="store_true",
        help="통합 자동화가 단일 요약 메일을 보낼 때 SRS 개별 메일을 생략",
    )
    p.add_argument(
        "--recheck-known-problems",
        action="store_true",
        help="config의 render.known_problem_srs 캐시를 무시하고 모든 SRS를 정상 렌더링부터 재확인",
    )
    p.add_argument(
        "--catch-up",
        action="store_true",
        help="이번 주에 이미 수행한 기록이 있으면 아무것도 하지 않고 종료 (PC 시작 시 트리거용)",
    )
    p.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="지정한 기준일과 현재를 비교하는 리포트만 생성 (PDF 생성/배포/메일 없음). "
        "예: --since 2026-07-25",
    )
    return p.parse_args()


def _send_pipeline_notification(args, config, summary: dict, report_path: Path) -> bool:
    if args.no_mail:
        return False
    return send_run_report(
        load_mail_settings(config.raw, PROJECT_ROOT),
        summary,
        report_path=report_path,
    )


def _load_all_records_from_disk(config, run_date: str, logger) -> list[dict]:
    all_records: list[dict] = []
    for project in config.projects:
        snap = load_snapshot(config.snapshots_dir, run_date, project.id)
        if not snap:
            logger.error("오늘자(%s) Snapshot이 없습니다: %s. 먼저 --crawl-only 또는 전체 실행을 하세요.", run_date, project.id)
            raise SystemExit(2)
        all_records.extend(snap.values())
    return all_records


def _build_summary(
    *,
    run_date: str,
    elapsed_seconds: float,
    collect_stats,
    all_records: list[dict],
    pdf_results,
    validation,
    diffs,
    previous_date: str | None,
    fallback_records: list[dict],
    publish_result,
    report_path,
    log_path,
) -> dict:
    """메일 본문에 쓸 실행 요약을 만든다. SRS 원문은 담지 않는다."""
    counts = {"new": 0, "changed": 0, "deleted": 0, "unchanged": 0}
    for d in diffs:
        if d.change_type in counts:
            counts[d.change_type] += 1

    if collect_stats:
        collected = ", ".join(f"{s.project_id} {s.total_items}/{s.expected_total}" for s in collect_stats)
    else:
        collected = f"{len(all_records)}건 (오늘자 Snapshot 재사용 - 재수집 안 함)"

    reason_text = {
        "known_problem_cache": "이미 확인된 렌더링 문제 - 본문 변경 없어 서식 단순화 유지",
        "known_problem_recheck": "본문 변경 후 재확인했으나 여전히 렌더링 문제 - 서식 단순화",
        "bisect_timeout": "렌더링 시간 초과로 신규 격리 - 서식 단순화",
    }
    warnings = [
        f"{rec.get('uid')} ({rec.get('title')}): {reason_text.get(rec.get('render_fallback_reason'), '서식 단순화')}"
        for rec in fallback_records
    ]

    failed_images = sum(1 for r in all_records for i in r.get("image_results", []) if not i.get("ok"))
    if failed_images:
        warnings.append(f"이미지 다운로드 실패 {failed_images}건 - 해당 이미지는 PDF에서 빠집니다(실행 실패는 아님).")
    if publish_result.skipped:
        warnings.append("지식파일 폴더 반영이 수행되지 않았습니다(검증 실패 또는 --dry-run).")

    purge_text = (
        f"{len(publish_result.purged_generations)}세대 / {publish_result.purged_files}개 파일 삭제"
        f" ({', '.join(publish_result.purged_generations)})"
        if publish_result.purged_generations
        else "정리 대상 없음"
    )

    minutes, seconds = divmod(int(elapsed_seconds), 60)
    return {
        "ok": validation.ok,
        "run_date": run_date,
        "duration": f"{minutes}분 {seconds}초",
        "collected": collected,
        "diff": {**counts, "previous": previous_date},
        "pdfs": [
            {"name": pr.path.name, "pages": pr.page_count, "size_mb": pr.size_bytes / 1024 / 1024}
            for pr in pdf_results
            if pr.ok
        ],
        "published": publish_result.published,
        "retained": publish_result.retained,
        "purge_text": purge_text,
        "failed_checks": [c["name"] for c in validation.checks if not c["passed"]],
        "warnings": warnings,
        "report_path": str(report_path),
        "log_path": str(log_path),
    }


def _run_since_report(config, args, logger, run_date: str) -> int:
    """기준일 → 현재 기간 변경 리포트만 생성한다 (PDF 생성/배포/메일 없음).

    "2차 검증이 끝난 7월 25일 이후 사양이 어떻게 바뀌었나"처럼 검증 회차 단위 구간을
    확인하는 용도다. 중간 이력은 보여주지 않고 '기준 시점 → 현재'만 낸다.
    """
    try:
        since = date.fromisoformat(args.since)
    except ValueError:
        logger.error("--since 날짜 형식이 잘못되었습니다: %r (YYYY-MM-DD 형식이어야 합니다)", args.since)
        return 2

    if since.isoformat() >= run_date:
        logger.error("--since 날짜(%s)는 오늘(%s)보다 앞선 날짜여야 합니다.", since.isoformat(), run_date)
        return 2

    current = _load_all_records_from_disk(config, run_date, logger)
    current_by_uid = {r["uid"]: r for r in current}

    # 기간 내 변경 목록은 Polarion에서 직접 조회한다 - 스냅샷 보관 여부와 무관하게
    # 임의 과거 날짜에 대해 정확한 목록을 얻을 수 있다(실측 확인).
    client = PolarionClient(
        host=config.host,
        token=config.token,
        verify_ssl=config.verify_ssl,
        timeout_seconds=config.timeout_seconds,
        request_interval_seconds=config.request_interval_seconds,
    )
    changed = fetch_changed_in_period(client, config.projects, since.isoformat(), run_date)

    # 기준 시점 본문은 그 시점 이하의 스냅샷이 있을 때만 만들 수 있다.
    baseline = find_baseline_snapshot_date(config.snapshots_dir, since.isoformat(), run_date)
    baseline_by_uid: dict[str, dict] = {}
    if baseline:
        for project in config.projects:
            baseline_by_uid.update(load_snapshot(config.snapshots_dir, baseline, project.id))
        if baseline != since.isoformat():
            logger.info(
                "요청 기준일 %s 에는 스냅샷이 없어, 그 이전 중 가장 늦은 %s 스냅샷을 본문 비교 기준으로 씁니다.",
                since.isoformat(),
                baseline,
            )
    elif list_snapshot_dates(config.snapshots_dir):
        # 요청 기준일 이하 스냅샷은 없지만, 보관된 가장 이른 스냅샷을 쓰면 그 이후에 일어난
        # 변경만큼은 본문 비교가 된다. 전부 '목록만'으로 떨어뜨리는 것보다 정보가 많고,
        # 어디까지가 비교 가능 구간인지는 리포트에 명시한다.
        earliest = [d for d in list_snapshot_dates(config.snapshots_dir) if d < run_date]
        if earliest:
            baseline = earliest[0]
            for project in config.projects:
                baseline_by_uid.update(load_snapshot(config.snapshots_dir, baseline, project.id))
            logger.warning(
                "%s 이하의 스냅샷이 없습니다. 보관된 가장 이른 스냅샷 %s를 부분 비교 기준으로 씁니다 "
                "- %s 이후 변경은 본문 비교가 되고, 그 이전 변경은 목록만 표시됩니다.",
                since.isoformat(),
                baseline,
                baseline,
            )
    if not baseline:
        logger.warning(
            "%s 이하의 스냅샷이 없어 '기준 시점 본문'은 만들 수 없습니다. "
            "변경된 SRS 목록과 현재 내용만 리포트합니다. (보관된 스냅샷: %s)",
            since.isoformat(),
            ", ".join(list_snapshot_dates(config.snapshots_dir)) or "없음",
        )

    report = build_period_report(
        since=since.isoformat(),
        until=run_date,
        changed=changed,
        current_by_uid=current_by_uid,
        baseline_by_uid=baseline_by_uid,
        baseline_snapshot=baseline,
        alm_host=config.host,
    )
    md_path, html_path = save_period_report(ensure_dir(config.base_dir / run_date / "reports"), report)

    logger.info(
        "기간 변경 리포트 완료 (%s -> %s): 변경 %d건 (본문 비교 가능 %d건, 변경 사실만 %d건)",
        report.since,
        report.until,
        len(report.changes),
        len(report.comparable),
        len(report.listed_only),
    )
    logger.info("리포트: %s", md_path)
    logger.info("리포트: %s", html_path)
    return 0


def _run_pipeline(config, args, logger, run_date: str, file_date: str) -> int:
    """CatchUp 판단부터 반영/알림까지 - 파이프라인 본체.

    이 함수 전체가 호출부(`main`)에서 RunLock으로 감싸진다. 정기 실행 작업과
    부팅 CatchUp 작업은 서로 다른 두 개의 Windows 작업 스케줄러 항목이라, 하나가
    예정 시각을 놓쳐 `StartWhenAvailable`로 지연 실행되는 동안 다른 하나가 부팅
    트리거로 겹쳐 실행될 수 있다(2026-09-21 실측 - `run_lock.py` 상단 설명 참고).
    """
    # PC 시작 시 트리거로 들어온 경우: 이번 주에 이미 수행했으면 아무것도 하지 않는다.
    # (예정 시각에 PC가 꺼져 있어 실행을 놓친 주만 여기서 만회된다)
    if args.catch_up:
        ran, previous = already_ran_this_week(config.logs_dir)
        if ran:
            logger.info(
                "--catch-up: 이번 주에 이미 수행했습니다(run_date=%s, ok=%s). 아무것도 하지 않고 종료합니다.",
                previous.get("run_date") if previous else "?",
                previous.get("ok") if previous else "?",
            )
            return 0
        logger.info("--catch-up: 이번 주 실행 기록이 없습니다. 놓친 실행을 지금 수행합니다.")

    logger.info("=== SRS 사양서 자동화 시작 (run_date=%s) ===", run_date)
    started_at = monotonic()

    out_dir = config.base_dir / run_date
    html_dir = ensure_dir(out_dir / "html")
    pdf_dir = ensure_dir(out_dir / "pdf")
    reports_dir = ensure_dir(out_dir / "reports")
    images_root = ensure_dir(config.snapshots_dir / run_date / "_images")

    all_records: list[dict] = []
    collect_stats: list[CollectStats] = []
    exit_code = 0

    try:
        if args.diff_only:
            all_records = _load_all_records_from_disk(config, run_date, logger)
        elif args.export_only:
            all_records = _load_all_records_from_disk(config, run_date, logger)
        else:
            existing_snapshot_present = all(
                bool(load_snapshot(config.snapshots_dir, run_date, p.id)) for p in config.projects
            )
            if existing_snapshot_present and not args.force:
                logger.info("오늘자 Snapshot이 이미 존재합니다. --force 없이 재수집을 건너뜁니다.")
                all_records = _load_all_records_from_disk(config, run_date, logger)
            else:
                client = PolarionClient(
                    host=config.host,
                    token=config.token,
                    verify_ssl=config.verify_ssl,
                    timeout_seconds=config.timeout_seconds,
                    request_interval_seconds=config.request_interval_seconds,
                )
                for project in config.projects:
                    logger.info("Polarion 접속 확인: %s", project.id)
                    client.ping(project.id)
                    logger.info("Polarion 접속 성공: %s", project.id)

                    stats = CollectStats(project_id=project.id)
                    records = collect_project(client, project, config, images_root, stats)
                    collect_stats.append(stats)
                    all_records.extend(records)
                    save_snapshot(config.snapshots_dir, run_date, project.id, records)

                save_manifest(
                    config.snapshots_dir,
                    run_date,
                    {
                        "run_date": run_date,
                        "generated_at": datetime.now().isoformat(),
                        "stats": [vars(s) for s in collect_stats],
                    },
                )

            if args.crawl_only:
                logger.info("--crawl-only: 수집/Snapshot 저장까지만 수행하고 종료합니다.")
                return 0

        # ---- Partition + Render + PDF ----
        pdf_results = []
        groups = []
        fallback_records: list[dict] = []
        if not args.diff_only:
            groups, partition_warnings = assign_file_groups(all_records, config)
            known_problem_srs = set(config.known_problem_srs)
            problem_state = ProblemState.load(config.snapshots_dir)
            if known_problem_srs and not args.recheck_known_problems:
                logger.info(
                    "이미 확인된 렌더링 문제 SRS %d건 - 본문 변경이 없으면 이분 탐색을 생략합니다: %s",
                    len(known_problem_srs),
                    sorted(known_problem_srs),
                )
            elif args.recheck_known_problems:
                logger.info("--recheck-known-problems: 문제 SRS 캐시를 무시하고 전부 정상 렌더링부터 재확인합니다.")

            for group in groups:
                if not group.records:
                    logger.warning("빈 그룹 (%s) - PDF를 생성하지 않습니다.", group.display_name)
                    continue
                project_label = group.records[0]["project_id"]
                pdf_filename = build_pdf_filename(config.filename_prefix, group.display_name, file_date)
                pdf_path = pdf_dir / pdf_filename

                pdf_result, isolated = render_group_pdf_with_recovery(
                    group,
                    project_label=project_label,
                    html_dir=html_dir,
                    pdf_path=pdf_path,
                    known_problem_srs=known_problem_srs,
                    problem_state=problem_state,
                    recheck_known=args.recheck_known_problems,
                    timeout_seconds=config.pdf_timeout_seconds,
                )
                pdf_results.append(pdf_result)
                fallback_records.extend(isolated)

            problem_state.save()

            if fallback_records:
                logger.warning(
                    "렌더링 시간 초과로 서식이 단순화된 SRS %d건: %s",
                    len(fallback_records),
                    [r["uid"] for r in fallback_records],
                )

            if args.export_only:
                ok_all = all(r.ok for r in pdf_results)
                logger.info("--export-only 완료. PDF 생성 성공 여부: %s", ok_all)
                return 0 if ok_all else 1
        else:
            partition_warnings = []

        # ---- Diff ----
        previous_date = find_previous_snapshot_date(config.snapshots_dir, run_date)
        current_by_uid = {r["uid"]: r for r in all_records}
        previous_by_uid: dict[str, dict] = {}
        if previous_date:
            for project in config.projects:
                previous_by_uid.update(load_snapshot(config.snapshots_dir, previous_date, project.id))

        diffs = diff_snapshots(previous_by_uid, current_by_uid)

        pdf_sanity = []
        for pr in pdf_results:
            status = "OK" if pr.ok else "FAIL"
            pdf_sanity.append(
                {"file": pr.path.name, "status": status, "detail": f"{pr.page_count} pages, {pr.size_bytes} bytes, err={pr.error}"}
            )
        for reason, label in (
            ("bisect_timeout", "(렌더링 시간 초과 - 이분 탐색으로 신규 격리)"),
            ("known_problem_cache", "(기존 확인된 렌더링 문제 - 본문 변경 없어 서식 단순화 유지)"),
        ):
            matched = [r for r in fallback_records if r.get("render_fallback_reason") == reason]
            if matched:
                pdf_sanity.append(
                    {
                        "file": label,
                        "status": "WARN",
                        "detail": ", ".join(f"{r['uid']} ({r.get('title')})" for r in matched),
                    }
                )

        md_path, html_path = save_reports(
            reports_dir,
            run_date,
            diffs,
            previous_date=previous_date,
            current_date=run_date,
            pdf_sanity=pdf_sanity,
            alm_host=config.host,
        )
        logger.info("변경 리포트 생성: %s / %s", md_path, html_path)

        if args.diff_only:
            return 0

        # ---- Validation ----
        validation = validate_run(
            collect_stats=collect_stats or [CollectStats(project_id=p.id, total_items=0, expected_total=0) for p in config.projects],
            all_records=all_records,
            pdf_results=pdf_results,
            expected_pdf_count=len(groups),
            partition_warnings=partition_warnings,
            assigned_records=[record for group in groups for record in group.records],
            required_pdf_names=[build_pdf_filename(config.filename_prefix, group.display_name, file_date)
                                for group in groups if group.records],
            previous_srs_count=len(previous_by_uid) if previous_date else None,
            min_expected_srs_ratio=config.min_expected_srs_ratio,
            require_all_pdfs=config.require_all_pdfs,
        )

        publish_result = PublishResult(skipped=True)
        if not validation.ok:
            logger.error("실행 성공 판정 실패 - 기존 지식파일 폴더는 변경하지 않습니다.")
            exit_code = 1
        elif args.dry_run:
            logger.info("--dry-run: 지식파일 폴더 반영을 생략합니다.")
        else:
            publish_result = archive_and_publish(
                knowledge_folder=config.knowledge_folder,
                generated_pdfs=[pr.path for pr in pdf_results if pr.ok],
                file_date=file_date,
                org_folder=config.org_folder,
            )

        # 이번 주 실행 기록을 남긴다. 성공/실패 모두 기록해, 실패한 주를 부팅 시
        # 트리거가 자동으로 다시 돌리지 않게 한다(실패는 메일로만 알린다).
        # --dry-run은 배포를 하지 않았으므로 기록하지 않는다.
        if not args.dry_run:
            write_run(config.logs_dir, run_date, ok=validation.ok)

        # ---- 결과 알림 ----
        # 알림 실패가 자동화 결과를 바꾸면 안 된다(사양서는 이미 반영되었을 수 있다).
        try:
            summary = _build_summary(
                run_date=run_date,
                elapsed_seconds=monotonic() - started_at,
                collect_stats=collect_stats,
                all_records=all_records,
                pdf_results=pdf_results,
                validation=validation,
                diffs=diffs,
                previous_date=previous_date,
                fallback_records=fallback_records,
                publish_result=publish_result,
                report_path=md_path,
                log_path=config.logs_dir / f"automation_{run_date.replace('-', '')}.log",
            )
            _send_pipeline_notification(args, config, summary, html_path)
        except Exception:
            logger.warning("결과 알림 처리 중 오류(자동화 결과에는 영향 없음): %s", traceback.format_exc())

        logger.info("=== SRS 사양서 자동화 종료 (exit_code=%d) ===", exit_code)
        return exit_code

    except PolarionApiError as exc:
        logger.error("Polarion 접근 실패: %s", exc)
        return 3
    except Exception:
        logger.error("예상치 못한 오류 발생:\n%s", traceback.format_exc())
        return 1


def main() -> int:
    args = parse_args()
    run_date = date.today().isoformat()
    file_date_placeholder = "temp"

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[설정 오류] {exc}", file=sys.stderr)
        return 2

    logger = setup_logging(config.logs_dir, run_date.replace("-", ""))
    file_date = date.today().strftime(config.filename_date_format)

    # 기간 변경 리포트만 뽑는 조회 모드. 배포/메일/마커를 건드리지 않고, PDF/snapshot을
    # 쓰지 않는 순수 조회라 파이프라인과 동시에 실행돼도 안전하다 - 아래 실행 락 대상에서
    # 제외한다.
    if args.since:
        try:
            return _run_since_report(config, args, logger, run_date)
        except PolarionApiError as exc:
            logger.error("Polarion 접근 실패: %s", exc)
            return 3
        except Exception:
            logger.error("기간 변경 리포트 생성 실패:\n%s", traceback.format_exc())
            return 1

    # 정기 실행 작업과 부팅 CatchUp 작업이 겹쳐 실행되는 것을 막는다 - 서로 다른 두 개의
    # Windows 작업 스케줄러 항목이라 MultipleInstances=IgnoreNew로는 막을 수 없다
    # (run_lock.py 참고).
    lock = RunLock(lock_path(config.logs_dir))
    try:
        lock.acquire()
    except LockHeldError as exc:
        logger.info("다른 실행이 이미 진행 중이라 이번 실행은 넘깁니다: %s", exc)
        return 0

    try:
        return _run_pipeline(config, args, logger, run_date, file_date)
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
