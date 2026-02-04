# TG Fitness Bot (MVP)

Быстрый старт бота с календарем тренировок, КБЖУ, прогрессом и ИИ‑советами.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройки

Скопируй `.env.example` в `.env` и заполни значения.

```bash
cp .env.example .env
```

## Запуск

```bash
python -m app.bot
```

## Команды

- `/today` — план на сегодня
- `/progress` — запись веса/обмеров
- `/calendar` — календарь месяца
- `/attendance` — табель посещений
- `/chart` — график прогресса
- `/advice` — ИИ‑совет по данным
- `/ai on|off` — включить/выключить советы
- `/reminder` — напоминания (list/set/off)
- `/medlog` — записать лог уколов (без рекомендаций)
- `/startdate` — задать стартовую дату цикла
- `/stats` — статистика за 7 дней
- `/autoprog` — автопрогрессия
- `/syncplan` — синхронизация плана из Google Sheets
- `/dailyreport` — ежедневный отчет (вкл/выкл/время)
- `/weeklypdf` — еженедельный PDF (вкл/выкл/день/время)
- `/pdf` — PDF отчет по кнопке
- `/admin` — админ‑панель

## Напоминания

Примеры:

```bash
/reminder set water 10:00
/reminder set motivation 12:00
/reminder set ai 21:00
/reminder off water
```

## План тренировок

Файл: `data/plan.yaml`

Там ты задаешь:
- `cycle_order` — порядок тренировок
- `macros` — КБЖУ для трен/отдыха
- `workouts` — 3 уровня сложности на каждый день

После редактирования просто перезапусти бота.

## Прогрессия

В тренировочный день нажми кнопку `ДОБАВИТЬ ПРОГРЕССИЮ` и введи:\n
`упражнение | +2 повт` или `упражнение | +2.5 кг`.
Бот запомнит и будет показывать рядом с упражнением.

## Автопрогрессия

Примеры:

```bash
/autoprog set chest_shoulders | Жим штанги лёжа | +2.5 кг | 7
/autoprog list
```

Автопрогрессия применяется раз в `N` дней и автоматически добавляется в план.

## График и табель

- `/chart` — график веса и обмеров
- `/attendance` — табель посещений по месяцам

## Postgres

Если используешь облако (Render), задай `DB_URL`:

```bash
DB_URL=postgresql://user:pass@host:5432/dbname
```

SQLite автоматически используется, если `DB_URL` не задан.

## Render

Если сборка падает на Python 3.13, в репозитории есть `render.yaml` с `pythonVersion: 3.11.9`.

## Google Sheets

В `.env` укажи:

```
SHEET_ID=...
SHEET_GID_PLAN=...
SHEET_GID_MACROS=...
SHEET_GID_CYCLE=...
ADMIN_IDS=123456789
```

Формат листов:

**PLAN** (gid_plan):
Колонки: `workout_key`, `title`, `level`, `name`, `sets`, `reps`, `weight`

**MACROS** (gid_macros):
Колонки: `day_type` (`train`/`rest`), `kcal`, `protein`, `fat`, `carbs`

**CYCLE** (gid_cycle):
Колонка: `workout_key` (порядок циклов)

Синхронизация:
```
/syncplan <sheet_url_or_id> <gid_plan> <gid_macros> <gid_cycle>
```

## Отчеты

Ежедневный отчет (по умолчанию 23:00 МСК):
```
/dailyreport on
/dailyreport off
/dailyreport time 23:00
```

Еженедельный PDF (по умолчанию воскресенье 20:00 МСК):
```
/weeklypdf on
/weeklypdf off
/weeklypdf time sun 20:00
```

## Админ

Добавь в `.env` список `ADMIN_IDS` (id Telegram через пробел или запятую).
Команда `/admin` откроет панель: синхронизация плана, переключение ИИ и отчетов.
