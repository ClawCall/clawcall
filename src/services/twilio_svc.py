import logging
from twilio.rest import Client
from src.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WEBHOOK_BASE_URL

logger = logging.getLogger(__name__)
_client = None


def get_client() -> Client:
    global _client
    if _client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise RuntimeError("Twilio credentials not configured")
        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _client


def provision_number(area_code: str = "415") -> tuple[str, str]:
    """
    Buy a new dedicated Twilio number.
    Returns (twilio_sid, phone_number).
    """
    client = get_client()

    available = client.available_phone_numbers("US").local.list(
        area_code=area_code, limit=1, voice_enabled=True
    )
    if not available:
        available = client.available_phone_numbers("US").local.list(
            limit=1, voice_enabled=True
        )
    if not available:
        raise RuntimeError("No Twilio numbers available to provision")

    number = available[0].phone_number
    purchased = client.incoming_phone_numbers.create(
        phone_number=number,
        voice_url=f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/inbound",
        voice_method="POST",
        status_callback=f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/status",
        status_callback_method="POST",
    )

    logger.info(f"Provisioned Twilio number: {number} (SID: {purchased.sid})")
    return purchased.sid, number


def release_number(twilio_sid: str):
    """Release a Twilio number back to the pool."""
    client = get_client()
    client.incoming_phone_numbers(twilio_sid).delete()
    logger.info(f"Released Twilio number SID: {twilio_sid}")


def place_outbound_call(to_number: str, twiml_url: str) -> str:
    """
    Place an outbound call to `to_number`.
    Returns the Twilio CallSid.
    """
    client = get_client()
    from_number = _get_outbound_number()

    call = client.calls.create(
        to=to_number,
        from_=from_number,
        url=twiml_url,
        method="POST",
        record=True,
        recording_status_callback=f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/recording",
        recording_status_callback_method="POST",
        status_callback=f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/status",
        status_callback_method="POST",
        status_callback_event=["completed", "failed", "busy", "no-answer"],
    )

    logger.info(f"Placed outbound call to {to_number}, SID: {call.sid}")
    return call.sid


def _get_outbound_number() -> str:
    """Pick a ClawCall-owned number to use as the caller ID."""
    from src.db.client import db_exec
    row = db_exec(
        "SELECT number FROM phone_numbers LIMIT 1",
        fetchone=True,
    )
    if row:
        return row["number"]
    raise RuntimeError("No phone numbers available in pool")


def start_recording(call_sid: str):
    """
    Start recording an already-connected inbound call.
    The recording webhook will POST to /webhooks/twilio/recording when done.
    """
    client = get_client()
    client.calls(call_sid).recordings.create(
        recording_status_callback=f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/recording",
        recording_status_callback_method="POST",
    )
    logger.info(f"Recording started for call {call_sid}")
