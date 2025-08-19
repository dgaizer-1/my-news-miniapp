import os
import asyncio
import logging
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

# Логи, чтобы видеть, что происходит
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN пуст. Открой .env и вставь токен из @BotFather (BOT_TOKEN=...).")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(m: Message):
    logging.info(f"/start от @{m.from_user.username} (id={m.from_user.id})")
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Открыть мини‑приложение", web_app=WebAppInfo(url=WEBAPP_URL))]
        ],
        resize_keyboard=True
    )
    await m.answer("Привет! Нажми кнопку, чтобы открыть мини‑приложение.", reply_markup=kb)

# Просто чтобы видеть, что сообщения доходят
@dp.message(F.text)
async def echo(m: Message):
    logging.info(f"Текст от @{m.from_user.username}: {m.text!r}")
    await m.answer(f"Эхо: {m.text}")

async def main():
    # На всякий случай снимаем вебхук, чтобы polling точно работал
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Запускаю polling…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())