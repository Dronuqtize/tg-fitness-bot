from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any


def load_plan(plan_path: Path) -> dict[str, Any]:
    with plan_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def get_cycle_order(plan: dict[str, Any]) -> list[str]:
    return list(plan.get("cycle_order") or [])


def get_macros(plan: dict[str, Any], day_type: str) -> dict[str, int]:
    macros = (plan.get("macros") or {}).get(day_type, {})
    return {
        "kcal": int(macros.get("kcal", 0)),
        "protein": int(macros.get("protein", 0)),
        "fat": int(macros.get("fat", 0)),
        "carbs": int(macros.get("carbs", 0)),
    }


def get_workout(plan: dict[str, Any], workout_key: str, level: str) -> list[dict[str, Any]]:
    workouts = plan.get("workouts") or {}
    day = workouts.get(workout_key) or {}
    return list(day.get(level) or [])


def get_workout_title(plan: dict[str, Any], workout_key: str) -> str:
    workouts = plan.get("workouts") or {}
    day = workouts.get(workout_key) or {}
    return str(day.get("title", workout_key))
