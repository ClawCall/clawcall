"""
Crypto billing routes — Base chain (USDC / USDT).

POST /api/v1/billing/checkout     → get payment address, amount, token options
POST /api/v1/billing/verify       → submit tx hash, upgrade tier instantly
GET  /api/v1/billing/status       → plan, expiry, payment history
POST /api/v1/billing/cancel       → cancel auto-renew

Payment flow:
  1. Agent calls /checkout with { tier, token }
  2. Returns EVM wallet + exact amount to send
  3. User sends USDC or USDT on Base to that wallet
  4. Agent calls /verify with { tx_hash, tier, token }
  5. We verify on-chain → tier upgraded immediately
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, g
from src.db.client import db_exec
from src.middleware.auth import require_api_key
from src.services.base_chain import verify_base_payment
from src.services.minutes import set_tier, clear_overage
from src.config import (
    CLAWCALL_EVM_WALLET,
    BASE_USDC_ADDRESS, BASE_USDT_ADDRESS,
    TIER_PRICE_BASE, TIER_LIMITS,
    SUBSCRIPTION_DAYS, SUPPORTED_TOKENS,
    OVERAGE_RATE_RAW,
)

logger = logging.getLogger(__name__)
billing_bp = Blueprint("billing", __name__)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

@billing_bp.route("/api/v1/billing/checkout", methods=["POST"])
@require_api_key
def checkout():
    """
    Returns the Base wallet address and exact amount to send.
    Supports USDC and USDT on Base mainnet.

    Body: { "tier": "pro" | "team", "token": "usdc" | "usdt" }
    """
    if not CLAWCALL_EVM_WALLET:
        return jsonify({"ok": False, "error": "Payments not configured on this server"}), 503

    body  = request.get_json(silent=True) or {}
    tier  = (body.get("tier")  or "").strip().lower()
    token = (body.get("token") or "usdc").strip().lower()

    if tier not in ("pro", "team"):
        return jsonify({"ok": False, "error": "tier must be 'pro' or 'team'"}), 400
    if token not in SUPPORTED_TOKENS:
        return jsonify({"ok": False, "error": "token must be 'usdc' or 'usdt'"}), 400

    user = db_exec("SELECT overage_minutes FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
    overage_minutes = user["overage_minutes"] if user else 0
    overage_raw = overage_minutes * OVERAGE_RATE_RAW

    base_raw       = TIER_PRICE_BASE[tier]
    amount_raw     = base_raw + overage_raw
    amount_display = amount_raw / 1_000_000

    contract = SUPPORTED_TOKENS[token]

    return jsonify({
        "ok": True,
        "tier": tier,
        "payment": {
            "chain":          "base-mainnet",
            "chain_id":       8453,
            "wallet":         CLAWCALL_EVM_WALLET,
            "token":          token.upper(),
            "contract":       contract,
            "amount":         amount_display,
            "amount_raw":     amount_raw,
            "decimals":       6,
        },
        "instructions": (
            f"Send exactly {amount_display} {token.upper()} on Base mainnet "
            f"to {CLAWCALL_EVM_WALLET}. "
            f"Then call POST /api/v1/billing/verify with your transaction hash."
        ),
        "options": {
            "usdc": {
                "contract":  BASE_USDC_ADDRESS,
                "amount":    amount_display,
                "amount_raw": amount_raw,
            },
            "usdt": {
                "contract":  BASE_USDT_ADDRESS,
                "amount":    amount_display,
                "amount_raw": amount_raw,
            },
        },
        "overage": {
            "minutes": overage_minutes,
            "rate_per_min": 0.05,
            "amount": overage_raw / 1_000_000,
        },
    })


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

@billing_bp.route("/api/v1/billing/verify", methods=["POST"])
@require_api_key
def verify():
    """
    Submit a Base transaction hash. We verify on-chain and upgrade immediately.

    Body: { "tx_hash": "0x...", "tier": "pro"|"team", "token": "usdc"|"usdt" }
    """
    agent = g.agent
    body  = request.get_json(silent=True) or {}
    tx_hash = (body.get("tx_hash") or "").strip()
    tier    = (body.get("tier")    or "").strip().lower()
    token   = (body.get("token")   or "usdc").strip().lower()

    if not tx_hash:
        return jsonify({"ok": False, "error": "tx_hash is required"}), 400
    if tier not in ("pro", "team"):
        return jsonify({"ok": False, "error": "tier must be 'pro' or 'team'"}), 400
    if token not in SUPPORTED_TOKENS:
        return jsonify({"ok": False, "error": "token must be 'usdc' or 'usdt'"}), 400

    # Prevent double-spend
    existing = db_exec(
        "SELECT id, status FROM payments WHERE tx_signature=%s",
        (tx_hash,), fetchone=True,
    )
    if existing:
        if existing["status"] == "confirmed":
            return jsonify({"ok": False, "error": "Transaction already used"}), 409

    expected = TIER_PRICE_BASE[tier]

    # Verify on Base chain
    result = verify_base_payment(tx_hash, expected, token)

    payment_id = str(uuid.uuid4())

    if not result["ok"]:
        db_exec(
            """
            INSERT INTO payments (id, user_id, tier, tx_signature, amount_usdc, status)
            VALUES (%s, %s, %s, %s, 0, 'failed')
            ON CONFLICT (tx_signature) DO NOTHING
            """,
            (payment_id, agent["user_id"], tier, tx_hash),
        )
        return jsonify({"ok": False, "error": result["error"]}), 402

    # Confirmed — upgrade tier
    now        = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=SUBSCRIPTION_DAYS)

    db_exec(
        """
        INSERT INTO payments
          (id, user_id, tier, tx_signature, amount_usdc, status, confirmed_at, valid_until)
        VALUES (%s, %s, %s, %s, %s, 'confirmed', NOW(), %s)
        ON CONFLICT (tx_signature) DO UPDATE
          SET status='confirmed', confirmed_at=NOW(), valid_until=%s
        """,
        (payment_id, agent["user_id"], tier, tx_hash,
         result["amount"], valid_until, valid_until),
    )

    set_tier(agent["user_id"], tier)
    clear_overage(agent["user_id"])

    db_exec(
        "UPDATE users SET subscription_valid_until=%s WHERE id=%s",
        (valid_until, agent["user_id"]),
    )

    logger.info(
        f"User {agent['user_id']} → {tier} via {token.upper()} "
        f"tx {tx_hash} ({result['amount']/1_000_000:.2f} {token.upper()})"
    )

    return jsonify({
        "ok":           True,
        "tier":         tier,
        "token":        result["token"],
        "amount_paid":  result["amount"] / 1_000_000,
        "minutes_limit": TIER_LIMITS[tier],
        "valid_until":  valid_until.isoformat(),
        "message": (
            f"Upgraded to {tier.capitalize()}! "
            f"Valid until {valid_until.strftime('%Y-%m-%d')}."
        ),
    })


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@billing_bp.route("/api/v1/billing/status", methods=["GET"])
@require_api_key
def billing_status():
    agent = g.agent
    user  = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)

    overage_minutes = user.get("overage_minutes", 0)
    overage_owed = overage_minutes * OVERAGE_RATE_RAW / 1_000_000

    payments = db_exec(
        """
        SELECT tier, amount_usdc, status, confirmed_at, valid_until, tx_signature
        FROM payments
        WHERE user_id=%s AND status='confirmed'
        ORDER BY confirmed_at DESC LIMIT 10
        """,
        (agent["user_id"],), fetchall=True,
    ) or []

    valid_until   = user.get("subscription_valid_until")
    days_remaining = None
    if valid_until:
        now = datetime.now(timezone.utc)
        if hasattr(valid_until, "tzinfo") and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        days_remaining = max(0, (valid_until - now).days)

    formatted_payments = []
    for p in payments:
        formatted_payments.append({
            "tier":         p["tier"],
            "amount":       p["amount_usdc"] / 1_000_000 if p["amount_usdc"] else 0,
            "status":       p["status"],
            "confirmed_at": p["confirmed_at"].isoformat() if p["confirmed_at"] else None,
            "valid_until":  p["valid_until"].isoformat()  if p["valid_until"]  else None,
            "tx":           p["tx_signature"],
        })

    return jsonify({
        "ok":                      True,
        "tier":                    user["tier"],
        "minutes_used":            user["minutes_used_this_month"],
        "minutes_limit":           user["minutes_limit"],
        "minutes_remaining":       max(0, user["minutes_limit"] - user["minutes_used_this_month"]),
        "subscription_valid_until": valid_until.isoformat() if valid_until else None,
        "days_remaining":          days_remaining,
        "accepted_tokens":         ["USDC", "USDT"],
        "chain":                   "base-mainnet",
        "payments":                formatted_payments,
        "overage_minutes":         overage_minutes,
        "overage_owed_usdc":       round(overage_owed, 4),
    })


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

@billing_bp.route("/api/v1/billing/cancel", methods=["POST"])
@require_api_key
def cancel():
    agent = g.agent
    db_exec(
        "UPDATE users SET subscription_auto_renew=FALSE WHERE id=%s",
        (agent["user_id"],),
    )
    user   = db_exec(
        "SELECT subscription_valid_until FROM users WHERE id=%s",
        (agent["user_id"],), fetchone=True,
    )
    expiry = user.get("subscription_valid_until")
    return jsonify({
        "ok":      True,
        "message": f"Auto-renew cancelled. Plan stays active until {expiry}.",
    })
