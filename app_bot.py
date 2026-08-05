import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# --- Настройки бота ---
TOKEN = "8613558590:AAEPGMyeGmNSMpDLFeIcuGr9HbujQdu54Zw"
PORT = 8080
WEB_APP_URL = "https://tubetok-downloader-3.onrender.com/app"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Обработчик команды /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Открыть TubeTok Downloader",
                    web_app=WebAppInfo(url=WEB_APP_URL)
                )
            ]
        ]
    )
    await message.answer(
        "Привет! Нажми на кнопку ниже, чтобы открыть поиск и скачать видео:",
        reply_markup=kb
    )

# --- Веб-сервер для Mini App и здоровья бота ---
async def handle_ping(request):
    return web.Response(text="Bot 2 is alive!", status=200)

async def handle_webapp(request):
    # Отдает файл index.html из корня проекта
    if os.path.exists("index.html"):
        return web.FileResponse("index.html")
    return web.Response(text="index.html not found", status=404)

def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/app", handle_webapp)
    return app

# --- Запуск всего вместе ---
async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    # Запускаем веб-сервер в фоне
    app = start_web_server()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Веб-сервер запущен на порту {PORT}")

    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
