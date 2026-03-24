"""
Agent ↔ ClawCall message bridge — long-poll edition.

Agents pull messages instead of receiving webhook pushes:
  1. queue_message()       — Twilio webhook stores message + wakes listener
  2. get_pending_message() — Agent's GET /api/v1/calls/listen blocks here
  3. submit_response()     — Agent's POST /api/v1/calls/respond signals waiter
  4. poll_result()         — Twilio webhook unblocks and returns TwiML

No public URL required on the agent side.
"""
import json
import random
import threading
import time
import logging
from src.config import FILLER_PHRASES, AGENT_RESPONSE_TIMEOUT

logger = logging.getLogger(__name__)

# ── Per-agent incoming message queue ────────────────────────────────────────
# {agent_id: [{"call_sid": ..., "message": ...}, ...]}
_AGENT_QUEUES: dict = {}
_AGENT_CONDITIONS: dict = {}  # threading.Condition per agent
_QUEUE_LOCK = threading.Lock()

# ── Per-call response slots ──────────────────────────────────────────────────
# {call_sid: {"status": "pending|done|error", "result": str, "end_call": bool, "event": Event, "ts": float}}
_PENDING: dict = {}
_PENDING_LOCK = threading.Lock()
_JOB_TTL = 300  # seconds

# ── Transcripts ──────────────────────────────────────────────────────────────
_TRANSCRIPTS: dict = {}
_TRANSCRIPT_LOCK = threading.Lock()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _agent_condition(agent_id: str) -> threading.Condition:
    with _QUEUE_LOCK:
        if agent_id not in _AGENT_CONDITIONS:
            _AGENT_CONDITIONS[agent_id] = threading.Condition(threading.Lock())
            _AGENT_QUEUES[agent_id] = []
        return _AGENT_CONDITIONS[agent_id]


def _cleanup():
    now = time.time()
    with _PENDING_LOCK:
        for sid in list(_PENDING.keys()):
            if now - _PENDING[sid].get("ts", now) > _JOB_TTL:
                del _PENDING[sid]


# ── Producer side (Twilio webhooks call these) ───────────────────────────────

def queue_message(agent_id: str, call_sid: str, message: str) -> None:
    """
    Queue a message for the agent and wake any waiting listen connection.
    Also registers a response slot so poll_result() can wait for the reply.
    """
    _cleanup()

    # Register response slot first so poll_result() can find it immediately
    event = threading.Event()
    with _PENDING_LOCK:
        _PENDING[call_sid] = {
            "status": "pending",
            "result": None,
            "end_call": False,
            "event": event,
            "ts": time.time(),
        }

    # Wake the agent's listen connection
    cond = _agent_condition(agent_id)
    with cond:
        _AGENT_QUEUES[agent_id].append({"call_sid": call_sid, "message": message})
        cond.notify()  # wake one waiting listener


def poll_result(call_sid: str, timeout: float = AGENT_RESPONSE_TIMEOUT) -> tuple[str, bool, bool]:
    """
    Block up to `timeout` seconds for the agent's response.
    Returns (text, end_call, got_result).
    Returns a filler phrase with got_result=False on timeout.
    """
    with _PENDING_LOCK:
        job = _PENDING.get(call_sid)
    if not job:
        return random.choice(FILLER_PHRASES), False, False

    got = job["event"].wait(timeout=timeout)
    if got:
        with _PENDING_LOCK:
            job = _PENDING.get(call_sid, {})
        return job.get("result") or "", job.get("end_call", False), True

    return random.choice(FILLER_PHRASES), False, False


def get_result(call_sid: str, wait: float = 5.0) -> tuple[str | None, bool, bool]:
    """
    Check for a result, waiting up to `wait` seconds before giving up.
    Used by /webhooks/twilio/poll — blocks briefly so the poll loop
    doesn't spam filler phrases on every Twilio redirect.
    """
    with _PENDING_LOCK:
        job = _PENDING.get(call_sid)
    if not job:
        return None, False, False
    if job["status"] == "done":
        return job["result"], job.get("end_call", False), True
    # Wait up to `wait` seconds for the agent to submit a response
    got = job["event"].wait(timeout=wait)
    if got:
        with _PENDING_LOCK:
            job = _PENDING.get(call_sid, {})
        return job.get("result") or "", job.get("end_call", False), True
    return None, False, False


def clear(call_sid: str):
    with _PENDING_LOCK:
        _PENDING.pop(call_sid, None)


# ── Consumer side (agent endpoints call these) ───────────────────────────────

def get_pending_message(agent_id: str, timeout: float = 25.0) -> dict | None:
    """
    Long-poll: block until a message is available or timeout expires.
    Returns {"call_sid": ..., "message": ...} or None on timeout.
    Called by GET /api/v1/calls/listen.
    """
    cond = _agent_condition(agent_id)
    deadline = time.time() + timeout

    with cond:
        while True:
            queue = _AGENT_QUEUES.get(agent_id, [])
            if queue:
                return queue.pop(0)
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            cond.wait(timeout=remaining)


def submit_response(call_sid: str, response: str, end_call: bool) -> bool:
    """
    Agent submits its response. Signals the waiting poll_result().
    Returns False if call_sid is unknown or already expired.
    Called by POST /api/v1/calls/respond/<call_sid>.
    """
    with _PENDING_LOCK:
        job = _PENDING.get(call_sid)
        if not job:
            return False
        job.update({
            "status": "done",
            "result": response.strip(),
            "end_call": bool(end_call),
            "ts": time.time(),
        })
        event = job["event"]  # capture ref while holding lock
    event.set()
    logger.info(f"Agent submitted response for {call_sid}, end_call={end_call}")
    return True


# ── Transcripts ──────────────────────────────────────────────────────────────

def append_transcript(call_sid: str, role: str, text: str):
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
