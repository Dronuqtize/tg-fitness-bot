from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.config import load_config
from app.db import (
    get_conn,
    init_db,
    get_or_create_user,
    get_settings,
    update_settings,
    upsert_adjustment,
    get_adjustments,
    upsert_progression_rule,
    list_progression_rules,
    apply_due_progressions,
)
from app.workouts import load_plan, get_cycle_order, get_macros, get_workout, get_workout_title
from app.calendar_image import render_month_calendar, render_attendance_table
from app.ai import generate_advice
from app.admin import parse_admin_ids
from app.charts import render_progress_chart
from app.pdf_report import generate_weekly_pdf, temp_pdf_path
from app.sheets import SheetConfig, sync_plan_from_sheets, write_plan_yaml

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


class CommentState(StatesGroup):
    waiting = State()


class ProgressState(StatesGroup):
    waiting = State()


class MedLogState(StatesGroup):
    waiting = State()


class ProgressionState(StatesGroup):
    waiting = State()


@dataclass
class DayPlan:
    date: date
    day_type: str
    workout_key: str | None
    macros: dict[str, int]


router = Router()
SCHEDULER: AsyncIOScheduler | None = None
BOT_REF: Bot | None = None

REMINDER_TYPES = {
    "water": "Напоминание: выпей воду.",
    "motivation": "Мотивация: держим курс на цель.",
    "sleep": "Напоминание: сон и восстановление сегодня важны.",
    "workout": "Пора тренироваться. Проверь /today.",
    "ai": "ИИ‑совет дня готов.",
}

REPORT_DEFAULTS = {
    "daily_report": {"time": "23:00", "enabled": True},
    "weekly_pdf": {"time": "20:00", "day": "sun", "enabled": True},
}


async def _apply_progressions_for_all_users() -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    today_iso = _get_today(cfg.timezone).isoformat()
    cur = conn.execute("SELECT id FROM users")
    for row in cur.fetchall():
        apply_due_progressions(conn, int(row["id"]), today_iso)


def _get_today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def _get_latest_day(conn, user_id: int) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM calendar_days WHERE user_id=? ORDER BY date DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _get_day(conn, user_id: int, day: date) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM calendar_days WHERE user_id=? AND date=?",
        (user_id, day.isoformat()),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _set_day(conn, user_id: int, day: DayPlan, status: str = "planned") -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO calendar_days (user_id, date, day_type, status, workout_key, kcal, protein, fat, carbs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            day_type=excluded.day_type,
            status=excluded.status,
            workout_key=excluded.workout_key,
            kcal=excluded.kcal,
            protein=excluded.protein,
            fat=excluded.fat,
            carbs=excluded.carbs,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            user_id,
            day.date.isoformat(),
            day.day_type,
            status,
            day.workout_key,
            day.macros["kcal"],
            day.macros["protein"],
            day.macros["fat"],
            day.macros["carbs"],
        ),
    )
    conn.commit()
    return _get_day(conn, user_id, day.date) or {}


def _mark_skipped_if_needed(conn, user_id: int, day: date) -> None:
    conn.execute(
        """
        UPDATE calendar_days
        SET status='skipped', updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND date<? AND status='planned'
        """,
        (user_id, day.isoformat()),
    )
    conn.commit()


def _build_today_plan(conn, user_id: int, plan: dict[str, Any], settings: dict[str, Any], today: date) -> DayPlan:
    _mark_skipped_if_needed(conn, user_id, today)

    existing = _get_day(conn, user_id, today)
    if existing:
        macros = {
            "kcal": existing["kcal"],
            "protein": existing["protein"],
            "fat": existing["fat"],
            "carbs": existing["carbs"],
        }
        return DayPlan(
            date=today,
            day_type=existing["day_type"],
            workout_key=existing.get("workout_key"),
            macros=macros,
        )

    start_date_str = settings.get("start_date")
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str).date()
        except ValueError:
            start_date = None
        if start_date and today < start_date:
            macros = get_macros(plan, "rest")
            return DayPlan(date=today, day_type="rest", workout_key=None, macros=macros)

    cycle = get_cycle_order(plan)
    if not cycle:
        raise RuntimeError("cycle_order is empty in plan.yaml")

    latest = _get_latest_day(conn, user_id)
    if not latest:
        workout_key = cycle[int(settings.get("cycle_index", 0)) % len(cycle)]
        macros = get_macros(plan, "train")
        return DayPlan(date=today, day_type="train", workout_key=workout_key, macros=macros)

    last_type = latest["day_type"]
    last_status = latest["status"]
    last_workout = latest.get("workout_key")

    if last_type == "train" and last_status != "done":
        macros = get_macros(plan, "train")
        return DayPlan(date=today, day_type="train", workout_key=last_workout, macros=macros)

    if last_type == "train":
        macros = get_macros(plan, "rest")
        return DayPlan(date=today, day_type="rest", workout_key=None, macros=macros)

    cycle_index = int(settings.get("cycle_index", 0))
    workout_key = cycle[cycle_index % len(cycle)]
    macros = get_macros(plan, "train")
    return DayPlan(date=today, day_type="train", workout_key=workout_key, macros=macros)


def _day_message(plan: dict[str, Any], day: DayPlan) -> str:
    if day.day_type == "train":
        title = get_workout_title(plan, day.workout_key or "")
        return (
            f"Сегодня тренировка: {title}\n"
            f"КБЖУ: {day.macros['kcal']} ккал, Б {day.macros['protein']}, Ж {day.macros['fat']}, У {day.macros['carbs']}"
        )
    return (
        f"Сегодня отдых\n"
        f"КБЖУ: {day.macros['kcal']} ккал, Б {day.macros['protein']}, Ж {day.macros['fat']}, У {day.macros['carbs']}"
    )


def _workout_text(
    plan: dict[str, Any],
    workout_key: str,
    level: str,
    adjustments: dict[str, str] | None = None,
) -> str:
    title = get_workout_title(plan, workout_key)
    items = get_workout(plan, workout_key, level)
    if not items:
        return f"{title}\nПлан для уровня {level} пока пуст."
    lines = [f"{title} — {level}"]
    for idx, ex in enumerate(items, 1):
        name = ex.get("name", "")
        sets = ex.get("sets", "")
        reps = ex.get("reps", "")
        weight = ex.get("weight", "")
        line = f"{idx}. {name} — {sets}x{reps}"
        if weight:
            line += f" ({weight})"
        if adjustments and name in adjustments:
            line += f" | прогрессия: {adjustments[name]}"
        lines.append(line)
    return "\n".join(lines)


def _day_keyboard(day: DayPlan) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if day.day_type == "train":
        kb.button(text="Легкая", callback_data="level:easy")
        kb.button(text="Средняя", callback_data="level:medium")
        kb.button(text="Сложная", callback_data="level:hard")
        kb.button(text="ДОБАВИТЬ ПРОГРЕССИЮ", callback_data="progression")
        kb.button(text="ЗАВЕРШИЛ ТРЕНИРОВКУ", callback_data="done:train")
    else:
        kb.button(text="ОТДЫХАЛ", callback_data="done:rest")
    kb.button(text="Календарь", callback_data="calendar")
    kb.button(text="Совет дня", callback_data="advice")
    kb.adjust(2, 2, 1)
    return kb


async def _send_calendar_message(message: Message, conn, user_id: int, tz: str) -> None:
    today_date = _get_today(tz)
    year, month = today_date.year, today_date.month

    cur = conn.execute(
        "SELECT date, status, day_type FROM calendar_days WHERE user_id=? AND date LIKE ?",
        (user_id, f"{year:04d}-{month:02d}%"),
    )
    statuses = {}
    for row in cur.fetchall():
        d = int(row["date"].split("-")[2])
        status = row["status"]
        if row["day_type"] == "rest" and status == "planned":
            status = "rest"
        statuses[d] = status

    img_path = render_month_calendar(year, month, statuses)
    await message.answer_photo(FSInputFile(img_path))


def _parse_time(value: str) -> tuple[int, int] | None:
    value = value.strip()
    if ":" not in value:
        return None
    parts = value.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _extract_sheet_id(value: str) -> str | None:
    if "/d/" in value:
        try:
            return value.split("/d/")[1].split("/")[0]
        except Exception:
            return None
    return value if value else None


def _normalize_reminders(reminders: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in reminders.items():
        if isinstance(value, str):
            normalized[key] = {"time": value, "enabled": True}
        elif isinstance(value, dict):
            normalized[key] = {
                "time": value.get("time"),
                "enabled": bool(value.get("enabled", True)),
            }
    return normalized


def _get_report_cfg(reminders: dict[str, Any], key: str) -> dict[str, Any]:
    cfg = reminders.get(key)
    if not isinstance(cfg, dict):
        cfg = {}
    base = REPORT_DEFAULTS.get(key, {})
    merged = {
        "time": cfg.get("time", base.get("time")),
        "enabled": bool(cfg.get("enabled", base.get("enabled", True))),
        "day": cfg.get("day", base.get("day")),
    }
    return merged


async def _send_reminder_job(user_id: int, reminder_type: str) -> None:
    if not BOT_REF:
        return
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)

    cur = conn.execute("SELECT chat_id FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row or not row["chat_id"]:
        return

    settings = get_settings(conn, user_id)
    if reminder_type == "ai":
        if not settings.get("ai_enabled", 1):
            return
        if not cfg.openai_api_key:
            await BOT_REF.send_message(row["chat_id"], "ИИ‑советы отключены: нет OPENAI_API_KEY.")
            return
        context = _build_ai_context(conn, user_id)
        try:
            advice_text = generate_advice(cfg.openai_api_key, context)
        except Exception as exc:
            update_settings(conn, user_id, ai_enabled=0)
            await BOT_REF.send_message(
                row["chat_id"],
                f"ИИ‑советы временно выключены (нет токенов или ошибка API): {exc}",
            )
            return
        _store_advice(conn, user_id, _get_today(cfg.timezone), advice_text)
        await BOT_REF.send_message(row["chat_id"], advice_text)
        return

    text = REMINDER_TYPES.get(reminder_type, "Напоминание.")
    await BOT_REF.send_message(row["chat_id"], text)


def _schedule_user_reminders(conn, user_id: int, tz: str) -> None:
    if not SCHEDULER:
        return
    settings = get_settings(conn, user_id)
    reminders = settings.get("reminders") or {}

    for r_type in REMINDER_TYPES.keys():
        job_id = f"reminder:{user_id}:{r_type}"
        if SCHEDULER.get_job(job_id):
            SCHEDULER.remove_job(job_id)

        cfg = reminders.get(r_type)
        if not cfg or not cfg.get("enabled") or not cfg.get("time"):
            continue

        parsed = _parse_time(str(cfg.get("time")))
        if not parsed:
            continue
        hour, minute = parsed
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
        SCHEDULER.add_job(
            _send_reminder_job,
            trigger,
            args=[user_id, r_type],
            id=job_id,
            replace_existing=True,
        )


async def _send_daily_report_job(user_id: int) -> None:
    if not BOT_REF:
        return
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)

    cur = conn.execute("SELECT chat_id FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row or not row["chat_id"]:
        return

    report_text = await _build_daily_report(conn, user_id, cfg)
    await BOT_REF.send_message(row["chat_id"], report_text)


async def _send_weekly_pdf_job(user_id: int) -> None:
    if not BOT_REF:
        return
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)

    cur = conn.execute("SELECT chat_id FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row or not row["chat_id"]:
        return

    pdf_path = _build_weekly_pdf(conn, user_id, cfg)
    await BOT_REF.send_document(row["chat_id"], FSInputFile(pdf_path))


def _schedule_user_reports(conn, user_id: int, tz: str) -> None:
    if not SCHEDULER:
        return
    settings = get_settings(conn, user_id)
    reminders = settings.get("reminders") or {}

    daily = _get_report_cfg(reminders, "daily_report")
    weekly = _get_report_cfg(reminders, "weekly_pdf")

    daily_job_id = f"report:daily:{user_id}"
    if SCHEDULER.get_job(daily_job_id):
        SCHEDULER.remove_job(daily_job_id)
    if daily.get("enabled") and daily.get("time"):
        parsed = _parse_time(str(daily.get("time")))
        if parsed:
            hour, minute = parsed
            trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
            SCHEDULER.add_job(_send_daily_report_job, trigger, args=[user_id], id=daily_job_id)

    weekly_job_id = f"report:weekly:{user_id}"
    if SCHEDULER.get_job(weekly_job_id):
        SCHEDULER.remove_job(weekly_job_id)
    if weekly.get("enabled") and weekly.get("time") and weekly.get("day"):
        parsed = _parse_time(str(weekly.get("time")))
        if parsed:
            hour, minute = parsed
            trigger = CronTrigger(day_of_week=str(weekly.get("day")), hour=hour, minute=minute, timezone=tz)
            SCHEDULER.add_job(_send_weekly_pdf_job, trigger, args=[user_id], id=weekly_job_id)


def _schedule_all_reminders(conn, tz: str) -> None:
    cur = conn.execute("SELECT id FROM users")
    for row in cur.fetchall():
        _schedule_user_reminders(conn, row["id"], tz)
        _schedule_user_reports(conn, row["id"], tz)


def _is_admin(cfg, user_id: int) -> bool:
    return user_id in cfg.admin_ids


@router.message(CommandStart())
async def start(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)
    if not settings.get("start_date"):
        update_settings(conn, user_id, start_date=None)
    await message.answer(
        "Привет! Я готов вести твой календарь тренировок, КБЖУ и прогресс.\n"
        "Команды: /today, /progress, /calendar, /attendance, /chart, /advice, /medlog, /ai, /reminder, "
        "/startdate, /stats, /autoprog, /syncplan, /dailyreport, /weeklypdf, /pdf"
    )


@router.message(Command("today"))
async def today(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)
    plan = load_plan(cfg.plan_path)

    today_date = _get_today(cfg.timezone)
    apply_due_progressions(conn, user_id, today_date.isoformat())
    day_plan = _build_today_plan(conn, user_id, plan, settings, today_date)
    _set_day(conn, user_id, day_plan, status="planned")

    text = _day_message(plan, day_plan)
    kb = _day_keyboard(day_plan)
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("level:"))
async def show_level(call: CallbackQuery) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        call.from_user.id,
        call.from_user.full_name,
        cfg.timezone,
        chat_id=call.message.chat.id if call.message else None,
    )
    plan = load_plan(cfg.plan_path)

    day = _get_day(conn, user_id, _get_today(cfg.timezone))
    if not day or day["day_type"] != "train":
        await call.answer("Сегодня не тренировочный день", show_alert=True)
        return

    level = call.data.split(":", 1)[1]
    conn.execute(
        "UPDATE calendar_days SET level=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (level, day["id"]),
    )
    conn.commit()

    adjustments = get_adjustments(conn, user_id, day["workout_key"])
    text = _workout_text(plan, day["workout_key"], level, adjustments)
    await call.message.answer(text)
    await call.answer()


@router.callback_query(F.data == "calendar")
async def show_calendar(call: CallbackQuery) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        call.from_user.id,
        call.from_user.full_name,
        cfg.timezone,
        chat_id=call.message.chat.id if call.message else None,
    )

    await _send_calendar_message(call.message, conn, user_id, cfg.timezone)
    await call.answer()


@router.callback_query(F.data == "progression")
async def add_progression(call: CallbackQuery, state: FSMContext) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        call.from_user.id,
        call.from_user.full_name,
        cfg.timezone,
        chat_id=call.message.chat.id if call.message else None,
    )

    day = _get_day(conn, user_id, _get_today(cfg.timezone))
    if not day or day["day_type"] != "train":
        await call.answer("Сегодня нет тренировки", show_alert=True)
        return

    await state.update_data(workout_key=day["workout_key"])
    await call.message.answer(
        "Добавить прогрессию: напиши в формате\n"
        "`упражнение | +2 повт` или `упражнение | +2.5 кг`",
        parse_mode="Markdown",
    )
    await state.set_state(ProgressionState.waiting)
    await call.answer()


@router.message(ProgressionState.waiting)
async def save_progression(message: Message, state: FSMContext) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    data = await state.get_data()
    workout_key = data.get("workout_key")
    if not workout_key:
        await message.answer("Не удалось определить тренировку. Вызови /today еще раз.")
        await state.clear()
        return

    text = message.text.strip()
    if "|" not in text:
        await message.answer("Формат: упражнение | +2 повт")
        return
    name, delta = [part.strip() for part in text.split("|", 1)]
    if not name or not delta:
        await message.answer("Формат: упражнение | +2 повт")
        return

    upsert_adjustment(conn, user_id, workout_key, name, delta)
    await message.answer(f"Прогрессия сохранена для «{name}»: {delta}")
    await state.clear()

@router.callback_query(F.data.startswith("done:"))
async def finish_day(call: CallbackQuery, state: FSMContext) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        call.from_user.id,
        call.from_user.full_name,
        cfg.timezone,
        chat_id=call.message.chat.id if call.message else None,
    )

    today_date = _get_today(cfg.timezone)
    day = _get_day(conn, user_id, today_date)
    if not day:
        await call.answer("Сначала запроси /today", show_alert=True)
        return

    if call.data.endswith("train"):
        conn.execute(
            "UPDATE calendar_days SET status='done', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (day["id"],),
        )
        settings = get_settings(conn, user_id)
        update_settings(conn, user_id, cycle_index=int(settings.get("cycle_index", 0)) + 1)
    else:
        conn.execute(
            "UPDATE calendar_days SET status='done', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (day["id"],),
        )
    conn.commit()

    await call.message.answer("Короткий комментарий по дню?")
    await state.set_state(CommentState.waiting)
    await call.answer()


@router.message(CommentState.waiting)
async def save_comment(message: Message, state: FSMContext) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    today_date = _get_today(cfg.timezone)
    conn.execute(
        "UPDATE calendar_days SET note=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND date=?",
        (message.text.strip(), user_id, today_date.isoformat()),
    )
    conn.commit()
    await message.answer("Записал комментарий.")
    await state.clear()


@router.message(Command("progress"))
async def progress(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Введи прогресс одной строкой: вес, талия, живот, бицепс, грудь.\n"
        "Пример: 92.5, 84, 89, 36, 102"
    )
    await state.set_state(ProgressState.waiting)


@router.message(ProgressState.waiting)
async def save_progress(message: Message, state: FSMContext) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    parts = [p.strip() for p in message.text.replace(";", ",").split(",") if p.strip()]
    if len(parts) < 5:
        await message.answer("Нужно 5 чисел: вес, талия, живот, бицепс, грудь")
        return

    try:
        weight, waist, belly, biceps, chest = map(float, parts[:5])
    except ValueError:
        await message.answer("Похоже, есть нечисловые значения. Попробуй еще раз.")
        return

    conn.execute(
        """
        INSERT INTO progress_logs (user_id, date, weight, waist, belly, biceps, chest)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, _get_today(cfg.timezone).isoformat(), weight, waist, belly, biceps, chest),
    )
    conn.commit()
    await message.answer("Прогресс записан.")
    await state.clear()


@router.message(Command("medlog"))
async def medlog(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Лог уколов: введи одной строкой: название, мг, мл, комментарий.\n"
        "Пример: тестостерон, 125, 0.5, после тренировки"
    )
    await state.set_state(MedLogState.waiting)


@router.message(MedLogState.waiting)
async def save_medlog(message: Message, state: FSMContext) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    parts = [p.strip() for p in message.text.replace(";", ",").split(",") if p.strip()]
    if len(parts) < 3:
        await message.answer("Нужно минимум 3 поля: название, мг, мл.")
        return

    name = parts[0]
    try:
        amount_mg = float(parts[1])
    except ValueError:
        amount_mg = None
    try:
        amount_ml = float(parts[2])
    except ValueError:
        amount_ml = None
    note = parts[3] if len(parts) > 3 else None

    conn.execute(
        """
        INSERT INTO med_logs (user_id, date, name, amount_mg, amount_ml, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, _get_today(cfg.timezone).isoformat(), name, amount_mg, amount_ml, note),
    )
    conn.commit()
    await message.answer("Записал лог.")
    await state.clear()


@router.message(Command("reminder"))
async def reminder(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)
    reminders = settings.get("reminders") or {}

    parts = message.text.strip().split()
    if len(parts) == 1 or parts[1].lower() == "list":
        lines = ["Текущие напоминания:"]
        for key in REMINDER_TYPES.keys():
            cfg_item = reminders.get(key)
            if cfg_item and cfg_item.get("enabled") and cfg_item.get("time"):
                lines.append(f"- {key}: {cfg_item['time']}")
            else:
                lines.append(f"- {key}: выключено")
        lines.append("Формат: /reminder set water 10:00 или /reminder off water")
        await message.answer("\n".join(lines))
        return

    action = parts[1].lower()
    if action in ("set", "on"):
        if len(parts) < 4:
            await message.answer("Формат: /reminder set water 10:00")
            return
        r_type = parts[2].lower()
        time_str = parts[3]
        if r_type not in REMINDER_TYPES:
            await message.answer(f"Типы: {', '.join(REMINDER_TYPES.keys())}")
            return
        parsed = _parse_time(time_str)
        if not parsed:
            await message.answer("Время в формате HH:MM, например 10:00")
            return
        reminders[r_type] = {"time": time_str, "enabled": True}
        update_settings(conn, user_id, reminders=reminders)
        _schedule_user_reminders(conn, user_id, cfg.timezone)
        await message.answer(f"Ок, напоминание {r_type} в {time_str}")
        return

    if action in ("off", "disable"):
        if len(parts) < 3:
            await message.answer("Формат: /reminder off water")
            return
        r_type = parts[2].lower()
        if r_type not in REMINDER_TYPES:
            await message.answer(f"Типы: {', '.join(REMINDER_TYPES.keys())}")
            return
        reminders[r_type] = {"time": None, "enabled": False}
        update_settings(conn, user_id, reminders=reminders)
        _schedule_user_reminders(conn, user_id, cfg.timezone)
        await message.answer(f"Ок, напоминание {r_type} выключено")
        return

    await message.answer("Команды: /reminder list | /reminder set water 10:00 | /reminder off water")


@router.message(Command("autoprog"))
async def autoprog(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    plan = load_plan(cfg.plan_path)
    workout_keys = list((plan.get("workouts") or {}).keys())

    text = message.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        await message.answer(
            "Команды:\n"
            "/autoprog list\n"
            "/autoprog set workout_key | упражнение | +1 повт | 7"
        )
        return

    action = parts[1].strip().lower()
    if action == "list":
        rules = list_progression_rules(conn, user_id)
        if not rules:
            await message.answer("Правил автопрогрессии пока нет.")
            return
        lines = ["Правила автопрогрессии:"]
        for row in rules:
            lines.append(
                f"- {row['workout_key']} | {row['exercise_name']} | {row['delta_text']} | {row['interval_days']}д"
            )
        await message.answer("\n".join(lines))
        return

    if not parts[1].lower().startswith("set"):
        await message.answer("Формат: /autoprog set workout_key | упражнение | +1 повт | 7")
        return

    if "|" not in text:
        await message.answer("Формат: /autoprog set workout_key | упражнение | +1 повт | 7")
        return

    try:
        payload = text.split("set", 1)[1].strip()
        fields = [f.strip() for f in payload.split("|") if f.strip()]
    except Exception:
        fields = []

    if len(fields) < 3:
        await message.answer("Формат: /autoprog set workout_key | упражнение | +1 повт | 7")
        return

    workout_key = fields[0]
    exercise_name = fields[1]
    delta_text = fields[2]
    interval_days = 7
    if len(fields) >= 4:
        try:
            interval_days = int(fields[3])
        except ValueError:
            interval_days = 7

    if workout_key not in workout_keys:
        await message.answer(f"Нет такого workout_key. Доступны: {', '.join(workout_keys)}")
        return

    upsert_progression_rule(conn, user_id, workout_key, exercise_name, delta_text, interval_days)
    await message.answer(
        f"Ок, правило сохранено: {workout_key} | {exercise_name} | {delta_text} | {interval_days}д"
    )


@router.message(Command("syncplan"))
async def syncplan(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    parts = message.text.strip().split()
    sheet_id = cfg.sheet_id
    gid_plan = cfg.sheet_gid_plan
    gid_macros = cfg.sheet_gid_macros
    gid_cycle = cfg.sheet_gid_cycle

    if len(parts) >= 2:
        sheet_id = _extract_sheet_id(parts[1])
    if len(parts) >= 5:
        gid_plan, gid_macros, gid_cycle = parts[2], parts[3], parts[4]

    if len(parts) >= 2 and parts[1].lower() in ("apply", "confirm"):
        pending_path = cfg.plan_path.with_suffix(".pending.yaml")
        if not pending_path.exists():
            await message.answer("Нет ожидающего плана. Сначала /syncplan")
            return
        pending_path.replace(cfg.plan_path)
        await message.answer("План применен.")
        return

    if not sheet_id or not gid_plan or not gid_macros or not gid_cycle:
        await message.answer(
            "Нужны параметры. Варианты:\n"
            "/syncplan <sheet_url_or_id> <gid_plan> <gid_macros> <gid_cycle>\n"
            "или задай в .env: SHEET_ID, SHEET_GID_PLAN, SHEET_GID_MACROS, SHEET_GID_CYCLE"
        )
        return

    try:
        plan = sync_plan_from_sheets(
            SheetConfig(
                sheet_id=sheet_id,
                gid_plan=str(gid_plan),
                gid_macros=str(gid_macros),
                gid_cycle=str(gid_cycle),
            )
        )
        pending_path = cfg.plan_path.with_suffix(".pending.yaml")
        write_plan_yaml(plan, str(pending_path))
    except Exception as exc:
        await message.answer(f"Не удалось синхронизировать план: {exc}")
        return

    workouts_count = sum(len(v.get("easy", [])) + len(v.get("medium", [])) + len(v.get("hard", [])) for v in plan.get("workouts", {}).values())
    cycle_count = len(plan.get("cycle_order", []))
    await message.answer(
        f"План загружен в ожидании применения. Упражнений: {workouts_count}, дней в цикле: {cycle_count}.\n"
        "Применить: /syncplan apply"
    )


@router.message(Command("dailyreport"))
async def dailyreport(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)
    reminders = _normalize_reminders(settings.get("reminders") or {})
    cfg_item = _get_report_cfg(reminders, "daily_report")

    parts = message.text.strip().split()
    if len(parts) == 1:
        status = "включен" if cfg_item.get("enabled") else "выключен"
        await message.answer(
            f"Ежедневный отчет сейчас {status}, время {cfg_item.get('time')}.\n"
            "Команды: /dailyreport on | /dailyreport off | /dailyreport time 23:00"
        )
        return

    action = parts[1].lower()
    if action in ("on", "off"):
        cfg_item["enabled"] = action == "on"
    elif action == "time" and len(parts) >= 3:
        if not _parse_time(parts[2]):
            await message.answer("Время в формате HH:MM")
            return
        cfg_item["time"] = parts[2]
        cfg_item["enabled"] = True
    else:
        await message.answer("Команды: /dailyreport on | /dailyreport off | /dailyreport time 23:00")
        return

    reminders["daily_report"] = cfg_item
    update_settings(conn, user_id, reminders=reminders)
    _schedule_user_reports(conn, user_id, cfg.timezone)
    await message.answer(f"Ок, ежедневный отчет: {'вкл' if cfg_item['enabled'] else 'выкл'} в {cfg_item.get('time')}")


@router.message(Command("weeklypdf"))
async def weeklypdf(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)
    reminders = _normalize_reminders(settings.get("reminders") or {})
    cfg_item = _get_report_cfg(reminders, "weekly_pdf")

    parts = message.text.strip().split()
    if len(parts) == 1:
        status = "включен" if cfg_item.get("enabled") else "выключен"
        await message.answer(
            f"Еженедельный PDF сейчас {status}, день {cfg_item.get('day')}, время {cfg_item.get('time')}.\n"
            "Команды: /weeklypdf on | /weeklypdf off | /weeklypdf time sun 20:00"
        )
        return

    action = parts[1].lower()
    if action in ("on", "off"):
        cfg_item["enabled"] = action == "on"
    elif action == "time" and len(parts) >= 4:
        day = parts[2].lower()
        if day not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            await message.answer("День: mon/tue/wed/thu/fri/sat/sun")
            return
        if not _parse_time(parts[3]):
            await message.answer("Время в формате HH:MM")
            return
        cfg_item["day"] = day
        cfg_item["time"] = parts[3]
        cfg_item["enabled"] = True
    else:
        await message.answer("Команды: /weeklypdf on | /weeklypdf off | /weeklypdf time sun 20:00")
        return

    reminders["weekly_pdf"] = cfg_item
    update_settings(conn, user_id, reminders=reminders)
    _schedule_user_reports(conn, user_id, cfg.timezone)
    await message.answer(
        f"Ок, еженедельный PDF: {'вкл' if cfg_item['enabled'] else 'выкл'} "
        f"{cfg_item.get('day')} {cfg_item.get('time')}"
    )


@router.message(Command("pdf"))
async def pdf_report(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    pdf_path = _build_weekly_pdf(conn, user_id, cfg)
    await message.answer_document(FSInputFile(pdf_path))


@router.message(Command("calendar"))
async def calendar_cmd(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    await _send_calendar_message(message, conn, user_id, cfg.timezone)


@router.message(Command("attendance"))
async def attendance(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    today_date = _get_today(cfg.timezone)
    year, month = today_date.year, today_date.month

    cur = conn.execute(
        "SELECT date, status, day_type FROM calendar_days WHERE user_id=? AND date LIKE ?",
        (user_id, f\"{year:04d}-{month:02d}%\"),
    )
    statuses = {}
    for row in cur.fetchall():
        d = int(row["date"].split("-")[2])
        status = row["status"]
        if row["day_type"] == "rest" and status == "planned":
            status = "rest"
        statuses[d] = status

    img_path = render_attendance_table(year, month, statuses)
    await message.answer_photo(FSInputFile(img_path))


@router.message(Command("chart"))
async def chart(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    cur = conn.execute(
        """
        SELECT date, weight, waist, belly, biceps, chest
        FROM progress_logs
        WHERE user_id=?
        ORDER BY date ASC
        LIMIT 90
        """,
        (user_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if len(rows) < 2:
        await message.answer("Мало данных для графика. Добавь больше /progress.")
        return

    img_path = render_progress_chart(rows)
    await message.answer_photo(FSInputFile(img_path))


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    today_date = _get_today(cfg.timezone)
    week = _get_weekly_stats(conn, user_id, today_date)
    lines = [f"Статистика за 7 дней ({week['start_date'].isoformat()} — {today_date.isoformat()}):"]
    lines.extend(_stats_lines(week, today_date)[1:])

    await message.answer("\n".join(lines))


def _build_ai_context(conn, user_id: int) -> list[str]:
    cur = conn.execute(
        "SELECT date, day_type, status, note, kcal, protein, fat, carbs FROM calendar_days "
        "WHERE user_id=? ORDER BY date DESC LIMIT 7",
        (user_id,),
    )
    lines = []
    for row in cur.fetchall():
        lines.append(
            f"{row['date']}: {row['day_type']} {row['status']}. "
            f"КБЖУ {row['kcal']}/{row['protein']}/{row['fat']}/{row['carbs']}. "
            f"Комментарий: {row['note'] or '-'}"
        )

    cur = conn.execute(
        "SELECT date, weight, waist, belly, biceps, chest FROM progress_logs "
        "WHERE user_id=? ORDER BY date DESC LIMIT 3",
        (user_id,),
    )
    for row in cur.fetchall():
        lines.append(
            f"Прогресс {row['date']}: вес {row['weight']}, талия {row['waist']}, живот {row['belly']}, "
            f"бицепс {row['biceps']}, грудь {row['chest']}"
        )
    return lines


def _get_weekly_stats(conn, user_id: int, today_date: date) -> dict[str, Any]:
    start_date = today_date - timedelta(days=6)
    cur = conn.execute(
        """
        SELECT day_type, status, COUNT(*) as cnt
        FROM calendar_days
        WHERE user_id=? AND date BETWEEN ? AND ?
        GROUP BY day_type, status
        """,
        (user_id, start_date.isoformat(), today_date.isoformat()),
    )
    counts = {(row["day_type"], row["status"]): row["cnt"] for row in cur.fetchall()}

    cur = conn.execute(
        """
        SELECT AVG(kcal) as kcal, AVG(protein) as protein, AVG(fat) as fat, AVG(carbs) as carbs
        FROM calendar_days
        WHERE user_id=? AND date BETWEEN ? AND ?
        """,
        (user_id, start_date.isoformat(), today_date.isoformat()),
    )
    row = cur.fetchone()
    averages = {
        "kcal": int(row["kcal"] or 0),
        "protein": int(row["protein"] or 0),
        "fat": int(row["fat"] or 0),
        "carbs": int(row["carbs"] or 0),
    }

    cur = conn.execute(
        """
        SELECT date, weight FROM progress_logs
        WHERE user_id=? AND date BETWEEN ? AND ?
        ORDER BY date ASC
        """,
        (user_id, start_date.isoformat(), today_date.isoformat()),
    )
    progress = cur.fetchall()
    weight_change = None
    if len(progress) >= 2:
        weight_change = float(progress[-1]["weight"] or 0) - float(progress[0]["weight"] or 0)

    return {
        "start_date": start_date,
        "counts": counts,
        "averages": averages,
        "weight_change": weight_change,
    }


def _stats_lines(week: dict[str, Any], today_date: date) -> list[str]:
    counts = week["counts"]
    train_done = counts.get(("train", "done"), 0)
    train_skipped = counts.get(("train", "skipped"), 0)
    rest_done = counts.get(("rest", "done"), 0)
    total_days = sum(counts.values())
    averages = week["averages"]

    lines = [
        f"Период: {week['start_date'].isoformat()} — {today_date.isoformat()}",
        f"Тренировки: {train_done} выполнено, {train_skipped} пропущено",
        f"Отдых: {rest_done} отмечено",
        f"Записанных дней: {total_days}",
        f"Среднее КБЖУ: {averages['kcal']} ккал, Б {averages['protein']}, Ж {averages['fat']}, У {averages['carbs']}",
    ]
    if week["weight_change"] is not None:
        lines.append(f"Изменение веса: {week['weight_change']:+.1f} кг")
    else:
        lines.append("Изменение веса: данных мало")
    return lines


async def _build_daily_report(conn, user_id: int, cfg) -> str:
    plan = load_plan(cfg.plan_path)
    today_date = _get_today(cfg.timezone)
    settings = get_settings(conn, user_id)

    day_plan = _build_today_plan(conn, user_id, plan, settings, today_date)
    existing_day = _get_day(conn, user_id, today_date)
    status = existing_day.get("status", "planned") if existing_day else "planned"
    _set_day(conn, user_id, day_plan, status=status)

    lines = [f"Ежедневный отчет — {today_date.isoformat()}"]
    lines.append(_day_message(plan, day_plan))

    cur = conn.execute(
        "SELECT note, status FROM calendar_days WHERE user_id=? AND date=?",
        (user_id, today_date.isoformat()),
    )
    row = cur.fetchone()
    if row:
        lines.append(f"Статус: {row['status']}")
        if row.get("note"):
            lines.append(f"Комментарий: {row['note']}")

    cur = conn.execute(
        "SELECT date, weight, waist, belly, biceps, chest FROM progress_logs "
        "WHERE user_id=? ORDER BY date DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        lines.append(
            f"Последний прогресс ({row['date']}): вес {row['weight']}, талия {row['waist']}, "
            f"живот {row['belly']}, бицепс {row['biceps']}, грудь {row['chest']}"
        )

    reminders = _normalize_reminders(settings.get("reminders") or {})
    rem_lines = []
    for key in REMINDER_TYPES.keys():
        cfg_item = reminders.get(key)
        if cfg_item and cfg_item.get("enabled") and cfg_item.get("time"):
            rem_lines.append(f"{key}: {cfg_item['time']}")
    if rem_lines:
        lines.append("Напоминания: " + ", ".join(rem_lines))

    if settings.get("ai_enabled", 1) and cfg.openai_api_key:
        try:
            advice_text = generate_advice(cfg.openai_api_key, _build_ai_context(conn, user_id))
            _store_advice(conn, user_id, today_date, advice_text)
            lines.append("ИИ‑совет: " + advice_text)
        except Exception as exc:
            update_settings(conn, user_id, ai_enabled=0)
            lines.append(f"ИИ‑совет: выключен (ошибка: {exc})")

    return "\n".join(lines)


def _build_weekly_pdf(conn, user_id: int, cfg) -> str:
    today_date = _get_today(cfg.timezone)
    week = _get_weekly_stats(conn, user_id, today_date)
    stats_lines = _stats_lines(week, today_date)

    cur = conn.execute(
        """
        SELECT date, weight, waist, belly, biceps, chest
        FROM progress_logs
        WHERE user_id=?
        ORDER BY date ASC
        LIMIT 90
        """,
        (user_id,),
    )
    progress_rows = [dict(r) for r in cur.fetchall()]

    year, month = today_date.year, today_date.month
    cur = conn.execute(
        "SELECT date, status, day_type FROM calendar_days WHERE user_id=? AND date LIKE ?",
        (user_id, f\"{year:04d}-{month:02d}%\"),
    )
    statuses = {}
    for row in cur.fetchall():
        d = int(row["date"].split("-")[2])
        status = row["status"]
        if row["day_type"] == "rest" and status == "planned":
            status = "rest"
        statuses[d] = status

    pdf_path = temp_pdf_path(\"weekly_\")
    generate_weekly_pdf(
        pdf_path,
        title=f\"Отчет за неделю ({today_date.isoformat()})\",
        stats_lines=stats_lines,
        progress_rows=progress_rows,
        attendance_statuses=statuses,
        year=year,
        month=month,
    )
    return str(pdf_path)


def _store_advice(conn, user_id: int, day: date, advice_text: str) -> None:
    conn.execute(
        "UPDATE calendar_days SET ai_advice=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND date=?",
        (advice_text, user_id, day.isoformat()),
    )
    conn.commit()


@router.message(Command("advice"))
async def advice(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)

    if not settings.get("ai_enabled", 1):
        await message.answer("ИИ‑советы выключены в настройках.")
        return
    if not cfg.openai_api_key:
        await message.answer("Нет OPENAI_API_KEY в .env, советы пока недоступны.")
        return

    context = _build_ai_context(conn, user_id)
    await message.answer("Секунду, генерирую совет...")
    try:
        advice_text = generate_advice(cfg.openai_api_key, context)
    except Exception as exc:
        update_settings(conn, user_id, ai_enabled=0)
        await message.answer(f"ИИ‑советы выключены: {exc}")
        return

    _store_advice(conn, user_id, _get_today(cfg.timezone), advice_text)
    await message.answer(advice_text)


@router.callback_query(F.data == "advice")
async def advice_button(call: CallbackQuery) -> None:
    message = call.message
    if message:
        await advice(message)
    await call.answer()


@router.message(Command("ai"))
async def ai_toggle(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )
    settings = get_settings(conn, user_id)

    text = message.text.strip().lower()
    if "on" in text or "вкл" in text:
        update_settings(conn, user_id, ai_enabled=1)
        await message.answer("ИИ‑советы включены.")
        return
    if "off" in text or "выкл" in text:
        update_settings(conn, user_id, ai_enabled=0)
        await message.answer("ИИ‑советы выключены.")
        return

    status = "включены" if settings.get("ai_enabled", 1) else "выключены"
    await message.answer(f"Сейчас советы {status}. Команда: /ai on или /ai off")


@router.message(Command("startdate"))
async def set_start_date(message: Message) -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        message.from_user.id,
        message.from_user.full_name,
        cfg.timezone,
        chat_id=message.chat.id,
    )

    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Использование: /startdate 2026-02-02 или /startdate today")
        return

    val = parts[1].lower()
    if val == "today":
        start = _get_today(cfg.timezone)
    else:
        try:
            start = datetime.fromisoformat(val).date()
        except ValueError:
            await message.answer("Неверный формат даты. Пример: 2026-02-02")
            return

    update_settings(conn, user_id, start_date=start.isoformat())
    await message.answer(f"Стартовая дата установлена: {start.isoformat()}")


@router.message(Command("admin"))
async def admin_menu(message: Message) -> None:
    cfg = load_config()
    if not _is_admin(cfg, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="Синхронизировать план", callback_data="admin:syncplan")
    kb.button(text="AI вкл/выкл", callback_data="admin:ai_toggle")
    kb.button(text="Ежедневный отчет вкл/выкл", callback_data="admin:daily_toggle")
    kb.button(text="Weekly PDF вкл/выкл", callback_data="admin:weekly_toggle")
    kb.button(text="Тест: ежедневный отчет", callback_data="admin:test_daily")
    kb.button(text="Тест: PDF отчет", callback_data="admin:test_pdf")
    kb.adjust(2, 2, 2)
    await message.answer("Админ‑панель:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("admin:"))
async def admin_action(call: CallbackQuery) -> None:
    cfg = load_config()
    if not _is_admin(cfg, call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    conn = get_conn(cfg.db_dsn)
    init_db(conn)
    user_id = get_or_create_user(
        conn,
        call.from_user.id,
        call.from_user.full_name,
        cfg.timezone,
        chat_id=call.message.chat.id if call.message else None,
    )

    action = call.data.split(":", 1)[1]
    settings = get_settings(conn, user_id)
    reminders = settings.get("reminders") or {}

    if action == "syncplan":
        try:
            plan = sync_plan_from_sheets(
                SheetConfig(
                    sheet_id=cfg.sheet_id or "",
                    gid_plan=str(cfg.sheet_gid_plan or ""),
                    gid_macros=str(cfg.sheet_gid_macros or ""),
                    gid_cycle=str(cfg.sheet_gid_cycle or ""),
                )
            )
            pending_path = cfg.plan_path.with_suffix(".pending.yaml")
            write_plan_yaml(plan, str(pending_path))
            await call.message.answer("План загружен. Применить: /syncplan apply")
        except Exception as exc:
            await call.message.answer(f"Ошибка синхронизации: {exc}")
        await call.answer()
        return

    if action == "ai_toggle":
        new_value = 0 if settings.get("ai_enabled", 1) else 1
        update_settings(conn, user_id, ai_enabled=new_value)
        await call.message.answer(f"ИИ‑советы {'включены' if new_value else 'выключены'}.")
        await call.answer()
        return

    if action == "daily_toggle":
        cfg_item = _get_report_cfg(reminders, "daily_report")
        cfg_item["enabled"] = not bool(cfg_item.get("enabled"))
        reminders["daily_report"] = cfg_item
        update_settings(conn, user_id, reminders=reminders)
        _schedule_user_reports(conn, user_id, cfg.timezone)
        await call.message.answer(f"Ежедневный отчет {'включен' if cfg_item['enabled'] else 'выключен'}.")
        await call.answer()
        return

    if action == "weekly_toggle":
        cfg_item = _get_report_cfg(reminders, "weekly_pdf")
        cfg_item["enabled"] = not bool(cfg_item.get("enabled"))
        reminders["weekly_pdf"] = cfg_item
        update_settings(conn, user_id, reminders=reminders)
        _schedule_user_reports(conn, user_id, cfg.timezone)
        await call.message.answer(f"Weekly PDF {'включен' if cfg_item['enabled'] else 'выключен'}.")
        await call.answer()
        return

    if action == "test_daily":
        report_text = await _build_daily_report(conn, user_id, cfg)
        await call.message.answer(report_text)
        await call.answer()
        return

    if action == "test_pdf":
        pdf_path = _build_weekly_pdf(conn, user_id, cfg)
        await call.message.answer_document(FSInputFile(pdf_path))
        await call.answer()
        return

    await call.answer("Неизвестная команда", show_alert=True)

async def main() -> None:
    cfg = load_config()
    conn = get_conn(cfg.db_dsn)
    init_db(conn)

    bot = Bot(token=cfg.bot_token)
    global BOT_REF, SCHEDULER
    BOT_REF = bot

    if SCHEDULER is None:
        SCHEDULER = AsyncIOScheduler(timezone=cfg.timezone)
        SCHEDULER.start()
    if not SCHEDULER.get_job("progressions:daily"):
        SCHEDULER.add_job(
            _apply_progressions_for_all_users,
            CronTrigger(hour=6, minute=0, timezone=cfg.timezone),
            id="progressions:daily",
            replace_existing=True,
        )
    _schedule_all_reminders(conn, cfg.timezone)

    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
