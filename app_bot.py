import asyncio
import logging
import sys
import os
import yt_dlp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
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
        "Привет! Нажми на кнопку ниже, чтобы открыть мини-приложение, или просто отправь мне ссылку на YouTube / TikTok, и я скачаю видео!",
        reply_markup=kb
    )

# --- Функция скачивания видео через yt-dlp ---
def download_video(url: str) -> str:
    output_template = "downloaded_video.mp4"
    if os.path.exists(output_template):
        os.remove(output_template)
        
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': output_template,
        'max_filesize': 50 * 1024 * 1024, # Ограничение до 50 МБ для отправки в Telegram
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    return output_template

# --- Обработка ссылок из обычного чата ---
@dp.message(F.text.regexp(r'https?://[^\s]+'))
async def handle_url_message(message: types.Message):
    url = message.text.strip()
    status_msg = await message.answer("⏳ Скачиваю видео, подождите...")
    
    try:
        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download_video, url)
        
        if os.path.exists(file_path):
            video_file = FSInputFile(file_path)
            await message.answer_video(video_file, caption="✅ Вот твое видео!")
            os.remove(file_path)
        else:
            await message.answer("❌ Не удалось скачать файл.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании: {str(e)}")
    finally:
        await status_msg.delete()

# --- Обработка данных из Mini App (когда нажимают «Загрузить» внутри сайта) ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    try:
        import json
        data = json.loads(message.web_app_data.data)
        url = data.get("url")
        if not url:
            return
            
        status_msg = await message.answer("⏳ Скачиваю видео из мини-приложения...")
        
        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download_video, url)
        
        if os.path.exists(file_path):
            video_file = FSInputFile(file_path)
            await message.answer_video(video_file, caption="✅ Готово!")
            os.remove(file_path)
        else:
            await message.answer("❌ Не удалось скачать файл.")
        await status_msg.delete()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# --- Веб-сервер для Mini App и здоровья бота ---
async def handle_ping(request):
    return web.Response(text="Bot is alive!", status=200)

async def handle_webapp(request):
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
