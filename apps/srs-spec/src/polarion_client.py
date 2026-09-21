"""Polarion REST API v1 클라이언트.

기존 `ALM/polarion_query_backup.py` 의 PolarionClient 설계(Bearer PAT 인증,
JSON:API 페이지네이션, 첨부파일 스트리밍 다운로드)를 참고해 SRS 수집 목적에 맞게
재작성했다. 브라우저 자동화(Selenium/Playwright DOM 조작)는 사용하지 않는다 -
Playwright는 이후 단계에서 HTML->PDF 변환에만 사용한다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import requests

logger = logging.getLogger("srs_automation")


class PolarionApiError(RuntimeError):
    pass


@dataclass
class PolarionClient:
    host: str
    token: str
    verify_ssl: bool = True
    timeout_seconds: int = 90
    request_interval_seconds: float = 0.15

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = self._session.get(
                    url, params=params, timeout=self.timeout_seconds, verify=self.verify_ssl
                )
                if resp.status_code == 401:
                    raise PolarionApiError("Polarion 인증 실패(401) - 토큰이 유효하지 않거나 만료되었습니다.")
                if resp.status_code == 403:
                    raise PolarionApiError("Polarion 접근 거부(403) - 해당 프로젝트/리소스에 대한 권한이 없습니다.")
                if resp.status_code == 404:
                    raise PolarionApiError(f"Polarion 리소스를 찾을 수 없습니다(404): {url}")
                if 400 <= resp.status_code < 500:
                    # 4xx는 요청 자체가 잘못된 것이므로 재시도해도 결과가 바뀌지 않는다.
                    raise PolarionApiError(f"Polarion 요청 오류({resp.status_code}): {url} - {resp.text[:300]}")
                resp.raise_for_status()
                return resp.json()
            except PolarionApiError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Polarion 요청 실패(시도 %d/3): %s - %s", attempt, url, exc)
                time.sleep(min(2 ** attempt, 8))
            finally:
                time.sleep(self.request_interval_seconds)
        raise PolarionApiError(f"Polarion 요청이 반복 실패했습니다: {url}") from last_exc

    def ping(self, project_id: str) -> bool:
        """프로젝트 접근 가능 여부 확인(로그인/권한 sanity check)."""
        url = f"{self.host}/polarion/rest/v1/projects/{project_id}"
        self._get(url)
        return True

    # ------------------------------------------------------------------
    def iter_workitems(
        self, project_id: str, query: str, fields: str = "@all", sort: str = "oldId", page_size: int = 100
    ) -> Iterator[dict[str, Any]]:
        url = f"{self.host}/polarion/rest/v1/projects/{project_id}/workitems"
        page_number = 1
        seen_ids: set[str] = set()
        while True:
            params = {
                "query": query,
                "fields[workitems]": fields,
                "sort": sort,
                "page[size]": page_size,
                "page[number]": page_number,
            }
            data = self._get(url, params=params)
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                item_id = item.get("id")
                if item_id in seen_ids:
                    logger.warning("중복 페이지 응답 감지, 건너뜀: %s", item_id)
                    continue
                seen_ids.add(item_id)
                yield item
            if not data.get("links", {}).get("next"):
                break
            page_number += 1

    def get_total_count(self, project_id: str, query: str) -> int:
        url = f"{self.host}/polarion/rest/v1/projects/{project_id}/workitems"
        data = self._get(url, params={"query": query, "page[size]": 1, "page[number]": 1})
        return int(data.get("meta", {}).get("totalCount", 0))

    def get_linked_workitems(self, project_id: str, wi_id: str) -> list[dict[str, Any]]:
        url = f"{self.host}/polarion/rest/v1/projects/{project_id}/workitems/{wi_id}/linkedworkitems"
        try:
            data = self._get(url)
        except PolarionApiError:
            return []
        return data.get("data", [])

    def get_comments(self, project_id: str, wi_id: str) -> list[dict[str, Any]]:
        url = f"{self.host}/polarion/rest/v1/projects/{project_id}/workitems/{wi_id}/comments"
        try:
            data = self._get(url)
        except PolarionApiError:
            return []
        return data.get("data", [])

    def get_attachments(self, project_id: str, wi_id: str) -> list[dict[str, Any]]:
        url = f"{self.host}/polarion/rest/v1/projects/{project_id}/workitems/{wi_id}/attachments"
        try:
            data = self._get(url)
        except PolarionApiError:
            return []
        return data.get("data", [])

    def download_attachment(self, content_url: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        # 세션 기본 Accept(application/json)를 그대로 보내면 바이너리(이미지 등) 응답에 대해
        # 서버가 406을 반환한다 - 이 요청만 Accept를 */*로 덮어쓴다.
        with self._session.get(
            content_url,
            stream=True,
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
            headers={"Accept": "*/*"},
        ) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
        tmp_path.replace(dest_path)
        time.sleep(self.request_interval_seconds)
