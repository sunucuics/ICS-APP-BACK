import asyncio, ssl, smtplib
from email.message import EmailMessage
from typing import Optional
from backend.app.config import settings

async def send_email(to: str, subject: str, html: str, sender_name: Optional[str] = None):
    """
    Basit ve güvenli SMTP gönderici.
    - 465 ise SSL başlar; 587 ve smtp_use_starttls=True ise STARTTLS yapar.
    - Async uyumlu: bloklayan işlemi thread'e offload eder.
    """
    from_addr = settings.smtp_from or settings.smtp_user
    if not (settings.smtp_host and settings.smtp_port and settings.smtp_user and settings.smtp_password and from_addr):
        raise RuntimeError("SMTP config eksik: host/port/user/password/from kontrol edin")

    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = f"{sender_name} <{from_addr}>" if sender_name else from_addr
    msg["Subject"] = subject
    msg.set_content("HTML içerik için e-postayı HTML olarak görüntüleyin.")
    msg.add_alternative(html, subtype="html")

    def _send_blocking():
        context = ssl.create_default_context()
        if settings.smtp_use_starttls:
            # 587 / STARTTLS
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        else:
            # 465 / SSL
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_blocking)
