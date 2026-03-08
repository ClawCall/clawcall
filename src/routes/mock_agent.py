"""
Mock OpenClaw agent — simulates a real agent webhook for testing.
Mounted at /mock-agent/clawcall/message

Remove this file before going to production.
"""
import random
from flask import Blueprint, request, jsonify

mock_agent_bp = Blueprint("mock_agent", __name__)

RESPONSES = [
    ("Hey! I'm your ClawCall test agent. I heard you say: \"{msg}\". "
     "What else would you like to test?", False),
    ("Got it — \"{msg}\". I'm a mock agent running locally. "
     "The bridge is working perfectly! Anything else?", False),
    ("Testing complete. I received: \"{msg}\". "
     "You can replace me with your real OpenClaw agent. Goodbye!", True),
]

_call_counts: dict[str, int] = {}


@mock_agent_bp.route("/mock-agent/clawcall/message", methods=["POST"])
def mock_message():
    body = request.get_json(silent=True) or {}
    call_sid = body.get("call_sid", "unknown")
    msg = (body.get("message") or "").strip()

    # Cycle through responses per call
    count = _call_counts.get(call_sid, 0)
    _call_counts[call_sid] = count + 1

    template, end_call = RESPONSES[min(count, len(RESPONSES) - 1)]
    response_text = template.format(msg=msg)

    return jsonify({
        "response": response_text,
        "end_call": end_call,
    })


@mock_agent_bp.route("/mock-agent/clawcall/third-party-complete", methods=["POST"])
def mock_third_party_complete():
    return jsonify({"ok": True})
