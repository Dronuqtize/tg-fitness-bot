from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    psycopg = None
    dict_row = None


class DBConn:
    def __init__(self, conn, db_type: str):
        self.conn = conn
        self.db_type = db_type

    def _convert(self, query: str) -> str:
        if self.db_type == "postgres":
            return query.replace("?", "%s")
        return query

    def execute(self, query: str, params: Any | None = None):
        q = self._convert(query)
        if params is None:
            return self.conn.execute(q)
        return self.conn.execute(q, params)

    def executescript(self, script: str) -> None:
        if self.db_type == "sqlite":
            self.conn.executescript(script)
            return
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.conn.execute(stmt)

    def commit(self) -> None:
        self.conn.commit()


def get_conn(db_path_or_url: str | Path) -> DBConn:
    if isinstance(db_path_or_url, Path):
        db_path_or_url = str(db_path_or_url)

    if str(db_path_or_url).startswith("postgres://") or str(db_path_or_url).startswith("postgresql://"):
        if psycopg is None:
            raise RuntimeError("psycopg is not installed")
        conn = psycopg.connect(db_path_or_url, row_factory=dict_row)
        return DBConn(conn, "postgres")

    conn = sqlite3.connect(db_path_or_url)
    conn.row_factory = sqlite3.Row
    return DBConn(conn, "sqlite")


def init_db(conn: DBConn) -> None:
    if conn.db_type == "postgres":
        ddl = """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            tg_id BIGINT UNIQUE NOT NULL,
            name TEXT,
            tz TEXT,
            chat_id BIGINT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            start_date TEXT,
            cycle_index INTEGER DEFAULT 0,
            ai_enabled INTEGER DEFAULT 0,
            reminders_json TEXT DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS calendar_days (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            day_type TEXT NOT NULL,
            status TEXT NOT NULL,
            workout_key TEXT,
            level TEXT,
            kcal INTEGER,
            protein INTEGER,
            fat INTEGER,
            carbs INTEGER,
            note TEXT,
            ai_advice TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS progress_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            weight DOUBLE PRECISION,
            waist DOUBLE PRECISION,
            belly DOUBLE PRECISION,
            biceps DOUBLE PRECISION,
            chest DOUBLE PRECISION,
            note TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS med_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            amount_mg DOUBLE PRECISION,
            amount_ml DOUBLE PRECISION,
            note TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS workout_adjustments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            workout_key TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            delta_text TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, workout_key, exercise_name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS progression_rules (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            workout_key TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            delta_text TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 7,
            last_applied TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, workout_key, exercise_name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    else:
        ddl = """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            name TEXT,
            tz TEXT,
            chat_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            start_date TEXT,
            cycle_index INTEGER DEFAULT 0,
            ai_enabled INTEGER DEFAULT 1,
            reminders_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS calendar_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            day_type TEXT NOT NULL,
            status TEXT NOT NULL,
            workout_key TEXT,
            level TEXT,
            kcal INTEGER,
            protein INTEGER,
            fat INTEGER,
            carbs INTEGER,
            note TEXT,
            ai_advice TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS progress_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            weight REAL,
            waist REAL,
            belly REAL,
            biceps REAL,
            chest REAL,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS med_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            amount_mg REAL,
            amount_ml REAL,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS workout_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workout_key TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            delta_text TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, workout_key, exercise_name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS progression_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workout_key TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            delta_text TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 7,
            last_applied TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, workout_key, exercise_name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """

    conn.executescript(ddl)
    chat_col_type = "BIGINT" if conn.db_type == "postgres" else "INTEGER"
    _ensure_column(conn, "users", "chat_id", chat_col_type)
    conn.commit()


def _ensure_column(conn: DBConn, table: str, column: str, col_type: str) -> None:
    if conn.db_type == "postgres":
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?",
            (table,),
        )
        columns = {row["column_name"] for row in cur.fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        return

    cur = conn.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cur.fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def get_or_create_user(conn: DBConn, tg_id: int, name: str | None, tz: str, chat_id: int | None = None) -> int:
    cur = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    if row:
        conn.execute("UPDATE users SET name=?, tz=?, chat_id=? WHERE id=?", (name, tz, chat_id, row["id"]))
        conn.commit()
        return int(row["id"])

    if conn.db_type == "postgres":
        cur = conn.execute(
            "INSERT INTO users (tg_id, name, tz, chat_id) VALUES (?, ?, ?, ?) RETURNING id",
            (tg_id, name, tz, chat_id),
        )
        user_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            "INSERT INTO users (tg_id, name, tz, chat_id) VALUES (?, ?, ?, ?)",
            (tg_id, name, tz, chat_id),
        )
        user_id = cur.lastrowid

    conn.execute("INSERT INTO settings (user_id) VALUES (?)", (user_id,))
    conn.commit()
    return int(user_id)


def get_settings(conn: DBConn, user_id: int) -> dict[str, Any]:
    cur = conn.execute("SELECT * FROM settings WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Settings not found")
    settings = dict(row)
    settings["reminders"] = json.loads(settings.get("reminders_json") or "{}")
    return settings


def update_settings(conn: DBConn, user_id: int, **updates: Any) -> None:
    if not updates:
        return
    fields = []
    values = []
    for key, value in updates.items():
        if key == "reminders":
            key = "reminders_json"
            value = json.dumps(value, ensure_ascii=False)
        fields.append(f"{key}=?")
        values.append(value)
    values.append(user_id)
    conn.execute(
        f"UPDATE settings SET {', '.join(fields)}, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
        values,
    )
    conn.commit()


def upsert_adjustment(
    conn: DBConn,
    user_id: int,
    workout_key: str,
    exercise_name: str,
    delta_text: str,
) -> None:
    conn.execute(
        """
        INSERT INTO workout_adjustments (user_id, workout_key, exercise_name, delta_text)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, workout_key, exercise_name) DO UPDATE SET
            delta_text=excluded.delta_text,
            updated_at=CURRENT_TIMESTAMP
        """,
        (user_id, workout_key, exercise_name, delta_text),
    )
    conn.commit()


def get_adjustments(conn: DBConn, user_id: int, workout_key: str) -> dict[str, str]:
    cur = conn.execute(
        "SELECT exercise_name, delta_text FROM workout_adjustments WHERE user_id=? AND workout_key=?",
        (user_id, workout_key),
    )
    return {row["exercise_name"]: row["delta_text"] for row in cur.fetchall()}


def upsert_progression_rule(
    conn: DBConn,
    user_id: int,
    workout_key: str,
    exercise_name: str,
    delta_text: str,
    interval_days: int,
) -> None:
    conn.execute(
        """
        INSERT INTO progression_rules (user_id, workout_key, exercise_name, delta_text, interval_days)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, workout_key, exercise_name) DO UPDATE SET
            delta_text=excluded.delta_text,
            interval_days=excluded.interval_days,
            updated_at=CURRENT_TIMESTAMP
        """,
        (user_id, workout_key, exercise_name, delta_text, interval_days),
    )
    conn.commit()


def list_progression_rules(conn: DBConn, user_id: int) -> list[Any]:
    cur = conn.execute(
        "SELECT workout_key, exercise_name, delta_text, interval_days, last_applied "
        "FROM progression_rules WHERE user_id=?",
        (user_id,),
    )
    return cur.fetchall()


def apply_due_progressions(conn: DBConn, user_id: int, today_iso: str) -> int:
    cur = conn.execute(
        """
        SELECT id, workout_key, exercise_name, delta_text, interval_days, last_applied
        FROM progression_rules
        WHERE user_id=?
        """,
        (user_id,),
    )
    updated = 0
    for row in cur.fetchall():
        last_applied = row["last_applied"]
        if last_applied:
            try:
                last_date = datetime.fromisoformat(last_applied).date()
            except ValueError:
                last_date = None
        else:
            last_date = None

        due = False
        if last_date is None:
            due = True
        else:
            try:
                today = datetime.fromisoformat(today_iso).date()
            except ValueError:
                continue
            if (today - last_date).days >= int(row["interval_days"]):
                due = True

        if due:
            upsert_adjustment(conn, user_id, row["workout_key"], row["exercise_name"], row["delta_text"])
            conn.execute(
                "UPDATE progression_rules SET last_applied=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (today_iso, row["id"]),
            )
            updated += 1
    conn.commit()
    return updated
