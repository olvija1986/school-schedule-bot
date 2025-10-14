# bot_schedule_webhook.py
import os
import json
import uuid
import asyncio
from datetime import datetime, timedelta
import httpx
from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, InlineQueryHandler, CommandHandler, ContextTypes

# ================== Настройки ==================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_IDS = os.environ.get("CHAT_IDS")  # через запятую: "123456,789012"
BOT_URL = os.environ.get("BOT_URL")  # Например: https://your-bot-url.com
WEBHOOK_PATH = f"/webhook/{TOKEN}"

if not TOKEN or not BOT_URL:
    raise RuntimeError("Не заданы переменные окружения TOKEN или BOT_URL")

# ================== Загрузка расписания ==================
with open("schedule.json", "r", encoding="utf-8") as f:
    schedule = json.load(f)

# ================== Маппинг английских дней на русские ==================
DAY_MAP = {
    "Monday": "Понедельник",
    "Tuesday": "Вторник",
    "Wednesday": "Среда",
    "Thursday": "Четверг",
    "Friday": "Пятница",
    "Saturday": "Суббота",
    "Sunday": "Воскресенье"
}

# ================== FastAPI ==================
app = FastAPI()
bot_app = ApplicationBuilder().token(TOKEN).build()

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
        "Используй inline-запрос: @YourBotName today / tomorrow / week"
    )

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(InlineQueryHandler(inline_schedule))

# ================== Авторассылка ==================
async def scheduled_message():
    if CHAT_IDS:
        for chat_id in CHAT_IDS.split(","):
            try:
                day_eng = datetime.today().strftime("%A")
                day = DAY_MAP.get(day_eng, "Сегодня")
                lessons = schedule.get(day, ["Сегодня нет занятий"])
                text = f"Расписание на сегодня ({day}):\n" + "\n".join(lessons)
                await bot_app.bot.send_message(chat_id=int(chat_id), text=text)
            except Exception as e:
                print(f"Ошибка отправки в {chat_id}: {e}")

# Рассылка каждый день в 7:00 кроме субботы и воскресенья
trigger = CronTrigger(hour=7, minute=0, day_of_week='mon-fri')
scheduler = AsyncIOScheduler()
scheduler.add_job(scheduled_message, trigger)

# ================== Webhook endpoint ==================
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.update_queue.put(update)
    return {"ok": True}

# ================== Lifespan ==================
@app.on_event("startup")
async def startup_event():
    await bot_app.initialize()
    await bot_app.start()

    # Устанавливаем webhook
    await bot_app.bot.set_webhook(f"{BOT_URL}{WEBHOOK_PATH}")
    print(f"✅ Webhook установлен: {BOT_URL}{WEBHOOK_PATH}")

    # Запуск планировщика
    scheduler.start()
    print("⏱️ Планировщик запущен")

    # ================== Ping self task ==================
    async def ping_self():
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    await client.get(BOT_URL)
                    print("🏓 Ping отправлен")
                except Exception as e:
                    print(f"Ошибка ping: {e}")
                await asyncio.sleep(600)  # каждые 10 минут

    asyncio.create_task(ping_self())

# ================== Shutdown ==================
@app.on_event("shutdown")
async def shutdown_event():
    await bot_app.stop()
    await bot_app.shutdown()
    print("🛑 Бот остановлен")

# ================== Стартовая страница ==================
@app.get("/")
def root():
    return {"status": "Bot is running ✅"}
