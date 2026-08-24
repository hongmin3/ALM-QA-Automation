"""임의 기간 SRS 변경 리포트 ("7월 25일 대비 지금 무엇이 바뀌었나").

주간 리포트는 '직전 스냅샷 대비'만 보여준다. 그런데 검증 회차는 주 단위로 끊기지
않는다 - 2차 검증이 7월 25일에 끝났고 3차 검증을 시작한다면, 그 사이 전체 기간의
변경을 한 번에 봐야 한다.

## 무엇을 어디서 가져오는가 (실측 확인 결과)

- **변경된 SRS 목록**: Polarion 쿼리 `updated:[YYYYMMDD TO YYYYMMDD]`.
  이 서버에서 200으로 정상 동작하는 것을 확인했다. 스냅샷 보관 여부와 무관하게
  **임의 과거 날짜**에 대해 정확한 목록을 얻을 수 있다.
- **현재 내용**: 오늘자 스냅샷.
- **기준 시점 내용**: 그 시점 이하의 스냅샷이 있으면 그것을 쓴다.
  **없으면 '이전 내용'을 만들어낼 수 없다** - 이 서버의 REST API는 work item
  revision을 노출하지 않는다(`/workitems/{id}/revisions` → 404,
  `?revision=N` → 404). 없는 이력을 추정해서 채우지 않고, 리포트에 그대로
  "기준 시점 본문 없음"으로 표시한다.

그래서 리포트는 SRS를 두 부류로 나눠 보여준다.

1. **이전 → 현재 비교 가능** (기준 스냅샷에 존재) - 본문 차이를 그대로 보여준다.
2. **변경 사실만 확인** (기준 스냅샷보다 앞선 기간의 변경) - 언제 바뀌었는지와
   현재 내용을 보여주고, 그 시점 본문은 당시 배포된 사양서 PDF를 참조하도록 안내한다.
"""
from __future__ import annotations

import html as html_lib
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .diff import SrsDiff, diff_snapshots
from .links import workitem_url
from .report import MAX_ROWS_PER_SRS, _before_after_rows, _md_cell

logger = logging.getLogger("srs_automation")


@dataclass
class PeriodChange:
    uid: str
    id: str
    project_id: str
    title: str
    updated: str | None
    comparable: bool
    diff: SrsDiff | None = None


@dataclass
class PeriodReport:
    since: str
    until: str
    baseline_snapshot: str | None
    alm_host: str = ""
    changes: list[PeriodChange] = field(default_factory=list)
    total_srs: int = 0
    query_counts: dict[str, int] = field(default_factory=dict)

    @property
    def comparable(self) -> list[PeriodChange]:
        return [c for c in self.changes if c.comparable]

    @property
    def listed_only(self) -> list[PeriodChange]:
        return [c for c in self.changes if not c.comparable]


def _yyyymmdd(d: str) -> str:
    return d.replace("-", "")


def fetch_changed_in_period(client, projects, since: str, until: str) -> dict[str, dict[str, Any]]:
    """기간 내 updated된 SRS를 Polarion에서 조회한다. uid -> {id, title, updated}"""
    changed: dict[str, dict[str, Any]] = {}
    for project in projects:
        query = f"{project.query} AND updated:[{_yyyymmdd(since)} TO {_yyyymmdd(until)}]"
        logger.info("[%s] 기간 변경 조회: %s", project.id, query)
        items = client.iter_workitems(
            project.id, query=query, fields="id,title,updated,status,type"
        )
        count = 0
        for item in items:
            attrs = item.get("attributes", {}) or {}
            wi_id = attrs.get("id")
            if not wi_id:
                continue
            changed[f"{project.id}/{wi_id}"] = {
                "id": wi_id,
                "project_id": project.id,
                "title": attrs.get("title") or "",
                "updated": attrs.get("updated"),
            }
            count += 1
        logger.info("[%s] 기간 내 변경 SRS %d건", project.id, count)
    return changed


def build_period_report(
    *,
    since: str,
    until: str,
    changed: dict[str, dict[str, Any]],
    current_by_uid: dict[str, dict],
    baseline_by_uid: dict[str, dict],
    baseline_snapshot: str | None,
    alm_host: str = "",
) -> PeriodReport:
    report = PeriodReport(
        since=since,
        until=until,
        baseline_snapshot=baseline_snapshot,
        alm_host=alm_host,
        total_srs=len(current_by_uid),
    )

    # 기준 스냅샷이 있는 SRS만 실제 본문 비교가 가능하다.
    comparable_uids = {u for u in changed if u in baseline_by_uid and u in current_by_uid}
    if comparable_uids:
        diffs = diff_snapshots(
            {u: baseline_by_uid[u] for u in comparable_uids},
            {u: current_by_uid[u] for u in comparable_uids},
        )
        diff_by_uid = {d.uid: d for d in diffs}
    else:
        diff_by_uid = {}

    for uid, meta in sorted(changed.items(), key=lambda kv: (kv[1].get("updated") or "", kv[0])):
        report.changes.append(
            PeriodChange(
                uid=uid,
                id=meta["id"],
                project_id=meta["project_id"],
                title=meta["title"],
                updated=meta.get("updated"),
                comparable=uid in comparable_uids,
                diff=diff_by_uid.get(uid),
            )
        )

    for c in report.changes:
        report.query_counts[c.project_id] = report.query_counts.get(c.project_id, 0) + 1
    return report


# ---------------------------------------------------------------- Markdown


def render_markdown(report: PeriodReport) -> str:
    L: list[str] = [
        f"# SRS 기간 변경 리포트 ({report.since} → {report.until})",
        "",
        f"- 기준 시점: **{report.since}**",
        f"- 비교 시점: **{report.until}**",
        f"- 기간 내 변경된 SRS: **{len(report.changes)}건** (Polarion `updated` 기준)",
        f"- 본문 비교 가능: {len(report.comparable)}건",
        f"- 변경 사실만 확인: {len(report.listed_only)}건",
        f"- 기준 스냅샷: {report.baseline_snapshot or '없음'}",
        "",
    ]
    if report.query_counts:
        L.append("프로젝트별 변경 건수: " + ", ".join(f"{k} {v}건" for k, v in sorted(report.query_counts.items())))
        L.append("")

    if report.listed_only:
        L += [
            "> **참고.** 기준 시점 본문이 없는 SRS가 있습니다. 이 Polarion 서버의 REST API는",
            "> work item 이력(revision)을 제공하지 않고, 구조화된 스냅샷은"
            f" {report.baseline_snapshot or '(없음)'}부터 보관되어 있습니다.",
            "> 그 이전 시점의 원래 본문은 당시 배포된 사양서 PDF를 참조하세요.",
            "> 아래 '변경 사실만 확인' 목록은 **언제 바뀌었는지와 현재 내용**을 보여줍니다.",
            "",
        ]

    if report.comparable:
        L += ["## 이전 → 현재 비교", ""]
        for c in report.comparable:
            d = c.diff
            url = workitem_url(report.alm_host, c.project_id, c.id)
            L.append(f"### [{c.id} - {c.title}]({url})")
            L.append("")
            L.append(f"- 최종 수정: {c.updated or '-'} · [ALM에서 상세 보기]({url})")
            if d and d.field_changes:
                L.append(f"- 변경 항목: {', '.join(d.field_changes)}")
            rows = _before_after_rows(d) if d else []
            if rows:
                L.append("")
                L.append("| 항목 | Before | After |")
                L.append("|---|---|---|")
                for label, before, after in rows:
                    L.append(f"| {_md_cell(label)} | {_md_cell(before)} | {_md_cell(after)} |")
                if d and len(d.sentence_changes) > MAX_ROWS_PER_SRS:
                    L.append("")
                    L.append(
                        f"_(본문 변경 {len(d.sentence_changes)}건 중 {MAX_ROWS_PER_SRS}건만 표시 - 전체는 ALM에서 확인)_"
                    )
            elif d and d.change_type == "unchanged":
                L.append("- 기준 스냅샷 이후로는 본문 변경 없음 (기준 시점 이전에 수정됨)")
            L.append("")

    if report.listed_only:
        L += ["## 변경 사실만 확인 (기준 시점 본문 없음)", "", "| SRS | 제목 | 최종 수정 | ALM |", "|---|---|---|---|"]
        for c in report.listed_only:
            url = workitem_url(report.alm_host, c.project_id, c.id)
            L.append(f"| {c.id} | {c.title} | {(c.updated or '-')[:19]} | [상세 보기]({url}) |")
        L.append("")

    if not report.changes:
        L += ["## 변경 없음", "", "이 기간에 수정된 SRS가 없습니다.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- HTML

_CSS = """
body{font-family:'Segoe UI','Malgun Gothic',sans-serif;font-size:13px;color:#111;max-width:1000px;margin:24px auto;padding:0 16px;}
.summary{background:#f6f8fa;border:1px solid #d0d7de;padding:12px 16px;border-radius:6px;}
.note{background:#fff8c5;border:1px solid #d4a72c;padding:10px 14px;border-radius:6px;margin:16px 0;}
.srs{border:1px solid #d0d7de;border-radius:6px;padding:10px 14px;margin:12px 0;}
.srs h3{margin:0 0 6px;font-size:14px;}
.meta{color:#57606a;font-size:12px;}
table{border-collapse:collapse;width:100%;}
th,td{border:1px solid #d0d7de;padding:5px 10px;text-align:left;vertical-align:top;}
th{background:#f6f8fa;}
a.alm-link{color:#0969da;text-decoration:none;} a.alm-link:hover{text-decoration:underline;}
"""


def _changes_table_html(d: SrsDiff | None) -> str:
    rows = _before_after_rows(d) if d else []
    if not rows:
        return ""
    body = "".join(
        f"<tr><td style='white-space:nowrap;color:#555;'>{html_lib.escape(label)}</td>"
        f"<td>{html_lib.escape(before)}</td><td>{html_lib.escape(after)}</td></tr>"
        for label, before, after in rows
    )
    note = ""
    if d and len(d.sentence_changes) > MAX_ROWS_PER_SRS:
        note = (
            f"<p style='color:#888;font-size:12px;'>본문 변경 {len(d.sentence_changes)}건 중 "
            f"{MAX_ROWS_PER_SRS}건만 표시 - 전체는 ALM에서 확인</p>"
        )
    return f"<table><tr><th>항목</th><th>Before</th><th>After</th></tr>{body}</table>{note}"


def render_html(report: PeriodReport) -> str:
    e = html_lib.escape
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>SRS 기간 변경 리포트 {e(report.since)} → {e(report.until)}</title>",
        f"<style>{_CSS}</style>",
        f"<h1>SRS 기간 변경 리포트</h1>",
        "<div class='summary'>",
        f"<b>기준 시점</b> {e(report.since)} &nbsp;→&nbsp; <b>비교 시점</b> {e(report.until)}<br/>",
        f"기간 내 변경된 SRS <b>{len(report.changes)}건</b> (Polarion <code>updated</code> 기준)<br/>",
        f"본문 비교 가능 {len(report.comparable)}건 · 변경 사실만 확인 {len(report.listed_only)}건<br/>",
        f"기준 스냅샷: {e(report.baseline_snapshot or '없음')}",
        "</div>",
    ]

    if report.listed_only:
        parts.append(
            "<div class='note'><b>참고.</b> 기준 시점 본문이 없는 SRS가 있습니다. 이 Polarion 서버의 "
            "REST API는 work item 이력(revision)을 제공하지 않고, 구조화된 스냅샷은 "
            f"{e(report.baseline_snapshot or '(없음)')}부터 보관되어 있습니다. 그 이전 시점의 원래 본문은 "
            "당시 배포된 사양서 PDF를 참조하세요.</div>"
        )

    if report.comparable:
        parts.append("<h2>이전 → 현재 비교</h2>")
        for c in report.comparable:
            d = c.diff
            url = workitem_url(report.alm_host, c.project_id, c.id)
            parts.append("<div class='srs'>")
            parts.append(
                f"<h3><a class='alm-link' href='{e(url)}' target='_blank' rel='noopener'>{e(c.id)} - {e(c.title)}</a></h3>"
            )
            meta = [f"최종 수정 {e(str(c.updated or '-'))[:19]}"]
            if d and d.field_changes:
                meta.append("변경 항목: " + e(", ".join(d.field_changes)))
            parts.append(f"<div class='meta'>{' · '.join(meta)}</div>")
            table_html = _changes_table_html(d)
            if table_html:
                parts.append(table_html)
            elif d and d.change_type == "unchanged":
                parts.append("<p class='meta'>기준 스냅샷 이후로는 본문 변경 없음 (기준 시점 이전에 수정됨)</p>")
            parts.append("</div>")

    if report.listed_only:
        parts.append("<h2>변경 사실만 확인 (기준 시점 본문 없음)</h2>")
        parts.append("<table><tr><th>SRS</th><th>제목</th><th>최종 수정</th><th>ALM</th></tr>")
        for c in report.listed_only:
            url = workitem_url(report.alm_host, c.project_id, c.id)
            parts.append(
                f"<tr><td>{e(c.id)}</td><td>{e(c.title)}</td><td>{e(str(c.updated or '-'))[:19]}</td>"
                f"<td><a class='alm-link' href='{e(url)}' target='_blank' rel='noopener'>상세 보기</a></td></tr>"
            )
        parts.append("</table>")

    if not report.changes:
        parts.append("<h2>변경 없음</h2><p>이 기간에 수정된 SRS가 없습니다.</p>")
    return "\n".join(parts)


def save_period_report(reports_dir: Path, report: PeriodReport) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"SRS_Period_Report_{report.since}_to_{report.until}"
    md_path = reports_dir / f"{stem}.md"
    html_path = reports_dir / f"{stem}.html"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return md_path, html_path
