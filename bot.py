import os, json, uuid, asyncio, httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ApplicationBuilder, InlineQueryHandler, CommandHandler, ContextTypes

# ================== Настройки ==================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
BOT_URL = os.environ.get("BOT_URL")  # Например: https://school-schedule-bot.onrender.com
WEBHOOK_PATH = f"/webhook/{TOKEN}"

if not TOKEN or not BOT_URL:
    raise RuntimeError("Не заданы переменные окружения TELEGRAM_TOKEN или BOT_URL")

# ================== Загрузка расписания ==================
with open("schedule.json", "r", encoding="utf-8") as f:
    schedule = json.load(f)

DAY_MAP = {
    "Monday": "Понедельник",
    "Tuesday": "Вторник",
    "Wednesday": "Среда",
    "Thursday": "Четверг",
    "Friday": "Пятница",
    "Saturday": "Суббота",
    "Sunday": "Воскресенье"
}

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
        "Используй inline-запрос: @rasp7V_bot today / tomorrow / week"
    )

# ================== FastAPI ==================
app = FastAPI()
bot_app = ApplicationBuilder().token(TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(InlineQueryHandler(inline_schedule))

# ================== Webhook endpoint ==================
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    # ✅ Новый способ вместо update_queue:
    await bot_app.process_update(update)
    return {"ok": True}

# ================== Lifespan ==================
@app.on_event("startup")
async def startup_event():
    await bot_app.initialize()
    await bot_app.bot.set_webhook(f"{BOT_URL.rstrip('/')}{WEBHOOK_PATH}")
    await bot_app.start()
    print("✅ Webhook установлен, бот готов к работе")

    # ================== Ping self ==================
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
