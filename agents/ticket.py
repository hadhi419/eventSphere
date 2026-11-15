import secrets
from time import time
from sqlalchemy import text
from message_bus import Performative


def ticket_agent(db, message):
    values = {"event": message.content["event_id"]}
    if message.content["action"] == "issue":
        values["code"] = secrets.token_urlsafe(24)
        db.execute(text("INSERT INTO tickets (code, event_id) VALUES (:code, :event)"), values)
        return Performative.AGREE, {"code": values["code"]}
    # Serialize cancellation and check-in on the same event.
    db.execute(text("UPDATE events SET remaining=remaining WHERE id=:event"), values)
    if message.content["action"] == "cancel_event":
        db.execute(text("UPDATE tickets SET used=-1 WHERE event_id=:event AND used=0"), values)
        return Performative.AGREE, {"event_id": values["event"]}
    if message.content["action"] == "cancel":
        codes = [{"code": code} for code in message.content["codes"]]
        for code in codes:
            used = db.execute(text("SELECT used FROM tickets WHERE code=:code"), code).scalar_one()
            if used:
                return Performative.REFUSE, {"reason": "Booking contains a checked-in ticket"}
        db.execute(text("UPDATE tickets SET used=-1 WHERE code=:code"), codes)
        return Performative.AGREE, {"revoked": len(codes)}
    values["code"] = message.content["code"]
    result = db.execute(text(
        "UPDATE tickets SET used = 1 WHERE code = :code AND event_id = :event AND used = 0"
    ), values)
    if result.rowcount == 1:
        db.execute(text("INSERT INTO check_in_times (code, checked_in_at) VALUES (:code, :now)"),
                   {"code": values["code"], "now": int(time())})
        return Performative.AGREE, {"decision": "Admit"}
    existing = db.execute(
        text("SELECT event_id, used FROM tickets WHERE code = :code"), values
    ).mappings().one_or_none()
    if existing is None:
        reason = "Unknown ticket"
    elif existing["event_id"] != values["event"]:
        reason = "Ticket is for another event"
    elif existing["used"] == -1:
        reason = "Ticket cancelled"
    else:
        reason = "Ticket already used"
    return Performative.REFUSE, {"reason": reason}


