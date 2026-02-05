from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import load_config
from app.db import get_conn, init_db, get_or_create_user, get_settings
from app.workouts import load_plan, get_cycle_order, get_macros, get_workout, get_workout_title


app = FastAPI(title="Fitness Bot API")

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _parse_init_data(init_data: str) -> dict[str, str]:
    pairs = init_data.split("&")
    data = {}
    for pair in pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            data[k] = v
    return data


def _check_init_data(init_data: str, bot_token: str) -> dict[str, str]:
    data = _parse_init_data(init_data)
    if "hash" not in data:
        raise HTTPException(status_code=401, detail="Missing hash")
    received_hash = data.pop("hash")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if calculated_hash != received_hash:
        raise HTTPException(status_code=401, detail="Invalid init data")
    return data


def _get_user_from_init(init_data: str) -> tuple[int, str | None]:
    cfg = load_config()
    data = _check_init_data(init_data, cfg.bot_token)
    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Missing user")
    try:
        import urllib.parse
        import json

        user_json = json.loads(urllib.parse.unquote(user_raw))
    except Exception:
        raise HTTPException(status_code=400, detail="Bad user data")

    tg_id = int(user_json.get("id"))
    name = user_json.get("first_name")
    return tg_id, name


def _get_today(tz: str) -> datetime.date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).date()


def _get_day(conn, user_id: int, day: datetime.date) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM calendar_days WHERE user_id=? AND date=?",
        (user_id, day.isoformat()),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _set_day(conn, user_id: int, day: datetime.date, day_type: str, workout_key: str | None, macros: dict[str, int]) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO calendar_days (user_id, date, day_type, status, workout_key, kcal, protein, fat, carbs)
        VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            day_type=excluded.day_type,
            workout_key=excluded.workout_key,
            kcal=excluded.kcal,
            protein=excluded.protein,
            fat=excluded.fat,
            carbs=excluded.carbs,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            user_id,
            day.isoformat(),
            day_type,
            workout_key,
            macros["kcal"],
            macros["protein"],
            macros["fat"],
            macros["carbs"],
        ),
    )
    conn.commit()
    return _get_day(conn, user_id, day) or {}


def _build_today(conn, user_id: int, plan: dict[str, Any], settings: dict[str, Any], today: datetime.date) -> dict[str, Any]:
    existing = _get_day(conn, user_id, today)
    if existing:
        return existing

    cycle = get_cycle_order(plan)
    if not cycle:
        raise HTTPException(status_code=500, detail="cycle_order is empty")

    cur = conn.execute(
        "SELECT * FROM calendar_days WHERE user_id=? ORDER BY date DESC LIMIT 1",
        (user_id,),
    )
    latest = cur.fetchone()

    if not latest:
        workout_key = cycle[int(settings.get("cycle_index", 0)) % len(cycle)]
        macros = get_macros(plan, "train")
        return _set_day(conn, user_id, today, "train", workout_key, macros)

    last_type = latest["day_type"]
    last_status = latest["status"]
    last_workout = latest.get("workout_key")

    if last_type == "train" and last_status != "done":
        macros = get_macros(plan, "train")
        return _set_day(conn, user_id, today, "train", last_workout, macros)

    if last_type == "train":
        macros = get_macros(plan, "rest")
        return _set_day(conn, user_id, today, "rest", None, macros)

    cycle_index = int(settings.get("cycle_index", 0))
    workout_key = cycle[cycle_index % len(cycle)]
    macros = get_macros(plan, "train")
    return _set_day(conn, user_id, today, "train", workout_key, macros)


class ProgressIn(BaseModel):
    weight: float
    waist: float
    belly: float
    biceps: float
    chest: float


class ProgressUpdate(BaseModel):
    weight: float | None = None
    waist: float | None = None
    belly: float | None = None
    biceps: float | None = None
    chest: float | None = None


@app.on_event("startup")
def on_startup() -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/today")
def api_today(x_tg_init_data: str | None = Header(None)) -> dict[str, Any]:
    if not x_tg_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    cfg = load_config()
    tg_id, name = _get_user_from_init(x_tg_init_data)

    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(conn, tg_id, name, cfg.timezone)
    settings = get_settings(conn, user_id)
    plan = load_plan(cfg.plan_path)

    today_date = _get_today(cfg.timezone)
    day = _build_today(conn, user_id, plan, settings, today_date)

    workout = None
    if day["day_type"] == "train" and day.get("workout_key"):
        workout = {
            "title": get_workout_title(plan, day["workout_key"]),
            "easy": get_workout(plan, day["workout_key"], "easy"),
            "medium": get_workout(plan, day["workout_key"], "medium"),
            "hard": get_workout(plan, day["workout_key"], "hard"),
        }

    return {
        "date": day["date"],
        "day_type": day["day_type"],
        "status": day["status"],
        "macros": {
            "kcal": day["kcal"],
            "protein": day["protein"],
            "fat": day["fat"],
            "carbs": day["carbs"],
        },
        "workout": workout,
    }


@app.post("/api/progress")
def api_progress_add(payload: ProgressIn, x_tg_init_data: str | None = Header(None)) -> dict[str, Any]:
    if not x_tg_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    cfg = load_config()
    tg_id, name = _get_user_from_init(x_tg_init_data)

    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(conn, tg_id, name, cfg.timezone)
    today_date = _get_today(cfg.timezone)

    conn.execute(
        """
        INSERT INTO progress_logs (user_id, date, weight, waist, belly, biceps, chest)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, today_date.isoformat(), payload.weight, payload.waist, payload.belly, payload.biceps, payload.chest),
    )
    conn.commit()
    return {"status": "ok"}


@app.get("/api/progress")
def api_progress_list(x_tg_init_data: str | None = Header(None)) -> list[dict[str, Any]]:
    if not x_tg_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    cfg = load_config()
    tg_id, name = _get_user_from_init(x_tg_init_data)

    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(conn, tg_id, name, cfg.timezone)

    cur = conn.execute(
        "SELECT id, date, weight, waist, belly, biceps, chest FROM progress_logs WHERE user_id=? ORDER BY date DESC LIMIT 50",
        (user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


@app.put("/api/progress/{progress_id}")
def api_progress_update(progress_id: int, payload: ProgressUpdate, x_tg_init_data: str | None = Header(None)) -> dict[str, Any]:
    if not x_tg_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    cfg = load_config()
    tg_id, name = _get_user_from_init(x_tg_init_data)

    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(conn, tg_id, name, cfg.timezone)

    fields = []
    values = []
    for key, value in payload.dict().items():
        if value is not None:
            fields.append(f"{key}=?")
            values.append(value)
    if not fields:
        return {"status": "no изменений"}

    values.extend([user_id, progress_id])
    conn.execute(
        f"UPDATE progress_logs SET {', '.join(fields)} WHERE user_id=? AND id=?",
        values,
    )
    conn.commit()
    return {"status": "ok"}
