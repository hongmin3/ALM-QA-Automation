from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin

import requests
import yaml
from bs4 import BeautifulSoup


IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"
}

DEFAULT_VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mov", ".wmv", ".mkv", ".webm"
}

# 일시적인 서버/과부하 응답. 이 코드들만 재시도한다.
# (401/403/404처럼 설정을 고쳐야 하는 오류는 재시도해도 의미가 없다.)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Polarion 검색 Query(Lucene)에서 특별한 뜻을 갖는 문자.
# -id 옵션으로 받은 이슈 ID를 쿼리로 바꿀 때 이스케이프한다.
LUCENE_SPECIAL_CHARACTERS = set(r'+-&|!(){}[]^"~*?:\/')

# config.example.yaml을 복사만 하고 값을 채우지 않은 상태를 걸러내기 위한 값.
PLACEHOLDER_CONFIG_VALUES = {
    "https://your-polarion-server.example.com",
    "YOUR_PROJECT_ID",
}

# 결과 문서(PDF/HTML/Markdown) 맨 위에 찍히는 제목.
# config.yaml의 output.document_title 또는 명령행 --title 로 바꿀 수 있다.
DEFAULT_DOCUMENT_TITLE = "ALM Issue Report"


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return yaml.safe_load(file)


def safe_filename(value: str, max_length: int = 140) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" .")
    return value[:max_length] or "unnamed"


def pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def attrs(resource: dict[str, Any]) -> dict[str, Any]:
    return resource.get("attributes") or {}


def rich_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def plain_text(value: Any) -> str:
    value = rich_value(value)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return pretty(value)
    if isinstance(value, str):
        return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)
    return str(value)


def sanitize_rich_html(value: Any) -> str:
    if value is None:
        return '<span class="empty">-</span>'

    if isinstance(value, dict) and "value" in value:
        mime = str(value.get("type", "")).lower()
        raw = str(value.get("value") or "")

        if "html" in mime:
            soup = BeautifulSoup(raw, "html.parser")

            for tag in soup.find_all(["script", "iframe", "object", "embed"]):
                tag.decompose()

            for tag in soup.find_all(True):
                for attr_name in list(tag.attrs):
                    lowered = attr_name.lower()
                    if lowered.startswith("on"):
                        del tag.attrs[attr_name]
                    elif lowered in {"src", "href"}:
                        attr_value = str(tag.attrs[attr_name])
                        if attr_value.lower().startswith("javascript:"):
                            del tag.attrs[attr_name]

            return str(soup)

        return html.escape(raw).replace("\n", "<br>")

    if isinstance(value, str):
        return html.escape(value).replace("\n", "<br>")

    return f"<pre>{html.escape(pretty(value))}</pre>"


def relationship_data(resource: dict[str, Any], rel_name: str) -> list[dict[str, Any]]:
    relationship = (resource.get("relationships") or {}).get(rel_name) or {}
    data = relationship.get("data", [])

    if isinstance(data, dict):
        data = [data]

    return [item for item in data if isinstance(item, dict)]


def relationship_ids(resource: dict[str, Any], rel_name: str) -> list[str]:
    return [
        str(item.get("id", ""))
        for item in relationship_data(resource, rel_name)
        if item.get("id")
    ]


def rel_ids(resource: dict[str, Any], rel_name: str) -> list[str]:
    return relationship_ids(resource, rel_name)


def normalize_relationship_id(resource_id: str) -> str:
    tail = resource_id.rsplit("/", 1)[-1]

    match = re.fullmatch(r"(.+?)_(\d+)_(\d+)_(\d+)(?:_(\d+))?", tail)
    if match:
        product = match.group(1).replace("_", " ")
        version_parts = [match.group(2), match.group(3), match.group(4)]
        if match.group(5) is not None:
            version_parts.append(match.group(5).zfill(3))
        return f"{product} {'.'.join(version_parts)}"

    return tail


def relationship_display(resource: dict[str, Any], rel_name: str) -> str:
    values = [
        normalize_relationship_id(resource_id)
        for resource_id in relationship_ids(resource, rel_name)
    ]

    if not values:
        return '<span class="empty">-</span>'

    return html.escape(", ".join(values))


RELATIONSHIP_ONLY_FIELDS = ("author", "assignee", "reviewer")


def inject_relationship_fields(
    workitem: dict[str, Any],
    workitem_attributes: dict[str, Any],
) -> dict[str, Any]:
    for key in RELATIONSHIP_ONLY_FIELDS:
        if workitem_attributes.get(key):
            continue

        ids = relationship_ids(workitem, key)

        if ids:
            workitem_attributes[key] = ", ".join(
                normalize_relationship_id(resource_id) for resource_id in ids
            )

    return workitem_attributes


def is_image_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def normalize_attachment_key(value: str) -> str:
    value = unquote(str(value or "")).strip().lower()
    value = value.split("?", 1)[0]
    value = value.rsplit("/", 1)[-1]

    for prefix in ("workitemimg:", "attachment:"):
        if value.startswith(prefix):
            value = value.split(":", 1)[1]

    return value


class PolarionClient:
    def __init__(self, config: dict[str, Any]):
        polarion_config = config["polarion"]

        self.host = str(polarion_config["host"]).rstrip("/")
        self.project_id = str(polarion_config["project_id"])
        self.base_api = f"{self.host}/polarion/rest/v1"
        self.verify_ssl = bool(polarion_config.get("verify_ssl", True))
        self.timeout = int(polarion_config.get("timeout_seconds", 90))
        self.interval = float(polarion_config.get("request_interval_seconds", 0.15))
        self.page_size = int(polarion_config.get("page_size", 100))
        self.max_retries = max(
            0,
            int(polarion_config.get("max_retries", 3)),
        )
        self.retry_backoff = max(
            0.0,
            float(polarion_config.get("retry_backoff_seconds", 2.0)),
        )

        token_environment_name = polarion_config.get(
            "token_env",
            "POLARION_TOKEN",
        )
        token = os.environ.get(token_environment_name, "").strip()

        if not token:
            raise RuntimeError(
                f"환경변수 {token_environment_name}가 없습니다. "
                f'PowerShell 예: $env:{token_environment_name}="PAT 토큰"'
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Polarion-Query-Backup/6.0",
            }
        )

    def wait_before_retry(
        self,
        attempt: int,
        reason: str,
        response: requests.Response | None = None,
    ) -> None:
        delay = self.retry_backoff * (2**attempt)

        if response is not None:
            retry_after = str(
                response.headers.get("Retry-After", "")
            ).strip()

            if retry_after.isdigit():
                delay = max(delay, float(retry_after))

        print(
            f"    일시적 오류({reason}) — {delay:.0f}초 후 재시도 "
            f"[{attempt + 1}/{self.max_retries}]"
        )
        time.sleep(delay)

    def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        last_network_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            time.sleep(self.interval)

            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                    **kwargs,
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as network_error:
                last_network_error = network_error

                if attempt >= self.max_retries:
                    break

                self.wait_before_retry(
                    attempt,
                    type(network_error).__name__,
                )
                continue

            if response.status_code == 401:
                raise RuntimeError("401 Unauthorized: PAT를 확인하세요.")

            if response.status_code == 403:
                raise RuntimeError(
                    f"403 Forbidden: 조회 권한이 없습니다. URL={response.url}"
                )

            if response.status_code == 404:
                raise RuntimeError(
                    f"404 Not Found: URL 또는 Project ID를 확인하세요. URL={response.url}"
                )

            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self.max_retries
            ):
                self.wait_before_retry(
                    attempt,
                    f"HTTP {response.status_code}",
                    response,
                )
                continue

            response.raise_for_status()
            return response

        raise RuntimeError(
            f"서버에 연결하지 못했습니다 ({self.max_retries + 1}회 시도). "
            "네트워크/VPN 연결과 config.yaml의 polarion.host를 확인하세요. "
            f"원인: {last_network_error}"
        )

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.request("GET", url, params=params).json()

    def all_pages(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params = dict(params or {})

        while next_url:
            payload = self.get_json(next_url, next_params)
            data = payload.get("data", [])

            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)

            next_url = (payload.get("links") or {}).get("next")
            next_params = {}

        return results

    def query_workitems(
        self,
        query: str,
        sort: str,
        fields: str,
    ) -> list[dict[str, Any]]:
        url = (
            f"{self.base_api}/projects/"
            f"{quote(self.project_id, safe='')}/workitems"
        )

        params = {
            "query": query,
            "sort": sort,
            "page[size]": self.page_size,
            "fields[workitems]": fields,
        }

        return self.all_pages(url, params)

    def related_resources(
        self,
        workitem: dict[str, Any],
        relationship_name: str,
        sparse_type: str,
        sparse_fields: str,
    ) -> list[dict[str, Any]]:
        relationship = (
            (workitem.get("relationships") or {}).get(relationship_name) or {}
        )

        related_url = (relationship.get("links") or {}).get("related")

        if not related_url:
            return []

        return self.all_pages(
            related_url,
            {
                "page[size]": self.page_size,
                f"fields[{sparse_type}]": sparse_fields,
            },
        )

    def workitem_children(
        self,
        workitem: dict[str, Any],
        relationship_candidates: list[tuple[str, str]],
        sparse_fields: str,
    ) -> list[dict[str, Any]]:
        relationships = workitem.get("relationships") or {}

        for relationship_name, resource_type in relationship_candidates:
            if relationship_name in relationships:
                return self.related_resources(
                    workitem,
                    relationship_name,
                    resource_type,
                    sparse_fields,
                )

        return []

    def get_workitem_resource(
        self,
        resource_id: str,
        fields: str = "@all",
    ) -> dict[str, Any]:
        target_id = resource_id.rsplit("/", 1)[-1]
        url = (
            f"{self.base_api}/projects/"
            f"{quote(self.project_id, safe='')}/workitems/"
            f"{quote(target_id, safe='')}"
        )

        payload = self.get_json(
            url,
            {"fields[workitems]": fields},
        )

        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def download_binary(self, url: str) -> requests.Response:
        return self.request(
            "GET",
            url,
            headers={"Accept": "*/*"},
        )

    def download(self, url: str, target: Path) -> None:
        response = self.request(
            "GET",
            url,
            stream=True,
            headers={"Accept": "*/*"},
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target.with_suffix(target.suffix + ".part")

        try:
            with temporary_path.open("wb") as file:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        file.write(chunk)

            temporary_path.replace(target)

        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise


def find_attachment_filename(
    attachment: dict[str, Any],
    index: int,
) -> str:
    attachment_attributes = attrs(attachment)

    for key in ("fileName", "filename", "name", "title"):
        if attachment_attributes.get(key):
            return safe_filename(str(attachment_attributes[key]))

    resource_id = str(attachment.get("id", ""))
    return safe_filename(
        resource_id.rsplit("/", 1)[-1] or f"attachment-{index}"
    )


def build_attachment_lookup(
    attachments: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}

    for attachment in attachments:
        attachment_attributes = attrs(attachment)

        filename = str(
            attachment_attributes.get("fileName")
            or attachment_attributes.get("filename")
            or ""
        ).strip()

        attachment_id = str(
            attachment_attributes.get("id")
            or attachment.get("id", "").rsplit("/", 1)[-1]
        ).strip()

        candidates = {
            filename,
            attachment_id,
            re.sub(r"^\d+-", "", attachment_id),
        }

        for candidate in candidates:
            normalized = normalize_attachment_key(candidate)
            if normalized:
                lookup[normalized] = attachment

    return lookup


def find_used_image_references(
    workitem_attributes: dict[str, Any],
) -> set[str]:
    used: set[str] = set()

    for value in workitem_attributes.values():
        if not (
            isinstance(value, dict)
            and "value" in value
            and "html" in str(value.get("type", "")).lower()
        ):
            continue

        soup = BeautifulSoup(
            str(value.get("value") or ""),
            "html.parser",
        )

        for image in soup.find_all("img"):
            source = str(image.get("src") or "").strip()
            if not source:
                continue

            normalized = normalize_attachment_key(source)
            if normalized:
                used.add(normalized)
                used.add(re.sub(r"^\d+-", "", normalized))

    return used


def guess_mime_type(
    filename: str,
    content_type: str | None = None,
) -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip()
        if normalized.startswith("image/"):
            return normalized

    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "image/png"


def fetch_attachment_image_bytes(
    client: PolarionClient,
    attachment: dict[str, Any],
) -> tuple[bytes, str] | None:
    filename = find_attachment_filename(attachment, 0)

    if not is_image_filename(filename):
        return None

    content_url = (attachment.get("links") or {}).get("content")

    if not content_url:
        return None

    try:
        response = client.request(
            "GET",
            content_url,
            headers={"Accept": "image/*,*/*;q=0.8"},
        )

        binary = response.content

        if not binary:
            return None

        mime_type = guess_mime_type(
            filename,
            response.headers.get("Content-Type"),
        )

        return binary, mime_type

    except Exception as exception:
        print(
            f"  첨부 이미지 조회 실패: {filename}\n"
            f"    원인: {exception}"
        )
        return None


def embed_images_in_rich_html(
    value: Any,
    client: PolarionClient,
    attachment_lookup: dict[str, dict[str, Any]],
    enabled: bool,
) -> Any:
    if not enabled:
        return value

    if not (
        isinstance(value, dict)
        and "value" in value
        and "html" in str(value.get("type", "")).lower()
    ):
        return value

    raw_html = str(value.get("value") or "")
    soup = BeautifulSoup(raw_html, "html.parser")

    for image in soup.find_all("img"):
        source = str(image.get("src") or "").strip()

        if not source or source.startswith("data:"):
            continue

        attachment = None
        normalized_reference = normalize_attachment_key(source)

        if normalized_reference:
            attachment = attachment_lookup.get(normalized_reference)

        if not attachment and normalized_reference:
            attachment = attachment_lookup.get(
                re.sub(r"^\d+-", "", normalized_reference)
            )

        content_url = None

        if attachment:
            content_url = (attachment.get("links") or {}).get("content")

        if not content_url:
            if source.startswith(("http://", "https://")):
                content_url = source
            elif source.startswith("/"):
                content_url = urljoin(client.host, source)

        if not content_url:
            image["alt"] = (
                image.get("alt")
                or source
                or "이미지 참조를 찾지 못함"
            )
            continue

        try:
            response = client.request(
                "GET",
                content_url,
                headers={"Accept": "image/*,*/*;q=0.8"},
            )

            binary = response.content

            if not binary:
                raise RuntimeError("이미지 데이터가 비어 있습니다.")

            filename_for_mime = (
                find_attachment_filename(attachment, 0)
                if attachment
                else normalized_reference
            )

            mime_type = guess_mime_type(
                filename_for_mime,
                response.headers.get("Content-Type"),
            )

            encoded = base64.b64encode(binary).decode("ascii")

            image["src"] = f"data:{mime_type};base64,{encoded}"
            image["data-original-src"] = source
            image.attrs.pop("width", None)
            image.attrs.pop("height", None)

            existing_classes = image.get("class", [])
            if isinstance(existing_classes, str):
                existing_classes = [existing_classes]

            image["class"] = list(existing_classes) + [
                "embedded-workitem-image"
            ]

        except Exception as exception:
            print(
                f"  본문 이미지 삽입 실패: {source}\n"
                f"    원인: {exception}"
            )

            image["alt"] = (
                image.get("alt")
                or source
            ) + f" [이미지 로드 실패: {exception}]"

    updated = dict(value)
    updated["value"] = str(soup)
    return updated


def resource_display(value: Any) -> str:
    if value is None:
        return '<span class="empty">-</span>'

    if isinstance(value, list):
        return (
            "<ul>"
            + "".join(f"<li>{resource_display(item)}</li>" for item in value)
            + "</ul>"
        )

    if isinstance(value, dict):
        if "value" in value:
            return sanitize_rich_html(value)

        preferred = (
            value.get("name")
            or value.get("title")
            or value.get("id")
            or value.get("value")
        )

        if preferred is not None:
            return html.escape(str(preferred))

        return f"<pre>{html.escape(pretty(value))}</pre>"

    return html.escape(str(value))


def inventory_fields(
    workitems: list[dict[str, Any]],
) -> dict[str, int]:
    counter: Counter[str] = Counter()

    for workitem in workitems:
        counter.update(attrs(workitem).keys())

    return dict(sorted(counter.items()))


def compute_summary_stats(
    workitems: list[dict[str, Any]],
) -> dict[str, Counter]:
    status_counter: Counter[str] = Counter()
    author_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    version_counter: Counter[str] = Counter()

    for workitem in workitems:
        workitem_attributes = inject_relationship_fields(
            workitem, dict(attrs(workitem))
        )

        status_counter[
            plain_text(workitem_attributes.get("status")) or "(미지정)"
        ] += 1
        author_counter[
            plain_text(workitem_attributes.get("author")) or "(미지정)"
        ] += 1
        type_counter[
            plain_text(workitem_attributes.get("type")) or "(미지정)"
        ] += 1

        versions = [
            normalize_relationship_id(resource_id)
            for resource_id in relationship_ids(workitem, "occurredVersion")
        ]

        for version in versions or ["(미지정)"]:
            version_counter[version] += 1

    return {
        "status": status_counter,
        "author": author_counter,
        "type": type_counter,
        "version": version_counter,
    }


def render_relationship_fields(workitem: dict[str, Any]) -> str:
    specifications = [
        ("project", "Project"),
        ("releaseVision", "Release Vision"),
        ("occurredVersion", "Occurred in Version"),
        ("targetVersion", "Target Version"),
    ]

    rows = []

    for relationship_name, label in specifications:
        rows.append(
            f"<tr><th>{html.escape(label)}</th>"
            f"<td>{relationship_display(workitem, relationship_name)}</td></tr>"
        )

    return (
        "<h2>Project 및 Version 정보</h2>"
        "<table class='fields relationship-fields'>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_comments(
    comments: list[dict[str, Any]],
    client: PolarionClient,
    attachment_lookup: dict[str, dict[str, Any]],
    embed_images: bool,
) -> str:
    if not comments:
        return '<p class="empty">댓글 없음</p>'

    output = []

    for index, comment in enumerate(comments, 1):
        comment_attributes = attrs(comment)
        body = (
            comment_attributes.get("text")
            or comment_attributes.get("content")
            or comment_attributes.get("comment")
        )

        body = embed_images_in_rich_html(
            body,
            client,
            attachment_lookup,
            embed_images,
        )

        author_ids = relationship_ids(comment, "author")
        author = ", ".join(
            resource_id.rsplit("/", 1)[-1]
            for resource_id in author_ids
        )

        created = plain_text(
            comment_attributes.get("created")
            or comment_attributes.get("createdAt")
        )

        output.append(
            '<article class="comment">'
            f'<div class="comment-head">#{index} '
            f'{html.escape(author)} {html.escape(created)}</div>'
            f'<div class="comment-body">{sanitize_rich_html(body)}</div>'
            '</article>'
        )

    return "".join(output)


def get_workitem_type_info(item_type: str) -> dict[str, str]:
    normalized = item_type.strip().lower()

    issue_types = {
        "issue", "defect", "bug"
    }

    specification_types = {
        "requirement",
        "systemrequirement",
        "system_requirement",
        "softwarerequirement",
        "software_requirement",
        "srs",
        "specification",
        "spec",
    }

    if normalized in specification_types:
        return {
            "category": "사양",
            "icon_text": "SRS",
            "css_class": "type-icon-srs",
        }

    if normalized in issue_types:
        return {
            "category": "이슈",
            "icon_text": "IS",
            "css_class": "type-icon-issue",
        }

    return {
        "category": "기타",
        "icon_text": item_type.upper()[:4] or "?",
        "css_class": "type-icon-other",
    }


def render_linked(
    linked: list[dict[str, Any]],
) -> str:
    if not linked:
        return '<p class="empty">연결된 사양 및 이슈 없음</p>'

    grouped: dict[str, list[str]] = {
        "사양": [],
        "이슈": [],
        "기타": [],
    }

    for item in linked:
        role = plain_text(attrs(item).get("role"))

        target_ids = rel_ids(item, "workItem")
        target_resource_id = (
            target_ids[0]
            if target_ids
            else str(item.get("id", ""))
        )

        target = item.get("target") or {}
        target_attributes = attrs(target)

        display_id = target_resource_id.rsplit("/", 1)[-1]
        title = (
            plain_text(target_attributes.get("title"))
            or "(제목 조회 안 됨)"
        )
        item_type = (
            plain_text(target_attributes.get("type"))
            or "unknown"
        )

        type_info = get_workitem_type_info(item_type)
        category = type_info["category"]

        icon_html = (
            f'<span class="workitem-type-icon '
            f'{type_info["css_class"]}">'
            f'{html.escape(type_info["icon_text"])}'
            '</span>'
        )

        portal = (target.get("links") or {}).get("portal")

        if portal:
            number_html = (
                f'<a href="{html.escape(portal)}">'
                f'{icon_html}'
                f'<span class="linked-id">'
                f'{html.escape(display_id)}</span>'
                '</a>'
            )
        else:
            number_html = (
                f'{icon_html}'
                f'<span class="linked-id">'
                f'{html.escape(display_id)}</span>'
            )

        grouped.setdefault(category, []).append(
            "<tr>"
            f"<td>{number_html}</td>"
            f"<td>{html.escape(title)}</td>"
            f"<td>{html.escape(item_type)}</td>"
            f"<td>{html.escape(role)}</td>"
            "</tr>"
        )

    section_labels = {
        "사양": "연관된 사양",
        "이슈": "연관된 이슈",
        "기타": "기타 연결 항목",
    }

    sections = []

    for category in ("사양", "이슈", "기타"):
        rows = grouped.get(category, [])

        if not rows:
            continue

        sections.append(
            f"<h3>{section_labels[category]}</h3>"
            "<table class='linked-table'>"
            "<thead><tr>"
            "<th>번호</th><th>제목</th>"
            "<th>Type</th><th>Link Role</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )

    return "".join(sections)


def render_attachments(
    records: list[dict[str, Any]],
) -> str:
    visible_records = [
        record
        for record in records
        if not (
            record.get("used_in_body")
            and record.get("image_data_uri")
        )
    ]

    if not visible_records:
        return '<p class="empty">추가 첨부파일 없음</p>'

    blocks = []

    for record in visible_records:
        filename = html.escape(str(record.get("filename", "")))
        relative_path = str(record.get("relative_path", ""))
        image_data_uri = str(record.get("image_data_uri", ""))
        status = html.escape(str(record.get("status", "")))
        error = str(record.get("error", ""))

        size = record.get("size", "")

        if isinstance(size, (int, float)):
            size_text = f"{int(size):,} bytes"
        else:
            size_text = html.escape(str(size))

        if image_data_uri:
            blocks.append(
                "<figure class='attachment-image'>"
                f"<figcaption>{filename}</figcaption>"
                f"<img src='{image_data_uri}' "
                f"alt='{filename}'>"
                f"<div class='attachment-meta'>"
                f"{size_text} · {status}"
                "</div>"
                "</figure>"
            )
            continue

        if relative_path:
            filename_html = (
                f'<a href="{html.escape(relative_path)}">'
                f"{filename}</a>"
            )
        else:
            filename_html = filename

        if error:
            status += f"<br><small>{html.escape(error)}</small>"

        blocks.append(
            "<table class='attachment-file'>"
            "<tbody><tr>"
            f"<th>파일명</th><td>{filename_html}</td>"
            f"<th>크기</th><td>{size_text}</td>"
            f"<th>상태</th><td>{status}</td>"
            "</tr></tbody></table>"
        )

    return "".join(blocks)


def render_workitem(
    client: PolarionClient,
    workitem: dict[str, Any],
    comments: list[dict[str, Any]],
    linked: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    display_fields: list[dict[str, str]],
    priority_fields: list[dict[str, str]],
    hidden_fields: set[str],
    attachment_lookup: dict[str, dict[str, Any]],
    embed_images: bool,
    anchor: str,
    status_display: str = "",
) -> str:
    workitem_attributes = inject_relationship_fields(
        workitem, dict(attrs(workitem))
    )

    for key, value in list(workitem_attributes.items()):
        workitem_attributes[key] = embed_images_in_rich_html(
            value,
            client,
            attachment_lookup,
            embed_images,
        )

    workitem_id = str(
        workitem_attributes.get("id")
        or str(workitem.get("id", "")).rsplit("/", 1)[-1]
    )

    title = (
        plain_text(workitem_attributes.get("title"))
        or "(제목 없음)"
    )

    used: set[str] = {"id", "title"}
    priority_rows: list[str] = []
    normal_rows: list[str] = []

    for specification in priority_fields:
        key = specification["key"]
        used.add(key)

        if key in hidden_fields:
            continue

        priority_rows.append(
            f"<tr><th>{html.escape(specification['label'])}</th>"
            f"<td>{resource_display(workitem_attributes.get(key))}</td></tr>"
        )

    for specification in display_fields:
        key = specification["key"]

        if (
            key in used
            or key in hidden_fields
            or key not in workitem_attributes
        ):
            continue

        used.add(key)

        normal_rows.append(
            f"<tr><th>{html.escape(specification['label'])}</th>"
            f"<td>{resource_display(workitem_attributes.get(key))}</td></tr>"
        )

    for key in sorted(
        current_key
        for current_key in workitem_attributes.keys()
        if current_key not in used
        and current_key not in hidden_fields
    ):
        normal_rows.append(
            f"<tr><th>{html.escape(key)}</th>"
            f"<td>{resource_display(workitem_attributes.get(key))}</td></tr>"
        )

    priority_section = ""

    if priority_rows:
        priority_section = (
            "<h2>발생원인 및 조치내역</h2>"
            "<table class='fields important-fields'>"
            f"<tbody>{''.join(priority_rows)}</tbody></table>"
        )

    normal_section = ""

    if normal_rows:
        normal_section = (
            "<h2>전체 필드</h2>"
            "<table class='fields'>"
            f"<tbody>{''.join(normal_rows)}</tbody></table>"
        )

    return f"""
<section class="work-item" id="{html.escape(anchor)}"
         data-wi-status="{html.escape(status_display, quote=True)}">
  <header>
    <a class="back-to-toc screen-only" href="#toc">↑ 목차</a>
    <div class="wi-id">{html.escape(workitem_id)}</div>
    <h1>{html.escape(title)}</h1>
  </header>

  {priority_section}
  {render_relationship_fields(workitem)}
  {normal_section}

  <h2>Comments</h2>
  {render_comments(
      comments,
      client,
      attachment_lookup,
      embed_images,
  )}

  <h2>Linked Work Items</h2>
  {render_linked(linked)}

  <h2>Attachments</h2>
  {render_attachments(attachments)}
</section>
"""


DOCUMENT_CSS = """
@page {
    size: A4 portrait;
    margin: 12mm 12mm 14mm 12mm;
}

* {
    box-sizing: border-box;
}

html,
body {
    margin: 0;
    padding: 0;
}

body {
    font-family:
        "Malgun Gothic",
        "Noto Sans KR",
        Arial,
        sans-serif;
    color: #222;
    font-size: 9.5pt;
    line-height: 1.45;
}

.cover {
    min-height: 90vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
    break-after: page;
    page-break-after: always;
}

.cover h1 {
    font-size: 26pt;
}

.query {
    white-space: pre-wrap;
    background: #f3f5f7;
    border: 1px solid #ccd3da;
    padding: 12px;
}

.work-item {
    break-after: page;
    page-break-after: always;
}

.work-item:last-child {
    break-after: auto;
    page-break-after: auto;
}

.work-item > header {
    border-bottom: 3px solid #176b87;
    padding-bottom: 10px;
    margin-bottom: 16px;
    break-inside: avoid;
    page-break-inside: avoid;
}

.wi-id {
    font-size: 15pt;
    font-weight: 700;
}

h1 {
    margin: 4px 0 8px;
    font-size: 19pt;
}

h2 {
    margin-top: 22px;
    font-size: 13pt;
    border-left: 5px solid #176b87;
    padding-left: 8px;
    break-after: avoid;
    page-break-after: avoid;
}

h3 {
    margin-top: 16px;
    font-size: 11.5pt;
    break-after: avoid;
    page-break-after: avoid;
}

table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
}

th,
td {
    border: 1px solid #bbb;
    padding: 6px;
    vertical-align: top;
    overflow-wrap: anywhere;
}

.fields {
    break-inside: auto;
    page-break-inside: auto;
}

.fields tr {
    break-inside: avoid;
    page-break-inside: avoid;
}

.fields th {
    width: 28%;
    background: #f5f5f5;
}

.important-fields {
    border: 2px solid #176b87;
}

.important-fields th {
    background: #eaf4f7;
    font-weight: 700;
}

.relationship-fields th {
    background: #f1f6fb;
    font-weight: 700;
}

.comment {
    border: 1px solid #bbb;
    margin: 10px 0;
    break-inside: avoid;
    page-break-inside: avoid;
}

.comment-head {
    background: #f3f3f3;
    font-weight: 700;
    padding: 7px;
}

.comment-body {
    padding: 8px;
}

.linked-table tr {
    break-inside: avoid;
    page-break-inside: avoid;
}

.linked-table th:nth-child(1) {
    width: 18%;
}

.linked-table th:nth-child(3) {
    width: 14%;
}

.linked-table th:nth-child(4) {
    width: 18%;
}

.workitem-type-icon {
    display: inline-block;
    min-width: 24px;
    margin-right: 5px;
    padding: 1px 3px;
    border-radius: 2px;
    color: white;
    font-size: 8pt;
    font-weight: 700;
    line-height: 1.2;
    text-align: center;
    vertical-align: middle;
}

.type-icon-issue {
    background: #d92727;
}

.type-icon-srs {
    background: #326da8;
}

.type-icon-other {
    background: #777;
}

.linked-id {
    font-weight: 700;
    vertical-align: middle;
}

.linked-table a {
    color: inherit;
    text-decoration: none;
}

.embedded-workitem-image {
    display: block;
    max-width: 100%;
    max-height: 230mm;
    width: auto;
    height: auto;
    margin: 8px auto;
    object-fit: contain;
    break-inside: avoid;
    page-break-inside: avoid;
}

.attachment-image {
    margin: 14px 0 22px;
    padding: 10px;
    border: 1px solid #bbb;
    break-inside: avoid;
    page-break-inside: avoid;
}

.attachment-image figcaption {
    margin-bottom: 8px;
    font-weight: 700;
    overflow-wrap: anywhere;
}

.attachment-image img {
    display: block;
    max-width: 100%;
    max-height: 230mm;
    width: auto;
    height: auto;
    margin: 0 auto;
    object-fit: contain;
}

.attachment-meta {
    margin-top: 7px;
    color: #666;
    font-size: 8.5pt;
}

.attachment-file {
    margin: 8px 0;
}

.attachment-file th {
    width: auto;
    background: #f5f5f5;
}

img {
    max-width: 100%;
    height: auto;
}

pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 8pt;
    background: #f5f5f5;
    padding: 8px;
}

.empty {
    color: #777;
    font-style: italic;
}

@media print {
    html,
    body {
        width: 210mm;
        margin: 0;
    }

    a {
        color: #000;
        text-decoration: none;
    }

    /* 검색창·목차 복귀 링크 등 화면 전용 UI는 인쇄물에 남기지 않는다. */
    .screen-only {
        display: none !important;
    }
}

.summary-dashboard,
.toc {
    break-after: page;
    page-break-after: always;
}

.stats-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
}

.stats-block {
    flex: 1 1 220px;
}

.stats-block h3 {
    margin-top: 0;
    font-size: 11pt;
}

.stats-table,
.toc-table {
    width: 100%;
    border-collapse: collapse;
}

.stats-table th,
.stats-table td,
.toc-table th,
.toc-table td {
    border: 1px solid #bbb;
    padding: 5px 6px;
    font-size: 8.5pt;
    overflow-wrap: anywhere;
}

.toc-table a {
    color: inherit;
    text-decoration: none;
}

/* ------------------------------------------------------------------
   화면(브라우저)에서만 적용되는 스타일.
   위쪽 규칙은 A4 인쇄 기준(pt 단위)이라 모니터에서는 글자가 작고
   본문이 화면 끝까지 늘어난다. 아래 블록은 화면에서만 덮어쓰므로
   PDF 출력 결과는 이전과 완전히 동일하다.
   ------------------------------------------------------------------ */
@media screen {
    body {
        max-width: 1100px;
        margin: 0 auto;
        padding: 0 28px 96px;
        font-size: 15px;
        line-height: 1.65;
        color: #1f2933;
        background: #ffffff;
    }

    .cover {
        min-height: auto;
        padding: 8px 0 26px;
        margin-bottom: 4px;
        border-bottom: 1px solid #dde3e9;
    }

    .cover h1 {
        font-size: 30px;
    }

    h1 {
        font-size: 23px;
    }

    h2 {
        margin-top: 30px;
        font-size: 18px;
    }

    h3 {
        font-size: 16px;
    }

    .wi-id {
        font-size: 19px;
        color: #176b87;
    }

    th,
    td {
        padding: 9px 10px;
        font-size: 14.5px;
    }

    .stats-table th,
    .stats-table td,
    .toc-table th,
    .toc-table td {
        padding: 8px 10px;
        font-size: 14px;
    }

    pre {
        font-size: 13px;
    }

    .attachment-meta {
        font-size: 13px;
    }

    .workitem-type-icon {
        font-size: 11px;
    }

    /* 화면에서는 A4 한 페이지 높이에 맞출 이유가 없다. */
    .embedded-workitem-image,
    .attachment-image img {
        max-height: none;
    }

    /* 인쇄물에서는 링크 표시가 방해지만, 화면에서는 눌러야 할 것이
       눌러야 할 것처럼 보여야 한다. */
    .toc-table a,
    .linked-table a {
        color: #176b87;
        text-decoration: underline;
        text-underline-offset: 2px;
    }

    .toc-table a:hover,
    .linked-table a:hover {
        color: #0f4f66;
    }

    .toc-table tbody tr:hover {
        background: #f4f8fa;
    }

    .work-item {
        margin-top: 34px;
        padding-top: 18px;
        border-top: 1px solid #e3e8ed;
    }

    /* 상단 고정 도구모음에 제목이 가려지지 않도록 여유를 둔다. */
    .work-item,
    .toc,
    .summary-dashboard {
        scroll-margin-top: 76px;
    }

    .screen-toolbar {
        position: sticky;
        top: 0;
        z-index: 20;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 10px;
        margin: 0 -28px 20px;
        padding: 12px 28px;
        background: #ffffff;
        border-bottom: 1px solid #dde3e9;
    }

    .screen-toolbar input[type="search"],
    .screen-toolbar select {
        font: inherit;
        font-size: 14px;
        padding: 7px 10px;
        color: inherit;
        background: #fff;
        border: 1px solid #c3ccd4;
        border-radius: 4px;
    }

    .screen-toolbar input[type="search"] {
        flex: 1 1 260px;
        min-width: 160px;
    }

    .toolbar-count {
        font-size: 13.5px;
        color: #5b6b7a;
        white-space: nowrap;
    }

    .toolbar-link {
        margin-left: auto;
        padding: 6px 12px;
        font-size: 14px;
        color: #176b87;
        text-decoration: none;
        border: 1px solid #c3ccd4;
        border-radius: 4px;
        white-space: nowrap;
    }

    .toolbar-link:hover {
        background: #f0f6f8;
    }

    .back-to-toc {
        float: right;
        font-size: 13px;
        color: #176b87;
        text-decoration: none;
    }

    .back-to-toc:hover {
        text-decoration: underline;
    }

    .filter-hidden {
        display: none !important;
    }

    .filter-empty {
        margin: 24px 0;
        padding: 16px;
        color: #5b6b7a;
        background: #f6f8fa;
        border: 1px dashed #c3ccd4;
    }
}
"""


def render_stats_table(counter: Counter, label: str) -> str:
    if not counter:
        return ""

    rows = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{value}</td></tr>"
        for key, value in counter.most_common()
    )

    return (
        "<table class='stats-table'>"
        f"<thead><tr><th>{html.escape(label)}</th><th>건수</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_summary_dashboard(stats: dict[str, Counter]) -> str:
    labels = {
        "status": "Status",
        "author": "Author",
        "type": "Type",
        "version": "Occurred Version",
    }

    blocks = []

    for key, label in labels.items():
        counter = stats.get(key)

        if not counter:
            continue

        blocks.append(
            "<div class='stats-block'>"
            f"<h3>{html.escape(label)}별 건수</h3>"
            f"{render_stats_table(counter, label)}"
            "</div>"
        )

    if not blocks:
        return ""

    return (
        "<section class='summary-dashboard'>"
        "<h2>요약 대시보드</h2>"
        f"<div class='stats-grid'>{''.join(blocks)}</div>"
        "</section>"
    )


def render_toc(entries: list[dict[str, str]]) -> str:
    if not entries:
        return ""

    row_list: list[str] = []

    for entry in entries:
        anchor = html.escape(entry["anchor"])
        link_target = f"#{anchor}"

        row_list.append(
            f"<tr data-anchor='{anchor}'>"
            f"<td><a href='{link_target}'>{html.escape(entry['id'])}</a></td>"
            f"<td><a href='{link_target}'>{html.escape(entry['title'])}</a></td>"
            f"<td>{html.escape(entry['status'])}</td>"
            "</tr>"
        )

    return (
        "<section class='toc' id='toc'>"
        "<h2>목차</h2>"
        "<table class='toc-table'>"
        "<thead><tr><th>번호</th><th>제목</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(row_list)}</tbody></table>"
        "</section>"
    )


def render_screen_toolbar(entries: list[dict[str, str]]) -> str:
    """화면에서만 보이는 검색/필터 도구모음 (인쇄·PDF에서는 숨겨진다)."""
    if not entries:
        return ""

    statuses = sorted(
        {
            entry["status"]
            for entry in entries
            if entry.get("status")
        }
    )

    options = "".join(
        f'<option value="{html.escape(status, quote=True)}">'
        f"{html.escape(status)}</option>"
        for status in statuses
    )

    return (
        "<div class='screen-toolbar screen-only'>"
        "<input type='search' id='wi-filter' autocomplete='off' "
        "placeholder='이슈 ID · 제목 · 본문 내용으로 검색'>"
        "<select id='wi-status'>"
        f"<option value=''>Status 전체</option>{options}"
        "</select>"
        f"<span class='toolbar-count' id='wi-count'>{len(entries)}건</span>"
        "<a class='toolbar-link' href='#toc'>목차로</a>"
        "</div>"
    )


# 화면 전용 검색/필터 동작. 인쇄·PDF에는 영향을 주지 않으며,
# 스크립트가 실행되지 않아도 문서 내용은 그대로 다 보인다.
SCREEN_SCRIPT = """
(function () {
  var input = document.getElementById('wi-filter');

  if (!input) {
    return;
  }

  var statusSelect = document.getElementById('wi-status');
  var counter = document.getElementById('wi-count');
  var emptyNotice = document.getElementById('wi-empty');

  var sections = Array.prototype.slice.call(
    document.querySelectorAll('.work-item')
  );
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('.toc-table tbody tr')
  );

  sections.forEach(function (section) {
    section.setAttribute(
      'data-search-text',
      (section.textContent || '').toLowerCase()
    );
  });

  function applyFilter() {
    var term = input.value.trim().toLowerCase();
    var status = statusSelect ? statusSelect.value : '';
    var matchedAnchors = {};
    var visible = 0;

    sections.forEach(function (section) {
      var text = section.getAttribute('data-search-text') || '';
      var sectionStatus = section.getAttribute('data-wi-status') || '';
      var matched =
        (term === '' || text.indexOf(term) !== -1) &&
        (status === '' || sectionStatus === status);

      section.classList.toggle('filter-hidden', !matched);
      matchedAnchors[section.id] = matched;

      if (matched) {
        visible += 1;
      }
    });

    rows.forEach(function (row) {
      var anchor = row.getAttribute('data-anchor') || '';
      row.classList.toggle('filter-hidden', !matchedAnchors[anchor]);
    });

    if (counter) {
      counter.textContent =
        term === '' && status === ''
          ? sections.length + '건'
          : visible + ' / ' + sections.length + '건';
    }

    if (emptyNotice) {
      emptyNotice.classList.toggle('filter-hidden', visible !== 0);
    }
  }

  input.addEventListener('input', applyFilter);

  if (statusSelect) {
    statusSelect.addEventListener('change', applyFilter);
  }
})();
"""


def make_html(
    project: str,
    query: str,
    count: int,
    sections: list[str],
    toc_entries: list[dict[str, str]] | None = None,
    summary_stats: dict[str, Counter] | None = None,
    document_title: str = DEFAULT_DOCUMENT_TITLE,
) -> str:
    dashboard_html = render_summary_dashboard(summary_stats or {})
    toc_html = render_toc(toc_entries or [])
    toolbar_html = render_screen_toolbar(toc_entries or [])

    # 브라우저 탭에서 구분되도록 프로젝트명을 붙이되,
    # 제목에 이미 들어 있으면 중복해서 쓰지 않는다.
    browser_title = (
        document_title
        if project.lower() in document_title.lower()
        else f"{document_title} - {project}"
    )

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(browser_title)}</title>
<style>{DOCUMENT_CSS}</style>
</head>
<body>

{toolbar_html}

<section class="cover">
  <h1>{html.escape(document_title)}</h1>
  <p><b>Project:</b> {html.escape(project)}</p>
  <p><b>Count:</b> {count}</p>
  <div class="query">
    <b>Query</b><br>{html.escape(query)}
  </div>
</section>

{dashboard_html}
{toc_html}
{''.join(sections)}

<div class="filter-empty screen-only filter-hidden" id="wi-empty">
  검색 조건에 맞는 이슈가 없습니다.
</div>

<script>{SCREEN_SCRIPT}</script>
</body>
</html>
"""


def make_standalone_html(workitem_id: str, title: str, section_html: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(workitem_id)} - {html.escape(title)}</title>
<style>{DOCUMENT_CSS}</style>
<style>
/* 이 문서에는 이슈가 1건뿐이라 돌아갈 목차가 없다. */
.back-to-toc {{
    display: none;
}}

@media screen {{
    .work-item {{
        margin-top: 0;
        padding-top: 0;
        border-top: none;
    }}

    body {{
        padding-top: 24px;
    }}
}}
</style>
</head>
<body>

{section_html}

</body>
</html>
"""


def markdown_field_value(value: Any) -> str:
    text = plain_text(value)
    return text if text.strip() else "-"


def resolve_body_image_path(
    source: str,
    attachment_lookup: dict[str, dict[str, Any]],
    image_relative_by_filename: dict[str, str],
) -> str | None:
    normalized = normalize_attachment_key(source)

    attachment = attachment_lookup.get(normalized) or attachment_lookup.get(
        re.sub(r"^\d+-", "", normalized)
    )

    if not attachment:
        return None

    filename = find_attachment_filename(attachment, 0)
    return image_relative_by_filename.get(filename)


def markdown_rich_value(
    value: Any,
    attachment_lookup: dict[str, dict[str, Any]],
    image_relative_by_filename: dict[str, str],
) -> str:
    text = markdown_field_value(value)

    if not (
        isinstance(value, dict)
        and "value" in value
        and "html" in str(value.get("type", "")).lower()
    ):
        return text

    soup = BeautifulSoup(str(value.get("value") or ""), "html.parser")
    image_lines = []

    for image in soup.find_all("img"):
        source = str(image.get("src") or "").strip()

        if not source:
            continue

        relative_path = resolve_body_image_path(
            source,
            attachment_lookup,
            image_relative_by_filename,
        )

        if relative_path:
            image_lines.append(f"![inline-image]({relative_path})")
        else:
            image_lines.append(f"[이미지 참조를 찾지 못함: {source}]")

    if not image_lines:
        return text

    return text + "\n\n" + "\n".join(image_lines)


def render_relationship_fields_markdown(workitem: dict[str, Any]) -> str:
    specifications = [
        ("project", "Project"),
        ("releaseVision", "Release Vision"),
        ("occurredVersion", "Occurred in Version"),
        ("targetVersion", "Target Version"),
    ]

    lines = []

    for relationship_name, label in specifications:
        values = [
            normalize_relationship_id(resource_id)
            for resource_id in relationship_ids(workitem, relationship_name)
        ]
        lines.append(f"- **{label}**: {', '.join(values) if values else '-'}")

    return "\n".join(lines)


def render_comments_markdown(
    comments: list[dict[str, Any]],
    attachment_lookup: dict[str, dict[str, Any]],
    image_relative_by_filename: dict[str, str],
) -> str:
    if not comments:
        return "댓글 없음"

    blocks = []

    for index, comment in enumerate(comments, 1):
        comment_attributes = attrs(comment)
        body = (
            comment_attributes.get("text")
            or comment_attributes.get("content")
            or comment_attributes.get("comment")
        )

        author_ids = relationship_ids(comment, "author")
        author = ", ".join(
            resource_id.rsplit("/", 1)[-1] for resource_id in author_ids
        )

        created = plain_text(
            comment_attributes.get("created") or comment_attributes.get("createdAt")
        )

        body_text = markdown_rich_value(
            body,
            attachment_lookup,
            image_relative_by_filename,
        )

        blocks.append(f"**#{index} {author} {created}**\n\n{body_text}")

    return "\n\n".join(blocks)


def render_linked_markdown(linked: list[dict[str, Any]]) -> str:
    if not linked:
        return "연결된 사양 및 이슈 없음"

    grouped: dict[str, list[str]] = {"사양": [], "이슈": [], "기타": []}

    for item in linked:
        role = plain_text(attrs(item).get("role"))

        target_ids = rel_ids(item, "workItem")
        target_resource_id = (
            target_ids[0] if target_ids else str(item.get("id", ""))
        )

        target = item.get("target") or {}
        target_attributes = attrs(target)

        display_id = target_resource_id.rsplit("/", 1)[-1]
        title = plain_text(target_attributes.get("title")) or "(제목 조회 안 됨)"
        item_type = plain_text(target_attributes.get("type")) or "unknown"

        category = get_workitem_type_info(item_type)["category"]

        grouped.setdefault(category, []).append(
            f"- [{item_type}] {display_id} — {title} ({role})"
        )

    labels = {"사양": "연관된 사양", "이슈": "연관된 이슈", "기타": "기타 연결 항목"}
    sections = []

    for category in ("사양", "이슈", "기타"):
        rows = grouped.get(category, [])
        if rows:
            sections.append(f"**{labels[category]}**\n\n" + "\n".join(rows))

    return "\n\n".join(sections) if sections else "연결된 사양 및 이슈 없음"


def render_attachments_markdown(records: list[dict[str, Any]]) -> str:
    if not records:
        return "첨부파일 없음"

    lines = []

    for record in records:
        filename = str(record.get("filename", ""))
        relative_path = str(record.get("relative_path", ""))
        status = str(record.get("status", ""))

        if relative_path and is_image_filename(filename):
            lines.append(f"![{filename}]({relative_path})")
        elif relative_path:
            lines.append(f"- [{filename}]({relative_path}) — {status}")
        else:
            lines.append(f"- {filename} — {status}")

    return "\n".join(lines)


def render_workitem_markdown(
    workitem: dict[str, Any],
    comments: list[dict[str, Any]],
    linked: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    display_fields: list[dict[str, str]],
    priority_fields: list[dict[str, str]],
    hidden_fields: set[str],
    attachment_lookup: dict[str, dict[str, Any]],
    image_relative_by_filename: dict[str, str],
) -> str:
    workitem_attributes = inject_relationship_fields(
        workitem, dict(attrs(workitem))
    )

    workitem_id = str(
        workitem_attributes.get("id")
        or str(workitem.get("id", "")).rsplit("/", 1)[-1]
    )

    title = plain_text(workitem_attributes.get("title")) or "(제목 없음)"

    used: set[str] = {"id", "title"}
    priority_blocks: list[str] = []
    normal_lines: list[str] = []

    def field_markdown(key: str) -> str:
        return markdown_rich_value(
            workitem_attributes.get(key),
            attachment_lookup,
            image_relative_by_filename,
        )

    for specification in priority_fields:
        key = specification["key"]
        used.add(key)

        if key in hidden_fields:
            continue

        priority_blocks.append(
            f"**{specification['label']}**\n\n{field_markdown(key)}"
        )

    for specification in display_fields:
        key = specification["key"]

        if key in used or key in hidden_fields or key not in workitem_attributes:
            continue

        used.add(key)

        normal_lines.append(f"- **{specification['label']}**: {field_markdown(key)}")

    for key in sorted(
        current_key
        for current_key in workitem_attributes.keys()
        if current_key not in used and current_key not in hidden_fields
    ):
        normal_lines.append(f"- **{key}**: {field_markdown(key)}")

    parts = [f"## {workitem_id} — {title}"]

    if priority_blocks:
        parts += ["", "### 발생원인 및 조치내역", "", "\n\n".join(priority_blocks)]

    parts += [
        "",
        "### Project 및 Version 정보",
        "",
        render_relationship_fields_markdown(workitem),
    ]

    if normal_lines:
        parts += ["", "### 전체 필드", "", "\n".join(normal_lines)]

    parts += [
        "",
        "### Comments",
        "",
        render_comments_markdown(comments, attachment_lookup, image_relative_by_filename),
    ]
    parts += ["", "### Linked Work Items", "", render_linked_markdown(linked)]
    parts += ["", "### Attachments", "", render_attachments_markdown(attachments)]

    return "\n".join(parts)


def make_markdown(
    project: str,
    query: str,
    count: int,
    sections: list[str],
    summary_stats: dict[str, Counter] | None = None,
    document_title: str = DEFAULT_DOCUMENT_TITLE,
) -> str:
    lines = [
        f"# {document_title}",
        "",
        f"- **Project**: {project}",
        f"- **Count**: {count}",
        f"- **Query**: `{query}`",
        "",
    ]

    for key, label in (
        ("status", "Status"),
        ("author", "Author"),
        ("type", "Type"),
        ("version", "Occurred Version"),
    ):
        counter = (summary_stats or {}).get(key)

        if not counter:
            continue

        lines.append(f"## {label}별 건수")
        lines.append("")

        for item_key, item_value in counter.most_common():
            lines.append(f"- {item_key}: {item_value}")

        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("\n\n---\n\n".join(sections))

    return "\n".join(lines)


def generate_pdf_from_html(
    html_path: Path,
    pdf_path: Path,
    landscape: bool = False,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exception:
        raise RuntimeError(
            "playwright가 설치되어 있지 않습니다. "
            "python -m pip install playwright 후 "
            "python -m playwright install chromium 을 실행하세요."
        ) from exception

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            try:
                page = browser.new_page()
                page.goto(html_path.resolve().as_uri())
                page.emulate_media(media="print")
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    landscape=landscape,
                    print_background=True,
                    margin={
                        "top": "12mm",
                        "bottom": "14mm",
                        "left": "12mm",
                        "right": "12mm",
                    },
                )
            finally:
                browser.close()

    except Exception as exception:
        if "Executable doesn't exist" in str(exception):
            raise RuntimeError(
                "Playwright용 Chromium이 설치되어 있지 않습니다. "
                "python -m playwright install chromium 을 실행하세요."
            ) from exception

        raise


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))

    if total_seconds < 60:
        return f"{total_seconds}초"

    minutes, second = divmod(total_seconds, 60)

    if minutes < 60:
        return f"{minutes}분 {second}초"

    hour, minute = divmod(minutes, 60)
    return f"{hour}시간 {minute}분"


def format_size(byte_count: int) -> str:
    size = float(byte_count)

    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"

        size /= 1024

    return f"{size:.1f} GB"


def open_in_default_application(target: Path) -> None:
    """생성된 결과를 OS 기본 프로그램으로 연다."""
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
    except Exception as exception:
        print(f"결과 자동 열기 실패 (경로를 직접 열어 주세요): {exception}")


def confirm_directory_removal(target: Path) -> bool:
    """기존 결과 폴더를 지우기 전에 확인한다.

    사람이 직접 실행한 경우에만 묻는다. 예약 실행처럼 입력을 받을 수 없는
    환경에서는 기존 동작 그대로 경고만 남기고 진행한다.
    """
    issue_directories = [
        entry for entry in target.iterdir() if entry.is_dir()
    ] if target.is_dir() else []

    if not sys.stdin.isatty():
        print(f"[경고] 기존 결과 폴더를 지우고 새로 만듭니다: {target}")
        return True

    print()
    print(f"[확인] 결과 폴더가 이미 있습니다: {target}")

    if issue_directories:
        print(
            f"        안에 이슈 폴더 {len(issue_directories)}개가 있고, "
            "모두 삭제됩니다."
        )

    print(
        "        이전 결과를 남기려면 -o 다른폴더 또는 "
        "--timestamp 를 사용하세요."
    )

    try:
        answer = input("        삭제하고 계속할까요? [y/N] ").strip().lower()
    except EOFError:
        return False

    return answer in {"y", "yes"}


def prepare_output_directory(
    output_directory: Path,
    assume_yes: bool = False,
) -> None:
    resolved = output_directory.resolve()

    forbidden_paths = {
        Path.cwd().resolve(),
        Path.home().resolve(),
        Path(resolved.anchor).resolve(),
    }

    if resolved in forbidden_paths:
        raise RuntimeError(
            f"안전을 위해 출력 폴더를 삭제할 수 없습니다: {resolved}"
        )

    if resolved.exists():
        if not assume_yes and not confirm_directory_removal(resolved):
            raise RuntimeError(
                "사용자가 취소했습니다. 기존 결과는 그대로 두었습니다."
            )

        print(f"기존 결과 폴더 삭제: {resolved}")
        shutil.rmtree(resolved)

    resolved.mkdir(parents=True, exist_ok=True)


def report_check_result(todo: list[str]) -> bool:
    print("-" * 58)

    if not todo:
        print("점검 결과: 준비 완료. 바로 실행할 수 있습니다.")
        return True

    print(f"점검 결과: 해결해야 할 항목 {len(todo)}개")

    for index, item in enumerate(todo, 1):
        print(f"  {index}. {item}")

    return False


def check_pdf_engine(todo: list[str]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [X ] PDF 엔진: playwright 미설치")
        todo.append(
            "python -m pip install -r requirements.txt 를 실행하세요."
        )
        return

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()

        print("  [OK] PDF 엔진(Chromium) 실행 확인")
    except Exception as exception:
        if "Executable doesn't exist" in str(exception):
            print("  [X ] PDF 엔진: Chromium 미설치")
            todo.append("python -m playwright install chromium 을 실행하세요.")
            return

        print(f"  [X ] PDF 엔진 오류: {exception}")
        todo.append(
            "PDF가 필요 없다면 config.yaml의 output.generate_pdf 를 "
            "false 로 두어도 됩니다."
        )


def run_environment_check(config_path: Path) -> bool:
    """Polarion에 접속하기 전에 막힐 만한 지점을 미리 점검한다."""
    print("실행 환경 점검")
    print("-" * 58)

    todo: list[str] = []

    if not config_path.is_file():
        print(f"  [X ] 설정 파일 없음: {config_path.resolve()}")
        todo.append(
            "설정 파일을 만드세요:  "
            f"copy config.example.yaml {config_path.name}"
        )
        return report_check_result(todo)

    print(f"  [OK] 설정 파일: {config_path.resolve()}")

    try:
        config = load_config(config_path)
    except Exception as exception:
        print(f"  [X ] 설정 파일을 읽을 수 없습니다: {exception}")
        todo.append(f"{config_path.name} 의 YAML 문법을 확인하세요.")
        return report_check_result(todo)

    polarion_config = config.get("polarion") or {}
    output_config = config.get("output") or {}

    host = str(polarion_config.get("host", "")).strip()
    project_id = str(polarion_config.get("project_id", "")).strip()

    if not host or host in PLACEHOLDER_CONFIG_VALUES:
        print(f"  [X ] 서버 주소가 예시값입니다: {host or '(비어 있음)'}")
        todo.append(
            f"{config_path.name} 의 polarion.host 에 "
            "사내 Polarion 주소를 넣으세요."
        )
    else:
        print(f"  [OK] 서버 주소: {host}")

    if not project_id or project_id in PLACEHOLDER_CONFIG_VALUES:
        print(
            "  [X ] 프로젝트 ID가 예시값입니다: "
            f"{project_id or '(비어 있음)'}"
        )
        todo.append(
            f"{config_path.name} 의 polarion.project_id 를 "
            "사내 프로젝트 ID로 바꾸세요."
        )
    else:
        print(f"  [OK] 프로젝트 ID: {project_id}")

    token_name = str(polarion_config.get("token_env", "POLARION_TOKEN"))
    token = os.environ.get(token_name, "").strip()

    if token:
        print(f"  [OK] 환경변수 {token_name}: 설정됨 ({len(token)}자)")
    else:
        print(f"  [X ] 환경변수 {token_name}: 없어서 로그인할 수 없습니다")
        todo.append(
            f'이번 창에서만:  $env:{token_name}="발급받은 PAT"'
        )
        todo.append(
            f'계속 쓰려면:    setx {token_name} "발급받은 PAT"  '
            "(새 PowerShell 창부터 적용)"
        )

    if bool(output_config.get("generate_pdf", True)):
        check_pdf_engine(todo)
    else:
        print("  [--] PDF 생성이 꺼져 있어 Chromium 점검은 건너뜁니다.")

    if not todo:
        try:
            client = PolarionClient(config)
            client.get_json(
                f"{client.base_api}/projects/"
                f"{quote(client.project_id, safe='')}"
            )
            print("  [OK] Polarion 접속/권한 확인")
        except Exception as exception:
            print(f"  [X ] Polarion 접속 실패: {exception}")
            todo.append(
                "위 오류 메시지를 보고 주소·PAT·프로젝트 ID를 확인하세요."
            )

    return report_check_result(todo)


def escape_query_value(value: str) -> str:
    """Polarion(Lucene) 쿼리에서 특별한 뜻을 갖는 문자를 그대로 쓰게 만든다.

    이슈 ID의 '-'가 대표적이다. VP-6955를 그대로 넣으면 '6955를 제외하라'는
    뜻이 되므로 VP\\-6955로 써야 한다.
    """
    return "".join(
        f"\\{character}" if character in LUCENE_SPECIAL_CHARACTERS else character
        for character in value
    )


def build_query_from_ids(raw_ids: str) -> str:
    """'VP-6955, VP-7001' 같은 입력을 Polarion 검색 Query로 바꾼다."""
    tokens = [
        token.strip()
        for token in re.split(r"[,\s]+", raw_ids)
        if token.strip()
    ]

    if not tokens:
        return ""

    return " OR ".join(f"id:{escape_query_value(token)}" for token in tokens)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polarion_query_backup.py",
        description=(
            "Polarion 검색 Query 결과를 "
            "PDF/HTML/Markdown으로 Export합니다."
        ),
        epilog=(
            "예시:\n"
            "  python polarion_query_backup.py -id VP-6955\n"
            "  python polarion_query_backup.py -id VP-6955,VP-7001 --open\n"
            "  python polarion_query_backup.py "
            "-query \"status:(in_progress) AND author.id:(hong)\"\n"
            "  python polarion_query_backup.py -id VP-6955 -o 결과_6955\n"
            "  python polarion_query_backup.py --limit 1"
            "          # 필드 확인용으로 1건만\n"
            "  python polarion_query_backup.py --check"
            "            # 실행 전 환경 점검만\n"
            "  python polarion_query_backup.py"
            "                  # config.yaml의 search.query 사용"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    target_group = parser.add_argument_group(
        "검색 대상 (-id 와 -query 중 하나만)"
    )

    target_group.add_argument(
        "-id",
        "--id",
        dest="issue_ids",
        default=None,
        metavar="ID",
        help=(
            "이슈 ID를 직접 지정합니다. 쉼표나 공백으로 여러 개를 넣을 수 있고, "
            "쿼리 문법(\\-)은 자동으로 처리합니다. 예: -id VP-6955,VP-7001"
        ),
    )

    target_group.add_argument(
        "-q",
        "-query",
        "--query",
        dest="query",
        default=None,
        metavar="QUERY",
        help=(
            "이번 실행에만 사용할 Polarion 검색 Query. "
            "지정하면 설정 파일의 search.query 대신 이 값을 사용합니다."
        ),
    )

    target_group.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "이번 실행에만 적용할 최대 건수 "
            "(설정 파일의 search.max_items 대신 사용). "
            "필드 매핑을 확인할 때 --limit 1 이 편합니다."
        ),
    )

    output_group = parser.add_argument_group("출력")

    output_group.add_argument(
        "-o",
        "--out",
        dest="out",
        default=None,
        metavar="PATH",
        help=(
            "결과를 저장할 폴더 "
            "(설정 파일의 output.directory 대신 사용)"
        ),
    )

    output_group.add_argument(
        "--title",
        dest="document_title",
        default=None,
        metavar="TEXT",
        help=(
            "결과 문서 맨 위에 찍히는 제목 "
            "(기본값: config.yaml 의 output.document_title, "
            f"없으면 \"{DEFAULT_DOCUMENT_TITLE}\")"
        ),
    )

    output_group.add_argument(
        "--timestamp",
        action="store_true",
        help=(
            "결과를 [출력폴더]/20260915_143012/ 처럼 실행 시각 하위 폴더에 "
            "저장합니다. 이전 결과가 지워지지 않습니다."
        ),
    )

    output_group.add_argument(
        "--open",
        dest="open_result",
        action="store_true",
        help=(
            "완료 후 결과 문서를 자동으로 엽니다 "
            "(PDF를 만들었으면 PDF, 아니면 HTML)."
        ),
    )

    output_group.add_argument(
        "-y",
        "--yes",
        dest="assume_yes",
        action="store_true",
        help="기존 결과 폴더 삭제 확인을 건너뜁니다.",
    )

    etc_group = parser.add_argument_group("기타")

    etc_group.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="설정 파일 경로 (기본값: config.yaml)",
    )

    etc_group.add_argument(
        "--check",
        action="store_true",
        help=(
            "Polarion에 접속하지 않고 설정·토큰·PDF 엔진 준비 상태만 "
            "점검하고 끝냅니다."
        ),
    )

    return parser


def resolve_query(
    cli_query: str | None,
    cli_ids: str | None,
    search_config: dict[str, Any],
    config_path: Path,
) -> tuple[str, str]:
    """-id > -query > 설정 파일 순으로 검색 조건을 정한다."""
    config_source = f"{config_path.name} > search.query"

    if cli_ids is not None:
        id_query = build_query_from_ids(cli_ids)

        if not id_query:
            raise RuntimeError(
                "-id 값이 비어 있습니다. 예: -id VP-6955 또는 -id VP-6955,VP-7001"
            )

        return id_query, "명령행 -id"

    if cli_query is not None:
        return cli_query.strip(), "명령행 -query"

    config_query = search_config.get("query")

    if config_query is None:
        return "", config_source

    return str(config_query).strip(), config_source


def main() -> None:
    arguments = build_argument_parser().parse_args()

    config_path = Path(arguments.config)

    if arguments.check:
        sys.exit(0 if run_environment_check(config_path) else 1)

    if arguments.issue_ids is not None and arguments.query is not None:
        raise RuntimeError(
            "-id 와 -query 는 함께 쓸 수 없습니다. 둘 중 하나만 지정하세요."
        )

    if not config_path.is_file():
        raise RuntimeError(
            f"설정 파일을 찾을 수 없습니다: {config_path.resolve()}\n"
            "  다음 명령으로 만든 뒤 사내 환경에 맞게 값을 채우세요.\n"
            f"    copy config.example.yaml {config_path.name}\n"
            "  준비 상태 확인:  python polarion_query_backup.py --check"
        )

    config = load_config(config_path)

    search_config = config["search"]
    fields_config = config["fields"]
    output_config = config["output"]

    query, query_source = resolve_query(
        arguments.query,
        arguments.issue_ids,
        search_config,
        config_path,
    )

    if not query:
        raise RuntimeError(
            "검색 조건이 비어 있습니다. 아래 중 하나를 쓰세요.\n"
            "  이슈 ID로:    -id VP-6955\n"
            '  검색 쿼리로:  -query "status:(in_progress)"\n'
            f"  설정 파일로:  {config_path.name} 의 search.query 채우기"
        )

    document_title = (
        arguments.document_title
        or str(
            output_config.get(
                "document_title",
                DEFAULT_DOCUMENT_TITLE,
            )
        ).strip()
        or DEFAULT_DOCUMENT_TITLE
    )

    started_at = time.monotonic()

    print(f"Query: {query}    (출처: {query_source})")
    print("Polarion 검색 중...")

    client = PolarionClient(config)

    workitems = client.query_workitems(
        query=query,
        sort=str(search_config.get("sort", "id")),
        fields=str(
            fields_config.get(
                "workitem_sparse_fields",
                "@all",
            )
        ),
    )

    max_items = (
        arguments.limit
        if arguments.limit is not None
        else int(search_config.get("max_items", 0))
    )

    if max_items > 0:
        workitems = workitems[:max_items]

    if not workitems:
        print(
            "검색 결과가 0건입니다. 기존 결과 폴더는 그대로 두었습니다.\n"
            "  Polarion 검색 화면의 Query Pane > Convert to Text 결과를 "
            "그대로 쓰는 것이 가장 안전합니다."
        )
        return

    print(f"검색 결과: {len(workitems)}건")

    output_directory = Path(
        arguments.out
        if arguments.out
        else output_config.get(
            "directory",
            "polarion_backup",
        )
    )

    if arguments.timestamp:
        output_directory = output_directory / time.strftime("%Y%m%d_%H%M%S")

    output_directory = output_directory.resolve()

    prepare_output_directory(output_directory, arguments.assume_yes)
    print(f"출력 폴더: {output_directory}")

    if output_config.get("save_field_inventory", True):
        (
            output_directory / "field_inventory.json"
        ).write_text(
            pretty(inventory_fields(workitems)),
            encoding="utf-8",
        )

    sections: list[str] = []
    markdown_sections: list[str] = []
    toc_entries: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    summary_stats = compute_summary_stats(workitems)

    generate_markdown = bool(output_config.get("generate_markdown", True))
    generate_per_issue_html = bool(
        output_config.get("generate_per_issue_html", True)
    )
    generate_pdf = bool(output_config.get("generate_pdf", True))

    skip_extensions = {
        (
            str(extension).lower()
            if str(extension).startswith(".")
            else f".{str(extension).lower()}"
        )
        for extension in output_config.get(
            "skip_attachment_extensions",
            sorted(DEFAULT_VIDEO_EXTENSIONS),
        )
    }

    continue_on_attachment_error = bool(
        output_config.get(
            "continue_on_attachment_error",
            True,
        )
    )

    embed_images = bool(
        output_config.get(
            "embed_images_in_html",
            True,
        )
    )

    for index, workitem in enumerate(workitems, 1):
        workitem_attributes = attrs(workitem)

        workitem_id = str(
            workitem_attributes.get("id")
            or str(workitem.get("id", "")).rsplit("/", 1)[-1]
        )

        item_started = time.monotonic()

        print(f"[{index}/{len(workitems)}] {workitem_id}", flush=True)

        anchor = f"wi-{index}"

        workitem_directory = (
            output_directory / safe_filename(workitem_id)
        )
        workitem_directory.mkdir(parents=True, exist_ok=True)

        try:
            comments = client.workitem_children(
                workitem,
                [("comments", "workitem_comments")],
                str(
                    fields_config.get(
                        "comment_sparse_fields",
                        "@all",
                    )
                ),
            )

            attachments_raw = client.workitem_children(
                workitem,
                [("attachments", "workitem_attachments")],
                str(
                    fields_config.get(
                        "attachment_sparse_fields",
                        "@all",
                    )
                ),
            )

            linked = client.workitem_children(
                workitem,
                [
                    ("linkedWorkItems", "linkedworkitems"),
                    ("linkedworkitems", "linkedworkitems"),
                ],
                str(
                    fields_config.get(
                        "linkeditem_sparse_fields",
                        "@all",
                    )
                ),
            )

            if output_config.get(
                "enrich_linked_workitems",
                True,
            ):
                for linked_item in linked:
                    target_ids = rel_ids(
                        linked_item,
                        "workItem",
                    )

                    if not target_ids:
                        continue

                    try:
                        linked_item["target"] = (
                            client.get_workitem_resource(
                                target_ids[0],
                                fields="@all",
                            )
                        )
                    except Exception as linked_error:
                        linked_item["target_error"] = str(
                            linked_error
                        )

            attachment_lookup = build_attachment_lookup(
                attachments_raw
            )

            used_image_references = (
                find_used_image_references(
                    workitem_attributes
                )
            )

            attachment_records: list[dict[str, Any]] = []

            for attachment_index, attachment in enumerate(
                attachments_raw,
                1,
            ):
                filename = find_attachment_filename(
                    attachment,
                    attachment_index,
                )

                extension = Path(filename).suffix.lower()
                attachment_attributes = attrs(attachment)
                content_url = (
                    attachment.get("links") or {}
                ).get("content")

                attachment_id = str(
                    attachment_attributes.get("id")
                    or attachment.get(
                        "id",
                        "",
                    ).rsplit("/", 1)[-1]
                ).strip().lower()

                normalized_filename = (
                    filename.strip().lower()
                )

                attachment_without_prefix = re.sub(
                    r"^\d+-",
                    "",
                    attachment_id,
                )

                used_in_body = any(
                    candidate in used_image_references
                    for candidate in {
                        attachment_id,
                        normalized_filename,
                        attachment_without_prefix,
                    }
                )

                record: dict[str, Any] = {
                    "filename": filename,
                    "size": (
                        attachment_attributes.get("size")
                        or attachment_attributes.get("sizeBytes")
                        or attachment_attributes.get("length")
                        or ""
                    ),
                    "status": "목록만 저장",
                    "relative_path": "",
                    "error": "",
                    "image_data_uri": "",
                    "used_in_body": used_in_body,
                }

                if is_image_filename(filename):
                    fetched = fetch_attachment_image_bytes(client, attachment)

                    if fetched:
                        binary, mime_type = fetched
                        encoded = base64.b64encode(binary).decode("ascii")
                        record["image_data_uri"] = (
                            f"data:{mime_type};base64,{encoded}"
                        )
                        record["status"] = "HTML에 이미지 포함"

                        try:
                            target = (
                                workitem_directory / "attachments" / filename
                            )
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(binary)
                            record["relative_path"] = target.relative_to(
                                output_directory
                            ).as_posix()
                        except Exception as save_error:
                            print(
                                f"  이미지 파일 저장 실패: {filename}\n"
                                f"    원인: {save_error}"
                            )
                    else:
                        record["status"] = "이미지 삽입 실패"

                    attachment_records.append(record)
                    continue

                if extension in skip_extensions:
                    record["status"] = (
                        f"다운로드 제외 파일 ({extension})"
                    )

                    attachment_records.append(record)
                    print(f"  첨부파일 스킵: {filename}")
                    continue

                if not output_config.get(
                    "download_attachments",
                    True,
                ):
                    record["status"] = (
                        "첨부파일 다운로드 비활성화"
                    )
                    attachment_records.append(record)
                    continue

                if not content_url:
                    record["status"] = "content 링크 없음"
                    attachment_records.append(record)
                    print(
                        f"  첨부파일 링크 없음: {filename}"
                    )
                    continue

                try:
                    target = (
                        workitem_directory
                        / "attachments"
                        / filename
                    )

                    print(
                        f"    첨부 {attachment_index}/{len(attachments_raw)} "
                        f"내려받는 중: {filename}",
                        flush=True,
                    )

                    client.download(content_url, target)

                    record["status"] = "다운로드 완료"
                    record["relative_path"] = (
                        target.relative_to(
                            output_directory
                        ).as_posix()
                    )

                except Exception as attachment_error:
                    record["status"] = "다운로드 실패"
                    record["error"] = str(attachment_error)

                    print(
                        f"  첨부파일 다운로드 실패: {filename}\n"
                        f"    원인: {attachment_error}"
                    )

                    if not continue_on_attachment_error:
                        raise

                attachment_records.append(record)

            if output_config.get(
                "save_raw_json",
                True,
            ):
                raw_backup = {
                    "workitem": workitem,
                    "comments": comments,
                    "linkedWorkItems": linked,
                    "attachments": attachments_raw,
                }

                (
                    workitem_directory / "backup.json"
                ).write_text(
                    pretty(raw_backup),
                    encoding="utf-8",
                )

            display_fields = fields_config.get("display", [])
            priority_fields = fields_config.get("priority_fields", [])
            hidden_fields = set(fields_config.get("hidden_fields", []))

            title_display = (
                plain_text(workitem_attributes.get("title")) or "(제목 없음)"
            )
            status_display = plain_text(workitem_attributes.get("status")) or "-"

            toc_entries.append(
                {
                    "id": workitem_id,
                    "title": title_display,
                    "status": status_display,
                    "anchor": anchor,
                }
            )

            section_html = render_workitem(
                client,
                workitem,
                comments,
                linked,
                attachment_records,
                display_fields,
                priority_fields,
                hidden_fields,
                attachment_lookup,
                embed_images,
                anchor,
                status_display,
            )

            sections.append(section_html)

            if generate_per_issue_html:
                (workitem_directory / "report.html").write_text(
                    make_standalone_html(
                        workitem_id,
                        title_display,
                        section_html,
                    ),
                    encoding="utf-8",
                )

            if generate_markdown:
                image_relative_by_filename = {
                    record["filename"]: record["relative_path"]
                    for record in attachment_records
                    if record.get("relative_path")
                    and is_image_filename(record["filename"])
                }

                markdown_sections.append(
                    render_workitem_markdown(
                        workitem,
                        comments,
                        linked,
                        attachment_records,
                        display_fields,
                        priority_fields,
                        hidden_fields,
                        attachment_lookup,
                        image_relative_by_filename,
                    )
                )

            item_elapsed = time.monotonic() - item_started
            remaining_estimate = (
                (time.monotonic() - started_at) / index
                * (len(workitems) - index)
            )

            print(
                f"    완료 · 댓글 {len(comments)} · 링크 {len(linked)} · "
                f"첨부 {len(attachment_records)} · "
                f"{item_elapsed:.1f}초"
                + (
                    f" · 남은 예상 {format_duration(remaining_estimate)}"
                    if index < len(workitems)
                    else ""
                )
            )

        except Exception as exception:
            failures.append(
                {
                    "id": workitem_id,
                    "error": str(exception),
                }
            )

            print(
                f"  Work Item 처리 실패: {workitem_id}\n"
                f"    원인: {exception}"
            )

            toc_entries.append(
                {
                    "id": workitem_id,
                    "title": "(처리 실패)",
                    "status": "실패",
                    "anchor": anchor,
                }
            )

            sections.append(
                f'<section class="work-item" id="{html.escape(anchor)}"'
                ' data-wi-status="실패">'
                '<a class="back-to-toc screen-only" href="#toc">↑ 목차</a>'
                f"<h1>{html.escape(workitem_id)} 실패</h1>"
                f"<pre>{html.escape(str(exception))}</pre>"
                "</section>"
            )

            if generate_markdown:
                markdown_sections.append(
                    f"## {workitem_id} — 처리 실패\n\n{exception}"
                )

    html_path = (
        output_directory
        / output_config.get(
            "html_filename",
            "polarion_query_backup.html",
        )
    )

    print("통합 문서 만드는 중...", flush=True)

    html_path.write_text(
        make_html(
            client.project_id,
            query,
            len(workitems),
            sections,
            toc_entries,
            summary_stats,
            document_title,
        ),
        encoding="utf-8",
    )

    result_lines: list[str] = []
    primary_result = html_path

    if generate_pdf:
        pdf_path = output_directory / output_config.get(
            "pdf_filename",
            "polarion_query_backup.pdf",
        )

        print("PDF로 변환하는 중...", flush=True)

        try:
            generate_pdf_from_html(
                html_path,
                pdf_path,
                landscape=bool(output_config.get("pdf_landscape", False)),
            )
            result_lines.append(f"PDF      : {pdf_path.resolve()}")
            primary_result = pdf_path
        except Exception as exception:
            print(
                f"PDF 생성 실패 (HTML/Markdown은 정상 생성됨): {exception}"
            )
            result_lines.append("PDF      : 생성 실패 (위 메시지 참고)")

    result_lines.append(f"HTML     : {html_path.resolve()}")

    if generate_markdown:
        md_path = output_directory / output_config.get(
            "md_filename",
            "polarion_query_backup.md",
        )

        md_path.write_text(
            make_markdown(
                client.project_id,
                query,
                len(workitems),
                markdown_sections,
                summary_stats,
                document_title,
            ),
            encoding="utf-8",
        )

        result_lines.append(f"Markdown : {md_path.resolve()}")

    total_elapsed = time.monotonic() - started_at

    manifest = {
        "documentTitle": document_title,
        "query": query,
        "querySource": query_source,
        "count": len(workitems),
        "failureCount": len(failures),
        "failures": failures,
        "outputDirectory": str(output_directory),
        "elapsedSeconds": round(total_elapsed, 1),
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    (
        output_directory / "manifest.json"
    ).write_text(
        pretty(manifest),
        encoding="utf-8",
    )

    print()
    print("-" * 58)
    print(
        f"완료: {len(workitems)}건 (실패 {len(failures)}건) · "
        f"총 {format_duration(total_elapsed)}"
    )

    for line in result_lines:
        print(f"  {line}")

    print(f"  폴더     : {output_directory}")

    if failures:
        print()
        print(f"  실패한 이슈 {len(failures)}건 (상세: manifest.json)")

        for failure in failures[:5]:
            print(f"    - {failure['id']}: {failure['error'][:80]}")

        if len(failures) > 5:
            print(f"    ... 외 {len(failures) - 5}건")

    if arguments.open_result:
        open_in_default_application(primary_result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exception:
        print(
            f"실행 실패: {exception}",
            file=sys.stderr,
        )
        sys.exit(1)
