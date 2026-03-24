import uuid
import hashlib
import logging
from flask import Blueprint, request, jsonify
from src.db.client import db_exec
from src.config import TIER_LIMITS

logger = logging.getLogger(__name__)
register_bp = Blueprint("register", __name__)


@register_bp.route("/api/v1/register", methods=["POST"])
def register():
    """
    Agent self-registration. Called once when the ClawCall skill is installed.

    Body:
      email           — user's email
      agent_webhook_url — the OpenClaw agent's public URL
      agent_name      — optional display name
      phone_number    — user's personal phone (for outbound callbacks)
    """
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    agent_webhook_url = (body.get("agent_webhook_url") or "").strip()
    agent_name = (body.get("agent_name") or "My Agent").strip()
    phone_number = (body.get("phone_number") or "").strip() or None

    if not email:
        return jsonify({"ok": False, "error": "email is required"}), 400
    if not agent_webhook_url:
        return jsonify({"ok": False, "error": "agent_webhook_url is required"}), 400

    # If user already exists — re-register (new API key, updated webhook URL)
    existing_user = db_exec("SELECT * FROM users WHERE email=%s", (email,), fetchone=True)

    if existing_user:
        existing_agent = db_exec(
            """
            SELECT a.*, pn.number AS assigned_number
            FROM agents a
            LEFT JOIN phone_numbers pn ON pn.agent_id = a.id
            WHERE a.user_id = %s
            ORDER BY a.created_at
            LIMIT 1
            """,
            (str(existing_user["id"]),),
            fetchone=True,
        )

        if existing_agent:
            # Rotate API key and update webhook URL
            new_key = _new_api_key()
            db_exec(
                "UPDATE agents SET api_key_hash=%s, webhook_url=%s WHERE id=%s",
                (new_key["hash"], agent_webhook_url, str(existing_agent["id"])),
            )
            if phone_number:
                db_exec("UPDATE users SET phone_number=%s WHERE id=%s", (phone_number, str(existing_user["id"])))

            assigned_number = existing_agent.get("assigned_number") or _assign_shared_number(str(existing_agent["id"]))
            return jsonify({
                "ok": True,
                "api_key": new_key["raw"],
                "phone_number": assigned_number,
                "tier": existing_user["tier"],
                "agent_id": str(existing_agent["id"]),
                "user_id": str(existing_user["id"]),
                "message": "Re-registered. New API key issued.",
            })

    # --- New user ---
    user_id = str(uuid.uuid4())
    db_exec(
        """
        INSERT INTO users (id, email, phone_number, tier, minutes_used_this_month, minutes_limit)
        VALUES (%s, %s, %s, 'free', 0, %s)
        """,
        (user_id, email, phone_number, TIER_LIMITS["free"]),
    )

    key = _new_api_key()
    agent_id = str(uuid.uuid4())
    db_exec(
        """
        INSERT INTO agents (id, user_id, name, webhook_url, api_key_hash)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (agent_id, user_id, agent_name, agent_webhook_url, key["hash"]),
    )

    # Assign a shared-pool number (free tier)
    assigned_number = _assign_shared_number(agent_id)

    logger.info(f"New registration: {email}, agent {agent_id}, number {assigned_number}")

    return jsonify({
        "ok": True,
        "api_key": key["raw"],
        "phone_number": assigned_number,
        "tier": "free",
        "agent_id": agent_id,
        "user_id": user_id,
        "message": (
            f"Setup complete! Your agent number is {assigned_number}. "
            "Store the api_key securely — it won't be shown again."
        ),
    })


def _new_api_key() -> dict:
    raw = str(uuid.uuid4())
    return {"raw": raw, "hash": hashlib.sha256(raw.encode()).hexdigest()}


def _assign_shared_number(agent_id: str) -> str | None:
    """Return the shared-pool number for this free-tier agent.
    All free-tier agents share the same number — routing is done by the
    caller's from_number at call time, so no exclusive assignment needed.
    """
    row = db_exec(
        "SELECT id, number FROM phone_numbers WHERE is_shared_pool=TRUE LIMIT 1",
        fetchone=True,
    )
    return row["number"] if row else None  # No shared numbers available — admin must add them
