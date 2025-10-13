import os, json, uuid
from datetime import datetime, timedelta
from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ApplicationBuilder, InlineQueryHandler, CommandHandler, ContextTypes

# ================== Загрузка токена ==================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("Не задан TELEGRAM_TOKEN в переменных окружения!")

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
        "Используй inline-запрос: @rasp7V_bot today / tomorrow / week"
    )

# ================== Основной запуск ==================
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(InlineQueryHandler(inline_schedule))
app.run_polling()