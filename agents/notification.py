from email.message import EmailMessage
from io import BytesIO
import json
import logging
import os
import smtplib
import ssl
from struct import pack
from time import time
from uuid import uuid4
from zlib import compress, crc32

from sqlalchemy import inspect, text

from message_bus import Performative

logger = logging.getLogger("uvicorn.error")


def initialize_email(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS notification_emails (
            username VARCHAR(64) PRIMARY KEY REFERENCES users(username),
            address VARCHAR(254) NOT NULL
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS notification_email_queue (
            id VARCHAR(32) PRIMARY KEY REFERENCES notifications(id),
            address VARCHAR(254) NOT NULL, body TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt BIGINT NOT NULL,
            booking_reference VARCHAR(36)
        )
    """))

    columns = inspect(db).get_columns("notification_email_queue")
    if not any(column["name"] == "booking_reference" for column in columns):
        db.execute(text("ALTER TABLE notification_email_queue ADD COLUMN booking_reference VARCHAR(36)"))


def ticket_qr_svg(code):
    import qrcode
    from qrcode.image.svg import SvgPathFillImage

    output = BytesIO()
    qrcode.make(code, image_factory=SvgPathFillImage).save(output)
    return output.getvalue()


def ticket_qr_png(code):
    import qrcode

    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(code)
    matrix = qr.get_matrix()
    scale = qr.box_size
    size = len(matrix) * scale
    rows = b"".join(
        (b"\x00" + b"".join((b"\x00" if dark else b"\xff") * scale for dark in row)) * scale
        for row in matrix
    )

    def chunk(kind, data):
        return pack(">I", len(data)) + kind + data + pack(">I", crc32(kind + data))

    header = pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", compress(rows)) + chunk(b"IEND", b""))


def notification_agent(db, message):
    values = dict(id=uuid4().hex, username=message.content["username"],
                  body=message.content["body"], created_at=int(time()),
                  booking_reference=message.content.get("booking_reference"))
    db.execute(text("""
        INSERT INTO notifications (id, username, body, created_at)
        VALUES (:id, :username, :body, :created_at)
    """), values)
    queued = db.execute(text("""
        INSERT INTO notification_email_queue (id, address, body, next_attempt, booking_reference)
        SELECT :id, address, :body, :created_at, :booking_reference FROM notification_emails WHERE username=:username
    """), values).rowcount
    return Performative.AGREE, {"delivered": True, "email_queued": bool(queued)}


def deliver_email_queue(engine):
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender:
        return
    implicit_tls = os.getenv("SMTP_SSL", "").lower() == "true"
    port = int(os.getenv("SMTP_PORT", "465" if implicit_tls else "587"))
    username = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    if username and not password:
        logger.warning("NotificationAgent SMTP configuration is missing SMTP_PASSWORD")
        return
    context = ssl.create_default_context()
    for _ in range(5):
        now = int(time())
        with engine.begin() as db:
            job = db.execute(text("""
                UPDATE notification_email_queue SET attempts=attempts+1, next_attempt=:retry
                WHERE id=(SELECT id FROM notification_email_queue
                          WHERE next_attempt<=:now ORDER BY next_attempt, id LIMIT 1)
                  AND next_attempt<=:now
                RETURNING id, address, body, attempts, booking_reference
            """), dict(now=now, retry=now + 300)).mappings().one_or_none()
        if job is None:
            break
        email = EmailMessage()
        email["From"] = sender
        email["To"] = job["address"]
        email["Subject"] = "EventSphere notification"
        email["Message-ID"] = f"<{job['id']}@eventsphere.local>"
        email.set_content(job["body"])
        if job["booking_reference"]:
            with engine.connect() as db:
                codes = db.execute(text("""
                    SELECT b.codes FROM bookings b
                    JOIN notifications n ON n.username=b.username
                    WHERE n.id=:id AND b.reference=:reference
                    AND NOT EXISTS (SELECT 1 FROM cancellations c WHERE c.reference=b.reference)
                """), {"id": job["id"], "reference": job["booking_reference"]}).scalar_one_or_none()
            if codes:
                for index, code in enumerate(json.loads(codes), start=1):
                    email.add_attachment(ticket_qr_png(code), maintype="image", subtype="png",
                                         filename=f"EventSphere-ticket-{index}.png")
        stage = "connect"
        try:
            if implicit_tls:
                connection = smtplib.SMTP_SSL(host, port, timeout=10, context=context)
            else:
                connection = smtplib.SMTP(host, port, timeout=10)
            with connection as smtp:
                if not implicit_tls:
                    stage = "STARTTLS"
                    smtp.starttls(context=context)
                if username:
                    stage = "authenticate"
                    smtp.login(username, password)
                stage = "send"
                smtp.send_message(email)
        except (OSError, smtplib.SMTPException) as exc:
            codes = ",".join(str(response[0]) for response in exc.recipients.values()) if isinstance(
                exc, smtplib.SMTPRecipientsRefused
            ) else getattr(exc, "smtp_code", None)
            logger.warning("NotificationAgent attempt %s failed at %s (%s, SMTP code %s, OS code %s)",
                           job["attempts"], stage, type(exc).__name__, codes or "-",
                           getattr(exc, "errno", None) or "-")
            continue
        with engine.begin() as db:
            db.execute(text("DELETE FROM notification_email_queue WHERE id=:id"), {"id": job["id"]})
        logger.info("NotificationAgent sent email for notification %s", job["id"])
