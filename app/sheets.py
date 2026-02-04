from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
import yaml


@dataclass
class SheetConfig:
    sheet_id: str
    gid_plan: str
    gid_macros: str
    gid_cycle: str


def _csv_url(sheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _fetch_csv(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text))


def sync_plan_from_sheets(cfg: SheetConfig) -> dict[str, Any]:
    plan_df = _fetch_csv(_csv_url(cfg.sheet_id, cfg.gid_plan))
    macros_df = _fetch_csv(_csv_url(cfg.sheet_id, cfg.gid_macros))
    cycle_df = _fetch_csv(_csv_url(cfg.sheet_id, cfg.gid_cycle))

    plan_df = plan_df.fillna("")
    macros_df = macros_df.fillna("")
    cycle_df = cycle_df.fillna("")

    workouts: dict[str, Any] = {}
    for _, row in plan_df.iterrows():
        workout_key = str(row.get("workout_key", "")).strip()
        title = str(row.get("title", "")).strip()
        level = str(row.get("level", "")).strip().lower()
        name = str(row.get("name", "")).strip()
        sets = row.get("sets", "")
        reps = row.get("reps", "")
        weight = row.get("weight", "")

        if not workout_key or not level or not name:
            continue
        if workout_key not in workouts:
            workouts[workout_key] = {"title": title or workout_key, "easy": [], "medium": [], "hard": []}
        if title:
            workouts[workout_key]["title"] = title
        workouts[workout_key].setdefault(level, []).append(
            {
                "name": name,
                "sets": int(sets) if str(sets).isdigit() else str(sets),
                "reps": str(reps),
                "weight": str(weight),
            }
        )

    macros: dict[str, Any] = {"train": {}, "rest": {}}
    for _, row in macros_df.iterrows():
        day_type = str(row.get("day_type", "")).strip().lower()
        if day_type not in ("train", "rest"):
            continue
        macros[day_type] = {
            "kcal": int(row.get("kcal", 0) or 0),
            "protein": int(row.get("protein", 0) or 0),
            "fat": int(row.get("fat", 0) or 0),
            "carbs": int(row.get("carbs", 0) or 0),
        }

    cycle_order: list[str] = []
    for _, row in cycle_df.iterrows():
        key = str(row.get("workout_key", "")).strip()
        if key:
            cycle_order.append(key)

    return {
        "cycle_order": cycle_order,
        "macros": macros,
        "workouts": workouts,
    }


def write_plan_yaml(plan: dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(plan, f, allow_unicode=True, sort_keys=False)
