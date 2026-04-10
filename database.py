"""
SQLite database — annoncer, søgeprofiler, notifikationer og prishistorik.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "deals.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                price       INTEGER NOT NULL,
                currency    TEXT    NOT NULL DEFAULT 'DKK',
                location    TEXT,
                url         TEXT    UNIQUE,
                source      TEXT    NOT NULL DEFAULT 'dba',
                query       TEXT,
                deal_score  REAL    DEFAULT 0,
                notified    INTEGER DEFAULT 0,
                created_at  TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS searches (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                query        TEXT    NOT NULL,
                sources      TEXT    NOT NULL DEFAULT 'dba+kleinanzeigen_ref',
                max_price    INTEGER,
                min_discount REAL    DEFAULT 25.0,
                last_run     TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                email        TEXT    NOT NULL,
                query        TEXT    NOT NULL,
                min_discount REAL    DEFAULT 25.0,
                max_price    INTEGER,
                active       INTEGER DEFAULT 1,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                query        TEXT    NOT NULL,
                dba_avg_dkk  REAL,
                dba_min_dkk  REAL,
                ka_avg_eur   REAL,
                ka_avg_dkk   REAL,
                eur_dkk_rate REAL,
                sample_size  INTEGER,
                recorded_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_listings_query   ON listings(query);
            CREATE INDEX IF NOT EXISTS idx_listings_price   ON listings(price);
            CREATE INDEX IF NOT EXISTS idx_history_query    ON price_history(query);
            CREATE INDEX IF NOT EXISTS idx_history_recorded ON price_history(recorded_at);
        """)


def upsert_listing(listing: dict) -> bool:
    with get_conn() as conn:
        if conn.execute("SELECT id FROM listings WHERE url = ?", (listing["url"],)).fetchone():
            return False
        conn.execute(
            """INSERT INTO listings
               (title, price, currency, location, url, source, query, deal_score, created_at)
               VALUES (:title, :price, :currency, :location, :url, :source, :query, :deal_score, :created_at)""",
            listing,
        )
        return True


def get_listings(query=None, source="dba", max_price=None, only_deals=False, limit=100):
    sql = "SELECT * FROM listings WHERE 1=1"
    params = []
    if query:
        sql += " AND query LIKE ?"; params.append(f"%{query}%")
    if source:
        sql += " AND source = ?"; params.append(source)
    if max_price:
        sql += " AND price <= ?"; params.append(max_price)
    if only_deals:
        sql += " AND deal_score > 0"
    sql += " ORDER BY deal_score DESC, price ASC LIMIT ?"; params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_unnotified_deals(query, min_discount, max_price):
    sql = "SELECT * FROM listings WHERE query LIKE ? AND deal_score >= ? AND notified = 0 AND source = 'dba'"
    params = [f"%{query}%", min_discount]
    if max_price:
        sql += " AND price <= ?"; params.append(max_price)
    sql += " ORDER BY deal_score DESC LIMIT 20"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def mark_notified(listing_ids):
    if not listing_ids:
        return
    with get_conn() as conn:
        conn.execute(f"UPDATE listings SET notified = 1 WHERE id IN ({','.join('?'*len(listing_ids))})", listing_ids)


def save_search(query, sources, max_price, min_discount):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO searches (query, sources, max_price, min_discount) VALUES (?,?,?,?)",
                           (query, sources, max_price, min_discount))
        return cur.lastrowid


def get_searches():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM searches ORDER BY id DESC").fetchall()]


def save_notification(email, query, min_discount, max_price):
    with get_conn() as conn:
        conn.execute("DELETE FROM notifications WHERE email = ? AND query = ?", (email, query))
        cur = conn.execute("INSERT INTO notifications (email, query, min_discount, max_price) VALUES (?,?,?,?)",
                           (email, query, min_discount, max_price))
        return cur.lastrowid


def get_active_notifications():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM notifications WHERE active = 1").fetchall()]


def record_price_history(query, dba_prices, ka_avg_eur, eur_dkk_rate):
    if not dba_prices:
        return
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO price_history
               (query, dba_avg_dkk, dba_min_dkk, ka_avg_eur, ka_avg_dkk, eur_dkk_rate, sample_size)
               VALUES (?,?,?,?,?,?,?)""",
            (query, round(sum(dba_prices)/len(dba_prices)), min(dba_prices),
             ka_avg_eur, round(ka_avg_eur * eur_dkk_rate) if ka_avg_eur else None,
             eur_dkk_rate, len(dba_prices)),
        )


def get_price_history(query, days=30):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM price_history WHERE query LIKE ? AND recorded_at >= datetime('now', ?) ORDER BY recorded_at ASC",
            (f"%{query}%", f"-{days} days")
        ).fetchall()]


init_db()
