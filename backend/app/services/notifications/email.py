"""Email notification delivery."""
import smtplib
from email.message import EmailMessage


def _split_recipients(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def send_email(
    cfg: dict,
    subject: str,
    body: str,
    *,
    smtp_cls: type[smtplib.SMTP] = smtplib.SMTP,
) -> None:
    recipients = _split_recipients(cfg.get("email_to", ""))
    host = cfg.get("smtp_host", "")
    port = int(cfg.get("smtp_port") or 587)
    username = cfg.get("smtp_username", "")
    password = cfg.get("smtp_password", "")
    sender = cfg.get("smtp_from") or username
    use_tls = bool(cfg.get("smtp_tls", True))

    if not recipients:
        raise ValueError("邮件配置不完整（email_to）")
    if not host or not sender:
        raise ValueError("邮件配置不完整（smtp_host / smtp_from）")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    with smtp_cls(host, port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if username or password:
            smtp.login(username, password)
        smtp.send_message(message)
