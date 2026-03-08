"""
Solana billing routes — USDC on Solana mainnet.

POST /api/v1/billing/checkout     → get Solana wallet address + exact USDC amount
POST /api/v1/billing/verify       → submit tx signature, upgrade tier instantly
GET  /api/v1/billing/status       → plan, expiry, payment history
POST /api/v1/billing/cancel       → cancel auto-renew

Payment flow:
  1. Agent calls /checkout with { tier }
  2. Returns Solana wallet + exact USDC amount to send
  3. User sends USDC on Solana mainnet to that wallet
  4. Agent calls /verify with { tx_signature, tier }
  5. We verify on-chain → tier upgraded immediately
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, g
from src.db.client import db_exec
from src.middleware.auth import require_api_key
from src.services.solana_chain import verify_solana_payment
from src.services.minutes import set_tier, clear_overage
from src.config import (
    CLAWCALL_WALLET,
    USDC_MINT,
    TIER_PRICE_BASE, TIER_LIMITS,
    SUBSCRIPTION_DAYS,
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
    Returns the Solana wallet address and exact USDC amount to send.

    Body: { "tier": "pro" | "team" }
    """
    if not CLAWCALL_WALLET:
        return jsonify({"ok": False, "error": "Payments not configured on this server"}), 503

    body = request.get_json(silent=True) or {}
    tier = (body.get("tier") or "").strip().lower()

    if tier not in ("pro", "team"):
        return jsonify({"ok": False, "error": "tier must be 'pro' or 'team'"}), 400

    user = db_exec("SELECT overage_minutes FROM users WHERE id=%s", (g.agent["user_id"],), fetchone=True)
    overage_minutes = user["overage_minutes"] if user else 0
    overage_raw     = overage_minutes * OVERAGE_RATE_RAW

    base_raw       = TIER_PRICE_BASE[tier]
    amount_raw     = base_raw + overage_raw
    amount_display = amount_raw / 1_000_000

    return jsonify({
        "ok":   True,
        "tier": tier,
        "payment": {
            "chain":      "solana-mainnet",
            "wallet":     CLAWCALL_WALLET,
            "token":      "USDC",
            "mint":       USDC_MINT,
            "amount":     amount_display,
            "amount_raw": amount_raw,
            "decimals":   6,
        },
        "instructions": (
            f"Send exactly {amount_display} USDC on Solana mainnet "
            f"to {CLAWCALL_WALLET}. "
            f"Then call POST /api/v1/billing/verify with your transaction signature."
        ),
        "overage": {
            "minutes":      overage_minutes,
            "rate_per_min": 0.05,
            "amount":       overage_raw / 1_000_000,
        },
    })


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

@billing_bp.route("/api/v1/billing/verify", methods=["POST"])
@require_api_key
def verify():
    """
    Submit a Solana transaction signature. We verify on-chain and upgrade immediately.

    Body: { "tx_signature": "...", "tier": "pro"|"team" }
    """
    agent = g.agent
    body  = request.get_json(silent=True) or {}
    tx_sig = (body.get("tx_signature") or "").strip()
    tier   = (body.get("tier")         or "").strip().lower()

    if not tx_sig:
        return jsonify({"ok": False, "error": "tx_signature is required"}), 400
    if tier not in ("pro", "team"):
        return jsonify({"ok": False, "error": "tier must be 'pro' or 'team'"}), 400

    # Prevent double-spend
    existing = db_exec(
        "SELECT id, status FROM payments WHERE tx_signature=%s",
        (tx_sig,), fetchone=True,
    )
    if existing and existing["status"] == "confirmed":
        return jsonify({"ok": False, "error": "Transaction already used"}), 409

    expected = TIER_PRICE_BASE[tier]

    # Verify on Solana
    result = verify_solana_payment(tx_sig, expected)

    payment_id = str(uuid.uuid4())

    if not result["ok"]:
        db_exec(
            """
            INSERT INTO payments (id, user_id, tier, tx_signature, amount_usdc, status)
            VALUES (%s, %s, %s, %s, 0, 'failed')
            ON CONFLICT (tx_signature) DO NOTHING
            """,
            (payment_id, agent["user_id"], tier, tx_sig),
        )
        return jsonify({"ok": False, "error": result["error"]}), 402

    # Confirmed — upgrade tier
    now         = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=SUBSCRIPTION_DAYS)

    db_exec(
        """
        INSERT INTO payments
          (id, user_id, tier, tx_signature, amount_usdc, status, confirmed_at, valid_until)
        VALUES (%s, %s, %s, %s, %s, 'confirmed', NOW(), %s)
        ON CONFLICT (tx_signature) DO UPDATE
          SET status='confirmed', confirmed_at=NOW(), valid_until=%s
        """,
        (payment_id, agent["user_id"], tier, tx_sig,
         result["amount"], valid_until, valid_until),
    )

    set_tier(agent["user_id"], tier)
    clear_overage(agent["user_id"])

    db_exec(
        "UPDATE users SET subscription_valid_until=%s WHERE id=%s",
        (valid_until, agent["user_id"]),
    )

    logger.info(
        f"User {agent['user_id']} → {tier} via Solana USDC "
        f"tx {tx_sig} ({result['amount'] / 1_000_000:.2f} USDC)"
    )

    return jsonify({
        "ok":            True,
        "tier":          tier,
        "token":         "USDC",
        "chain":         "solana-mainnet",
        "amount_paid":   result["amount"] / 1_000_000,
        "minutes_limit": TIER_LIMITS[tier],
        "valid_until":   valid_until.isoformat(),
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
    overage_owed    = overage_minutes * OVERAGE_RATE_RAW / 1_000_000

    payments = db_exec(
        """
        SELECT tier, amount_usdc, status, confirmed_at, valid_until, tx_signature
        FROM payments
        WHERE user_id=%s AND status='confirmed'
        ORDER BY confirmed_at DESC LIMIT 10
        """,
        (agent["user_id"],), fetchall=True,
    ) or []

    valid_until    = user.get("subscription_valid_until")
    days_remaining = None
    if valid_until:
        now = datetime.now(timezone.utc)
        if hasattr(valid_until, "tzinfo") and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        days_remaining = max(0, (valid_until - now).days)

    formatted_payments = [
        {
            "tier":         p["tier"],
            "amount":       p["amount_usdc"] / 1_000_000 if p["amount_usdc"] else 0,
            "status":       p["status"],
            "confirmed_at": p["confirmed_at"].isoformat() if p["confirmed_at"] else None,
            "valid_until":  p["valid_until"].isoformat()  if p["valid_until"]  else None,
            "tx":           p["tx_signature"],
        }
        for p in payments
    ]

    return jsonify({
        "ok":                       True,
        "tier":                     user["tier"],
        "minutes_used":             user["minutes_used_this_month"],
        "minutes_limit":            user["minutes_limit"],
        "minutes_remaining":        max(0, user["minutes_limit"] - user["minutes_used_this_month"]),
        "subscription_valid_until": valid_until.isoformat() if valid_until else None,
        "days_remaining":           days_remaining,
        "accepted_tokens":          ["USDC"],
        "chain":                    "solana-mainnet",
        "payments":                 formatted_payments,
        "overage_minutes":          overage_minutes,
        "overage_owed_usdc":        round(overage_owed, 4),
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
