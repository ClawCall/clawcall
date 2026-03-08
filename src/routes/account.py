from flask import Blueprint, request, jsonify, g
from src.db.client import db_exec
from src.middleware.auth import require_api_key

account_bp = Blueprint("account", __name__)


@account_bp.route("/api/v1/account", methods=["GET"])
@require_api_key
def get_account():
    """Return current tier, usage, phone number, and agent details."""
    agent = g.agent
    user = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    phone = db_exec(
        "SELECT number, is_dedicated FROM phone_numbers WHERE agent_id=%s LIMIT 1",
        (str(agent["id"]),),
        fetchone=True,
    )

    minutes_remaining = max(0, user["minutes_limit"] - user["minutes_used_this_month"])

    return jsonify({
        "ok": True,
        "email": user["email"],
        "tier": user["tier"],
        "phone_number": phone["number"] if phone else None,
        "is_dedicated": phone["is_dedicated"] if phone else False,
        "minutes_used": user["minutes_used_this_month"],
        "minutes_limit": user["minutes_limit"],
        "minutes_remaining": minutes_remaining,
        "agent_id": str(agent["id"]),
        "agent_name": agent["name"],
        "webhook_url": agent["webhook_url"],
    })


@account_bp.route("/api/v1/account/phone", methods=["POST"])
@require_api_key
def update_phone():
    """Update the user's personal phone number (for receiving outbound calls)."""
    agent = g.agent
    body = request.get_json(silent=True) or {}
    phone = (body.get("phone_number") or "").strip()

    if not phone:
        return jsonify({"ok": False, "error": "phone_number is required"}), 400

    db_exec(
        "UPDATE users SET phone_number=%s WHERE id=%s",
        (phone, agent["user_id"]),
    )
    return jsonify({"ok": True, "phone_number": phone})


@account_bp.route("/api/v1/account/voice", methods=["POST"])
@require_api_key
def update_voice():
    """
    Set the TTS voice for this agent (Polly voices).
    Body: { "voice": "aria" | "joanna" | "matthew" | "amy" | "brian" | "emma" | "olivia" }
    Accepts shortnames or full Polly IDs (e.g. "Polly.Matthew-Neural").
    """
    from src.config import POLLY_VOICES
    agent = g.agent
    body = request.get_json(silent=True) or {}
    voice = (body.get("voice") or "").strip().lower()

    if not voice:
        return jsonify({"ok": False, "error": "voice is required"}), 400

    # Shortname → full Polly ID
    if voice in POLLY_VOICES:
        polly_id = POLLY_VOICES[voice]
        db_exec("UPDATE agents SET voice=%s WHERE id=%s", (polly_id, str(agent["id"])))
        return jsonify({"ok": True, "voice": polly_id})

    # Already a full Polly ID (case-insensitive prefix match)
    if voice.startswith("polly."):
        polly_id = "Polly." + voice[6:]
        db_exec("UPDATE agents SET voice=%s WHERE id=%s", (polly_id, str(agent["id"])))
        return jsonify({"ok": True, "voice": polly_id})

    valid = ", ".join(POLLY_VOICES.keys())
    return jsonify({"ok": False, "error": f"Unknown voice. Valid options: {valid}"}), 400


@account_bp.route("/api/v1/account/webhook", methods=["POST"])
@require_api_key
def update_webhook_push():
    """
    Set (or clear) the webhook URL for call-event push notifications.
    Team tier only.
    Body: { "webhook_push_url": "https://..." }  — send empty string to disable.
    """
    agent = g.agent
    user = db_exec("SELECT tier FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    if not user or user["tier"] != "team":
        return jsonify({"ok": False, "error": "Webhook push requires Team tier"}), 403

    body = request.get_json(silent=True) or {}
    url = (body.get("webhook_push_url") or "").strip() or None

    db_exec("UPDATE agents SET webhook_push_url=%s WHERE id=%s", (url, str(agent["id"])))
    return jsonify({"ok": True, "webhook_push_url": url})
