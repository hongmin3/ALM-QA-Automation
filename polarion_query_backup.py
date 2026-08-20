from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import shutil
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

    def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        time.sleep(self.interval)

        response = self.session.request(
            method,
            url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            **kwargs,
        )

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

        response.raise_for_status()
        return response

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
<section class="work-item" id="{html.escape(anchor)}">
  <header>
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

    rows = "".join(
        "<tr>"
        f"<td><a href='#{html.escape(entry['anchor'])}'>{html.escape(entry['id'])}</a></td>"
        f"<td>{html.escape(entry['title'])}</td>"
        f"<td>{html.escape(entry['status'])}</td>"
        "</tr>"
        for entry in entries
    )

    return (
        "<section class='toc'>"
        "<h2>목차</h2>"
        "<table class='toc-table'>"
        "<thead><tr><th>번호</th><th>제목</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


def make_html(
    project: str,
    query: str,
    count: int,
    sections: list[str],
    toc_entries: list[dict[str, str]] | None = None,
    summary_stats: dict[str, Counter] | None = None,
) -> str:
    dashboard_html = render_summary_dashboard(summary_stats or {})
    toc_html = render_toc(toc_entries or [])

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Polarion Query Backup</title>
<style>{DOCUMENT_CSS}</style>
</head>
<body>

<section class="cover">
  <h1>Polarion Work Item Backup</h1>
  <p><b>Project:</b> {html.escape(project)}</p>
  <p><b>Count:</b> {count}</p>
  <div class="query">
    <b>Query</b><br>{html.escape(query)}
  </div>
</section>

{dashboard_html}
{toc_html}
{''.join(sections)}

</body>
</html>
"""


def make_standalone_html(workitem_id: str, title: str, section_html: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{html.escape(workitem_id)} - {html.escape(title)}</title>
<style>{DOCUMENT_CSS}</style>
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
) -> str:
    lines = [
        "# Polarion Work Item Backup",
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


def prepare_output_directory(output_directory: Path) -> None:
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
        print(f"기존 결과 폴더 삭제: {resolved}")
        shutil.rmtree(resolved)

    resolved.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    arguments = parser.parse_args()

    config = load_config(Path(arguments.config))
    client = PolarionClient(config)

    search_config = config["search"]
    fields_config = config["fields"]
    output_config = config["output"]

    query = str(search_config["query"]).strip()

    if not query:
        raise RuntimeError(
            "config.yaml의 search.query가 비어 있습니다."
        )

    print(f"Query: {query}")

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

    max_items = int(search_config.get("max_items", 0))

    if max_items > 0:
        workitems = workitems[:max_items]

    output_directory = Path(
        output_config.get(
            "directory",
            "polarion_backup",
        )
    ).resolve()

    prepare_output_directory(output_directory)

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

        print(f"[{index}/{len(workitems)}] {workitem_id}")

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
                f'<section class="work-item" id="{html.escape(anchor)}">'
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

    html_path.write_text(
        make_html(
            client.project_id,
            query,
            len(workitems),
            sections,
            toc_entries,
            summary_stats,
        ),
        encoding="utf-8",
    )

    if generate_pdf:
        pdf_path = output_directory / output_config.get(
            "pdf_filename",
            "polarion_query_backup.pdf",
        )

        try:
            generate_pdf_from_html(
                html_path,
                pdf_path,
                landscape=bool(output_config.get("pdf_landscape", False)),
            )
            print(f"PDF: {pdf_path.resolve()}")
        except Exception as exception:
            print(
                f"PDF 생성 실패 (HTML/Markdown은 정상 생성됨): {exception}"
            )

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
            ),
            encoding="utf-8",
        )

        print(f"Markdown: {md_path.resolve()}")

    manifest = {
        "query": query,
        "count": len(workitems),
        "failureCount": len(failures),
        "failures": failures,
    }

    (
        output_directory / "manifest.json"
    ).write_text(
        pretty(manifest),
        encoding="utf-8",
    )

    print(f"HTML: {html_path.resolve()}")
    print(
        f"총 {len(workitems)}개, "
        f"실패 {len(failures)}개"
    )


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
