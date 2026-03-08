import logging
from flask import Flask, jsonify
from src.config import PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


def create_app() -> Flask:
    app = Flask(__name__)

    # ----------------------------------------------------------------
    # Blueprints
    # ----------------------------------------------------------------
    from src.routes.register import register_bp
    from src.routes.account import account_bp
    from src.routes.agents import agents_bp
    from src.routes.calls import calls_bp
    from src.routes.webhooks import webhooks_bp
    from src.routes.billing import billing_bp
    from src.routes.mock_agent import mock_agent_bp

    app.register_blueprint(register_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(calls_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(mock_agent_bp)

    # ----------------------------------------------------------------
    # Health check
    # ----------------------------------------------------------------
    @app.route("/health")
    def health():
        return jsonify({"ok": True, "service": "clawcall"})

    # ----------------------------------------------------------------
    # DB schema
    # ----------------------------------------------------------------
    try:
        from src.db.schema import init_db
        init_db()
        logging.info("Database schema ready")
    except Exception as e:
        logging.warning(f"DB init skipped (no DATABASE_URL?): {e}")

    # ----------------------------------------------------------------
    # Scheduler
    # ----------------------------------------------------------------
    try:
        from src.services.scheduler import start as start_scheduler
        start_scheduler()
    except Exception as e:
        logging.warning(f"Scheduler start failed: {e}")

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=PORT, debug=False)
