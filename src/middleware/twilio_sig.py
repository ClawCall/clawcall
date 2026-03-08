from functools import wraps
from flask import request, abort
from twilio.request_validator import RequestValidator
from src.config import TWILIO_AUTH_TOKEN


def validate_twilio_signature(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not TWILIO_AUTH_TOKEN:
            return f(*args, **kwargs)

        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")

        # Use the full URL including query string
        url = request.url
        params = request.form.to_dict()

        if not validator.validate(url, params, signature):
            abort(403)

        return f(*args, **kwargs)

    return decorated
