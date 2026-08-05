import asyncio
import logging
import sys
import os
import yt_dlp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiohttp import web

TOKEN = "8613558590:AAEPGMyeGmNSMpDLFeIcuGr9HbujQdu54Zw"
PORT = 8080
WEB_APP_URL = "https://tubetok-downloader-3.onrender.com/app"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Открыть Mini App",
                    web_app=WebAppInfo(url=WEB_APP_URL)
                )
            ]
        ]
    )
    welcome_text = (
        "👋 **Добро пожаловать в Tubetok Downloader!**\n\n"
        "🔍 **Как искать и скачивать:**\n"
        "• Просто отправьте мне **любой поисковый запрос** текстом (например: *музыка 2026*, *трейлер*), и я найду видео!\n"
        "• Или отправьте мне **прямую ссылку** на YouTube / TikTok.\n\n"
        "После этого я предложу вам выбрать качество для скачивания!"
    )
    await message.answer(welcome_text, reply_markup=kb, parse_mode="Markdown")

# Функция генерации кнопок выбора качества
def get_quality_keyboard(url: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Высокое качество (MP4)", callback_data=f"dl_high:{url}")],
            [InlineKeyboardButton(text="📱 Среднее качество (Эконом)", callback_data=f"dl_low:{url}")],
            [InlineKeyboardButton(text="🎵 Только аудио (MP3)", callback_data=f"dl_audio:{url}")]
        ]
    )

# Поиск видео через yt-dlp по текстовому запросу
@dp.message(F.text & ~F.text.regexp(r'https?://[^\s]+'))
async def handle_search_query(message: types.Message):
    query = message.text.strip()
    searching_msg = await message.answer(f"🔎 Ищу на YouTube: <b>{query}</b>...", parse_mode="HTML")

    def search_videos():
        ydl_opts = {
            'extract_flat': True,
            'max_downloads': 5,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Ищем первые 5 результатов на YouTube
            results = ydl.extract_info(f"ytsearch5:{query}", download=False)
            return results.get('entries', [])

    try:
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(None, search_videos)

        if not entries:
            await message.answer("❌ Ничего не найдено по вашему запросу.")
            await searching_msg.delete()
            return

        await searching_msg.delete()
        await message.answer("✨ Вот что мне удалось найти. Нажмите на видео, чтобы выбрать качество:")

        for entry in entries:
            title = entry.get('title', 'Без названия')
            video_id = entry.get('id', '')
            if not video_id:
                continue
            watch_url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Кнопка для каждого найденного видео
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📥 Выбрать качество и скачать", callback_data=f"prep_dl:{watch_url}")]
                ]
            )
            await message.answer(f"📹 <b>{title}</b>\n🔗 {watch_url}", parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        await searching_msg.edit_text(f"❌ Ошибка при поиске: {str(e)}")

# Обработка прямой ссылки из чата
@dp.message(F.text.regexp(r'https?://[^\s]+'))
async def handle_url_message(message: types.Message):
    url = message.text.strip()
    await message.answer("👇 Выберите формат и качество для скачивания:", reply_markup=get_quality_keyboard(url))

# Обработка нажатия на кнопку "Выбрать качество" из результатов поиска
@dp.callback_query(F.data.startswith("prep_dl:"))
async def process_prep_download(callback: types.CallbackQuery):
    url = callback.data.split(":", 1)[1]
    await callback.message.answer("👇 Выберите формат и качество:", reply_markup=get_quality_keyboard(url))
    await callback.answer()

# Скачивание файла по выбранному качеству
@dp.callback_query(F.data.startswith("dl_"))
async def process_download_callback(callback: types.CallbackQuery):
    action, url = callback.data.split(":", 1)
    await callback.message.edit_text("⏳ Скачиваю файл, пожалуйста, подождите...")

    output_template = "downloaded_file.mp4"
    audio_template = "downloaded_audio.mp3"
    
    for f in [output_template, audio_template]:
        if os.path.exists(f):
            os.remove(f)

    ydl_opts = {}
    if action == "dl_high":
        ydl_opts = {'format': 'best[ext=mp4]/best', 'outtmpl': output_template, 'max_filesize': 50 * 1024 * 1024}
    elif action == "dl_low":
        ydl_opts = {'format': 'worst[ext=mp4]/worst', 'outtmpl': output_template, 'max_filesize': 50 * 1024 * 1024}
    elif action == "dl_audio":
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            'outtmpl': 'downloaded_audio',
            'max_filesize': 50 * 1024 * 1024
        }

    try:
        loop = asyncio.get_running_loop()
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        await loop.run_in_executor(None, download)

        target_file = audio_template if action == "dl_audio" else output_template
        if not os.path.exists(target_file):
            target_file = "downloaded_audio.mp3" if action == "dl_audio" else "downloaded_file.mp4"

        if os.path.exists(target_file):
            file_obj = FSInputFile(target_file)
            if action == "dl_audio":
                await callback.message.answer_audio(file_obj, caption="✅ Готово!")
            else:
                await callback.message.answer_video(file_obj, caption="✅ Готово!")
            os.remove(target_file)
            await callback.message.delete()
        else:
            await callback.message.edit_text("❌ Не удалось найти скачанный файл.")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при скачивании: {str(e)}")

# Веб-сервер
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
