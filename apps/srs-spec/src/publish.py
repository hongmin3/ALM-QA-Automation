"""검증 통과한 신규 PDF를 실제 지식파일 폴더에 반영한다.

교체된 이전 세대 사양서는 지식파일 폴더와 같은 프로젝트 안의 `ORG/<YYMMDD>/`로
옮겨 보관한다. 이 보관본은 **다음 배포가 완료되었을 때** 자동으로 삭제된다.
즉 ORG에는 항상 '직전 세대 하나'만 남는다.

왜 이 순서인가:
- 신규 사양서에 문제가 있으면 사람이 ORG에서 직전 버전을 바로 되찾을 수 있어야 한다.
- 다음 주 실행이 검증을 통과했다는 것은 그 사이 한 주 동안 직전 버전으로 되돌릴 일이
  없었다는 뜻이므로, 그때 정리하는 것이 안전하다.
- 이미 사람이 지웠거나 폴더가 없으면 아무 것도 하지 않는다(오류 아님).

검증에 실패하면 이 모듈은 아예 호출되지 않는다 - 실패한 실행은 기존 배포본을
건드리지 않는다(`main.py` 참고).
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("srs_automation")

SPEC_GLOB = "(사양서)*.pdf"


@dataclass
class PublishResult:
    """메일 알림과 리포트에 그대로 실어 보낼 수 있는 반영 결과."""

    published: list[str] = field(default_factory=list)
    retained: list[str] = field(default_factory=list)
    overwritten: list[str] = field(default_factory=list)
    purged_generations: list[str] = field(default_factory=list)
    purged_files: int = 0
    org_folder: str | None = None
    skipped: bool = False


def default_org_folder(knowledge_folder: Path) -> Path:
    """지식파일 폴더와 같은 프로젝트 안의 ORG 폴더."""
    return knowledge_folder.parent / "ORG"


def _purge_previous_generations(org_folder: Path, keep: str, result: PublishResult) -> None:
    """배포 완료 후 날짜 폴더의 관리 대상 PDF만 정리한다."""
    if not org_folder.exists():
        logger.info("ORG 보관 폴더가 없어 정리할 이전 세대가 없습니다: %s", org_folder)
        return

    for child in sorted(org_folder.iterdir()):
        if (not child.is_dir() or child.is_symlink()
                or child.resolve() != org_folder.resolve() / child.name
                or not re.fullmatch(r"\d{6}", child.name) or child.name == keep):
            continue
        pdfs = [p for p in child.glob(SPEC_GLOB) if p.is_file() and not p.is_symlink()]
        try:
            for pdf in pdfs:
                pdf.unlink()
                result.purged_files += 1
            if not any(child.iterdir()):
                child.rmdir()
        except OSError as exc:
            # 파일이 열려 있는 등으로 못 지워도 실행을 실패시키지 않는다. 다음 주에 다시 시도된다.
            logger.warning("ORG 이전 세대 정리 실패(다음 실행에서 재시도): %s - %s", child, exc)
            continue
        result.purged_generations.append(child.name)
        logger.info("ORG 이전 세대 자동 정리: %s (%d개 파일)", child, len(pdfs))

    if not result.purged_generations:
        logger.info("ORG에 정리할 이전 세대가 없습니다(이미 삭제됨 또는 최초 실행).")


def archive_and_publish(
    *,
    knowledge_folder: Path | None,
    generated_pdfs: list[Path],
    file_date: str,
    org_folder: Path | None = None,
) -> PublishResult:
    result = PublishResult()

    if not knowledge_folder:
        logger.warning("knowledge_folder 설정이 비어 있어 지식파일 폴더 반영을 건너뜁니다.")
        result.skipped = True
        return result

    if not generated_pdfs:
        raise ValueError("No generated PDFs to publish")
    if not file_date or Path(file_date).name != file_date or file_date in {".", ".."}:
        raise ValueError("file_date must be a folder name")
    names = [pdf.name.casefold() for pdf in generated_pdfs]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate generated PDF names")
    for pdf in generated_pdfs:
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise ValueError(f"Missing or empty generated PDF: {pdf.name}")

    knowledge_folder.mkdir(parents=True, exist_ok=True)
    org_root = org_folder or default_org_folder(knowledge_folder)
    result.org_folder = str(org_root)

    # 모든 복사와 복구 사본 준비를 끝낸 뒤에만 배포본을 교체한다.
    new_names = {pdf.name for pdf in generated_pdfs}
    originals = sorted(knowledge_folder.glob(SPEC_GLOB))
    # Prefix 변경 등으로 glob 밖의 동일 이름을 덮어쓸 때도 복구 사본을 확보한다.
    originals = sorted(set(originals) | {knowledge_folder / name for name in new_names
                                       if (knowledge_folder / name).exists()})
    org_root.mkdir(parents=True, exist_ok=True)
    org_target = org_root / file_date
    staging = Path(tempfile.mkdtemp(prefix=".srs-publish-", dir=knowledge_folder.parent)).resolve()
    archive_staging = None
    changed_live: list[Path] = []
    added_archives: list[Path] = []
    preserve_staging = False
    try:
        incoming = staging / "incoming"
        backup = staging / "originals"
        incoming.mkdir()
        backup.mkdir()
        for pdf in generated_pdfs:
            shutil.copy2(pdf, incoming / pdf.name)
        for original in originals:
            shutil.copy2(original, backup / original.name)

        archive_staging = Path(tempfile.mkdtemp(prefix=".srs-archive-", dir=org_root)).resolve()
        archive_plan = []
        reserved_archive_names = set()
        for original in originals:
            if original.name in new_names:
                result.overwritten.append(original.name)
                continue
            dest = org_target / original.name
            duplicate = 0
            while dest.exists() or dest.name.casefold() in reserved_archive_names:
                duplicate += 1
                dest = org_target / f"{original.stem}_dup{duplicate}{original.suffix}"
            reserved_archive_names.add(dest.name.casefold())
            staged = archive_staging / dest.name
            shutil.copy2(backup / original.name, staged)
            archive_plan.append((staged, dest))
            result.retained.append(original.name)

        for staged, dest in archive_plan:
            dest.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(dest)
            added_archives.append(dest)
        for pdf in generated_pdfs:
            dest = knowledge_folder / pdf.name
            (incoming / pdf.name).replace(dest)
            changed_live.append(dest)
            result.published.append(pdf.name)
        for original in originals:
            if original.name not in new_names:
                original.unlink()
                changed_live.append(original)
    except OSError:
        rollback_errors = []
        for dest in reversed(changed_live):
            try:
                saved = staging / "originals" / dest.name
                if saved.exists():
                    restore = staging / "restore.pdf"
                    shutil.copy2(saved, restore)
                    restore.replace(dest)
                else:
                    dest.unlink(missing_ok=True)
            except OSError as exc:
                rollback_errors.append(exc)
        # 복구 실패 시 새 ORG 사본과 staging도 남겨 수동 복구를 보장한다.
        if not rollback_errors:
            for dest in added_archives:
                try:
                    dest.unlink()
                except OSError as exc:
                    rollback_errors.append(exc)
        if rollback_errors:
            preserve_staging = True
            logger.error("배포 복구 실패. 복구 사본 보존: %s / %s", staging, archive_staging)
        raise
    except BaseException:
        # 강제 중단/예상 밖 오류는 복구 자료를 삭제하지 않는다.
        preserve_staging = True
        logger.error("배포 중단. 복구 사본 보존: %s / %s", staging, archive_staging)
        raise
    finally:
        if not preserve_staging:
            for folder, owner in [(staging, knowledge_folder.parent), (archive_staging, org_root)]:
                if folder is not None and folder.parent == owner.resolve():
                    try:
                        shutil.rmtree(folder)
                    except OSError as exc:
                        logger.warning("배포 임시 폴더 정리 실패: %s - %s", folder, exc)

    # 신규 배포와 직전 세대 보관 모두 완료된 뒤에만 이전 ORG를 정리한다.
    try:
        _purge_previous_generations(org_root, keep=file_date, result=result)
    except OSError as exc:
        logger.warning("배포 완료 후 ORG 정리 실패(다음 실행에서 재시도): %s", exc)

    return result
