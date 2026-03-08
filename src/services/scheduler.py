import logging
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="UTC")


def start():
    # Monthly reset of minute counters (1st of each month, midnight UTC)
    _scheduler.add_job(
        _monthly_reset,
        CronTrigger(day=1, hour=0, minute=0),
        id="monthly_minute_reset",
        replace_existing=True,
    )

    # Daily subscription expiry check (2am UTC)
    _scheduler.add_job(
        _check_expired_subscriptions,
        CronTrigger(hour=2, minute=0),
        id="subscription_expiry_check",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started")

    # Re-load any existing active scheduled calls from DB
    _reload_scheduled_calls()


def stop():
    _scheduler.shutdown(wait=False)


def add_scheduled_call(schedule_id: str, cron_expr: str, timezone: str, agent_id: str, task_context: str):
    """Register a cron job that fires an outbound call to the user."""
    try:
        tz = pytz.timezone(timezone)
    except Exception:
        tz = pytz.UTC

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {cron_expr!r}")

    minute, hour, day, month, day_of_week = parts
    trigger = CronTrigger(
        minute=minute, hour=hour, day=day,
        month=month, day_of_week=day_of_week,
        timezone=tz,
    )

    def fire():
        _fire_scheduled_call(schedule_id, agent_id, task_context)

    _scheduler.add_job(fire, trigger, id=schedule_id, replace_existing=True)
    logger.info(f"Scheduled call {schedule_id} registered: {cron_expr} ({timezone})")


def remove_scheduled_call(schedule_id: str):
    try:
        _scheduler.remove_job(schedule_id)
        logger.info(f"Removed scheduled call {schedule_id}")
    except Exception:
        pass


def _fire_scheduled_call(schedule_id: str, agent_id: str, task_context: str):
    """Called by APScheduler at the scheduled time."""
    from src.db.client import db_exec
    from src.services.twilio_svc import place_outbound_call
    from src.config import TWILIO_WEBHOOK_BASE_URL
    import urllib.parse

    try:
        agent = db_exec("SELECT * FROM agents WHERE id=%s", (agent_id,), fetchone=True)
        if not agent:
            logger.warning(f"Scheduled call {schedule_id}: agent {agent_id} not found")
            return

        user = db_exec("SELECT * FROM users WHERE id=%s", (agent["user_id"],), fetchone=True)
        if not user or not user.get("phone_number"):
            logger.warning(f"Scheduled call {schedule_id}: no phone for user {agent['user_id']}")
            return

        from src.services.minutes import within_limit
        if not within_limit(str(user["id"])):
            logger.warning(f"Scheduled call {schedule_id}: user {user['id']} over minute limit")
            return

        ctx_enc = urllib.parse.quote(task_context or "")
        twiml_url = f"{TWILIO_WEBHOOK_BASE_URL}/webhooks/twilio/scheduled?agent_id={agent_id}&context={ctx_enc}"
        call_sid = place_outbound_call(user["phone_number"], twiml_url)

        db_exec(
            "UPDATE scheduled_calls SET last_run_at=NOW() WHERE id=%s",
            (schedule_id,),
        )
        logger.info(f"Scheduled call {schedule_id} placed, SID: {call_sid}")

    except Exception as e:
        logger.exception(f"Scheduled call {schedule_id} failed: {e}")


def _reload_scheduled_calls():
    """Re-register active scheduled calls from DB on server start."""
    from src.db.client import db_exec
    try:
        rows = db_exec(
            "SELECT * FROM scheduled_calls WHERE is_active=TRUE",
            fetchall=True,
        ) or []
        for row in rows:
            try:
                add_scheduled_call(
                    str(row["id"]),
                    row["cron_expression"],
                    row["timezone"] or "UTC",
                    str(row["agent_id"]),
                    row["task_context"] or "",
                )
            except Exception as e:
                logger.warning(f"Could not reload schedule {row['id']}: {e}")
        logger.info(f"Reloaded {len(rows)} scheduled calls")
    except Exception as e:
        logger.warning(f"Could not reload scheduled calls: {e}")


def _monthly_reset():
    from src.services.minutes import reset_all_monthly
    reset_all_monthly()


def _check_expired_subscriptions():
    """Downgrade users whose subscription has expired back to free tier."""
    from src.db.client import db_exec
    from src.config import TIER_LIMITS
    expired = db_exec(
        """
        SELECT id FROM users
        WHERE tier IN ('pro','team')
        AND subscription_valid_until IS NOT NULL
        AND subscription_valid_until < NOW()
        """,
        fetchall=True,
    ) or []
    for row in expired:
        db_exec(
            "UPDATE users SET tier='free', minutes_limit=%s WHERE id=%s",
            (TIER_LIMITS["free"], str(row["id"])),
        )
        logger.info(f"Downgraded user {row['id']} to free (subscription expired)")
    if expired:
        logger.info(f"Expiry check: downgraded {len(expired)} users")
