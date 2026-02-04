from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render_progress_chart(rows: Sequence[dict]) -> Path:
    dates = [r["date"] for r in rows]
    weight = [r.get("weight") for r in rows]
    waist = [r.get("waist") for r in rows]
    belly = [r.get("belly") for r in rows]
    biceps = [r.get("biceps") for r in rows]
    chest = [r.get("chest") for r in rows]

    fig, ax = plt.subplots(figsize=(8, 4))
    if any(w is not None for w in weight):
        ax.plot(dates, weight, label="Вес")
    if any(w is not None for w in waist):
        ax.plot(dates, waist, label="Талия")
    if any(w is not None for w in belly):
        ax.plot(dates, belly, label="Живот")
    if any(w is not None for w in biceps):
        ax.plot(dates, biceps, label="Бицепс")
    if any(w is not None for w in chest):
        ax.plot(dates, chest, label="Грудь")

    ax.set_title("Прогресс")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Значение")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp.name, dpi=150)
    plt.close(fig)
    return Path(tmp.name)
