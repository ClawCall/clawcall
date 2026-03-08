import hashlib
from functools import wraps
from flask import request, jsonify, g
from src.db.client import db_exec


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"ok": False, "error": "Missing Authorization header"}), 401

        token = auth[7:].strip()
        key_hash = hashlib.sha256(token.encode()).hexdigest()

        agent = db_exec(
            "SELECT * FROM agents WHERE api_key_hash=%s",
            (key_hash,),
            fetchone=True,
        )
        if not agent:
            return jsonify({"ok": False, "error": "Invalid API key"}), 401

        g.agent = agent
        return f(*args, **kwargs)

    return decorated
