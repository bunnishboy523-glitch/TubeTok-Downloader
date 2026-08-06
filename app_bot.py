import logging
import asyncio
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
import yt_dlp

TOKEN = "8613558590:AAEPGMyeGmNSMpDLFeIcuGr9HbujQdu54Zw"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# База данных-заглушка для подписок (в реальном проекте лучше использовать SQLite/PostgreSQL)
subscribers = set()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь мне ссылку на видео, и я предложу варианты скачивания.\n\n",
        parse_mode="HTML"
    )

@dp.message(Command("sub"))
async def cmd_subscribe(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ 350 ⭐ — месяц", callback_data="buy_month")],
            [InlineKeyboardButton(text="⭐ 1000 ⭐ — 6 месяцев", callback_data="buy_6months")],
            [InlineKeyboardButton(text="⭐ 1500 ⭐ — год", callback_data="buy_year")]
        ]
    )
    await message.answer(
        "✨ **SaveYouTube ПЛЮС**\n\n"
        "• Видео в качестве 4K, 2K, FULLHD, HD, 240p, 144p\n"
        "• Субтитры и скачивание плейлистов\n"
        "• Скорость в 3 раза выше и без очереди\n\n"
        "📅 **Выбери период:**",
        reply_markup=keyboard,
        parse_mode="markdown"
    )

# Покупка подписки за Telegram Stars
@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    plan = callback.data.split("_")[1]
    
    prices = {
        "month": (350, "Подписка на 1 месяц"),
        "6months": (1000, "Подписка на 6 месяцев"),
        "year": (1500, "Подписка на 1 год")
    }
    
    amount, title = prices[plan]
    
    # Создаем инвойс для оплаты Telegram Stars
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title,
        description="Доступ к премиум-функциям скачивания видео (4K, 2K, 144p и др.)",
        payload=f"sub_{plan}_{callback.from_user.id}",
        currency="XTR",  # Валюта Telegram Stars
        prices=[LabeledPrice(label=title, amount=amount)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    user_id = message.from_user.id
    subscribers.add(user_id)
    await message.answer("🎉 Спасибо за покупку! Подписка успешно активирована. Теперь вам доступны все форматы (4K, 2K, 144p и др.)!")

# Обработка ссылки
@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_url(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if w.startswith("http://") or w.startswith("https://")), None)
    if not url:
        return

    is_sub = message.from_user.id in subscribers

    # Формируем кнопки в зависимости от наличия подписки
    if is_sub:
        keyboard_buttons = [
            [InlineKeyboardButton(text="🎬 4K / 2K (Ultra HD)", callback_data=f"dl|4k|{url}")],
            [InlineKeyboardButton(text="🎬 1080p", callback_data=f"dl|1080|{url}"), InlineKeyboardButton(text="🎬 720p", callback_data=f"dl|720|{url}")],
            [InlineKeyboardButton(text="🎬 240p / 144p (Низкое)", callback_data=f"dl|144|{url}")],
            [InlineKeyboardButton(text="🎵 Аудио MP3", callback_data=f"dl|mp3|{url}")]
        ]
        text_info = "✨ **Премиум-доступ активен!** Выберите качество:"
    else:
        keyboard_buttons = [
            [InlineKeyboardButton(text="🎬 1080p", callback_data=f"dl|1080|{url}"), InlineKeyboardButton(text="🎬 720p", callback_data=f"dl|720|{url}")],
            [InlineKeyboardButton(text="⭐ 4K, 2K и 144p доступны в подписке (/sub)", callback_data="locked")]
        ]
        text_info = "📺 Выберите стандартное качество (для 4K, 2K и 144p оформить подписку /sub):"

    await message.answer(text_info, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons), parse_mode="markdown")

@dp.callback_query(F.data == "locked")
async def locked_callback(callback: types.CallbackQuery):
    await callback.answer("🔒 Это качество доступно только по подписке SaveYouTube ПЛЮС! Напишите /sub", show_alert=True)

@dp.callback_query(F.data.startswith("dl|"))
async def callback_download(callback: types.CallbackQuery):
    _, quality, url = callback.data.split("|", 2)
    await callback.message.edit_text(f"⏳ Скачиваю видео (качество: {quality})...")

    ydl_opts = {'max_filesize': 50 * 1024 * 1024}

    if quality == "4k":
        ydl_opts['format'] = 'bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/best[height<=2160]'
    elif quality == "1080":
        ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]'
    elif quality == "720":
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]'
    elif quality == "144":
        ydl_opts['format'] = 'bestvideo[height<=144][ext=mp4]+bestaudio[ext=m4a]/best[height<=144]'
    elif quality == "mp3":
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            'outtmpl': 'audio_%(id)s.%(ext)s',
        })

    try:
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download)

        if quality == "mp3" and not file_path.endswith('.mp3'):
            file_path = os.path.splitext(file_path)[0] + '.mp3'

        await callback.message.edit_text("📤 Загружаю файл...")
        
        if quality == "mp3":
            await callback.message.answer_audio(types.FSInputFile(file_path))
        else:
            await callback.message.answer_video(types.FSInputFile(file_path))
        
        if os.path.exists(file_path):
            os.remove(file_path)
        await callback.message.delete()

    except Exception as e:
        await callback.message.edit_text("❌ Ошибка при скачивании файла.")
        print(e)

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот с тарифами за звезды запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
