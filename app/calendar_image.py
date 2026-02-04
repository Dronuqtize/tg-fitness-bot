from __future__ import annotations

import calendar
import tempfile
from datetime import date
from pathlib import Path
from typing import Dict

from PIL import Image, ImageDraw, ImageFont


STATUS_COLORS = {
    "done": (76, 175, 80),
    "planned": (66, 165, 245),
    "skipped": (239, 83, 80),
    "rest": (255, 193, 7),
}


def render_month_calendar(year: int, month: int, statuses: Dict[int, str]) -> Path:
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(year, month)

    cell_w = 90
    cell_h = 70
    padding = 20
    header_h = 50
    width = padding * 2 + cell_w * 7
    height = padding * 2 + header_h + cell_h * len(weeks)

    img = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    title = f"{calendar.month_name[month]} {year}"
    draw.text((padding, padding), title, fill=(30, 30, 30), font=font)

    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, wd in enumerate(weekdays):
        x = padding + i * cell_w + 5
        y = padding + header_h - 25
        draw.text((x, y), wd, fill=(80, 80, 80), font=font)

    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            x0 = padding + col * cell_w
            y0 = padding + header_h + row * cell_h
            x1 = x0 + cell_w - 5
            y1 = y0 + cell_h - 5
            if day != 0:
                status = statuses.get(day)
                if status:
                    color = STATUS_COLORS.get(status, (200, 200, 200))
                    draw.rectangle([x0, y0, x1, y1], fill=color)
                else:
                    draw.rectangle([x0, y0, x1, y1], outline=(200, 200, 200))
                draw.text((x0 + 5, y0 + 5), str(day), fill=(20, 20, 20), font=font)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img.save(tmp.name)
    return Path(tmp.name)


def render_attendance_table(year: int, month: int, statuses: Dict[int, str]) -> Path:
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(year, month)

    cell_w = 90
    cell_h = 50
    padding = 20
    header_h = 40
    width = padding * 2 + cell_w * 7
    height = padding * 2 + header_h + cell_h * len(weeks) + 40

    img = Image.new("RGB", (width, height), (248, 248, 248))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    title = f"Табель посещений — {calendar.month_name[month]} {year}"
    draw.text((padding, padding), title, fill=(30, 30, 30), font=font)

    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, wd in enumerate(weekdays):
        x = padding + i * cell_w + 5
        y = padding + header_h - 20
        draw.text((x, y), wd, fill=(80, 80, 80), font=font)

    symbol_map = {
        "done": "✔",
        "skipped": "✘",
        "planned": "·",
        "rest": "R",
    }

    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            x0 = padding + col * cell_w
            y0 = padding + header_h + row * cell_h
            x1 = x0 + cell_w - 5
            y1 = y0 + cell_h - 5
            draw.rectangle([x0, y0, x1, y1], outline=(200, 200, 200))
            if day != 0:
                status = statuses.get(day, "planned")
                symbol = symbol_map.get(status, "·")
                draw.text((x0 + 5, y0 + 5), str(day), fill=(20, 20, 20), font=font)
                draw.text((x0 + 35, y0 + 5), symbol, fill=(20, 20, 20), font=font)

    legend_y = padding + header_h + len(weeks) * cell_h + 10
    legend = "Легенда: ✔ тренировка выполнена, ✘ пропуск, R отдых, · план"
    draw.text((padding, legend_y), legend, fill=(60, 60, 60), font=font)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=\".png\")
    img.save(tmp.name)
    return Path(tmp.name)
