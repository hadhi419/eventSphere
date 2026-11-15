import logging
from enum import Enum
from pydantic import BaseModel
from sqlalchemy import text


logger = logging.getLogger("uvicorn.error")

DEFAULT_ACTIONS = {"BookingAgent": "book", "VenueAgent": "publish",
                   "NotificationAgent": "save_notification"}
RESULT_FIELDS = ("reason", "decision", "status", "quantity", "revoked",
                 "refunded_bookings", "refund_minor", "delivered")


class Performative(str, Enum):
    REQUEST = "REQUEST"
    AGREE = "AGREE"
    REFUSE = "REFUSE"
    PROPOSE = "PROPOSE"
    INFORM = "INFORM"
    FAILURE = "FAILURE"


class Message(BaseModel):
    sender: str
    receiver: str
    performative: Performative
    content: dict
    conversation_id: str


def record(db, message):
    db.execute(
        text("INSERT INTO messages (conversation_id, body) VALUES (:id, :body)"),
        {"id": message.conversation_id, "body": message.model_dump_json()},
    )


def notify(dispatch, db, source, username, body, **details):
    if username:
        dispatch(db, Message(sender=source.receiver, receiver="NotificationAgent",
                             performative="INFORM", content=dict(username=username, body=body, **details),
                             conversation_id=source.conversation_id))


def dispatch_message(db, message, handlers):
    record(db, message)
    action = message.content.get("action", DEFAULT_ACTIONS.get(message.receiver, "handle"))
    logger.info("[agents %s] %s -> %s %s action=%s",
                message.conversation_id, message.sender, message.receiver,
                message.performative.value, action)
    try:
        performative, content = handlers[message.receiver](db, message)
    except Exception as exc:
        logger.error("[agents %s] %s action=%s failed (%s)",
                     message.conversation_id, message.receiver, action, type(exc).__name__)
        raise
    reply = Message(
        sender=message.receiver, receiver=message.sender,
        performative=performative, content=content,
        conversation_id=message.conversation_id,
    )
    record(db, reply)
    details = {key: content[key] for key in RESULT_FIELDS if key in content}
    logger.info("[agents %s] %s -> %s %s action=%s %s",
                reply.conversation_id, reply.sender, reply.receiver,
                reply.performative.value, action, details)
    return reply
