import asyncio
import logging
import sys
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

# --- Настройки второго бота ---
TOKEN = "8613558590:AAFgkkzsIeAe5kaMVOnt0XiKtyDpTkSxI4Q"  # Токен твоего второго бота
PORT = 8080  
WEB_APP_URL = "https://tubetok-bot.onrender.com/app"  # Замени на адрес своего сайта на Render

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Веб-сервер ---
async def handle_ping(request):
    return web.Response(text="Bot 2 is alive!", status=200)

async def handle_webapp(request):
    return web.FileResponse("index.html")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/app", handle_webapp)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Сервер второго бота запущен на порту {PORT}")

# --- Логика ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 Открыть Mini App", 
                web_app=WebAppInfo(url=WEB_APP_URL)
            )
        ]
    ])
    await message.answer(
        "Привет! Это второй бот с мини-приложением. Нажми кнопку ниже:",
        reply_markup=keyboard
    )

@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        if data.get("action") == "download":
            url = data.get("url")
            process_msg = await message.answer(f"⏳ Обрабатываю ссылку: `{url}`", parse_mode="Markdown")
            await asyncio.sleep(2)
            await process_msg.edit_text("✅ Готово!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

async def main():
    await start_web_server()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print("🚀 Второй бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
