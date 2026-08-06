import logging
import asyncio
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle, InputTextMessageContent
import yt_dlp

TOKEN = "8613558590:AAEPGMyeGmNSMpDLFeIcuGr9HbujQdu54Zw"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь мне ссылку на видео (YouTube / TikTok), и я предложу выбрать качество и язык озвучки.",
        parse_mode="HTML"
    )

# Обработка инлайн-поиска (оставляем удобный поиск)
@dp.inline_query()
async def inline_search(query: types.InlineQuery):
    text = query.query.strip()
    if not text:
        return

    ydl_opts = {'extract_flat': True, 'quiet': True, 'default_search': 'ytsearch5'}
    results = []
    try:
        def search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch5:{text}", download=False).get('entries', [])
        
        loop = asyncio.get_running_loop()
        videos = await loop.run_in_executor(None, search)

        for i, v in enumerate(videos):
            title = v.get('title', 'Видео')
            url = v.get('url') or f"https://www.youtube.com/watch?v={v.get('id')}"
            uploader = v.get('uploader', 'YouTube')
            thumbnail = v.get('thumbnail') or f"https://img.youtube.com/vi/{v.get('id')}/hqdefault.jpg"
            
            results.append(
                InlineQueryResultArticle(
                    id=str(i),
                    title=title,
                    description=f"Автор: {uploader} | Нажми для выбора качества",
                    thumbnail_url=thumbnail,
                    input_message_content=InputTextMessageContent(message_text=url)
                )
            )
    except Exception as e:
        print(f"Ошибка инлайн-поиска: {e}")

    await query.answer(results, cache_time=1)

# Обработка отправленной ссылки — анализ доступных форматов и языков
@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_url(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if w.startswith("http://") or w.startswith("https://")), None)
    
    if not url:
        return

    status_msg = await message.answer("🔍 Анализирую видео, доступное качество и языки...")

    try:
        def get_info():
            ydl_opts = {'quiet': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, get_info)
        
        title = info.get('title', 'Видео')
        
        # Проверяем наличие разных языковых аудиодорожек (субтитров/аудио)
        audio_languages = set()
        if 'automatic_captions' in info and info['automatic_captions']:
            audio_languages.update(info['automatic_captions'].keys())
        if 'subtitles' in info and info['subtitles']:
            audio_languages.update(info['subtitles'].keys())
            
        # Формируем клавиатуру выбора качества
        keyboard_buttons = [
            [
                InlineKeyboardButton(text="🎬 1080p (Full HD)", callback_data=f"dl|1080|{url}"),
                InlineKeyboardButton(text="🎬 720p (HD)", callback_data=f"dl|720|{url}")
            ],
            [
                InlineKeyboardButton(text="🎬 480p", callback_data=f"dl|480|{url}"),
                InlineKeyboardButton(text="🎬 360p", callback_data=f"dl|360|{url}")
            ],
            [
                InlineKeyboardButton(text="🎵 Только аудио (MP3)", callback_data=f"dl|mp3|{url}")
            ]
        ]

        lang_text = ""
        if audio_languages:
            lang_text = f"\n\n🌐 Доступные языки/дорожки: {', '.join(list(audio_languages)[:5])}"

        await status_msg.edit_text(
            f"<b>{title}</b>{lang_text}\n\n👇 Выберите желаемое качество:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
            parse_mode="HTML"
        )

    except Exception as e:
        await status_msg.edit_text("❌ Не удалось проанализировать ссылку. Убедитесь, что она корректна.")
        print(f"Ошибка анализа: {e}")

# Обработка нажатия на кнопку качества
@dp.callback_query(F.data.startswith("dl|"))
async def callback_download(callback: types.CallbackQuery):
    _, quality, url = callback.data.split("|", 2)
    await callback.message.edit_text(f"⏳ Скачиваю видео в качестве <b>{quality}</b>, подождите...", parse_mode="HTML")

    ydl_opts = {
        'max_filesize': 50 * 1024 * 1024, # Ограничение Телеграма на 50 МБ для ботов
    }

    if quality == "mp3":
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            'outtmpl': 'audio_%(id)s.%(ext)s',
        })
    elif quality == "1080":
        ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
    elif quality == "720":
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
    elif quality == "480":
        ydl_opts['format'] = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'
    else: # 360p
        ydl_opts['format'] = 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best'

    try:
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download)

        # Если качали mp3, расширение файла изменится
        if quality == "mp3" and not file_path.endswith('.mp3'):
            file_path = os.path.splitext(file_path)[0] + '.mp3'

        await callback.message.edit_text("📤 Загружаю файл в чат...")
        
        if quality == "mp3":
            audio_file = types.FSInputFile(file_path)
            await callback.message.answer_audio(audio_file)
        else:
            video_file = types.FSInputFile(file_path)
            await callback.message.answer_video(video_file)
        
        if os.path.exists(file_path):
            os.remove(file_path)
            
        await callback.message.delete()

    except Exception as e:
        await callback.message.edit_text("❌ Ошибка при скачивании (возможно, выбранное качество слишком тяжелое для бота или превышает 50 МБ).")
        print(f"Ошибка скачивания: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот с выбором качества запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
