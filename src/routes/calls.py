import uuid
import logging
from flask import Blueprint, request, jsonify, g
from src.db.client import db_exec
from src.middleware.auth import require_api_key
from src.services.twilio_svc import place_outbound_call
from src.services.scheduler import add_scheduled_call, remove_scheduled_call
from src.services.minutes import within_limit
from src.services import bridge
from src.config import TWILIO_WEBHOOK_BASE_URL

logger = logging.getLogger(__name__)
calls_bp = Blueprint("calls", __name__)


# ---------------------------------------------------------------------------
# Long-poll listen + respond (no public URL required on agent side)
# ---------------------------------------------------------------------------

@calls_bp.route("/api/v1/calls/listen", methods=["GET"])
@require_api_key
def listen():
    """
    Long-poll endpoint. Agent calls this to receive incoming call messages.
    Blocks until a message arrives or timeout (max 25s).

    Returns:
      { ok: true, call_sid, message }          — a call message is waiting
      { ok: true, timeout: true }              — no message within timeout
    """
    agent = g.agent
    timeout = min(float(request.args.get("timeout", 25)), 25)

    msg = bridge.get_pending_message(str(agent["id"]), timeout=timeout)

    if msg is None:
        return jsonify({"ok": True, "timeout": True})

    return jsonify({
        "ok": True,
        "call_sid": msg["call_sid"],
        "message": msg["message"],
    })


@calls_bp.route("/api/v1/calls/respond/<call_sid>", methods=["POST"])
@require_api_key
def respond(call_sid):
    """
    Agent submits its response to a queued call message.
    Body: { response: "...", end_call: false }
    """
    body = request.get_json(silent=True) or {}
    response_text = (body.get("response") or "").strip()
    end_call = bool(body.get("end_call", False))

    if not response_text:
        return jsonify({"ok": False, "error": "response is required"}), 400

    ok = bridge.submit_response(call_sid, response_text, end_call)
    if not ok:
        return jsonify({"ok": False, "error": "Unknown or expired call_sid"}), 404

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Outbound — Task Completion Callback
# ---------------------------------------------------------------------------

@calls_bp.route("/api/v1/calls/outbound/callback", methods=["POST"])
@require_api_key
def outbound_callback():
    """
    Agent calls this when a background task finishes.
    ClawCall will ring the user and speak the message.
    """
    agent = g.agent
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    allow_followup = bool(body.get("allow_followup", True))

    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    user = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404

    if user["tier"] == "free":
        return jsonify({"ok": False, "error": "Outbound calls require Pro tier or above"}), 403

    if not within_limit(str(user["id"])):
        return jsonify({"ok": False, "error": "Monthly minute limit reached"}), 429

    if not user.get("phone_number"):
        return jsonify({"ok": False, "error": "No personal phone number registered. POST /api/v1/account/phone first"}), 400

    callback_id = str(uuid.uuid4())
    db_exec(
        "INSERT INTO pending_callbacks (id, agent_id, message, allow_followup) VALUES (%s, %s, %s, %s)",
        (callback_id, str(agent["id"]), message, allow_followup),
    )

    fu = "1" if allow_followup else "0"
    twiml_url = f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/callback?callback_id={callback_id}&allow_followup={fu}"

    try:
        call_sid = place_outbound_call(user["phone_number"], twiml_url)
        db_exec(
            "UPDATE pending_callbacks SET twilio_call_sid=%s, status='calling' WHERE id=%s",
            (call_sid, callback_id),
        )
        return jsonify({"ok": True, "call_sid": call_sid, "callback_id": callback_id})
    except Exception as e:
        logger.exception(f"Outbound callback failed: {e}")
        db_exec("DELETE FROM pending_callbacks WHERE id=%s", (callback_id,))
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Scheduled Calls
# ---------------------------------------------------------------------------

@calls_bp.route("/api/v1/calls/schedule", methods=["POST"])
@require_api_key
def create_schedule():
    """
    Schedule a recurring or one-time call.
    Body: { cron, label, task_context, timezone }
    """
    agent = g.agent
    body = request.get_json(silent=True) or {}
    cron_expr = (body.get("cron") or "").strip()
    label = (body.get("label") or "Scheduled call").strip()
    task_context = (body.get("task_context") or "").strip()
    timezone = (body.get("timezone") or "UTC").strip()

    if not cron_expr:
        return jsonify({"ok": False, "error": "cron is required"}), 400

    user = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404
    if user["tier"] == "free":
        return jsonify({"ok": False, "error": "Scheduled calls require Pro tier or above"}), 403

    schedule_id = str(uuid.uuid4())
    db_exec(
        """
        INSERT INTO scheduled_calls (id, agent_id, label, cron_expression, task_context, timezone)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (schedule_id, str(agent["id"]), label, cron_expr, task_context, timezone),
    )

    try:
        add_scheduled_call(schedule_id, cron_expr, timezone, str(agent["id"]), task_context)
    except ValueError as e:
        db_exec("DELETE FROM scheduled_calls WHERE id=%s", (schedule_id,))
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "id": schedule_id, "label": label, "cron": cron_expr, "timezone": timezone})


@calls_bp.route("/api/v1/calls/schedule", methods=["GET"])
@require_api_key
def list_schedules():
    agent = g.agent
    rows = db_exec(
        "SELECT id, label, cron_expression, task_context, timezone, is_active, last_run_at, created_at FROM scheduled_calls WHERE agent_id=%s ORDER BY created_at DESC",
        (str(agent["id"]),),
        fetchall=True,
    ) or []
    return jsonify({"ok": True, "schedules": rows})


@calls_bp.route("/api/v1/calls/schedule/<schedule_id>", methods=["DELETE"])
@require_api_key
def delete_schedule(schedule_id):
    agent = g.agent
    row = db_exec(
        "SELECT id FROM scheduled_calls WHERE id=%s AND agent_id=%s",
        (schedule_id, str(agent["id"])),
        fetchone=True,
    )
    if not row:
        return jsonify({"ok": False, "error": "Schedule not found"}), 404

    db_exec("DELETE FROM scheduled_calls WHERE id=%s", (schedule_id,))
    remove_scheduled_call(schedule_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Call History
# ---------------------------------------------------------------------------

@calls_bp.route("/api/v1/calls/history", methods=["GET"])
@require_api_key
def call_history():
    agent = g.agent
    limit = min(int(request.args.get("limit", 20)), 100)
    include_transcript = request.args.get("transcripts", "false").lower() == "true"
    transcript_col = ", transcript_json" if include_transcript else ""
    rows = db_exec(
        f"""
        SELECT id, twilio_call_sid, direction, call_type, from_number, to_number,
               duration_seconds, status, started_at, ended_at{transcript_col}
        FROM call_logs
        WHERE agent_id=%s
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (str(agent["id"]), limit),
        fetchall=True,
    ) or []

    calls = []
    for row in rows:
        r = dict(row)
        if include_transcript and r.get("transcript_json"):
            import json
            try:
                r["transcript"] = json.loads(r.pop("transcript_json"))
            except Exception:
                r["transcript"] = []
        elif "transcript_json" in r:
            r.pop("transcript_json")
        calls.append(r)

    return jsonify({"ok": True, "calls": calls})


# ---------------------------------------------------------------------------
# Third Party Calling (Pro+)
# ---------------------------------------------------------------------------

@calls_bp.route("/api/v1/calls/outbound/third-party", methods=["POST"])
@require_api_key
def third_party_call():
    """
    Agent calls a third party autonomously on the user's behalf.
    Body: { to_number, objective, context, callback_on_complete }
    """
    agent = g.agent
    body = request.get_json(silent=True) or {}
    to_number = (body.get("to_number") or "").strip()
    objective = (body.get("objective") or "").strip()
    context = (body.get("context") or "").strip()

    if not to_number:
        return jsonify({"ok": False, "error": "to_number is required"}), 400
    if not objective:
        return jsonify({"ok": False, "error": "objective is required"}), 400

    user = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404
    if user["tier"] not in ("pro", "team"):
        return jsonify({"ok": False, "error": "Third party calls require Pro tier or above"}), 403

    if not within_limit(str(user["id"])):
        return jsonify({"ok": False, "error": "Monthly minute limit reached"}), 429

    job_id = str(uuid.uuid4())
    db_exec(
        """
        INSERT INTO third_party_calls (id, agent_id, to_number, objective, context, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        """,
        (job_id, str(agent["id"]), to_number, objective, context),
    )

    twiml_url = f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/third-party?job_id={job_id}"

    try:
        call_sid = place_outbound_call(to_number, twiml_url)
        db_exec(
            "UPDATE third_party_calls SET twilio_call_sid=%s, status='calling' WHERE id=%s",
            (call_sid, job_id),
        )
        logger.info(f"Third party call placed to {to_number}, job {job_id}")
        return jsonify({"ok": True, "job_id": job_id, "call_sid": call_sid})
    except Exception as e:
        logger.exception(f"Third party call failed: {e}")
        db_exec("UPDATE third_party_calls SET status='failed' WHERE id=%s", (job_id,))
        return jsonify({"ok": False, "error": str(e)}), 500
