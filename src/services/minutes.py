import logging
from src.db.client import db_exec
from src.config import TIER_LIMITS

OVERAGE_CAP = 5  # max overage minutes allowed before blocking (Pro/Team)

logger = logging.getLogger(__name__)


def get_usage(user_id: str) -> dict | None:
    return db_exec(
        "SELECT minutes_used_this_month, minutes_limit, tier, overage_minutes FROM users WHERE id=%s",
        (user_id,),
        fetchone=True,
    )


def within_limit(user_id: str) -> bool:
    """
    Free tier: hard block at included minutes.
    Pro/Team: allow up to OVERAGE_CAP (5) minutes past the included limit, then block.
    """
    row = get_usage(user_id)
    if not row:
        return False
    if row["tier"] == "free":
        return row["minutes_used_this_month"] < row["minutes_limit"]
    return (row["overage_minutes"] or 0) < OVERAGE_CAP


def add_seconds(user_id: str, seconds: int):
    """Increment monthly usage and track overage for paid tiers."""
    minutes = max(1, -(-seconds // 60))  # ceiling division
    row = get_usage(user_id)
    if not row:
        return

    db_exec(
        "UPDATE users SET minutes_used_this_month = minutes_used_this_month + %s WHERE id=%s",
        (minutes, user_id),
    )
    logger.info(f"Charged {minutes} min to user {user_id} ({seconds}s call)")

    if row["tier"] in ("pro", "team"):
        used = row["minutes_used_this_month"]
        limit = row["minutes_limit"]
        prev_over = max(0, used - limit)
        new_over = max(0, used + minutes - limit)
        incremental = new_over - prev_over
        if incremental > 0:
            db_exec(
                "UPDATE users SET overage_minutes = overage_minutes + %s WHERE id=%s",
                (incremental, user_id),
            )
            logger.info(f"Overage: +{incremental} min for user {user_id}")


def set_tier(user_id: str, tier: str):
    """Upgrade/downgrade a user's tier and update their minute limit."""
    if tier not in TIER_LIMITS:
        raise ValueError(f"Unknown tier: {tier}")
    limit = TIER_LIMITS[tier]
    db_exec(
        "UPDATE users SET tier=%s, minutes_limit=%s WHERE id=%s",
        (tier, limit, user_id),
    )


def reset_all_monthly():
    """Reset every user's monthly minute counter. Run on 1st of month."""
    db_exec("UPDATE users SET minutes_used_this_month = 0")
    logger.info("Monthly minute usage reset for all users")


def clear_overage(user_id: str):
    """Zero out overage minutes after a successful renewal payment."""
    db_exec("UPDATE users SET overage_minutes = 0 WHERE id=%s", (user_id,))
