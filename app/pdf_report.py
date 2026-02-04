from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from app.calendar_image import render_attendance_table
from app.charts import render_progress_chart


def generate_weekly_pdf(
    output_path: Path,
    title: str,
    stats_lines: list[str],
    progress_rows: list[dict[str, Any]],
    attendance_statuses: dict[int, str],
    year: int,
    month: int,
) -> Path:
    c = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, height - 2 * cm, title)

    c.setFont("Helvetica", 10)
    y = height - 3 * cm
    for line in stats_lines:
        c.drawString(2 * cm, y, line)
        y -= 0.5 * cm

    # Chart
    if progress_rows:
        chart_path = render_progress_chart(progress_rows)
        c.drawImage(str(chart_path), 2 * cm, height - 14 * cm, width=16 * cm, height=6 * cm)

    # Attendance
    attendance_path = render_attendance_table(year, month, attendance_statuses)
    c.drawImage(str(attendance_path), 2 * cm, height - 23 * cm, width=16 * cm, height=7 * cm)

    c.showPage()
    c.save()
    return output_path


def temp_pdf_path(prefix: str = "report") -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix=prefix)
    return Path(tmp.name)
