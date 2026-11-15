from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import logging
from contextlib import suppress
from contextvars import ContextVar

from base64 import b64encode
import json
import os
from time import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from auth import initialize_auth, install_auth
from agents.booking import booking_agent
from agents.notification import deliver_email_queue, initialize_email, notification_agent, ticket_qr_svg
from agents.ticket import ticket_agent
from agents.venue import venue_agent
from agents.waitlist import initialize_waitlist, waitlist_agent
from message_bus import Message, Performative, dispatch_message, notify
from venues import initialize_venues, install_venue_routes, money_for_api

database_url = os.getenv("DATABASE_URL", "sqlite:///eventsphere.db")
if database_url.startswith(("postgres://", "postgresql://")):
    database_url = "postgresql+psycopg://" + database_url.split("://", 1)[1]
engine = create_engine(database_url)
app = FastAPI(title="EventSphere API", description="Prices are in LKR. For example, enter 500 for LKR 500. Event and hire times default to Sri Lanka time.", swagger_ui_parameters={"docExpansion": "none", "filter": True})
require_role = install_auth(app, engine)


request_id = ContextVar("request_id", default=None)


@app.middleware("http")
async def log_api_result(request: Request, call_next):
    logger = logging.getLogger("uvicorn.error")
    started = time()
    request_token = request_id.set(uuid4().hex)
    try:
        response = await call_next(request)
    except Exception as exc:
        route = request.scope.get("route")
        logger.error("[api] %s %s failed (%s)", request.method,
                     getattr(route, "path", "<unmatched>"), type(exc).__name__)
        raise
    finally:
        request_id.reset(request_token)
    route = request.scope.get("route")
    logger.info("[api] %s %s status=%s duration_ms=%.0f", request.method,
                getattr(route, "path", "<unmatched>"), response.status_code,
                (time() - started) * 1000)
    return response


def dispatch(db, message):
    return dispatch_message(db, message, handlers)


handlers = {
    "BookingAgent": lambda db, message: booking_agent(db, message, dispatch),
    "TicketAgent": ticket_agent,
    "VenueAgent": lambda db, message: venue_agent(db, message, dispatch),
    "NotificationAgent": notification_agent,
    "WaitlistAgent": lambda db, message: waitlist_agent(db, message, dispatch, Message, notify),
}


def initialize():
    with engine.begin() as db:
        initialize_auth(db)
        db.execute(text("CREATE TABLE IF NOT EXISTS events (id VARCHAR(32) PRIMARY KEY, title TEXT NOT NULL, remaining INTEGER NOT NULL CHECK (remaining >= 0))"))
        db.execute(text("CREATE TABLE IF NOT EXISTS tickets (code VARCHAR(64) PRIMARY KEY, event_id VARCHAR(32) NOT NULL REFERENCES events(id), used INTEGER NOT NULL DEFAULT 0)"))
        db.execute(text("CREATE TABLE IF NOT EXISTS messages (conversation_id VARCHAR(32) NOT NULL, body TEXT NOT NULL)"))
        if db.dialect.name == "postgresql":
            db.execute(text("""
                ALTER TABLE messages ADD COLUMN IF NOT EXISTS log_sequence
                BIGINT GENERATED ALWAYS AS IDENTITY
            """))
        initialize_venues(db)
        initialize_waitlist(db)
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS notifications (
                id VARCHAR(32) PRIMARY KEY, username VARCHAR(64) NOT NULL REFERENCES users(username),
                body TEXT NOT NULL, created_at BIGINT NOT NULL
            )
        """))
        initialize_email(db)
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS check_in_times (
                code VARCHAR(64) PRIMARY KEY REFERENCES tickets(code),
                checked_in_at BIGINT NOT NULL
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS bookings (
                reference VARCHAR(36) PRIMARY KEY, username VARCHAR(64) REFERENCES users(username),
                event_id VARCHAR(32) NOT NULL REFERENCES events(id), category_id VARCHAR(32),
                quantity INTEGER NOT NULL, total_minor BIGINT NOT NULL, codes TEXT NOT NULL
            )
        """))

        if db.dialect.name == "postgresql":
            db.execute(text("""
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_sequence
                BIGINT GENERATED ALWAYS AS IDENTITY
            """))

        db.execute(text("""
            CREATE TABLE IF NOT EXISTS cancellations (
                reference VARCHAR(36) PRIMARY KEY REFERENCES bookings(reference),
                refund_minor BIGINT NOT NULL
            )
        """))


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}


class EventInput(BaseModel):
    title: str
    seats: int


class ScanInput(BaseModel):
    code: str


@app.post("/events", dependencies=[Depends(require_role("admin"))], tags=["Events"])
def create_event(event: EventInput):
    if not event.title.strip() or event.seats < 1:
        raise HTTPException(422, "A title and positive seat count are required")
    values = {"id": uuid4().hex, "title": event.title, "remaining": event.seats}
    with engine.begin() as db:
        db.execute(text("INSERT INTO events (id, title, remaining) VALUES (:id, :title, :remaining)"), values)
    return values


def ask(agent, content):
    message = Message(sender="API", receiver=agent, performative=Performative.REQUEST,
                      content=content, conversation_id=request_id.get() or uuid4().hex)
    with engine.begin() as db:
        if agent == "WaitlistAgent" and content.get("action") == "sweep" and request_id.get() is None:
            with db.begin_nested() as sweep:
                reply = dispatch(db, message)
                if not (reply.content.get("offers_created") or reply.content.get("entries_expired")):
                    sweep.rollback()
                return reply
        return dispatch(db, message)


@app.post("/events/{event_id}/book", tags=["Bookings"])
def book(event_id: str, category_id: str | None = None, quantity: int = Query(1, ge=1, le=6),
         username: str = Depends(require_role("attendee"))):
    reply = ask("BookingAgent", dict(event_id=event_id, category_id=category_id,
                                   quantity=quantity, username=username))
    return money_for_api(reply.model_dump(mode="json"))


def ticket_qr(code):
    return "data:image/svg+xml;base64," + b64encode(ticket_qr_svg(code)).decode("ascii")


@app.get("/bookings", tags=["Bookings"])
def booking_history(username: str = Depends(require_role("attendee"))):
    with engine.connect() as db:
        sequence = "rowid" if db.dialect.name == "sqlite" else "booking_sequence"
        bookings = db.execute(text(f"""
            SELECT b.reference AS payment_reference, b.event_id, e.title, b.category_id,
                   b.quantity, b.total_minor, b.codes, c.refund_minor
            FROM bookings b JOIN events e ON e.id=b.event_id
            LEFT JOIN cancellations c ON c.reference=b.reference WHERE b.username=:username
            ORDER BY b.{sequence} DESC LIMIT 10
        """), {"username": username}).mappings()
        result = []
        for row in bookings:
            codes = json.loads(row["codes"])
            qr_codes = [ticket_qr(code) for code in codes] if row["refund_minor"] is None else []
            result.append(dict(row, codes=codes, qr_codes=qr_codes))
        return money_for_api(result)


@app.post("/bookings/{reference}/cancel", tags=["Bookings"])
def cancel(reference: str, username: str = Depends(require_role("attendee"))):
    reply = ask("BookingAgent", {"action": "cancel", "reference": reference, "username": username})
    return money_for_api(reply.model_dump(mode="json"))


@app.post("/events/{event_id}/scan", dependencies=[Depends(require_role("staff"))], tags=["Gate check-in"])
def scan(event_id: str, scan: ScanInput):
    return ask("TicketAgent", {"action": "scan", "event_id": event_id, "code": scan.code})


@app.post("/events/{event_id}/cancel", dependencies=[Depends(require_role("admin"))], tags=["Events"])
def cancel_published_event(event_id: str):
    return ask("BookingAgent", {"action": "cancel_event", "event_id": event_id})


install_venue_routes(app, engine, require_role, ask)


@app.get("/notifications", tags=["Notifications"])
def notifications(username: str = Depends(require_role("attendee"))):
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text("""
            SELECT id, body, created_at FROM notifications
            WHERE username=:username ORDER BY created_at DESC, id DESC
        """), {"username": username}).mappings()]


class EmailPreference(BaseModel):
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s,@<>;]+@[^\s,@<>;]+\.[^\s,@<>;]+$")


@app.get("/notifications/email", tags=["Notifications"])
def email_preference(username: str = Depends(require_role("attendee"))):
    with engine.connect() as db:
        email = db.execute(text("SELECT address FROM notification_emails WHERE username=:username"),
                           {"username": username}).scalar_one_or_none()
    return {"email": email}


@app.put("/notifications/email", tags=["Notifications"])
def update_email_preference(preference: EmailPreference, username: str = Depends(require_role("attendee"))):
    with engine.begin() as db:
        db.execute(text("""
            DELETE FROM notification_email_queue WHERE id IN (
                SELECT id FROM notifications WHERE username=:username
            )
        """), {"username": username})
        if preference.email:
            db.execute(text("""
                INSERT INTO notification_emails (username, address) VALUES (:username, :email)
                ON CONFLICT (username) DO UPDATE SET address=excluded.address
            """), dict(username=username, email=preference.email))
        else:
            db.execute(text("DELETE FROM notification_emails WHERE username=:username"), {"username": username})
    return {"email": preference.email}


def sweep_waitlists():
    with engine.connect() as db:
        event_ids = db.execute(text("""
            SELECT DISTINCT event_id FROM waitlist WHERE status IN ('waiting','held')
        """)).scalars().all()
    for event_id in event_ids:
        ask("WaitlistAgent", {"action": "sweep", "event_id": event_id})


@app.on_event("startup")
async def start_waitlist():
    initialize()

    async def run():
        while True:
            await asyncio.sleep(15)
            try:
                await asyncio.to_thread(sweep_waitlists)
            except Exception:
                logging.getLogger(__name__).exception("Waitlist sweep failed")

    async def email_worker():
        while True:
            try:
                await asyncio.to_thread(deliver_email_queue, engine)
            except Exception:
                logging.getLogger(__name__).exception("NotificationAgent email worker failed")
            await asyncio.sleep(15)

    app.state.waitlist_task = asyncio.create_task(run())
    app.state.email_task = asyncio.create_task(email_worker())


@app.on_event("shutdown")
async def stop_waitlist():
    app.state.email_task.cancel()
    app.state.waitlist_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.email_task
    with suppress(asyncio.CancelledError):
        await app.state.waitlist_task


@app.post("/events/{event_id}/waitlist", tags=["Waitlist"])
def join_waitlist(event_id: str, category_id: str, username: str = Depends(require_role("attendee"))):
    return ask("WaitlistAgent", dict(action="join", event_id=event_id, category_id=category_id, username=username))


@app.get("/waitlist", tags=["Waitlist"])
def my_waitlist(username: str = Depends(require_role("attendee"))):
    with engine.connect() as db:
        entries = db.execute(text("""
            SELECT w.*, e.title, c.name AS category_name
            FROM waitlist w JOIN events e ON e.id=w.event_id
            JOIN ticket_categories c ON c.id=w.category_id
            WHERE w.username=:username ORDER BY w.joined_at
        """), {"username": username}).mappings()
        return [dict(entry, seconds_remaining=max(0, (entry["expires"] or 0)-int(time()))
                     if entry["status"] == "held" else 0) for entry in entries]


@app.post("/events/{event_id}/waitlist/{entry_id}/claim", tags=["Waitlist"])
def claim_waitlist(event_id: str, entry_id: str, username: str = Depends(require_role("attendee"))):
    reply = ask("WaitlistAgent", dict(action="claim", event_id=event_id, id=entry_id, username=username))
    return money_for_api(reply.model_dump(mode="json"))


@app.get("/admin/dashboard", dependencies=[Depends(require_role("admin"))], tags=["Admin"])
def dashboard():
    with engine.connect() as db:
        rows = db.execute(text("""
            WITH sales AS (
                SELECT b.event_id,
                       SUM(CASE WHEN c.reference IS NULL THEN b.quantity ELSE 0 END) AS tickets_sold,
                       SUM(b.total_minor - COALESCE(c.refund_minor, 0)) AS revenue_minor,
                       SUM(COALESCE(c.refund_minor, 0)) AS refunds_minor
                FROM bookings b LEFT JOIN cancellations c ON c.reference=b.reference
                GROUP BY b.event_id
            ), attendance AS (
                SELECT event_id, COUNT(*) AS check_ins FROM tickets WHERE used=1 GROUP BY event_id
            )
            SELECT e.id, e.title, v.name AS venue_name, v.image AS venue_image, v.capacity AS venue_capacity,
                   s.capacity AS ticket_capacity, e.remaining,
                   CASE WHEN x.id IS NOT NULL THEN 'cancelled'
                        WHEN s.published THEN 'published' ELSE 'draft' END AS status,
                   COALESCE(sales.tickets_sold, 0) AS tickets_sold,
                   COALESCE(sales.revenue_minor, 0) AS revenue_minor,
                   COALESCE(sales.refunds_minor, 0) AS refunds_minor,
                   COALESCE(attendance.check_ins, 0) AS check_ins
            FROM event_schedule s JOIN events e ON e.id=s.id JOIN venues v ON v.id=s.venue_id
            LEFT JOIN cancelled_events x ON x.id=e.id
            LEFT JOIN sales ON sales.event_id=e.id
            LEFT JOIN attendance ON attendance.event_id=e.id
            ORDER BY s.starts_at, e.id
        """)).mappings()
        return money_for_api([dict(row) for row in rows])


@app.get("/admin/check-ins", dependencies=[Depends(require_role("admin"))], tags=["Admin"])
def check_ins_over_time():
    with engine.connect() as db:
        rows = db.execute(text("""
            SELECT checked_in_at - ((checked_in_at + 19800) % 86400) AS day_start,
                   COUNT(*) AS count
            FROM check_in_times
            WHERE checked_in_at >= :since
            GROUP BY 1 ORDER BY 1
        """), {"since": ((int(time()) + 19800) // 86400 - 29) * 86400 - 19800}).mappings()
        return [dict(row) for row in rows]


@app.get("/admin/messages", dependencies=[Depends(require_role("admin"))], tags=["Admin"])
def agent_messages(conversation_id: str | None = None, limit: int = Query(25, ge=1, le=50)):
    with engine.connect() as db:
        sequence = "rowid" if db.dialect.name == "sqlite" else "log_sequence"
        rows = db.execute(text(f"""
            WITH request_groups AS (
                SELECT conversation_id, MAX({sequence}) AS last_message
                FROM messages
                WHERE (:show_all = TRUE OR conversation_id = :conversation_id)
                GROUP BY conversation_id
                ORDER BY last_message DESC
                LIMIT :limit
            )
            SELECT m.body FROM messages m
            JOIN request_groups g ON g.conversation_id = m.conversation_id
            ORDER BY g.last_message DESC, m.{sequence} ASC
        """), dict(show_all=conversation_id is None, conversation_id=conversation_id or "", limit=limit))
        messages = [json.loads(body) for (body,) in rows]
    for message in messages:
        for field in ("code", "codes"):
            if field in message["content"]:
                message["content"][field] = "[redacted]"
    return messages


frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
if (frontend_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="frontend-assets")


@app.get("/", include_in_schema=False)
def browser_home():
    built_index = frontend_dist / "index.html"
    index = built_index if (frontend_dist / "assets").is_dir() and built_index.is_file() else Path(__file__).with_name("index.html")
    return FileResponse(index, media_type="text/html")
