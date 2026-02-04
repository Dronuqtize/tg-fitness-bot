from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any

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


def _fetch_csv_rows(url: str) -> list[dict[str, str]]:
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    buf = io.StringIO(resp.text)
    reader = csv.DictReader(buf)
    return [dict(row) for row in reader]


def _clean_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def sync_plan_from_sheets(cfg: SheetConfig) -> dict[str, Any]:
    plan_rows = _fetch_csv_rows(_csv_url(cfg.sheet_id, cfg.gid_plan))
    macros_rows = _fetch_csv_rows(_csv_url(cfg.sheet_id, cfg.gid_macros))
    cycle_rows = _fetch_csv_rows(_csv_url(cfg.sheet_id, cfg.gid_cycle))

    workouts: dict[str, Any] = {}
    for row in plan_rows:
        workout_key = _clean_value(row.get("workout_key"))
        title = _clean_value(row.get("title"))
        level = _clean_value(row.get("level")).lower()
        name = _clean_value(row.get("name"))
        sets = _clean_value(row.get("sets"))
        reps = _clean_value(row.get("reps"))
        weight = _clean_value(row.get("weight"))

        if not workout_key or not level or not name:
            continue
        if workout_key not in workouts:
            workouts[workout_key] = {"title": title or workout_key, "easy": [], "medium": [], "hard": []}
        if title:
            workouts[workout_key]["title"] = title
        workouts[workout_key].setdefault(level, []).append(
            {
                "name": name,
                "sets": int(sets) if sets.isdigit() else sets,
                "reps": reps,
                "weight": weight,
            }
        )

    macros: dict[str, Any] = {"train": {}, "rest": {}}
    for row in macros_rows:
        day_type = _clean_value(row.get("day_type")).lower()
        if day_type not in ("train", "rest"):
            continue
        macros[day_type] = {
            "kcal": int(_clean_value(row.get("kcal")) or 0),
            "protein": int(_clean_value(row.get("protein")) or 0),
            "fat": int(_clean_value(row.get("fat")) or 0),
            "carbs": int(_clean_value(row.get("carbs")) or 0),
        }

    cycle_order: list[str] = []
    for row in cycle_rows:
        key = _clean_value(row.get("workout_key"))
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
