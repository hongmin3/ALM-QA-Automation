"""검증 통과한 신규 PDF를 실제 지식파일 폴더에 반영한다.

교체된 이전 세대 사양서는 지식파일 폴더와 같은 프로젝트 안의 `ORG/<YYMMDD>/`로
옮겨 보관한다. 이 보관본은 **다음 실행이 검증을 통과했을 때** 자동으로 삭제된다.
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
import shutil
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
    """직전 세대 보관본을 정리한다. 이번 실행이 검증을 통과했으므로 '문제 없음'으로 본다."""
    if not org_folder.exists():
        logger.info("ORG 보관 폴더가 없어 정리할 이전 세대가 없습니다: %s", org_folder)
        return

    for child in sorted(org_folder.iterdir()):
        if not child.is_dir() or child.name == keep:
            continue
        pdfs = list(child.glob(SPEC_GLOB))
        try:
            shutil.rmtree(child)
        except OSError as exc:
            # 파일이 열려 있는 등으로 못 지워도 실행을 실패시키지 않는다. 다음 주에 다시 시도된다.
            logger.warning("ORG 이전 세대 정리 실패(다음 실행에서 재시도): %s - %s", child, exc)
            continue
        result.purged_generations.append(child.name)
        result.purged_files += len(pdfs)
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

    knowledge_folder.mkdir(parents=True, exist_ok=True)
    org_root = org_folder or default_org_folder(knowledge_folder)
    result.org_folder = str(org_root)

    # 1) 직전 세대 보관본 정리 (이번 실행이 검증을 통과한 뒤에만 여기 도달한다)
    _purge_previous_generations(org_root, keep=file_date, result=result)

    # 2) 기존 사양서를 ORG/<YYMMDD>/ 로 이동
    #    같은 날 재실행이면 지식파일 폴더의 '오늘 날짜' 파일은 교체 대상인 이전 버전이
    #    아니라 같은 버전이므로 보관하지 않고 그대로 덮어쓴다. 보관하면 ORG 안에서
    #    진짜 직전 세대가 같은 날 사본에 묻힌다.
    new_names = {pdf.name for pdf in generated_pdfs}
    org_target = org_root / file_date
    for f in sorted(knowledge_folder.glob(SPEC_GLOB)):
        if f.name in new_names:
            result.overwritten.append(f.name)
            logger.info("같은 이름의 산출물로 갱신되므로 보관하지 않고 덮어씁니다: %s", f.name)
            continue
        org_target.mkdir(parents=True, exist_ok=True)
        dest = org_target / f.name
        if dest.exists():
            dest = org_target / f"{f.stem}_dup{f.suffix}"
        shutil.move(str(f), str(dest))
        result.retained.append(f.name)
        logger.info("이전 사양서 ORG 보관: %s -> %s", f.name, dest)

    if not result.retained:
        logger.info("ORG로 보관할 이전 세대 사양서가 없습니다.")

    # 3) 신규 사양서 반영
    for pdf in generated_pdfs:
        dest = knowledge_folder / pdf.name
        shutil.copy2(pdf, dest)
        result.published.append(pdf.name)
        logger.info("신규 사양서 반영: %s", dest)

    return result
