from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.admin import parse_admin_ids

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass
class Config:
    bot_token: str
    db_dsn: str
    plan_path: Path
    timezone: str
    openai_api_key: str | None
    sheet_id: str | None
    sheet_gid_plan: str | None
    sheet_gid_macros: str | None
    sheet_gid_cycle: str | None
    admin_ids: set[int]


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    db_dsn = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_dsn:
        db_dsn = str(BASE_DIR / "data" / "bot.db")

    plan_path = Path(os.getenv("PLAN_PATH", str(BASE_DIR / "data" / "plan.yaml"))).resolve()
    timezone = os.getenv("TZ", "Europe/Moscow")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    sheet_id = os.getenv("SHEET_ID")
    sheet_gid_plan = os.getenv("SHEET_GID_PLAN")
    sheet_gid_macros = os.getenv("SHEET_GID_MACROS")
    sheet_gid_cycle = os.getenv("SHEET_GID_CYCLE")

    admin_ids = parse_admin_ids(os.getenv("ADMIN_IDS"))

    return Config(
        bot_token=bot_token,
        db_dsn=db_dsn,
        plan_path=plan_path,
        timezone=timezone,
        openai_api_key=openai_api_key,
        sheet_id=sheet_id,
        sheet_gid_plan=sheet_gid_plan,
        sheet_gid_macros=sheet_gid_macros,
        sheet_gid_cycle=sheet_gid_cycle,
        admin_ids=admin_ids,
    )
