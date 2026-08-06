import logging
import asyncio
import os
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, BotCommand, InlineQueryResultArticle, InputTextMessageContent
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import yt_dlp
from aiohttp import web

TOKEN = "8613558590:AAEPGMyeGmNSMpDLFeIcuGr9HbujQdu54Zw"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# База данных-заглушка для подписок (твои ID добавлены для бесплатного премиум-доступа)
subscribers = {8549738631, 8932750237}

# Состояния для поиска через чат
class SearchState(StatesGroup):
    waiting_for_query = State()

# Установка кнопки Menu и команд в интерфейсе Telegram при запуске
async def set_main_menu(bot: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="search", description="🔍 Найти видео"),
        BotCommand(command="sub", description="⭐ Купить подписку (Плюс)"),
        BotCommand(command="help", description="💬 Поддержка")
    ]
    await bot.set_my_commands(commands)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Отправь мне ссылку на видео (YouTube / TikTok) для скачивания, "
        "или используй команду /search для поиска видео прямо в чате!\n\n"
        "⭐ Хочешь доступ к 4K, 2K, 144p и другим премиум-функциям? Напиши /sub",
        parse_mode="HTML"
    )

# Команда /search для поиска видео
@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    await message.answer("🔍 Введите название или ключевые слова для поиска видео:")

# Прием поискового запроса от пользователя в чате
@dp.message(SearchState.waiting_for_query)
async def process_search_query(message: types.Message, state: FSMContext):
    query_text = message.text.strip()
    await state.clear()

    if not query_text:
        await message.answer("❌ Запрос не может быть пустым.")
        return

    wait_msg = await message.answer("⏳ Ищу видео...")

    ydl_opts = {'extract_flat': True, 'quiet': True, 'default_search': 'ytsearch50'}
    try:
        def search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch50:{query_text}", download=False).get('entries', [])
        
        loop = asyncio.get_running_loop()
        videos = await loop.run_in_executor(None, search)

        if not videos:
            await wait_msg.edit_text("❌ По вашему запросу ничего не найдено.")
            return

        text_result = f"🔍 **Результаты поиска по запросу:** `{query_text}`\n\n"
        keyboard_rows = []

        for i, v in enumerate(videos[:10]):
            title = v.get('title', 'Видео')
            url = v.get('url') or f"https://www.youtube.com/watch?v={v.get('id')}"
            text_result += f"{i+1}. {title}\n🔗 {url}\n\n"
            keyboard_rows.append([InlineKeyboardButton(text=f"🎬 Скачать №{i+1}", callback_data=f"dl_link|{url}")])

        await wait_msg.edit_text(
            text_result, 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows), 
            parse_mode="markdown",
            disable_web_page_preview=True
        )

    except Exception as e:
        await wait_msg.edit_text("❌ Произошла ошибка при поиске.")
        print(f"Ошибка поиска: {e}")

@dp.message(Command("sub"))
async def cmd_subscribe(message: types.Message, state: FSMContext):
    await state.clear()
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
        "• Мгновенная скорость без ограничений по весу\n\n"
        "📅 **Выбери период:**",
        reply_markup=keyboard,
        parse_mode="markdown"
    )

# Команда /help с контактами поддержки
@dp.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Поддержка 1", url="https://t.me/bshbmw")],
            [InlineKeyboardButton(text="👤 Поддержка 2", url="https://t.me/bunbmw")]
        ]
    )
    await message.answer(
        "💬 **Служба поддержки**\n\n"
        "Если у вас возникли вопросы по работе бота, оплате подписки или появились предложения, вы можете обратиться к нашей администрации:",
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
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title,
        description="Доступ к премиум-функциям скачивания видео (4K, 2K, 144p и др.)",
        payload=f"sub_{plan}_{callback.from_user.id}",
        currency="XTR",
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
    await message.answer("🎉 Спасибо за покупку! Подписка успешно активирована.")

# Инлайн-поиск
@dp.inline_query()
async def inline_search(query: types.InlineQuery):
    text = query.query.strip()
    if not text:
        return

    ydl_opts = {'extract_flat': True, 'quiet': True, 'default_search': 'ytsearch50'}
    results = []
    try:
        def search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch50:{text}", download=False).get('entries', [])
        
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
                    description=f"Автор: {uploader} | Нажми, чтобы отправить",
                    thumbnail_url=thumbnail,
                    input_message_content=InputTextMessageContent(message_text=url)
                )
            )
    except Exception as e:
        print(f"Ошибка инлайн-поиска: {e}")

    await query.answer(results, cache_time=1)

@dp.callback_query(F.data.startswith("dl_link|"))
async def callback_dl_link(callback: types.CallbackQuery):
    _, url = callback.data.split("|", 1)
    await show_qualities(callback.message, url)
    await callback.answer()

@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_url(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if w.startswith("http://") or w.startswith("https://")), None)
    if not url:
        return
    await show_qualities(message, url)

async def show_qualities(message: types.Message, url: str):
    is_sub = message.from_user.id in subscribers

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

# Скачивание через публичные API-зеркала (работает без кук и без банов на серверах)
@dp.callback_query(F.data.startswith("dl|"))
async def callback_download(callback: types.CallbackQuery):
    _, quality, url = callback.data.split("|", 2)
    await callback.message.edit_text("⏳ Получаю быструю ссылку через API-зеркало...")

    video_id = None
    if "youtu.be/" in url:
        video_id = url.split("youtu.be/")[1].split("?")[0]
    elif "watch?v=" in url:
        video_id = url.split("watch?v=")[1].split("&")[0]

    if not video_id:
        await callback.message.edit_text("❌ Не удалось распознать ID видео.")
        return

    instances = [
        "https://invidious.privacyredirect.com",
        "https://inv.nadeko.net",
        "https://vid.puffyan.us"
    ]

    direct_url = None
    async with aiohttp.ClientSession() as session:
        for instance in instances:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            try:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        adaptive_formats = data.get("adaptiveFormats", [])
                        
                        for fmt in adaptive_formats:
                            if quality == "mp3" and "audio" in fmt.get("type", ""):
                                direct_url = fmt.get("url")
                                break
                            elif quality != "mp3":
                                res = fmt.get("resolution", "")
                                if quality in res and "video" in fmt.get("type", ""):
                                    direct_url = fmt.get("url")
                                    break
                        
                        if not direct_url and adaptive_formats:
                            direct_url = adaptive_formats[0].get("url")
                            
                        if direct_url:
                            break
            except Exception:
                continue

    if not direct_url:
        await callback.message.edit_text("❌ Все зеркала перегружены. Попробуйте чуть позже.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать файл без ограничений", url=direct_url)]
        ]
    )
    await callback.message.edit_text(
        "✅ **Готово!** Нажми кнопку ниже, чтобы скачать файл:",
        reply_markup=keyboard,
        parse_mode="markdown"
    )

# Веб-сервер для удержания открытого порта на Render
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Веб-сервер запущен на порту {port}")

async def main():
    logging.basicConfig(level=logging.INFO)
    await set_main_menu(bot)
    print("Бот запущен!")
    
    await asyncio.gather(
        web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
