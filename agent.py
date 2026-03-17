"""
ClawCall agent — powered by OpenAI GPT-4o-mini.
Uses requests library directly (avoids httpx Windows hang issue).
Exposes the two webhook endpoints ClawCall expects:
  POST /clawcall/message
  POST /clawcall/third-party-complete
"""
import os
import requests as req
from flask import Flask, request, jsonify

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

call_histories: dict[str, list] = {}

SYSTEM_PROMPT = (
    "You are a helpful, friendly phone assistant. "
    "Keep all replies short and conversational — your words will be spoken aloud via text-to-speech. "
    "Avoid bullet points, markdown, or long paragraphs. "
    "Speak naturally, as if you're on a phone call."
)


def ask_openai(messages: list) -> str:
    r = req.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": "gpt-4o-mini", "max_tokens": 200, "messages": messages},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


@app.route("/clawcall/message", methods=["POST"])
def message():
    body = request.get_json(silent=True) or {}
    call_sid = body.get("call_sid", "unknown")
    user_msg = (body.get("message") or "").strip()

    history = call_histories.setdefault(call_sid, [
        {"role": "system", "content": SYSTEM_PROMPT}
    ])
    history.append({"role": "user", "content": user_msg})

    print(f"[agent] {call_sid} in: {user_msg[:60]!r}", flush=True)
    reply = ask_openai(history)
    history.append({"role": "assistant", "content": reply})
    print(f"[agent] {call_sid} out: {reply[:60]!r}", flush=True)

    end_call = any(w in user_msg.lower() for w in ["goodbye", "bye", "hang up", "end call"])
    if end_call:
        del call_histories[call_sid]

    return jsonify({"response": reply, "end_call": end_call})


@app.route("/clawcall/third-party-complete", methods=["POST"])
def third_party_complete():
    body = request.get_json(silent=True) or {}
    print(f"[third-party-complete] job={body.get('job_id')} status={body.get('status')}", flush=True)
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set")
        exit(1)
    print("ClawCall agent running on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
