import threading
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import DATABASE_URL

_DB_CONN = None
_DB_LOCK = threading.RLock()  # Reentrant — db_exec holds lock then calls db_conn


def db_conn():
    global _DB_CONN
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    with _DB_LOCK:
        if _DB_CONN is None or _DB_CONN.closed != 0:
            _DB_CONN = psycopg2.connect(DATABASE_URL)
        return _DB_CONN


def db_exec(query, params=None, fetchone=False, fetchall=False):
    global _DB_CONN
    with _DB_LOCK:
        try:
            conn = db_conn()
            conn.autocommit = True
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params or ())
                if fetchone:
                    return cur.fetchone()
                if fetchall:
                    return cur.fetchall()
                return None
        except psycopg2.OperationalError:
            # Connection is broken — reset so db_conn() reconnects on next call
            _DB_CONN = None
            raise
