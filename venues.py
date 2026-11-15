import logging
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import text

BUFFER_SECONDS = 30 * 60


SRI_LANKA = timezone(timedelta(hours=5, minutes=30))
Lkr = Annotated[Decimal, Field(ge=0, le=Decimal("999999999999"), decimal_places=2,
                               examples=[500], description="Amount in rupees. Enter 500 for LKR 500.")]


def in_sri_lanka(value: datetime) -> datetime:
    return value.replace(tzinfo=SRI_LANKA) if value.tzinfo is None else value.astimezone(SRI_LANKA)


SriLankaDateTime = Annotated[datetime, AfterValidator(in_sri_lanka)]


def money_for_api(value):
    """Expose rupees at the HTTP boundary while agents and storage use minor units."""
    if isinstance(value, list):
        return [money_for_api(item) for item in value]
    if isinstance(value, dict):
        return {
            key.removesuffix("_minor") + "_lkr" if key.endswith("_minor") else key:
            item / 100 if key.endswith("_minor") and item is not None else money_for_api(item)
            for key, item in value.items()
        }
    return value


class VenueInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    location: str = Field(min_length=1)
    description: str = ""
    image: HttpUrl | None = None
    capacity: int = Field(gt=0, le=100_000)
    hourly_rate_lkr: Lkr
    private_hire: bool = False


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price_lkr: Lkr
    quantity: int = Field(gt=0, le=100_000)


class DraftInput(BaseModel):
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    venue_id: str
    starts_at: SriLankaDateTime = Field(examples=["2027-01-15T18:00"], description="Start in Sri Lanka time, using YYYY-MM-DDTHH:MM. An explicit offset is also accepted.")
    ends_at: SriLankaDateTime = Field(examples=["2027-01-15T20:00"], description="End in Sri Lanka time, using YYYY-MM-DDTHH:MM. An explicit offset is also accepted.")
    description: str = ""
    tickets: list[CategoryInput] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("End must be after start")
        if len({ticket.name.casefold() for ticket in self.tickets}) != len(self.tickets):
            raise ValueError("Ticket category names must be unique")
        return self


class HireInput(BaseModel):
    venue_id: str
    starts_at: SriLankaDateTime = Field(examples=["2027-01-15T18:00"], description="Start in Sri Lanka time, using YYYY-MM-DDTHH:MM. An explicit offset is also accepted.")
    ends_at: SriLankaDateTime = Field(examples=["2027-01-15T20:00"], description="End in Sri Lanka time, using YYYY-MM-DDTHH:MM. An explicit offset is also accepted.")
    purpose: str = Field(min_length=1, max_length=500)
    guests: int = Field(gt=0, le=100_000)

    @model_validator(mode="after")
    def validate_time(self):
        duration = int(self.ends_at.timestamp()) - int(self.starts_at.timestamp())
        if not 60 <= duration <= 86400:
            raise ValueError("Hire duration must be between one minute and 24 hours")
        return self


def initialize_venues(db):
    for statement in (
        """CREATE TABLE IF NOT EXISTS venues (
            id VARCHAR(32) PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL,
            description TEXT NOT NULL, image TEXT, capacity INTEGER NOT NULL,
            hourly_rate_minor BIGINT NOT NULL, private_hire BOOLEAN NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS event_schedule (
            id VARCHAR(32) PRIMARY KEY REFERENCES events(id),
            venue_id VARCHAR(32) NOT NULL REFERENCES venues(id),
            category TEXT NOT NULL, description TEXT NOT NULL, starts_at BIGINT NOT NULL,
            ends_at BIGINT NOT NULL, published BOOLEAN NOT NULL, capacity INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS hires (
            id VARCHAR(32) PRIMARY KEY, venue_id VARCHAR(32) NOT NULL REFERENCES venues(id),
            username VARCHAR(64) NOT NULL REFERENCES users(username), starts_at BIGINT NOT NULL,
            ends_at BIGINT NOT NULL, purpose TEXT NOT NULL, guests INTEGER NOT NULL,
            price_minor BIGINT NOT NULL, status VARCHAR(12) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS cancelled_events (
            id VARCHAR(32) PRIMARY KEY REFERENCES event_schedule(id))""",
        """CREATE TABLE IF NOT EXISTS ticket_categories (
            id VARCHAR(32) PRIMARY KEY, event_id VARCHAR(32) NOT NULL REFERENCES events(id),
            name TEXT NOT NULL, price_minor BIGINT NOT NULL, remaining INTEGER NOT NULL)""",
    ):
        db.execute(text(statement))


def event_response(row):
    event = dict(row)
    for field in ("starts_at", "ends_at"):
        event[field] = datetime.fromtimestamp(event[field], SRI_LANKA).isoformat()
    return event


def install_venue_routes(app, engine, require_role, ask):
    admin = [Depends(require_role("admin"))]

    @app.post("/venues", dependencies=admin, status_code=201, tags=["Venues"])
    def create_venue(venue: VenueInput):
        values = dict(id=uuid4().hex, hourly_rate_minor=int(venue.hourly_rate_lkr * 100), **venue.model_dump(mode="json", exclude={"hourly_rate_lkr"}))
        with engine.begin() as db:
            db.execute(text("""
                INSERT INTO venues (id,name,location,description,image,capacity,hourly_rate_minor,private_hire)
                VALUES (:id,:name,:location,:description,:image,:capacity,:hourly_rate_minor,:private_hire)
            """), values)
        return money_for_api(values)

    @app.put("/venues/{venue_id}", dependencies=admin, tags=["Venues"])
    def edit_venue(venue_id: str, venue: VenueInput):
        values = dict(id=venue_id, hourly_rate_minor=int(venue.hourly_rate_lkr * 100), **venue.model_dump(mode="json", exclude={"hourly_rate_lkr"}))
        with engine.begin() as db:
            updated = db.execute(text("""
                UPDATE venues SET name=:name, location=:location, description=:description, image=:image,
                    capacity=:capacity, hourly_rate_minor=:hourly_rate_minor, private_hire=:private_hire
                WHERE id=:id
            """), values)
            if not updated.rowcount:
                raise HTTPException(404, "Venue not found")
            clash = db.execute(text("""
                SELECT id FROM event_schedule
                WHERE venue_id=:id AND published=TRUE AND capacity>:capacity LIMIT 1
            """), values).scalar_one_or_none()
            if not clash:
                clash = db.execute(text("""
                    SELECT id FROM hires WHERE venue_id=:id AND status='confirmed'
                    AND guests>:capacity AND ends_at>:now LIMIT 1
                """), dict(values, now=int(datetime.now(timezone.utc).timestamp()))).scalar_one_or_none()
            if clash:
                raise HTTPException(409, "Capacity is below an existing reservation's allocation")
        return money_for_api(values)

    @app.get("/venues", tags=["Venues"])
    def venues():
        with engine.connect() as db:
            return money_for_api([dict(row) for row in db.execute(text("SELECT * FROM venues ORDER BY name")).mappings()])

    @app.post("/events/drafts", dependencies=admin, status_code=201, tags=["Events"])
    def create_draft(draft: DraftInput):
        event_id = uuid4().hex
        quantity = sum(ticket.quantity for ticket in draft.tickets)
        with engine.begin() as db:
            capacity = db.execute(
                text("SELECT capacity FROM venues WHERE id=:id"), {"id": draft.venue_id}
            ).scalar_one_or_none()
            if capacity is None:
                logging.getLogger("uvicorn.error").info(
                    "[draft] refused venue=%s reason=venue_not_found", draft.venue_id)
                raise HTTPException(404, "Venue not found")
            if quantity > capacity:
                logging.getLogger("uvicorn.error").info(
                    "[draft] refused venue=%s tickets=%s capacity=%s",
                    draft.venue_id, quantity, capacity)
                raise HTTPException(422, f"Total ticket quantity ({quantity}) exceeds venue capacity ({capacity})")
            db.execute(text("INSERT INTO events (id,title,remaining) VALUES (:id,:title,:remaining)"),
                       dict(id=event_id, title=draft.title, remaining=quantity))
            db.execute(text("""
                INSERT INTO event_schedule
                    (id,venue_id,category,description,starts_at,ends_at,published,capacity)
                VALUES (:id,:venue,:category,:description,:start,:end,FALSE,:capacity)
            """), dict(id=event_id, venue=draft.venue_id, category=draft.category,
                       description=draft.description, start=int(draft.starts_at.timestamp()),
                       end=int(draft.ends_at.timestamp()), capacity=quantity))
            categories = [dict(id=uuid4().hex, event=event_id, price_minor=int(ticket.price_lkr * 100), **ticket.model_dump(exclude={"price_lkr"})) for ticket in draft.tickets]
            db.execute(text("""
                INSERT INTO ticket_categories (id,event_id,name,price_minor,remaining)
                VALUES (:id,:event,:name,:price_minor,:quantity)
            """), categories)
        logging.getLogger("uvicorn.error").info(
            "[draft] saved event=%s venue=%s tickets=%s capacity=%s venue_reserved=false",
            event_id, draft.venue_id, quantity, capacity)
        return {"id": event_id, "status": "draft", "tickets": money_for_api(categories)}

    @app.post("/events/{event_id}/publish", dependencies=admin, tags=["Events"])
    def publish_event(event_id: str):
        return ask("VenueAgent", {"event_id": event_id})

    @app.delete("/events/drafts/{event_id}", dependencies=admin, tags=["Events"])
    def delete_draft(event_id: str):
        params = {"id": event_id}
        with engine.begin() as db:
            db.execute(text("""
                UPDATE venues SET capacity=capacity
                WHERE id=(SELECT venue_id FROM event_schedule WHERE id=:id)
            """), params)
            published = db.execute(text(
                "SELECT published FROM event_schedule WHERE id=:id"
            ), params).scalar_one_or_none()
            if published is None:
                raise HTTPException(404, "Event draft not found")
            cancelled = db.execute(text(
                "SELECT id FROM cancelled_events WHERE id=:id"
            ), params).scalar_one_or_none()
            if published or cancelled:
                raise HTTPException(409, "Only unpublished drafts can be deleted")
            db.execute(text("DELETE FROM ticket_categories WHERE event_id=:id"), params)
            db.execute(text("DELETE FROM event_schedule WHERE id=:id"), params)
            db.execute(text("DELETE FROM events WHERE id=:id"), params)
        return {"status": "deleted"}

    @app.get("/events", tags=["Events"])
    def published_events(
        keyword: str = "", category: str | None = None, venue_id: str | None = None,
        from_date: date | None = None, to_date: date | None = None,
        min_price_lkr: Lkr | None = None,
        max_price_lkr: Lkr | None = None,
    ):
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must be on or before end date")
        if min_price_lkr is not None and max_price_lkr is not None and min_price_lkr > max_price_lkr:
            raise HTTPException(422, "Minimum price must be on or below maximum price")
        sri_lanka = timezone(timedelta(hours=5, minutes=30))
        with engine.connect() as db:
            return [event_response(row) for row in db.execute(text("""
                SELECT s.*, e.title, e.remaining, v.name AS venue_name, v.image AS venue_image
                FROM event_schedule s JOIN events e ON e.id=s.id JOIN venues v ON v.id=s.venue_id
                WHERE s.published=TRUE AND LOWER(e.title) LIKE :keyword
                AND (:all_categories=TRUE OR LOWER(s.category)=:category)
                AND (:all_venues=TRUE OR s.venue_id=:venue_id)
                AND s.starts_at BETWEEN :start AND :end
                AND (:all_prices=TRUE OR EXISTS (
                    SELECT 1 FROM ticket_categories t WHERE t.event_id=s.id
                    AND t.price_minor BETWEEN :min_price AND :max_price
                ))
                ORDER BY s.starts_at, s.id
            """), dict(keyword=f"%{keyword.lower()}%", all_categories=category is None,
                       category=(category or "").lower(), all_venues=venue_id is None,
                       venue_id=venue_id or "", all_prices=min_price_lkr is None and max_price_lkr is None,
                       start=int(datetime.combine(from_date, time.min, sri_lanka).timestamp()) if from_date else -2**63,
                       end=int(datetime.combine(to_date, time(23, 59, 59), sri_lanka).timestamp()) if to_date else 2**63-1,
                       min_price=int((min_price_lkr or 0) * 100),
                       max_price=int(max_price_lkr * 100) if max_price_lkr is not None else 2**63-1)).mappings()]

    @app.get("/events/{event_id}", tags=["Events"])
    def event_details(event_id: str):
        with engine.connect() as db:
            event = db.execute(text("""
                SELECT s.*, e.title, e.remaining, v.name AS venue_name, v.image AS venue_image
                FROM event_schedule s JOIN events e ON e.id=s.id JOIN venues v ON v.id=s.venue_id
                WHERE s.id=:id AND s.published=TRUE
            """), {"id": event_id}).mappings().one_or_none()
            if event is None:
                raise HTTPException(404, "Published event not found")
            tickets = db.execute(text("""
                SELECT id,name,price_minor,remaining FROM ticket_categories WHERE event_id=:id ORDER BY name
            """), {"id": event_id}).mappings()
            return dict(event_response(event), tickets=money_for_api([dict(ticket) for ticket in tickets]))


    @app.post("/hires", tags=["Private hire"])
    def request_hire(hire: HireInput, username: str = Depends(require_role("attendee"))):
        reply = ask("VenueAgent", dict(hire.model_dump(), action="hire", username=username,
                                     starts_at=int(hire.starts_at.timestamp()),
                                     ends_at=int(hire.ends_at.timestamp())))
        return money_for_api(reply.model_dump(mode="json"))

    @app.get("/hires", tags=["Private hire"])
    def my_hires(username: str = Depends(require_role("attendee"))):
        with engine.connect() as db:
            return [money_for_api(event_response(row)) for row in db.execute(text(
                "SELECT * FROM hires WHERE username=:username ORDER BY starts_at"
            ), {"username": username}).mappings()]

    @app.get("/admin/hires", dependencies=admin, tags=["Private hire"])
    def pending_hires():
        with engine.connect() as db:
            return [money_for_api(event_response(row)) for row in db.execute(text(
                "SELECT * FROM hires WHERE status='pending' ORDER BY starts_at"
            )).mappings()]

    @app.post("/admin/hires/{hire_id}/review", dependencies=admin, tags=["Private hire"])
    def review_hire(hire_id: str, approve: bool):
        reply = ask("VenueAgent", dict(action="review", id=hire_id, approve=approve))
        return money_for_api(reply.model_dump(mode="json"))

