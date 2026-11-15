from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import text
from message_bus import notify
from venues import BUFFER_SECONDS, event_response


def publish(db, event_id):
    # Updating the venue row serializes decisions for that venue on both databases.
    db.execute(text("""
        UPDATE venues SET capacity = capacity
        WHERE id = (SELECT venue_id FROM event_schedule WHERE id = :id)
    """), {"id": event_id})
    event = db.execute(text("""
        SELECT s.*, v.capacity AS venue_capacity FROM event_schedule s
        JOIN venues v ON v.id = s.venue_id WHERE s.id = :id
    """), {"id": event_id}).mappings().one_or_none()
    if event is None:
        return "REFUSE", {"reason": "Event draft was not found"}
    if db.execute(text("SELECT id FROM cancelled_events WHERE id=:id"),
                  {"id": event_id}).scalar_one_or_none():
        return "REFUSE", {"reason": "Event was cancelled"}
    if event["published"]:
        return "AGREE", {"event_id": event_id, "status": "published"}
    if event["capacity"] > event["venue_capacity"]:
        return "REFUSE", {"reason": "Ticket quantities exceed venue capacity"}
    if event["starts_at"] <= datetime.now(timezone.utc).timestamp():
        return "REFUSE", {"reason": "Event must start in the future"}
    conflict = slot_conflict(db, event["venue_id"], event["starts_at"], event["ends_at"])
    if conflict:
        return "REFUSE", {"reason": "Venue already reserved within the event or its 30-minute buffer"}
    db.execute(text("UPDATE event_schedule SET published = TRUE WHERE id = :id"), {"id": event_id})
    return "AGREE", {"event_id": event_id, "status": "published"}


def slot_conflict(db, venue, start, end):
    return db.execute(text("""
        SELECT id FROM event_schedule
        WHERE venue_id=:venue AND published=TRUE AND starts_at<:end AND ends_at>:start
        UNION ALL
        SELECT id FROM hires
        WHERE venue_id=:venue AND status='confirmed' AND starts_at<:end AND ends_at>:start
        LIMIT 1
    """), dict(venue=venue, start=start-BUFFER_SECONDS, end=end+BUFFER_SECONDS)).scalar_one_or_none()


def hire_price(hourly_rate_minor, start, end):
    """Apply a 20% premium to time booked on Saturday or Sunday in Sri Lanka."""
    sri_lanka = timezone(timedelta(hours=5, minutes=30))
    weighted_seconds = 0
    while start < end:
        local = datetime.fromtimestamp(start, sri_lanka)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        segment_end = min(end, int(midnight.timestamp()))
        weighted_seconds += (segment_end - start) * (120 if local.weekday() >= 5 else 100)
        start = segment_end
    return (hourly_rate_minor * weighted_seconds + 359999) // 360000


def hire_decision(db, request):
    approval = request["action"] == "review"
    if approval:
        saved = db.execute(text("SELECT * FROM hires WHERE id=:id"), request).mappings().one_or_none()
        if saved is None:
            return "REFUSE", {"reason": "Hire request not found"}
        hire = dict(saved)
    else:
        hire = dict(request, id=uuid4().hex)

    venue_id = hire["venue_id"]
    db.execute(text("UPDATE venues SET capacity=capacity WHERE id=:id"), {"id": venue_id})
    if approval:
        status = db.execute(text("SELECT status FROM hires WHERE id=:id"), hire).scalar_one()
        if status != "pending":
            return "REFUSE", {"reason": "Only pending requests can be reviewed"}
        if not request["approve"]:
            db.execute(text("UPDATE hires SET status='rejected' WHERE id=:id"), hire)
            return "AGREE", {"hire_id": hire["id"], "status": "rejected"}

    venue = db.execute(text("SELECT * FROM venues WHERE id=:id"), {"id": venue_id}).mappings().one_or_none()
    if venue is None:
        return "REFUSE", {"reason": "Venue not found"}
    start, end = hire["starts_at"], hire["ends_at"]
    if start <= datetime.now(timezone.utc).timestamp():
        return "REFUSE", {"reason": "Hire must start in the future"}
    duration = end - start
    if not venue["private_hire"] or hire["guests"] > venue["capacity"]:
        reason = "Venue is closed for private hire or has insufficient capacity"
    elif slot_conflict(db, venue_id, start, end):
        reason = "Venue is occupied within the requested slot or its 30-minute buffer"
    else:
        reason = None

    if reason:
        alternatives = []
        candidates = db.execute(text("""
            SELECT id,name,image,hourly_rate_minor FROM venues
            WHERE private_hire=TRUE AND capacity>=:guests ORDER BY name
        """), hire).mappings().all()
        for days in range(8):
            offset = days * 86400
            for candidate in candidates:
                if not slot_conflict(db, candidate["id"], start+offset, end+offset):
                    alternatives.append(event_response(dict(
                        venue_id=candidate["id"], venue_name=candidate["name"], venue_image=candidate["image"],
                        starts_at=start+offset, ends_at=end+offset,
                        price_minor=hire_price(candidate["hourly_rate_minor"], start+offset, end+offset),
                    )))
            if len(alternatives) >= 3:
                break
        if alternatives:
            return "PROPOSE", {"reason": reason, "alternatives": alternatives[:3]}
        return "REFUSE", {"reason": reason + ". No alternatives found in the next seven days"}

    if approval:
        db.execute(text("UPDATE hires SET status='confirmed' WHERE id=:id"), hire)
        status = "confirmed"
    else:
        status = "pending" if duration > 4 * 3600 else "confirmed"
        hire.update(status=status, price_minor=hire_price(venue["hourly_rate_minor"], start, end))
        db.execute(text("""
            INSERT INTO hires (id,venue_id,username,starts_at,ends_at,purpose,guests,price_minor,status)
            VALUES (:id,:venue_id,:username,:starts_at,:ends_at,:purpose,:guests,:price_minor,:status)
        """), hire)
    return "AGREE", {"hire_id": hire["id"], "status": status, "price_minor": hire["price_minor"]}


def venue_agent(db, message, dispatch):
    if message.content.get("action") in ("hire", "review"):
        decision, content = hire_decision(db, message.content)
        if decision == "AGREE" and content.get("hire_id"):
            username = db.execute(text("SELECT username FROM hires WHERE id=:id"),
                                  {"id": content["hire_id"]}).scalar_one()
            notify(dispatch, db, message, username,
                   f"Venue hire request {content['hire_id']}: {content['status']}.")
        return decision, content
    return publish(db, message.content["event_id"])


