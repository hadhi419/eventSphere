from time import time, time_ns
from uuid import uuid4

from sqlalchemy import text

HOLD_SECONDS = 600


def initialize_waitlist(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id VARCHAR(32) PRIMARY KEY, event_id VARCHAR(32) NOT NULL,
            category_id VARCHAR(32) NOT NULL, username VARCHAR(64) NOT NULL,
            joined_at BIGINT NOT NULL, expires BIGINT, status VARCHAR(12) NOT NULL
        )
    """))


def restore_seat(db, entry):
    db.execute(text("UPDATE events SET remaining=remaining+1 WHERE id=:event_id"), entry)
    db.execute(text("UPDATE ticket_categories SET remaining=remaining+1 WHERE id=:category_id"), entry)


def waitlist_agent(db, message, dispatch, make_message, notify):
    data = message.content
    event_id = data["event_id"]
    db.execute(text("UPDATE events SET remaining=remaining WHERE id=:id"), {"id": event_id})
    now = int(time())
    event = db.execute(text("SELECT published, starts_at FROM event_schedule WHERE id=:id"),
                       {"id": event_id}).mappings().one_or_none()
    if not event or not event["published"] or event["starts_at"] <= now:
        closed = db.execute(text("""
            UPDATE waitlist SET status='expired' WHERE event_id=:id AND status IN ('waiting','held')
        """), {"id": event_id})
        return "REFUSE", {"reason": "Event is closed", "entries_expired": closed.rowcount}

    expired = db.execute(text("""
        UPDATE waitlist SET status='expired'
        WHERE event_id=:id AND status='held' AND expires<=:now
        RETURNING event_id, category_id
    """), {"id": event_id, "now": now}).mappings().all()
    for entry in expired:
        restore_seat(db, entry)

    action = data["action"]
    entry_id = data.get("id")
    if action == "join":
        category = db.execute(text("""
            SELECT id FROM ticket_categories WHERE id=:category_id AND event_id=:event_id
        """), data).scalar_one_or_none()
        if category is None:
            return "REFUSE", {"reason": "Ticket category not found"}
        entry_id = db.execute(text("""
            SELECT id FROM waitlist WHERE event_id=:event_id AND category_id=:category_id
            AND username=:username AND status IN ('waiting','held')
        """), data).scalar_one_or_none()
        if entry_id is None:
            entry_id = uuid4().hex
            db.execute(text("""
                INSERT INTO waitlist (id,event_id,category_id,username,joined_at,status)
                VALUES (:id,:event_id,:category_id,:username,:now,'waiting')
            """), dict(data, id=entry_id, now=time_ns() // 1000))

    if action == "claim":
        held = db.execute(text("""
            SELECT * FROM waitlist WHERE id=:id AND event_id=:event_id
            AND username=:username AND status='held'
        """), data).mappings().one_or_none()
        if held is not None:
            restore_seat(db, held)
            reply = dispatch(db, make_message(
                sender="WaitlistAgent", receiver="BookingAgent", performative="REQUEST",
                content=dict(event_id=event_id, category_id=held["category_id"], username=data["username"]),
                conversation_id=message.conversation_id,
            ))
            if reply.performative != "AGREE":
                raise RuntimeError("Held seat could not be allocated")
            db.execute(text("UPDATE waitlist SET status='claimed' WHERE id=:id"), data)
            return reply.performative, reply.content

    waiting = db.execute(text("""
        SELECT * FROM waitlist WHERE event_id=:id AND status='waiting' ORDER BY joined_at,id
    """), {"id": event_id}).mappings().all()
    offered = 0
    for entry in waiting:
        updated = db.execute(text("""
            UPDATE ticket_categories SET remaining=remaining-1
            WHERE id=:category_id AND event_id=:event_id AND remaining>0
        """), entry)
        if updated.rowcount:
            db.execute(text("UPDATE events SET remaining=remaining-1 WHERE id=:event_id"), entry)
            db.execute(text("UPDATE waitlist SET status='held',expires=:expires WHERE id=:id"),
                       dict(entry, expires=min(now+HOLD_SECONDS, event["starts_at"])))
            notify(dispatch, db, message, entry["username"],
                   f"A waitlist ticket is held for you. Open your waitlist to claim it. Offer ID: {entry['id']}.")
            offered += 1
    if action == "claim":
        return "REFUSE", {"reason": "No active hold for this account"}
    return "AGREE", {"waitlist_id": entry_id, "offers_created": offered, "entries_expired": len(expired)}
