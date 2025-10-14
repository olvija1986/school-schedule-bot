import os
import json
import uuid
import asyncio
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, InlineQueryHandler, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ================== Настройки ==================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
BOT_URL = os.environ.get("BOT_URL")  # например: https://school-schedule-bot.onrender.com
CHAT_ID = os.environ.get("CHAT_ID")  # ID чата, куда отправлять расписание
WEBHOOK_PATH = f"/webhook/{TOKEN}"

if not TOKEN or not BOT_URL:
    raise RuntimeError("Не заданы TELEGRAM_TOKEN или BOT_URL")
if not CHAT_ID:
    print("⚠️ Переменная CHAT_ID не задана — автоотправка не будет работать")

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

# ================== Inline-запросы ==================
async def inline_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.lower().strip()
    if not query:
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

# ================== Команды ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для школьного расписания.\n"
        "Используй inline-запрос: @твой_бот today / tomorrow / week"
    )

# ================== FastAPI ==================
app = FastAPI()
bot_app = ApplicationBuilder().token(TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(InlineQueryHandler(inline_schedule))

# ================== Webhook ==================
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.update_queue.put(update)
    return {"ok": True}

# ================== Ping Render ==================
async def ping_self():
    """Периодически пингует сам себя, чтобы Render Free не засыпал"""
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{BOT_URL}/")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔁 Ping status: {resp.status_code}")
        except Exception as e:
            print(f"[Ping error] {e}")
        await asyncio.sleep(600)  # каждые 10 минут

# ================== Автоотправка расписания ==================
async def send_daily_schedule():
    """Отправляет расписание на сегодня в указанный чат"""
    if not CHAT_ID:
        return
    try:
        day_eng = datetime.today().strftime("%A")
        day = DAY_MAP.get(day_eng, "Сегодня")
        lessons = schedule.get(day, ["Сегодня нет занятий"])
        text = f"📅 Расписание на сегодня ({day}):\n\n" + "\n".join(lessons)
        await bot_app.bot.send_message(chat_id=CHAT_ID, text=text)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Расписание отправлено в чат {CHAT_ID}")
    except Exception as e:
        print(f"[Send schedule error] {e}")

# ================== Lifespan ==================
@app.on_event("startup")
async def startup_event():
    await bot_app.initialize()
    await bot_app.bot.set_webhook(f"{BOT_URL}{WEBHOOK_PATH}")
    await bot_app.start()
    print("✅ Webhook установлен, бот готов к работе")

    # Пинг каждые 10 минут
    asyncio.create_task(ping_self())

    # Автоотправка расписания в 9:00 (по Москве)
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_daily_schedule, CronTrigger(hour=9, minute=0))
    scheduler.start()
    print("🕘 Автоотправка расписания включена (каждый день в 09:00 МСК)")

@app.on_event("shutdown")
async def shutdown_event():
    await bot_app.stop()
    await bot_app.shutdown()
    print("🛑 Бот остановлен")

# ================== Стартовая страница ==================
@app.get("/")
def root():
    return {"status": "Bot is running ✅"}
