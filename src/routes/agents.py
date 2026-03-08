"""
Multi-agent management — Team tier only.

GET    /api/v1/agents               → list all agents for authenticated user
POST   /api/v1/agents               → register additional agent (≤5 total)
PATCH  /api/v1/agents/<id>          → update name / webhook URL
DELETE /api/v1/agents/<id>          → remove agent (not the primary one)
"""
import uuid
import hashlib
import logging

from flask import Blueprint, request, jsonify, g

from src.db.client import db_exec
from src.middleware.auth import require_api_key
from src.config import TIER_MAX_AGENTS

logger = logging.getLogger(__name__)
agents_bp = Blueprint("agents", __name__)


# ---------------------------------------------------------------------------
# List agents
# ---------------------------------------------------------------------------

@agents_bp.route("/api/v1/agents", methods=["GET"])
@require_api_key
def list_agents():
    """Return all agents that belong to the authenticated user."""
    agent = g.agent
    rows = db_exec(
        """
        SELECT a.id, a.name, a.webhook_url, a.voice, a.webhook_push_url,
               a.created_at, pn.number AS phone_number, pn.is_dedicated
        FROM agents a
        LEFT JOIN phone_numbers pn ON pn.agent_id = a.id
        WHERE a.user_id = %s
        ORDER BY a.created_at
        """,
        (agent["user_id"],),
        fetchall=True,
    ) or []
    return jsonify({"ok": True, "agents": [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# Add agent
# ---------------------------------------------------------------------------

@agents_bp.route("/api/v1/agents", methods=["POST"])
@require_api_key
def add_agent():
    """
    Register an additional agent. Team tier only; maximum 5 agents total.
    Body: { "agent_webhook_url": "...", "agent_name": "..." }
    """
    agent = g.agent
    user = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404

    if user["tier"] != "team":
        return jsonify({"ok": False, "error": "Multi-agent support requires Team tier"}), 403

    body = request.get_json(silent=True) or {}
    webhook_url = (body.get("agent_webhook_url") or "").strip()
    name = (body.get("agent_name") or "Agent").strip()

    if not webhook_url:
        return jsonify({"ok": False, "error": "agent_webhook_url is required"}), 400

    count_row = db_exec(
        "SELECT COUNT(*) AS cnt FROM agents WHERE user_id=%s",
        (agent["user_id"],),
        fetchone=True,
    )
    current = int(count_row["cnt"]) if count_row else 0
    max_agents = TIER_MAX_AGENTS["team"]

    if current >= max_agents:
        return jsonify({"ok": False, "error": f"Team tier allows up to {max_agents} agents"}), 403

    raw_key = str(uuid.uuid4())
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    new_id = str(uuid.uuid4())

    db_exec(
        "INSERT INTO agents (id, user_id, name, webhook_url, api_key_hash) VALUES (%s, %s, %s, %s, %s)",
        (new_id, agent["user_id"], name, webhook_url, key_hash),
    )

    # Provision a dedicated Twilio number for the new agent
    phone_number = None
    try:
        from src.services.twilio_svc import provision_number
        twilio_sid, phone_number = provision_number()
        db_exec(
            """
            INSERT INTO phone_numbers (id, twilio_sid, number, agent_id, is_dedicated, assigned_at)
            VALUES (%s, %s, %s, %s, TRUE, NOW())
            """,
            (str(uuid.uuid4()), twilio_sid, phone_number, new_id),
        )
        logger.info(f"Provisioned dedicated number {phone_number} for team agent {new_id}")
    except Exception as e:
        logger.warning(f"Could not provision Twilio number for new team agent: {e}")

    return jsonify({
        "ok": True,
        "agent_id": new_id,
        "api_key": raw_key,
        "phone_number": phone_number,
        "name": name,
        "message": f"Agent '{name}' created. Store the api_key — it won't be shown again.",
    }), 201


# ---------------------------------------------------------------------------
# Update agent
# ---------------------------------------------------------------------------

@agents_bp.route("/api/v1/agents/<target_id>", methods=["PATCH"])
@require_api_key
def update_agent(target_id):
    """Update an agent's name or webhook URL."""
    agent = g.agent
    target = db_exec(
        "SELECT id FROM agents WHERE id=%s AND user_id=%s",
        (target_id, agent["user_id"]),
        fetchone=True,
    )
    if not target:
        return jsonify({"ok": False, "error": "Agent not found"}), 404

    body = request.get_json(silent=True) or {}
    updates, params = [], []

    if "agent_name" in body:
        updates.append("name=%s")
        params.append((body["agent_name"] or "Agent").strip())
    if "agent_webhook_url" in body:
        updates.append("webhook_url=%s")
        params.append((body["agent_webhook_url"] or "").strip())

    if not updates:
        return jsonify({"ok": False, "error": "No fields to update"}), 400

    params.append(target_id)
    db_exec(f"UPDATE agents SET {', '.join(updates)} WHERE id=%s", params)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Remove agent
# ---------------------------------------------------------------------------

@agents_bp.route("/api/v1/agents/<target_id>", methods=["DELETE"])
@require_api_key
def remove_agent(target_id):
    """
    Remove an agent. Cannot remove the primary (oldest) agent.
    Team tier only.
    """
    agent = g.agent
    user = db_exec("SELECT tier FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    if not user or user["tier"] != "team":
        return jsonify({"ok": False, "error": "Team tier required"}), 403

    target = db_exec(
        "SELECT id FROM agents WHERE id=%s AND user_id=%s",
        (target_id, agent["user_id"]),
        fetchone=True,
    )
    if not target:
        return jsonify({"ok": False, "error": "Agent not found"}), 404

    # Protect primary agent (earliest created_at)
    primary = db_exec(
        "SELECT id FROM agents WHERE user_id=%s ORDER BY created_at LIMIT 1",
        (agent["user_id"],),
        fetchone=True,
    )
    if primary and str(primary["id"]) == target_id:
        return jsonify({
            "ok": False,
            "error": "Cannot remove the primary agent. Downgrade your account instead.",
        }), 400

    # Release dedicated Twilio number
    phone_row = db_exec(
        "SELECT twilio_sid FROM phone_numbers WHERE agent_id=%s AND is_dedicated=TRUE",
        (target_id,),
        fetchone=True,
    )
    if phone_row:
        try:
            from src.services.twilio_svc import release_number
            release_number(phone_row["twilio_sid"])
        except Exception as e:
            logger.warning(f"Could not release Twilio number for agent {target_id}: {e}")
        db_exec("DELETE FROM phone_numbers WHERE agent_id=%s", (target_id,))

    # ON DELETE CASCADE handles scheduled_calls, call_logs foreign keys
    db_exec("DELETE FROM agents WHERE id=%s", (target_id,))
    logger.info(f"Agent {target_id} removed by user {agent['user_id']}")
    return jsonify({"ok": True, "message": "Agent removed."})
