import os, json, uuid, asyncio, httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import (
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

# ================== Inline-запрос ==================
async def inline_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.lower().strip()
    if not query:
        await update.inline_query.answer([], cache_time=0)
        return

    results = []

    if query in ["today", "сегодня"]:
        day_eng = datetime.today().strftime("%A")
        day = DAY_MAP.get(day_eng, "Сегодня")
        lessons = schedule.get(day, ["Сегодня нет занятий"])
        text = "\n".join(lessons)
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Расписание на сегодня ({day})",
            input_message_content=InputTextMessageContent(text)
        ))

    elif query in ["tomorrow", "завтра"]:
        day_eng = (datetime.today() + timedelta(days=1)).strftime("%A")
        day = DAY_MAP.get(day_eng, "Завтра")
        lessons = schedule.get(day, ["Завтра нет занятий"])
        text = "\n".join(lessons)
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Расписание на завтра ({day})",
            input_message_content=InputTextMessageContent(text)
        ))

    elif query in ["week", "неделя"]:
        text = ""
        for day, lessons in schedule.items():
            text += f"{day}:\n" + "\n".join(lessons) + "\n\n"
        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="Расписание на неделю",
            input_message_content=InputTextMessageContent(text.strip())
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
        "Чтобы очистить день — отправь слово: пусто\n"
        "Чтобы отменить — /cancel",
    )
    return EDIT_ENTER_LESSONS

async def edit_schedule_lessons_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("У вас нет прав на редактирование расписания.")
        return ConversationHandler.END

    day = context.user_data.get("edit_day")
    if not day:
        await update.message.reply_text("Сессия редактирования потеряна. Запусти заново: /edit_schedule")
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Сообщение пустое. Пришли список уроков или «пусто».")
        return EDIT_ENTER_LESSONS

    if text.lower() in {"пусто", "нет", "clear"}:
        lessons = []
    else:
        lessons = [line.strip() for line in text.splitlines() if line.strip()]

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

edit_conv = ConversationHandler(
    entry_points=[CommandHandler("edit_schedule", edit_schedule_start)],
    states={
        EDIT_CHOOSE_DAY: [CallbackQueryHandler(edit_schedule_day_chosen, pattern=r"^edit_")],
        EDIT_ENTER_LESSONS: [
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
