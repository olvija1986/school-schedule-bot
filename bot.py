# bot.py
import os
import asyncio
from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS")  # через запятую: "123456,789012"

app = FastAPI()

# Инициализация Telegram бота
bot_app = ApplicationBuilder().token(TOKEN).build()

# Пример команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Бот работает на PTB 20.8 и FastAPI!")

bot_app.add_handler(CommandHandler("start", start))

# Пример авторассылки каждые 10 секунд
async def scheduled_message():
    if CHAT_IDS:
        for chat_id in CHAT_IDS.split(","):
            await bot_app.bot.send_message(chat_id=int(chat_id), text="Авторассылка работает!")

scheduler = AsyncIOScheduler()
scheduler.add_job(scheduled_message, "interval", seconds=10)
scheduler.start()

# FastAPI webhook endpoint
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.update_queue.put(update)
    return {"ok": True}

# Запуск бота в фоне
@app.on_event("startup")
async def on_startup():
    asyncio.create_task(bot_app.initialize())
    asyncio.create_task(bot_app.start())

# Остановка бота
@app.on_event("shutdown")
async def on_shutdown():
    await bot_app.stop()
