"""SRS 변경 리포트 생성 (HTML + Markdown)."""
from __future__ import annotations

import html as html_lib
from pathlib import Path
from typing import Any

from .diff import SrsDiff
from .links import workitem_url

MAX_ROWS_PER_SRS = 60


def _summary_counts(diffs: list[SrsDiff]) -> dict[str, int]:
    counts = {"new": 0, "deleted": 0, "changed": 0, "unchanged": 0}
    for d in diffs:
        counts[d.change_type] = counts.get(d.change_type, 0) + 1
    return counts


def _before_after_rows(d: SrsDiff) -> list[tuple[str, str, str]]:
    """Status/제목/본문 변경을 하나의 (항목, Before, After) 표로 합친다.

    자세한 내용은 실제 SRS를 ALM에서 열어 확인하는 것을 전제로, 표는 "무엇이
    바뀌었는지"를 한눈에 보여주는 용도다.
    """
    rows: list[tuple[str, str, str]] = []
    if d.status_before != d.status_after and (d.status_before or d.status_after):
        rows.append(("상태", d.status_before or "-", d.status_after or "-"))
    if (d.title_before or "") != (d.title_after or "") and d.title_before:
        rows.append(("제목", d.title_before, d.title_after or "-"))
    for i, c in enumerate(d.sentence_changes[:MAX_ROWS_PER_SRS], start=1):
        before = c["before"] or "(신규 추가)"
        after = c["after"] or "(삭제됨)"
        rows.append((f"본문 {i}", before, after))
    return rows


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(
    diffs: list[SrsDiff],
    *,
    execution_date: str,
    previous_date: str | None,
    current_date: str,
    pdf_sanity: list[dict[str, Any]],
    alm_host: str,
) -> str:
    counts = _summary_counts(diffs)
    lines = [
        f"# SRS Change Report ({execution_date})",
        "",
        f"- Execution Date: {execution_date}",
        f"- Previous Snapshot: {previous_date or '(none - first run)'}",
        f"- Current Snapshot: {current_date}",
        f"- Total SRS: {len(diffs)}",
        f"- New: {counts['new']}",
        f"- Deleted: {counts['deleted']}",
        f"- Changed: {counts['changed']}",
        f"- Unchanged: {counts['unchanged']}",
        "",
        "## PDF Sanity Check",
        "",
    ]
    for p in pdf_sanity:
        lines.append(f"- {p['file']}: {p['status']} ({p.get('detail', '')})")

    lines.append("")
    lines.append("## Changed / New / Deleted SRS")
    lines.append("")
    for d in diffs:
        if d.change_type == "unchanged":
            continue
        url = workitem_url(alm_host, d.project_id, d.id)
        lines.append(f"### [{d.id} - {d.title or d.title_after or ''}]({url})")
        lines.append("")
        lines.append(f"Change Type: **{d.change_type}** · [ALM에서 상세 보기]({url})")
        if d.field_changes:
            lines.append("")
            lines.append("Detected changes: " + ", ".join(d.field_changes))
        rows = _before_after_rows(d)
        if rows:
            lines.append("")
            lines.append("| 항목 | Before | After |")
            lines.append("|---|---|---|")
            for label, before, after in rows:
                lines.append(f"| {_md_cell(label)} | {_md_cell(before)} | {_md_cell(after)} |")
            if len(d.sentence_changes) > MAX_ROWS_PER_SRS:
                lines.append("")
                lines.append(
                    f"_(본문 변경 {len(d.sentence_changes)}건 중 {MAX_ROWS_PER_SRS}건만 표시 - 전체는 ALM에서 확인)_"
                )
        lines.append("")
    return "\n".join(lines)


_HTML_CSS = """
body{font-family:'Segoe UI',sans-serif;font-size:13px;color:#111;max-width:1000px;margin:24px auto;}
.summary{background:#f5f5f5;border:1px solid #ddd;padding:12px 16px;border-radius:6px;}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;color:#fff;margin-right:4px;}
.badge.new{background:#2e7d32;} .badge.deleted{background:#c62828;} .badge.changed{background:#ef6c00;} .badge.unchanged{background:#9e9e9e;}
.srs{border:1px solid #ddd;border-radius:6px;padding:10px 14px;margin:12px 0;}
table.changes{border-collapse:collapse;width:100%;margin-top:8px;}
table.changes td, table.changes th{border:1px solid #ddd;padding:5px 10px;text-align:left;vertical-align:top;}
table.changes th{background:#f5f5f5;}
table.changes td.label{white-space:nowrap;color:#555;}
a.alm-link{color:#0969da;text-decoration:none;} a.alm-link:hover{text-decoration:underline;}
table.sanity{border-collapse:collapse;} table.sanity td, table.sanity th{border:1px solid #ccc;padding:4px 10px;}
"""


def _changes_table_html(d: SrsDiff) -> str:
    rows = _before_after_rows(d)
    if not rows:
        return ""
    body = "".join(
        f"<tr><td class='label'>{html_lib.escape(label)}</td>"
        f"<td>{html_lib.escape(before)}</td><td>{html_lib.escape(after)}</td></tr>"
        for label, before, after in rows
    )
    note = ""
    if len(d.sentence_changes) > MAX_ROWS_PER_SRS:
        note = (
            f"<p style='color:#888;font-size:12px;'>본문 변경 {len(d.sentence_changes)}건 중 "
            f"{MAX_ROWS_PER_SRS}건만 표시 - 전체는 ALM에서 확인</p>"
        )
    return (
        "<table class='changes'><tr><th>항목</th><th>Before</th><th>After</th></tr>"
        f"{body}</table>{note}"
    )


def render_html(
    diffs: list[SrsDiff],
    *,
    execution_date: str,
    previous_date: str | None,
    current_date: str,
    pdf_sanity: list[dict[str, Any]],
    alm_host: str,
) -> str:
    counts = _summary_counts(diffs)
    rows = []
    for d in diffs:
        if d.change_type == "unchanged":
            continue
        url = workitem_url(alm_host, d.project_id, d.id)
        changes_html = ", ".join(html_lib.escape(c) for c in d.field_changes) or "-"
        table_html = _changes_table_html(d)
        rows.append(
            f'<div class="srs"><h3><span class="badge {d.change_type}">{d.change_type}</span> '
            f'<a class="alm-link" href="{html_lib.escape(url)}" target="_blank" rel="noopener">'
            f'{html_lib.escape(d.id)} - {html_lib.escape(d.title or d.title_after or "")}</a></h3>'
            f"<p>Detected changes: {changes_html}</p>{table_html}</div>"
        )

    sanity_rows = "".join(
        f"<tr><td>{html_lib.escape(p['file'])}</td><td>{html_lib.escape(p['status'])}</td>"
        f"<td>{html_lib.escape(p.get('detail', ''))}</td></tr>"
        for p in pdf_sanity
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>SRS Change Report {execution_date}</title><style>{_HTML_CSS}</style></head>
<body>
<h1>SRS Change Report</h1>
<div class="summary">
<b>Execution Date:</b> {execution_date}<br/>
<b>Previous Snapshot:</b> {previous_date or '(none - first run)'}<br/>
<b>Current Snapshot:</b> {current_date}<br/><br/>
<b>Total SRS:</b> {len(diffs)} &nbsp;
<span class="badge new">New {counts['new']}</span>
<span class="badge deleted">Deleted {counts['deleted']}</span>
<span class="badge changed">Changed {counts['changed']}</span>
<span class="badge unchanged">Unchanged {counts['unchanged']}</span>
</div>
<h2>PDF Sanity Check</h2>
<table class="sanity"><tr><th>File</th><th>Status</th><th>Detail</th></tr>{sanity_rows}</table>
<h2>Changed / New / Deleted SRS</h2>
{''.join(rows) if rows else '<p>No changes detected.</p>'}
</body></html>"""


def save_reports(
    reports_dir: Path,
    date_str: str,
    diffs: list[SrsDiff],
    *,
    previous_date: str | None,
    current_date: str,
    pdf_sanity: list[dict[str, Any]],
    alm_host: str,
) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / f"SRS_Change_Report_{date_str}.md"
    html_path = reports_dir / f"SRS_Change_Report_{date_str}.html"
    md_path.write_text(
        render_markdown(
            diffs,
            execution_date=date_str,
            previous_date=previous_date,
            current_date=current_date,
            pdf_sanity=pdf_sanity,
            alm_host=alm_host,
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        render_html(
            diffs,
            execution_date=date_str,
            previous_date=previous_date,
            current_date=current_date,
            pdf_sanity=pdf_sanity,
            alm_host=alm_host,
        ),
        encoding="utf-8",
    )
    return md_path, html_path
