"""실행 결과 메일 알림.

주간 자동 실행은 사람이 보지 않는 시간에 돌기 때문에, 실패했는데 아무도 모르는
상황이 가장 위험하다. 그래서 성공/실패와 무관하게 매 실행 결과를 메일로 보낸다.

설계 원칙:
- **메일 실패가 자동화 실패가 되면 안 된다.** SMTP 오류는 경고로 남기고 종료 코드에
  영향을 주지 않는다. 사양서는 이미 정상 반영되었을 수 있기 때문이다.
- **자격증명을 코드나 커밋 대상 파일에 두지 않는다.** `.env`의 환경변수를 우선 사용하고,
  없으면 config에 지정된 외부 INI 파일의 `[email]` 섹션에서 읽는다(기존 사내 자동화의
  설정 파일을 그대로 재사용하기 위한 경로 - 비밀값을 이 저장소로 복사하지 않는다).
- **본문에 사양서 원문을 싣지 않는다.** 요약 수치와 파일명/페이지 수만 보낸다.
  변경 리포트 첨부는 기본 비활성이며, 켤 경우 SRS 제목이 외부 메일로 나간다는 점을
  이해한 상태여야 한다.
"""
from __future__ import annotations

import configparser
import html as html_lib
import logging
import mimetypes
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger("srs_automation")

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


@dataclass
class MailSettings:
    enabled: bool
    host: str | None
    port: int
    user: str | None
    password: str | None
    from_addr: str | None
    to_addrs: list[str]
    use_starttls: bool
    attach_report: bool
    timeout_seconds: int

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.host:
            missing.append("host")
        if not self.user:
            missing.append("user")
        if not self.password:
            missing.append("password")
        if not self.from_addr:
            missing.append("from")
        if not self.to_addrs:
            missing.append("to")
        return missing


def _read_ini_email_section(path: Path) -> dict[str, str]:
    """기존 자동화의 config.ini `[email]` 섹션을 읽는다 (비밀값 복사 방지용 경로)."""
    try:
        cfg = configparser.RawConfigParser()
        cfg.read(path, encoding="utf-8")
        if not cfg.has_section("email"):
            logger.warning("메일 자격증명 파일에 [email] 섹션이 없습니다: %s", path)
            return {}
        return dict(cfg.items("email"))
    except (OSError, configparser.Error) as exc:
        logger.warning("메일 자격증명 파일을 읽을 수 없습니다 (%s): %s", path, exc)
        return {}


def load_mail_settings(raw: dict, project_root: Path) -> MailSettings:
    """config.yaml의 `mail:` 블록 + 환경변수 + (선택) 외부 INI에서 설정을 조립한다.

    우선순위: 환경변수 > 외부 INI > config.yaml
    """
    mail = raw.get("mail", {}) or {}

    ini_values: dict[str, str] = {}
    cred_file = mail.get("credentials_ini")
    if cred_file:
        p = Path(cred_file)
        if not p.is_absolute():
            p = project_root / p
        ini_values = _read_ini_email_section(p)

    def pick(env_key: str, ini_key: str, yaml_key: str, default=None):
        return os.environ.get(env_key) or ini_values.get(ini_key) or mail.get(yaml_key) or default

    to_raw = pick("MAIL_TO", "to_addr", "to")
    if isinstance(to_raw, list):
        to_addrs = [str(x).strip() for x in to_raw if str(x).strip()]
    else:
        to_addrs = [x.strip() for x in str(to_raw or "").replace(";", ",").split(",") if x.strip()]

    port_raw = pick("SMTP_PORT", "smtp_port", "port", 587)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        logger.warning("SMTP 포트 값이 올바르지 않아 587로 대체합니다: %r", port_raw)
        port = 587

    return MailSettings(
        enabled=bool(mail.get("enabled", False)),
        host=pick("SMTP_HOST", "smtp_host", "host"),
        port=port,
        user=pick("SMTP_USER", "smtp_user", "user"),
        password=os.environ.get("SMTP_PASSWORD") or ini_values.get("smtp_password") or mail.get("password"),
        from_addr=pick("MAIL_FROM", "from_addr", "from"),
        to_addrs=to_addrs,
        use_starttls=bool(mail.get("use_starttls", True)),
        attach_report=bool(mail.get("attach_report", False)),
        timeout_seconds=int(mail.get("timeout_seconds", 30)),
    )


def _row(label: str, value: str) -> str:
    return (
        f'<tr><td style="padding:4px 12px 4px 0;color:#555;white-space:nowrap;">{html_lib.escape(label)}</td>'
        f'<td style="padding:4px 0;"><b>{value}</b></td></tr>'
    )


def build_body(summary: dict) -> str:
    ok = summary.get("ok", False)
    color = "#1a7f37" if ok else "#cf222e"
    status = "성공" if ok else "실패"

    pdf_rows = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;">{html_lib.escape(p["name"])}</td>'
        f'<td style="padding:3px 12px 3px 0;text-align:right;">{p["pages"]:,} p</td>'
        f'<td style="padding:3px 0;text-align:right;color:#555;">{p["size_mb"]:.1f} MB</td></tr>'
        for p in summary.get("pdfs", [])
    ) or '<tr><td colspan="3" style="color:#888;">생성된 PDF 없음</td></tr>'

    warn_items = "".join(f"<li>{html_lib.escape(w)}</li>" for w in summary.get("warnings", []))
    warn_block = (
        f'<h3 style="margin:20px 0 6px;font-size:14px;">확인이 필요한 사항</h3><ul style="margin:0;padding-left:20px;color:#9a6700;">{warn_items}</ul>'
        if warn_items
        else ""
    )

    fail_items = "".join(f"<li>{html_lib.escape(f)}</li>" for f in summary.get("failed_checks", []))
    fail_block = (
        f'<h3 style="margin:20px 0 6px;font-size:14px;color:#cf222e;">실패한 검증 항목</h3>'
        f'<ul style="margin:0;padding-left:20px;color:#cf222e;">{fail_items}</ul>'
        f'<p style="color:#cf222e;"><b>검증에 실패했으므로 지식파일 폴더의 기존 사양서는 교체되지 않았습니다.</b></p>'
        if fail_items
        else ""
    )

    diff = summary.get("diff", {})
    return f"""<div style="font-family:'Malgun Gothic',AppleGothic,sans-serif;font-size:13px;color:#24292f;line-height:1.6;word-break:keep-all;overflow-wrap:break-word;">
  <h2 style="margin:0 0 4px;font-size:18px;">VXvue SRS 사양서 자동 최신화</h2>
  <p style="margin:0 0 16px;">
    <span style="display:inline-block;padding:2px 10px;border-radius:10px;background:{color};color:#fff;font-weight:bold;">{status}</span>
    <span style="color:#555;margin-left:8px;">{html_lib.escape(str(summary.get('run_date','')))}</span>
  </p>

  <table style="border-collapse:collapse;">
    {_row("실행 시간", html_lib.escape(str(summary.get("duration", "-"))))}
    {_row("수집 SRS", html_lib.escape(str(summary.get("collected", "-"))))}
    {_row("변경 요약", f"변경 {diff.get('changed', 0)}건 · 신규 {diff.get('new', 0)}건 · 삭제 {diff.get('deleted', 0)}건 · 동일 {diff.get('unchanged', 0)}건")}
    {_row("비교 기준", html_lib.escape(str(diff.get("previous") or "(없음 - 최초 실행)")))}
  </table>

  <h3 style="margin:20px 0 6px;font-size:14px;">생성된 사양서</h3>
  <table style="border-collapse:collapse;">{pdf_rows}</table>

  <h3 style="margin:20px 0 6px;font-size:14px;">배포 결과</h3>
  <table style="border-collapse:collapse;">
    {_row("지식파일 폴더 반영", f"{len(summary.get('published', []))}개")}
    {_row("ORG 보관(직전 세대)", f"{len(summary.get('retained', []))}개")}
    {_row("ORG 자동 정리", summary.get("purge_text", "정리 대상 없음"))}
  </table>
  {fail_block}
  {warn_block}

  <h3 style="margin:20px 0 6px;font-size:14px;">경로</h3>
  <table style="border-collapse:collapse;color:#555;">
    {_row("변경 리포트", html_lib.escape(str(summary.get("report_path", "-"))))}
    {_row("실행 로그", html_lib.escape(str(summary.get("log_path", "-"))))}
  </table>

  <p style="margin-top:24px;color:#888;font-size:11px;">
    이 메일은 Windows Task Scheduler에 등록된 주간 자동화가 보냅니다.
    본문에는 SRS 원문이 포함되지 않습니다.
  </p>
</div>"""


def send_message(settings: MailSettings, message: EmailMessage) -> bool:
    """Send an already-rendered message with the existing SMTP policy."""
    if not settings.enabled or settings.missing_fields():
        return False
    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds) as server:
            server.ehlo()
            if settings.use_starttls:
                server.starttls()
                server.ehlo()
            server.login(settings.user, settings.password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("메일 발송 실패(자동화 결과에는 영향 없음): %s: %s", type(exc).__name__, exc)
        return False
    return True


def send_run_report(settings: MailSettings, summary: dict, report_path: Path | None = None) -> bool:
    """실행 결과 메일을 보낸다. 실패해도 예외를 밖으로 던지지 않는다."""
    if not settings.enabled:
        logger.info("메일 알림이 비활성화되어 있습니다(config.yaml의 mail.enabled).")
        return False

    missing = settings.missing_fields()
    if missing:
        logger.warning(
            "메일 알림 설정이 불완전해 발송을 건너뜁니다. 누락: %s "
            "(.env의 SMTP_HOST/SMTP_USER/SMTP_PASSWORD/MAIL_FROM/MAIL_TO 또는 mail.credentials_ini 확인)",
            ", ".join(missing),
        )
        return False

    status = "성공" if summary.get("ok") else "실패"
    msg = EmailMessage()
    msg["Subject"] = f"[VXvue SRS 자동화] {summary.get('run_date', '')} {status} - 변경 {summary.get('diff', {}).get('changed', 0)}건"
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content("HTML 메일입니다. HTML을 지원하는 클라이언트에서 확인하세요.")
    msg.add_alternative(build_body(summary), subtype="html")

    if settings.attach_report and report_path and report_path.exists():
        size = report_path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            logger.warning("변경 리포트가 첨부 한도(%dMB)를 초과해 첨부하지 않습니다: %.1fMB", MAX_ATTACHMENT_BYTES // 1024 // 1024, size / 1024 / 1024)
        else:
            ctype, _ = mimetypes.guess_type(report_path.name)
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(
                report_path.read_bytes(), maintype=maintype, subtype=subtype or "octet-stream", filename=report_path.name
            )
            logger.info("변경 리포트를 메일에 첨부했습니다: %s", report_path.name)

    if not send_message(settings, msg):
        return False

    logger.info("실행 결과 메일 발송 완료: %s", ", ".join(settings.to_addrs))
    return True
