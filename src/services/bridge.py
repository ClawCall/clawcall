"""
Bridge — forwards speech text to the OpenClaw agent webhook and
stores the response so Twilio poll endpoints can retrieve it.

Flow:
  1. ask_agent()  → fires HTTP POST to agent in background thread
  2. poll_result() → waits up to N seconds for the response
  3. get_result()  → non-blocking check (for Twilio redirect polling)
"""
import random
import threading
import time
import logging
import requests
from src.config import FILLER_PHRASES, AGENT_RESPONSE_TIMEOUT

logger = logging.getLogger(__name__)

# {call_sid: {status, result, end_call, ts}}
_PENDING: dict = {}
_LOCK = threading.Lock()
_JOB_TTL = 300  # seconds

# {call_sid: [ {role, text}, ... ]}
_TRANSCRIPTS: dict = {}
_TRANSCRIPT_LOCK = threading.Lock()


def append_transcript(call_sid: str, role: str, text: str):
    """Append a turn to the in-memory transcript for this call."""
    with _TRANSCRIPT_LOCK:
        if call_sid not in _TRANSCRIPTS:
            _TRANSCRIPTS[call_sid] = []
        _TRANSCRIPTS[call_sid].append({"role": role, "text": text})


def get_transcript(call_sid: str) -> list:
    with _TRANSCRIPT_LOCK:
        return list(_TRANSCRIPTS.get(call_sid, []))


def clear_transcript(call_sid: str):
    with _TRANSCRIPT_LOCK:
        _TRANSCRIPTS.pop(call_sid, None)


def _cleanup():
    now = time.time()
    with _LOCK:
        for sid in list(_PENDING.keys()):
            if now - _PENDING[sid].get("ts", now) > _JOB_TTL:
                del _PENDING[sid]


def ask_agent(agent_webhook_url: str, call_sid: str, message: str):
    """
    POST the caller's speech to the agent webhook in a background thread.
    The agent is expected to respond with:
      { "response": "...", "end_call": false }
    """
    _cleanup()
    with _LOCK:
        _PENDING[call_sid] = {
            "status": "running",
            "result": None,
            "end_call": False,
            "ts": time.time(),
        }

    def worker():
        try:
            url = f"{agent_webhook_url.rstrip('/')}/clawcall/message"
            r = requests.post(
                url,
                json={"call_sid": call_sid, "message": message},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
            with _LOCK:
                _PENDING[call_sid].update({
                    "status": "done",
                    "result": (data.get("response") or "").strip(),
                    "end_call": bool(data.get("end_call", False)),
                    "ts": time.time(),
                })
        except Exception as e:
            logger.exception(f"Agent bridge error for {call_sid}: {e}")
            with _LOCK:
                _PENDING[call_sid].update({
                    "status": "error",
                    "result": "I had a little trouble with that. Could you say it again?",
                    "end_call": False,
                    "ts": time.time(),
                })

    threading.Thread(target=worker, daemon=True).start()


def poll_result(call_sid: str, timeout: float = AGENT_RESPONSE_TIMEOUT) -> tuple[str, bool, bool]:
    """
    Block up to `timeout` seconds waiting for the agent response.
    Returns (text, end_call, got_result).
    If timed out, returns a filler phrase with got_result=False.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _LOCK:
            job = _PENDING.get(call_sid)
        if job and job["status"] in ("done", "error"):
            return job["result"], job.get("end_call", False), True
        time.sleep(0.1)

    return random.choice(FILLER_PHRASES), False, False


def get_result(call_sid: str) -> tuple[str | None, bool, bool]:
    """
    Non-blocking result check.
    Returns (text, end_call, got_result).
    """
    with _LOCK:
        job = _PENDING.get(call_sid)
    if not job:
        return None, False, False
    if job["status"] in ("done", "error"):
        return job["result"], job.get("end_call", False), True
    return None, False, False


def clear(call_sid: str):
    with _LOCK:
        _PENDING.pop(call_sid, None)
