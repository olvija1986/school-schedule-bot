import os, json, uuid, asyncio, httpx, html, re
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from zoneinfo import ZoneInfo
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ================== Настройки ==================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
BOT_URL = os.environ.get("BOT_URL")  # например: https://school-schedule-bot.onrender.com
WEBHOOK_PATH = f"/webhook/{TOKEN}"

if not TOKEN or not BOT_URL:
    raise RuntimeError("Не заданы переменные окружения TELEGRAM_TOKEN или BOT_URL")

# Необязательно: ограничение доступа к редактированию расписания
# Формат: "12345,67890"
_ADMIN_USER_IDS_RAW = (os.environ.get("ADMIN_USER_IDS") or "").strip()
ADMIN_USER_IDS = {
    int(x.strip())
    for x in _ADMIN_USER_IDS_RAW.split(",")
    if x.strip().isdigit()
}

# ================== Загрузка расписания ==================
with open("schedule.json", "r", encoding="utf-8") as f:
    schedule = json.load(f)

SUBSCRIPTIONS_PATH = "subscriptions.json"
subscriptions: dict[str, dict] = {}
scheduler: AsyncIOScheduler | None = None

SCHEDULE_DAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

DAY_MAP = {
    "Monday": "Понедельник",
    "Tuesday": "Вторник",
    "Wednesday": "Среда",
    "Thursday": "Четверг",
    "Friday": "Пятница",
    "Saturday": "Суббота",
    "Sunday": "Воскресенье"
}

_LESSON_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})\s+(?P<rest>.+?)\s*$"
)

def _load_subscriptions_from_disk() -> None:
    global subscriptions
    try:
        with open(SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            subscriptions = data
        else:
            subscriptions = {}
    except FileNotFoundError:
        subscriptions = {}
    except Exception:
        subscriptions = {}

def _save_subscriptions_to_disk() -> None:
    tmp_path = f"{SUBSCRIPTIONS_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(subscriptions, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, SUBSCRIPTIONS_PATH)

def _is_admin(update: Update) -> bool:
    if not ADMIN_USER_IDS:
        # Если админы не настроены — разрешаем всем (удобно для личного бота)
        return True
    user = update.effective_user
    return bool(user and user.id in ADMIN_USER_IDS)

def _save_schedule_to_disk() -> None:
    tmp_path = "schedule.json.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(tmp_path, "schedule.json")

def _parse_lesson_line(line: str) -> dict:
    raw = (line or "").strip()
    if not raw:
        return {"start": "", "end": "", "subject": "", "room": "", "raw": ""}

    m = _LESSON_RE.match(raw)
    if m:
        start = m.group("start")
        end = m.group("end")
        rest = m.group("rest").strip()
    else:
        start = ""
        end = ""
        rest = raw

    if "/" in rest:
        parts = [p.strip() for p in rest.split("/") if p.strip()]
        subject = parts[0] if parts else rest
        room = "/".join(parts[1:]) if len(parts) > 1 else ""
    else:
        subject = rest
        room = ""

    return {"start": start, "end": end, "subject": subject, "room": room, "raw": raw}

def _truncate(text: str, width: int) -> str:
    text = text or ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"

def _format_day_table_html(day: str, lessons: list[str]) -> str:
    rows = []
    for idx, line in enumerate(lessons or [], start=1):
        p = _parse_lesson_line(line)
        rows.append(
            {
                "n": str(idx),
                "start": p["start"],
                "end": p["end"],
                "subject": p["subject"],
                "room": p["room"],
            }
        )

    # ширины колонок (чтобы не разъезжалось в Telegram)
    n_w = max(1, min(2, max((len(r["n"]) for r in rows), default=1)))
    start_w = 5
    end_w = 5
    room_w = max(3, min(12, max((len(r["room"]) for r in rows), default=3)))
    subject_w = max(10, min(28, max((len(r["subject"]) for r in rows), default=10)))

    header = (
        f"{'#':<{n_w}}  "
        f"{'Нач':<{start_w}}  "
        f"{'Кон':<{end_w}}  "
        f"{'Предмет':<{subject_w}}  "
        f"{'Каб':<{room_w}}"
    )
    sep = (
        f"{'-'*n_w}  "
        f"{'-'*start_w}  "
        f"{'-'*end_w}  "
        f"{'-'*subject_w}  "
        f"{'-'*room_w}"
    )

    lines = [header, sep]
    if not rows:
        lines.append(
            f"{'':<{n_w}}  {'':<{start_w}}  {'':<{end_w}}  "
            f"{_truncate('Нет занятий', subject_w):<{subject_w}}  {'':<{room_w}}"
        )
    else:
        for r in rows:
            subj = _truncate(r["subject"], subject_w)
            room = _truncate(r["room"], room_w)
            lines.append(
                f"{r['n']:<{n_w}}  "
                f"{r['start']:<{start_w}}  "
                f"{r['end']:<{end_w}}  "
                f"{subj:<{subject_w}}  "
                f"{room:<{room_w}}"
            )

    pre = html.escape("\n".join(lines))
    return f"<b>{html.escape(day)}</b>\n<pre>{pre}</pre>"

def _get_tz() -> ZoneInfo:
    # Можно переопределить в Render: TZ=Etc/GMT-5 (UTC+5) или любая IANA TZ
    # Важно: у Etc/GMT-5 "минус" означает UTC+5 (так устроена зона Etc/*)
    name = (os.environ.get("TZ") or "Etc/GMT-5").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")

def _parse_hhmm(s: str) -> tuple[int, int] | None:
    s = (s or "").strip()
    m = re.match(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$", s)
    if not m:
        return None
    h = int(m.group("h"))
    mi = int(m.group("m"))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi

async def _send_daily_reminder(chat_id: int):
    # Расписание на сегодня
    day_eng = datetime.now(tz=_get_tz()).strftime("%A")
    day = DAY_MAP.get(day_eng, "Сегодня")
    lessons = schedule.get(day, [])
    text = _format_day_table_html(day, lessons)
    await bot_app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

def _job_id_for(user_id: int) -> str:
    return f"reminder:{user_id}"

def _reschedule_user(user_id: int):
    global scheduler
    if scheduler is None:
        return
    entry = subscriptions.get(str(user_id))
    job_id = _job_id_for(user_id)
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    if not entry:
        return
    time_str = entry.get("time", "")
    parsed = _parse_hhmm(time_str)
    if not parsed:
        return
    hour, minute = parsed
    chat_id = int(entry.get("chat_id"))
    trigger = CronTrigger(hour=hour, minute=minute, timezone=_get_tz())
    scheduler.add_job(
        _send_daily_reminder,
        trigger=trigger,
        args=[chat_id],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,  # если сервис был оффлайн, попытаться догнать в течение часа
        coalesce=True,
    )

# ================== Inline-запрос ==================
async def inline_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.lower().strip()
    if not query:
        # Подсказки, когда пользователь только открыл inline-режим
        day_eng = datetime.today().strftime("%A")
        today_day = DAY_MAP.get(day_eng, "Сегодня")
        today_lessons = schedule.get(today_day, [])

        tomorrow_eng = (datetime.today() + timedelta(days=1)).strftime("%A")
        tomorrow_day = DAY_MAP.get(tomorrow_eng, "Завтра")
        tomorrow_lessons = schedule.get(tomorrow_day, [])

        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"Сегодня ({today_day})",
                description="Подсказка: запрос today / сегодня",
                input_message_content=InputTextMessageContent(
                    _format_day_table_html(today_day, today_lessons),
                    parse_mode="HTML",
                ),
            ),
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"Завтра ({tomorrow_day})",
                description="Подсказка: запрос tomorrow / завтра",
                input_message_content=InputTextMessageContent(
                    _format_day_table_html(tomorrow_day, tomorrow_lessons),
                    parse_mode="HTML",
                ),
            ),
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="Неделя",
                description="Подсказка: запрос week / неделя",
                input_message_content=InputTextMessageContent(
                    "\n\n".join(
                        _format_day_table_html(day, schedule.get(day, []))
                        for day in SCHEDULE_DAYS
                        if day in schedule
                    )
                    or _format_day_table_html("Неделя", []),
                    parse_mode="HTML",
                ),
            ),
        ]
        await update.inline_query.answer(results, cache_time=0)
        return

    results = []

    if query in ["today", "сегодня"]:
        day_eng = datetime.today().strftime("%A")
        day = DAY_MAP.get(day_eng, "Сегодня")
        lessons = schedule.get(day, [])
        text = _format_day_table_html(day, lessons)
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Расписание на сегодня ({day})",
            input_message_content=InputTextMessageContent(text, parse_mode="HTML")
        ))

    elif query in ["tomorrow", "завтра"]:
        day_eng = (datetime.today() + timedelta(days=1)).strftime("%A")
        day = DAY_MAP.get(day_eng, "Завтра")
        lessons = schedule.get(day, [])
        text = _format_day_table_html(day, lessons)
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Расписание на завтра ({day})",
            input_message_content=InputTextMessageContent(text, parse_mode="HTML")
        ))

    elif query in ["week", "неделя"]:
        text = "\n\n".join(
            _format_day_table_html(day, schedule.get(day, []))
            for day in SCHEDULE_DAYS
            if day in schedule
        ) or _format_day_table_html("Неделя", [])
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="Расписание на неделю",
            input_message_content=InputTextMessageContent(text.strip(), parse_mode="HTML")
        ))

    else:
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="Введите today, tomorrow или week",
            input_message_content=InputTextMessageContent("Введите: today / tomorrow / week")
        ))

    await update.inline_query.answer(results, cache_time=0)

# ================== Команда /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для школьного расписания.\n"
        "Используй inline-запрос: @rasp7V_bot today / tomorrow / week\n"
        "Для админов: /edit_schedule — редактировать расписание"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/start — приветствие\n"
        "/help — помощь\n"
        "/edit_schedule — редактировать расписание (если разрешено)\n"
        "/cancel — отменить редактирование\n\n"
        "Напоминания:\n"
        "/subscribe 07:30 — присылать расписание каждый день в указанное время\n"
        "/unsubscribe — отключить напоминания\n\n"
        "Inline-режим:\n"
        "Набери @бота и выбери подсказку или введи: today / tomorrow / week\n\n"
        "Редактирование в группе с privacy mode:\n"
        "После выбора дня можно отправить:\n"
        "/set <список уроков, каждый с новой строки>\n"
        "или /set пусто"
    )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Формат: /subscribe HH:MM (например /subscribe 07:30)")
        return
    parsed = _parse_hhmm(context.args[0])
    if not parsed:
        await update.message.reply_text("Неверное время. Формат: HH:MM (например 07:30)")
        return
    hh, mm = parsed
    t = f"{hh:02d}:{mm:02d}"
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        await update.message.reply_text("Не удалось определить пользователя/чат.")
        return

    subscriptions[str(user.id)] = {"chat_id": chat.id, "time": t}
    _save_subscriptions_to_disk()
    _reschedule_user(user.id)
    await update.message.reply_text(
        f"Ок! Буду присылать расписание каждый день в {t} (TZ={_get_tz().key}).\n"
        "Важно: на Render free напоминания приходят только пока сервис не спит. Keep-alive помогает."
    )

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not user:
        await update.message.reply_text("Не удалось определить пользователя.")
        return
    subscriptions.pop(str(user.id), None)
    _save_subscriptions_to_disk()
    if scheduler is not None:
        try:
            scheduler.remove_job(_job_id_for(user.id))
        except Exception:
            pass
    await update.message.reply_text("Готово. Напоминания отключены.")

# ================== Редактирование расписания (/edit_schedule) ==================
EDIT_CHOOSE_DAY, EDIT_ENTER_LESSONS, EDIT_CONFIRM = range(3)

def _day_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, day in enumerate(SCHEDULE_DAYS, start=1):
        row.append(InlineKeyboardButton(day, callback_data=f"edit_day:{day}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Отмена", callback_data="edit_cancel")])
    return InlineKeyboardMarkup(rows)

async def edit_schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("У вас нет прав на редактирование расписания.")
        return ConversationHandler.END

    context.user_data.pop("edit_day", None)
    context.user_data.pop("edit_lessons", None)

    await update.message.reply_text(
        "Выбери день недели, который нужно изменить.",
        reply_markup=_day_keyboard(),
    )
    return EDIT_CHOOSE_DAY

async def edit_schedule_day_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        await query.edit_message_text("У вас нет прав на редактирование расписания.")
        return ConversationHandler.END

    data = query.data or ""
    if data == "edit_cancel":
        await query.edit_message_text("Редактирование отменено.")
        return ConversationHandler.END

    if not data.startswith("edit_day:"):
        await query.edit_message_text("Не понял выбор дня. Попробуй ещё раз: /edit_schedule")
        return ConversationHandler.END

    day = data.split("edit_day:", 1)[1].strip()
    if day not in SCHEDULE_DAYS:
        await query.edit_message_text("Некорректный день. Попробуй ещё раз: /edit_schedule")
        return ConversationHandler.END

    context.user_data["edit_day"] = day
    current = schedule.get(day, [])
    current_text = "\n".join(current) if current else "— (пусто) —"

    await query.edit_message_text(
        f"Текущие занятия для «{day}»:\n{current_text}\n\n"
        "Пришли новое расписание одним сообщением: по одной строке на урок.\n"
        "Если ты делаешь это в группе и у бота включён privacy mode — отправь так:\n"
        "/set <каждая строка = один урок>\n"
        "Чтобы очистить день — отправь слово: пусто\n"
        "Чтобы отменить — /cancel",
    )
    return EDIT_ENTER_LESSONS

def _parse_lessons_from_text(text: str) -> list[str] | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() in {"пусто", "нет", "clear"}:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]

async def edit_schedule_lessons_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("У вас нет прав на редактирование расписания.")
        return ConversationHandler.END

    day = context.user_data.get("edit_day")
    if not day:
        await update.message.reply_text("Сессия редактирования потеряна. Запусти заново: /edit_schedule")
        return ConversationHandler.END

    lessons = _parse_lessons_from_text(update.message.text or "")
    if lessons is None:
        await update.message.reply_text("Сообщение пустое. Пришли список уроков или «пусто».")
        return EDIT_ENTER_LESSONS

    context.user_data["edit_lessons"] = lessons
    preview = "\n".join(lessons) if lessons else "— (пусто) —"

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Сохранить", callback_data="edit_confirm"),
                InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel"),
            ]
        ]
    )

    await update.message.reply_text(
        f"Проверь, что всё верно для «{day}»:\n{preview}",
        reply_markup=keyboard,
    )
    return EDIT_CONFIRM

async def edit_schedule_lessons_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /set для групп с privacy mode (обычные сообщения могут не доходить)."""
    if not _is_admin(update):
        await update.message.reply_text("У вас нет прав на редактирование расписания.")
        return ConversationHandler.END

    day = context.user_data.get("edit_day")
    if not day:
        await update.message.reply_text("Сессия редактирования потеряна. Запусти заново: /edit_schedule")
        return ConversationHandler.END

    raw = update.message.text or ""
    parts = raw.split(None, 1)
    payload = parts[1] if len(parts) > 1 else ""
    lessons = _parse_lessons_from_text(payload)
    if lessons is None:
        await update.message.reply_text(
            "После /set нужно прислать список уроков (каждый с новой строки) или слово «пусто».\n"
            "Пример:\n"
            "/set 13:30-14:10 Математика/211\n"
            "14:20-15:00 Информатика/304"
        )
        return EDIT_ENTER_LESSONS

    # Переиспользуем общий шаг предпросмотра/подтверждения
    context.user_data["edit_lessons"] = lessons
    preview = "\n".join(lessons) if lessons else "— (пусто) —"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Сохранить", callback_data="edit_confirm"),
                InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel"),
            ]
        ]
    )
    await update.message.reply_text(
        f"Проверь, что всё верно для «{day}»:\n{preview}",
        reply_markup=keyboard,
    )
    return EDIT_CONFIRM

async def edit_schedule_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        await query.edit_message_text("У вас нет прав на редактирование расписания.")
        return ConversationHandler.END

    data = query.data or ""
    if data == "edit_cancel":
        await query.edit_message_text("Редактирование отменено.")
        return ConversationHandler.END

    if data != "edit_confirm":
        await query.edit_message_text("Не понял ответ. Попробуй ещё раз: /edit_schedule")
        return ConversationHandler.END

    day = context.user_data.get("edit_day")
    lessons = context.user_data.get("edit_lessons")
    if day is None or lessons is None:
        await query.edit_message_text("Сессия редактирования потеряна. Запусти заново: /edit_schedule")
        return ConversationHandler.END

    schedule[day] = lessons
    try:
        _save_schedule_to_disk()
    except Exception as e:
        await query.edit_message_text(f"Не удалось сохранить расписание: {e}")
        return ConversationHandler.END

    await query.edit_message_text(f"Готово! Расписание для «{day}» обновлено.")
    return ConversationHandler.END

async def edit_schedule_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Ок, отменил.")
    return ConversationHandler.END

# ================== FastAPI ==================
app = FastAPI()
bot_app = ApplicationBuilder().token(TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("help", help_command))
bot_app.add_handler(CommandHandler("subscribe", subscribe))
bot_app.add_handler(CommandHandler("unsubscribe", unsubscribe))

edit_conv = ConversationHandler(
    entry_points=[CommandHandler("edit_schedule", edit_schedule_start)],
    states={
        EDIT_CHOOSE_DAY: [CallbackQueryHandler(edit_schedule_day_chosen, pattern=r"^edit_")],
        EDIT_ENTER_LESSONS: [
            CommandHandler("set", edit_schedule_lessons_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_schedule_lessons_entered)
        ],
        EDIT_CONFIRM: [CallbackQueryHandler(edit_schedule_confirm, pattern=r"^edit_")],
    },
    fallbacks=[CommandHandler("cancel", edit_schedule_cancel)],
)
bot_app.add_handler(edit_conv)
bot_app.add_handler(InlineQueryHandler(inline_schedule))

# ================== Webhook endpoint ==================
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

# ================== Lifespan ==================
@app.on_event("startup")
async def startup_event():
    await bot_app.initialize()
    await bot_app.bot.set_webhook(f"{BOT_URL.rstrip('/')}{WEBHOOK_PATH}")
    # Подсказки команд в интерфейсе Telegram (меню при вводе "/")
    await bot_app.bot.set_my_commands(
        [
            BotCommand("start", "Запуск / приветствие"),
            BotCommand("help", "Подсказки и помощь"),
            BotCommand("edit_schedule", "Редактировать расписание"),
            BotCommand("subscribe", "Ежедневное напоминание (HH:MM)"),
            BotCommand("unsubscribe", "Отключить напоминания"),
            BotCommand("cancel", "Отменить редактирование"),
        ]
    )

    # Планировщик напоминаний
    global scheduler
    scheduler = AsyncIOScheduler(timezone=_get_tz())
    _load_subscriptions_from_disk()
    for user_id_str in list(subscriptions.keys()):
        if user_id_str.isdigit():
            _reschedule_user(int(user_id_str))
    scheduler.start()

    await bot_app.start()
    print("✅ Webhook установлен, бот готов к работе")

    # --- Автопинг для Render ---
    async def ping_self():
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    resp = await client.get(BOT_URL)
                    print(f"[ping] {resp.status_code} {datetime.now().strftime('%H:%M:%S')}")
                except Exception as e:
                    print(f"[ping error] {e}")
                await asyncio.sleep(600)  # каждые 10 минут

    asyncio.create_task(ping_self())

@app.on_event("shutdown")
async def shutdown_event():
    await bot_app.stop()
    await bot_app.shutdown()
    print("🛑 Бот остановлен")

# ================== Стартовая страница ==================
@app.get("/")
def root():
    return {"status": "Bot is running ✅"}
