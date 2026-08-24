"""SRS ID 기준 구조적 Diff (PDF byte 비교가 아니라 의미 있는 항목별 비교).

이전 Snapshot과 현재 Snapshot을 uid(project/id) 기준으로 매칭해 신규/삭제/변경/동일을
구분하고, 변경된 경우 어떤 항목이 바뀌었는지(Status/Title/Description/이미지/첨부/
Linked Work Items/Comments/취소선·밑줄 서식)를 세분화한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

_STRIKE_RE = re.compile(r"text-decoration\s*:\s*[^;\"']*line-through", re.IGNORECASE)
_UNDERLINE_RE = re.compile(r"text-decoration\s*:\s*[^;\"']*underline", re.IGNORECASE)


def _plain_text(html_str: str | None) -> str:
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["li", "p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.append("\n")
    return soup.get_text()


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")


def _split_sentences(text: str) -> list[str]:
    """줄바꿈과 문장부호 기준으로 비교 단위를 나눈다. Before/After 리포트 테이블의 한 행이 된다."""
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in _SENTENCE_SPLIT_RE.split(line):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def sentence_changes(before_text: str, after_text: str) -> list[dict[str, str]]:
    """문장 단위 Before/After 쌍. 동일한 문장은 결과에 포함하지 않는다."""
    import difflib

    before = _split_sentences(before_text)
    after = _split_sentences(after_text)
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    changes: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append({"before": " ".join(before[i1:i2]), "after": " ".join(after[j1:j2])})
    return changes


def _count(pattern: re.Pattern, html_str: str | None) -> int:
    return len(pattern.findall(html_str or ""))


@dataclass
class SrsDiff:
    uid: str
    id: str
    project_id: str
    title: str
    change_type: str  # new | deleted | unchanged | changed
    field_changes: list[str] = field(default_factory=list)
    status_before: str | None = None
    status_after: str | None = None
    title_before: str | None = None
    title_after: str | None = None
    text_diff_lines: list[str] = field(default_factory=list)
    sentence_changes: list[dict[str, str]] = field(default_factory=list)
    strikethrough_before: int = 0
    strikethrough_after: int = 0
    underline_before: int = 0
    underline_after: int = 0


def _linked_set(rec: dict[str, Any]) -> set[tuple[str, str]]:
    out = {(l["role"], l["target_id"]) for l in rec.get("linked_work_items", [])}
    if rec.get("parent_id"):
        out.add(("parent", rec["parent_id"]))
    return out


def _attachment_set(rec: dict[str, Any]) -> set[str]:
    return {a["filename"] for a in rec.get("attachments_meta", [])}


def _image_ok_set(rec: dict[str, Any]) -> set[str]:
    return {i["filename"] for i in rec.get("image_results", []) if i.get("ok")}


def _comments_signature(rec: dict[str, Any]) -> tuple:
    return tuple((c.get("id"), c.get("text")) for c in rec.get("comments", []))


def diff_snapshots(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> list[SrsDiff]:
    import difflib

    results: list[SrsDiff] = []
    all_uids = set(previous.keys()) | set(current.keys())

    for uid in sorted(all_uids):
        prev = previous.get(uid)
        curr = current.get(uid)

        if curr is None:
            results.append(
                SrsDiff(
                    uid=uid,
                    id=prev["id"],
                    project_id=prev["project_id"],
                    title=prev["title"],
                    change_type="deleted",
                )
            )
            continue

        if prev is None:
            results.append(
                SrsDiff(
                    uid=uid,
                    id=curr["id"],
                    project_id=curr["project_id"],
                    title=curr["title"],
                    change_type="new",
                )
            )
            continue

        changes: list[str] = []
        if prev.get("status") != curr.get("status"):
            changes.append("status")
        if prev.get("title") != curr.get("title"):
            changes.append("title")

        prev_text = _plain_text(prev.get("content_html"))
        curr_text = _plain_text(curr.get("content_html"))
        text_diff_lines: list[str] = []
        sentence_diff: list[dict[str, str]] = []
        if prev_text != curr_text:
            changes.append("description")
            text_diff_lines = list(
                difflib.unified_diff(
                    prev_text.splitlines(),
                    curr_text.splitlines(),
                    lineterm="",
                    fromfile="before",
                    tofile="after",
                )
            )
            sentence_diff = sentence_changes(prev_text, curr_text)

        strike_before = _count(_STRIKE_RE, prev.get("content_html"))
        strike_after = _count(_STRIKE_RE, curr.get("content_html"))
        if strike_after > strike_before:
            changes.append("strikethrough_added")
        elif strike_after < strike_before:
            changes.append("strikethrough_removed")

        underline_before = _count(_UNDERLINE_RE, prev.get("content_html"))
        underline_after = _count(_UNDERLINE_RE, curr.get("content_html"))
        if underline_after > underline_before:
            changes.append("underline_added")
        elif underline_after < underline_before:
            changes.append("underline_removed")

        for key in ("severity", "priority", "active", "jira_id", "old_id"):
            if prev.get(key) != curr.get(key):
                changes.append(f"custom_field:{key}")

        if _linked_set(prev) != _linked_set(curr):
            changes.append("linked_work_items")
        if _attachment_set(prev) != _attachment_set(curr):
            changes.append("attachments")
        if _image_ok_set(prev) != _image_ok_set(curr):
            changes.append("images")
        if _comments_signature(prev) != _comments_signature(curr):
            changes.append("comments")

        results.append(
            SrsDiff(
                uid=uid,
                id=curr["id"],
                project_id=curr["project_id"],
                title=curr["title"],
                change_type="changed" if changes else "unchanged",
                field_changes=changes,
                status_before=prev.get("status"),
                status_after=curr.get("status"),
                title_before=prev.get("title"),
                title_after=curr.get("title"),
                text_diff_lines=text_diff_lines,
                sentence_changes=sentence_diff,
                strikethrough_before=strike_before,
                strikethrough_after=strike_after,
                underline_before=underline_before,
                underline_after=underline_after,
            )
        )

    return results
