"""Polarion Work Item 상세 페이지 링크 생성."""
from __future__ import annotations


def workitem_url(host: str, project_id: str, wi_id: str) -> str:
    return f"{host.rstrip('/')}/polarion/#/project/{project_id}/workitem?id={wi_id}"
