"""설정 로딩: config.yaml + .env(POLARION_TOKEN 등)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ProjectSpec:
    id: str
    query: str
    fetch_comments: bool = True


@dataclass
class VXvueGroup:
    file_key: str
    display_name: str
    modules: list[str]


@dataclass
class SingleFileProject:
    project_id: str
    file_key: str
    display_name: str


@dataclass
class Config:
    host: str
    token: str
    verify_ssl: bool
    timeout_seconds: int
    request_interval_seconds: float
    page_size: int
    projects: list[ProjectSpec]
    content_field_priority: list[str]
    vxvue_project_id: str
    vxvue_groups: list[VXvueGroup]
    single_file_projects: list[SingleFileProject]
    base_dir: Path
    archive_dir: Path
    snapshots_dir: Path
    logs_dir: Path
    knowledge_folder: Path | None
    filename_prefix: str
    filename_date_format: str
    min_expected_srs_ratio: float
    require_all_pdfs: bool
    org_folder: Path | None = None
    known_problem_srs: list[str] = field(default_factory=list)
    pdf_timeout_seconds: int = 300
    raw: dict[str, Any] = field(default_factory=dict)


class ConfigError(RuntimeError):
    pass


def load_config(config_path: str | Path | None = None, env_path: str | Path | None = None) -> Config:
    load_dotenv(env_path or (PROJECT_ROOT / ".env"))

    path = Path(config_path) if config_path else (PROJECT_ROOT / "config" / "config.yaml")
    if not path.exists():
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {path}. "
            f"config/config.example.yaml 을 config/config.yaml 로 복사한 뒤 값을 채워주세요."
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    pol = raw.get("polarion", {})
    token_env = pol.get("token_env", "POLARION_TOKEN")
    token = os.environ.get(token_env)
    if not token:
        raise ConfigError(
            f"환경변수 {token_env} 가 설정되어 있지 않습니다. "
            f".env 파일 또는 시스템 환경변수에 Polarion Personal Access Token을 설정해주세요."
        )

    projects = [
        ProjectSpec(id=p["id"], query=p.get("query", "type:srs"), fetch_comments=p.get("fetch_comments", True))
        for p in raw.get("projects", [])
    ]
    if not projects:
        raise ConfigError("config.yaml의 projects 목록이 비어 있습니다.")

    partition = raw.get("partition", {})
    vxvue_groups = [
        VXvueGroup(file_key=g["file_key"], display_name=g["display_name"], modules=list(g["modules"]))
        for g in partition.get("vxvue_groups", [])
    ]
    single_file_projects = [
        SingleFileProject(project_id=s["project_id"], file_key=s["file_key"], display_name=s["display_name"])
        for s in partition.get("single_file_projects", [])
    ]

    out = raw.get("output", {})
    knowledge_folder_raw = out.get("knowledge_folder") or ""
    knowledge_folder = Path(knowledge_folder_raw) if knowledge_folder_raw else None
    org_folder_raw = out.get("org_folder") or ""
    org_folder = Path(org_folder_raw) if org_folder_raw else None

    validation = raw.get("validation", {})

    def _resolve_dir(name: str) -> Path:
        p = Path(out.get(name, name.split("_")[0]))
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    return Config(
        host=pol["host"].rstrip("/"),
        token=token,
        verify_ssl=bool(pol.get("verify_ssl", True)),
        timeout_seconds=int(pol.get("timeout_seconds", 90)),
        request_interval_seconds=float(pol.get("request_interval_seconds", 0.15)),
        page_size=int(pol.get("page_size", 100)),
        projects=projects,
        content_field_priority=raw.get("content", {}).get("field_priority", ["descriptionKR", "description"]),
        vxvue_project_id=partition.get("vxvue_project_id", ""),
        vxvue_groups=vxvue_groups,
        single_file_projects=single_file_projects,
        base_dir=_resolve_dir("base_dir"),
        archive_dir=_resolve_dir("archive_dir"),
        snapshots_dir=_resolve_dir("snapshots_dir"),
        logs_dir=_resolve_dir("logs_dir"),
        knowledge_folder=knowledge_folder,
        org_folder=org_folder,
        filename_prefix=out.get("filename_prefix", "(사양서) "),
        filename_date_format=out.get("filename_date_format", "%y%m%d"),
        min_expected_srs_ratio=float(validation.get("min_expected_srs_ratio", 0.95)),
        require_all_pdfs=bool(validation.get("require_all_pdfs", True)),
        known_problem_srs=[str(u) for u in (raw.get("render", {}) or {}).get("known_problem_srs", []) or []],
        pdf_timeout_seconds=int((raw.get("render", {}) or {}).get("pdf_timeout_seconds", 300)),
        raw=raw,
    )
