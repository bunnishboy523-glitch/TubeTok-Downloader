import os
import asyncio
import yt_dlp
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = "8613558590:AAFrlwYM10Zk912jyYG6-qu19F38ccJi5gQ"
CHANNEL_ID = -1003805473602
DOWNLOAD_FOLDER = "videos_and_photos"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_urls = {}

QUALITIES = {
    "360p": "best[height<=360][ext=mp4]/best[height<=360]",
    "480p": "best[height<=480][ext=mp4]/best[height<=480]",
    "720p": "best[height<=720][ext=mp4]/best[height<=720]",
    "1080p": "best[height<=1080][ext=mp4]/best[height<=1080]",
}


async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"Ошибка проверки подписки: {e}")
        return False


def download_video(url: str, filename: str, quality: str) -> str | None:
    output_path = os.path.join(DOWNLOAD_FOLDER, filename)
    ydl_opts = {
        "outtmpl": output_path,
        "format": QUALITIES[quality],
        "noplaylist": True,
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 5,
        "buffersize": 1024,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
                "skip": ["auth"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if os.path.exists(output_path):
            return output_path
        return None
    except Exception as e:
        print(f"Ошибка при скачивании видео: {e}")
        return None



def download_audio_direct(url: str, filename: str) -> str | None:
    output_path = os.path.join(DOWNLOAD_FOLDER, filename)
    ydl_opts = {
        "outtmpl": f"{output_path}.%(ext)s",
        "format": "ba/b",  # Выбираем только аудио (best audio)
        "noplaylist": True,
        # Обход блокировок YouTube: притворяемся обычным клиентом Android / Web
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
                "skip": ["auth"],
            }
        },
        "ignoreerrors": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None
            
            # Проверяем, какое расширение в итоге скачалось
            ext = info.get("ext", "m4a")
            full_path = f"{output_path}.{ext}"
            
            if os.path.exists(full_path):
                return full_path
                
            # Если не нашли по стандартному пути, ищем любой файл с этим префиксом
            for f in os.listdir(DOWNLOAD_FOLDER):
                if f.startswith(filename):
                    return os.path.join(DOWNLOAD_FOLDER, f)
        return None
    except Exception as e:
        print(f"Ошибка при скачивании аудио: {e}")
        return None


def format_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📉 360p", callback_data="q_360p"),
         InlineKeyboardButton(text="📊 480p", callback_data="q_480p")],
        [InlineKeyboardButton(text="📈 720p", callback_data="q_720p"),
         InlineKeyboardButton(text="🔝 1080p", callback_data="q_1080p"
)],
        [InlineKeyboardButton(text="🎵 Аудио (M4A/WebM)", callback_data="q_audio")],
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url="https://t.me/+FYXjPcKXJ-5hYjgy")],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
        ])
        await message.answer("👋 Привет!\n\nЧтобы пользоваться ботом, нужно подписаться на наш канал 👇", reply_markup=keyboard)
    else:
        await message.answer("👋 Привет! Отправь мне ссылку на видео с YouTube или TikTok — выберешь качество или скачаешь как аудио!")


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    subscribed = await check_subscription(callback.from_user.id)
    if subscribed:
        await callback.message.edit_text("✅ Отлично! Теперь отправь мне ссылку на видео!")
    else:
        await callback.answer("❌ Ты ещё не подписался на канал!", show_alert=True)


@dp.callback_query(F.data.startswith("q_"))
async def quality_chosen(callback: CallbackQuery):
    choice = callback.data[2:]
    user_id = callback.from_user.id
    url = user_urls.get(user_id)

    if not url:
        await callback.answer("❌ Ссылка устарела, отправь снова.", show_alert=True)
        return

    if choice == "audio":
        await callback.message.edit_text("⏳ Извлекаю аудио, подожди...")
        filename = f"audio_{uuid.uuid4().hex[:8]}"
        filepath = await asyncio.get_event_loop().run_in_executor(
            None, download_audio_direct, url, filename
        )
        
        user_urls.pop(user_id, None)

        if filepath is None or not os.path.exists(filepath):
            await callback.message.edit_text("❌ Не удалось извлечь аудио. Попробуй другое видео.")
            return

        await callback.message.edit_text("📤 Отправляю аудиофайл...")
        try:
            audio = FSInputFile(filepath)
            await callback.message.answer_audio(audio=audio, caption="✅ Готово! 🎵")
        except Exception as e:
            print(f"Ошибка при отправке аудио: {e}")
            await callback.message.answer("❌ Не удалось отправить аудио.")
        finally:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
            try:
                await callback.message.delete()
            except:
                pass
    else:
        await callback.message.edit_text(f"⏳ Скачиваю в {choice}, подожди...")
        filename = f"video_{uuid.uuid4().hex[:8]}.mp4"
        filepath = await asyncio.get_event_loop().run_in_executor(
            None, download_video, url, filename, choice
        )
        
        user_urls.pop(user_id, None)

        if filepath is None or not os.path.exists(filepath):
            await callback.message.edit_text("❌ Не удалось скачать видео. Попробуй другое качество или ссылку.")
            return

        file_size = os.path.getsize(filepath)
        if file_size > 50 * 1024 * 1024:
            await callback.message.edit_text("❌ Видео слишком большое (больше 50 МБ).")
            os.remove(filepath)
            return

        await callback.message.edit_text("📤 Отправляю видео...")
        try:
            video = FSInputFile(filepath)
            await callback.message.answer_video(video=video, caption=f"✅ Готово! Качество: {choice} 🎬")
        except Exception as e:
            print(f"Ошибка при отправке: {e}")
            await callback.message.answer("❌ Не удалось отправить видео.")
        finally:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
            try:
                await callback.message.delete()
            except:
                pass


@dp.message(F.text)
async def handle_link(message: Message):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url="https://t.me/+FYXjPcKXJ-5hYjgy")],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
        ])
        await message.answer("❌ Чтобы пользоваться ботом, нужно подписаться на канал 👇", reply_markup=keyboard)
        return

    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Пожалуйста, отправь корректную ссылку (начинается с http).")
        return

    user_urls[message.from_user.id] = url
    await message.answer("🎬 Выбери формат:", reply_markup=format_keyboard())


async def main():
    print("🤖 Бот запущен! Нажми Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
