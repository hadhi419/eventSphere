import json
from time import time
from uuid import uuid4
from sqlalchemy import text
from message_bus import Message, Performative, notify


def cancel_booking(db, message, dispatch):
    values = message.content
    booking = db.execute(text("""
        SELECT * FROM bookings WHERE reference=:reference AND username=:username
    """), values).mappings().one_or_none()
    if booking is None:
        return Performative.REFUSE, {"reason": "Booking not found"}
    event = {"id": booking["event_id"]}
    db.execute(text("UPDATE events SET remaining=remaining WHERE id=:id"), event)
    start = db.execute(text("SELECT starts_at FROM event_schedule WHERE id=:id"), event).scalar_one_or_none()
    refund = booking["total_minor"] if start is None or start - time() >= 86400 else 0
    payload = {"payment_reference": values["reference"], "refund_minor": refund}
    if db.execute(text("SELECT refund_minor FROM cancellations WHERE reference=:reference"),
                  values).scalar_one_or_none() is not None:
        return Performative.REFUSE, {"reason": "Booking already cancelled"}
    if start is not None and start <= time():
        return Performative.REFUSE, {"reason": "Event has already started"}
    reply = dispatch(db, Message(
        sender="BookingAgent", receiver="TicketAgent", performative=Performative.REQUEST,
        content={"action": "cancel", "codes": json.loads(booking["codes"]), "event_id": event["id"]},
        conversation_id=message.conversation_id,
    ))
    if reply.performative != Performative.AGREE:
        return reply.performative, reply.content
    db.execute(text("INSERT INTO cancellations (reference, refund_minor) VALUES (:reference,:refund)"),
               {"reference": values["reference"], "refund": refund})
    db.execute(text("UPDATE events SET remaining=remaining+:quantity WHERE id=:id"),
               dict(event, quantity=booking["quantity"]))
    db.execute(text("UPDATE ticket_categories SET remaining=remaining+:quantity WHERE id=:id"),
               {"id": booking["category_id"], "quantity": booking["quantity"]})
    if booking["category_id"]:
        dispatch(db, Message(sender="BookingAgent", receiver="WaitlistAgent", performative="REQUEST",
                             content={"action": "sweep", "event_id": event["id"]},
                             conversation_id=message.conversation_id))
    notify(dispatch, db, message, booking["username"], f"Booking cancelled. Simulated refund: {refund} minor units.")
    return Performative.AGREE, payload


def cancel_event(db, message, dispatch):
    params = {"id": message.content["event_id"]}
    db.execute(text("UPDATE events SET remaining=remaining WHERE id=:id"), params)
    db.execute(text("""
        UPDATE venues SET capacity=capacity
        WHERE id=(SELECT venue_id FROM event_schedule WHERE id=:id)
    """), params)
    updated = db.execute(text("""
        UPDATE event_schedule SET published=FALSE WHERE id=:id AND published=TRUE
    """), params)
    if updated.rowcount != 1:
        return Performative.REFUSE, {"reason": "Event is not published"}
    db.execute(text("INSERT INTO cancelled_events (id) VALUES (:id)"), params)
    db.execute(text("UPDATE waitlist SET status='expired' WHERE event_id=:id AND status IN ('waiting','held')"), params)
    affected = db.execute(text("""
        SELECT username, total_minor FROM bookings b WHERE event_id=:id
        AND NOT EXISTS (SELECT 1 FROM cancellations c WHERE c.reference=b.reference)
    """), params).mappings().all()
    refunds = db.execute(text("""
        INSERT INTO cancellations (reference, refund_minor)
        SELECT b.reference, b.total_minor FROM bookings b
        WHERE b.event_id=:id AND NOT EXISTS (
            SELECT 1 FROM cancellations c WHERE c.reference=b.reference
        )
    """), params)
    dispatch(db, Message(
        sender="BookingAgent", receiver="TicketAgent", performative=Performative.REQUEST,
        content={"action": "cancel_event", "event_id": params["id"]},
        conversation_id=message.conversation_id,
    ))
    for booking in affected:
        notify(dispatch, db, message, booking["username"],
               f"Event cancelled. Simulated refund: {booking['total_minor']} minor units.")
    return Performative.AGREE, {"event_id": params["id"], "refunded_bookings": refunds.rowcount}


def booking_agent(db, message, dispatch):
    if message.content.get("action") == "cancel_event":
        return cancel_event(db, message, dispatch)
    if message.content.get("action") == "cancel":
        return cancel_booking(db, message, dispatch)
    event_id = message.content["event_id"]
    quantity = message.content.get("quantity", 1)
    if not 1 <= quantity <= 6:
        return Performative.REFUSE, {"reason": "Choose between 1 and 6 tickets"}
    db.execute(text("UPDATE events SET remaining=remaining WHERE id=:id"), {"id": event_id})
    schedule = db.execute(text(
        "SELECT published, starts_at FROM event_schedule WHERE id=:id"
    ), {"id": event_id}).mappings().one_or_none()
    category_id = message.content.get("category_id")
    price = 0
    if schedule is not None:
        if not schedule["published"] or schedule["starts_at"] <= time():
            return Performative.REFUSE, {"reason": "Event is not open for booking"}
        if not category_id:
            return Performative.REFUSE, {"reason": "Choose a ticket category"}
        price = db.execute(text("""
            UPDATE ticket_categories SET remaining=remaining-:quantity
            WHERE id=:category AND event_id=:event AND remaining>=:quantity
            RETURNING price_minor
        """), {"category": category_id, "event": event_id, "quantity": quantity}).scalar_one_or_none()
        if price is None:
            return Performative.REFUSE, {"reason": "Ticket category is unavailable or sold out"}
    result = db.execute(
        text("UPDATE events SET remaining = remaining - :quantity WHERE id = :id AND remaining >= :quantity"),
        {"id": event_id, "quantity": quantity},
    )
    if result.rowcount != 1:
        return Performative.REFUSE, {"reason": "Event is unavailable or sold out"}
    codes = []
    for _ in range(quantity):
        issued = dispatch(db, Message(
            sender="BookingAgent", receiver="TicketAgent", performative=Performative.REQUEST,
            content={"action": "issue", "event_id": event_id},
            conversation_id=message.conversation_id,
        ))
        codes.append(issued.content["code"])
    reference = "SIM-" + uuid4().hex
    db.execute(text("""
        INSERT INTO bookings (reference, username, event_id, category_id, quantity, total_minor, codes)
        VALUES (:reference, :username, :event, :category, :quantity, :total, :codes)
    """), dict(reference=reference, username=message.content.get("username"), event=event_id,
               category=category_id, quantity=quantity, total=price * quantity, codes=json.dumps(codes)))
    notify(dispatch, db, message, message.content.get("username"),
           f"Booking confirmed for {quantity} ticket(s). Reference: {reference}. Open My bookings for your tickets.",
           booking_reference=reference)
    return Performative.AGREE, {
        "code": codes[0], "codes": codes, "quantity": quantity, "price_minor": price,
        "total_minor": price * quantity, "payment_reference": reference,
    }


