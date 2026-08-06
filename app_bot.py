import logging
import asyncio
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, BotCommand,
    InlineQueryResultArticle, InputTextMessageContent
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
import yt_dlp
from aiohttp import web

# ==== Токен читаем из переменной окружения, а не храним в коде ====
TOKEN = os.environ.get("8613558590:AAEPGMyeGmNSMpDLFeIcuGr9HbujQdu54Zw")
if not TOKEN:
    raise RuntimeError(
        "Не задан токен бота. Установите переменную окружения BOT_TOKEN, "
        "например: export BOT_TOKEN='ваш_токен'"
    )

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

# yt-dlp может подвиснуть на медленных/недоступных источниках — ограничиваем время
YTDLP_TIMEOUT = 25  # секунд
LINK_TTL = 3600      # сколько секунд хранить ссылку в кэше по короткому id


# ============================================================
#  БАЗА ДАННЫХ: подписки с сроком действия (SQLite вместо set())
# ============================================================

def db_init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id INTEGER PRIMARY KEY,
            expires_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def db_add_subscription(user_id: int, days: int):
    expires_at = int(time.time()) + days * 86400
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO subscribers (user_id, expires_at) VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
           expires_at = MAX(expires_at, excluded.expires_at)""",
        (user_id, expires_at),
    )
    conn.commit()
    conn.close()


def db_is_subscribed(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT expires_at FROM subscribers WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return False
    return row[0] > int(time.time())


# ============================================================
#  КЭШ ССЫЛОК: чтобы не превышать лимит 64 байта в callback_data
# ============================================================
# callback_data Telegram ограничен 64 байтами, а URL с параметрами
# (&list=, &t=, &index= и т.п.) легко превышает лимит. Поэтому
# в callback_data кладём короткий id, а сам URL храним отдельно.

_link_cache: dict[str, tuple[str, float]] = {}  # short_id -> (url, expires_at)


def cache_url(url: str) -> str:
    # чистим устаревшие записи, чтобы кэш не рос бесконечно
    now = time.time()
    expired = [k for k, (_, exp) in _link_cache.items() if exp < now]
    for k in expired:
        del _link_cache[k]

    short_id = uuid.uuid4().hex[:10]
    _link_cache[short_id] = (url, now + LINK_TTL)
    return short_id


def get_cached_url(short_id: str) -> str | None:
    entry = _link_cache.get(short_id)
    if not entry:
        return None
    url, expires_at = entry
    if expires_at < time.time():
        del _link_cache[short_id]
        return None
    return url


class SearchState(StatesGroup):
    waiting_for_query = State()


async def set_main_menu(bot: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="search", description="🔍 Найти видео"),
        BotCommand(command="sub", description="⭐ Купить подписку (Плюс)"),
        BotCommand(command="help", description="💬 Поддержка"),
    ]
    await bot.set_my_commands(commands)


# ============================================================
#  ВСПОМОГАТЕЛЬНОЕ: запуск yt-dlp с таймаутом в отдельном потоке
# ============================================================

async def run_ytdlp(func):
    """Выполняет блокирующую функцию yt-dlp в executor с таймаутом,
    чтобы бот не зависал, если YouTube/TikTok не отвечает."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func), timeout=YTDLP_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning("yt-dlp: превышено время ожидания")
        return None


# ============================================================
#  ХЕНДЛЕРЫ
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Отправь мне ссылку на видео (YouTube / TikTok) для скачивания, "
        "или используй команду /search для поиска видео прямо в чате!\n\n"
        "⭐ Хочешь доступ к 4K, 2K, 144p и другим премиум-функциям? Напиши /sub"
    )


@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    await message.answer("🔍 Введите название или ключевые слова для поиска видео:")


@dp.message(SearchState.waiting_for_query)
async def process_search_query(message: types.Message, state: FSMContext):
    query_text = message.text.strip() if message.text else ""
    await state.clear()

    if not query_text:
        await message.answer("❌ Запрос не может быть пустым.")
        return

    wait_msg = await message.answer("⏳ Ищу видео...")

    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "default_search": "ytsearch10",
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    def search():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch10:{query_text}", download=False)
            return info.get("entries", []) if info else []

    videos = await run_ytdlp(search)

    if videos is None:
        await wait_msg.edit_text("❌ Поиск занял слишком много времени. Попробуйте ещё раз.")
        return
    if not videos:
        await wait_msg.edit_text("❌ По вашему запросу ничего не найдено.")
        return

    text_result = f"🔍 <b>Результаты поиска по запросу:</b> {escape_html(query_text)}\n\n"
    keyboard_rows = []

    for i, v in enumerate(videos[:10]):
        title = v.get("title", "Видео")
        url = v.get("url") or f"https://www.youtube.com/watch?v={v.get('id')}"
        text_result += f"{i + 1}. {escape_html(title)}\n"
        short_id = cache_url(url)
        keyboard_rows.append(
            [InlineKeyboardButton(text=f"🎬 Скачать №{i + 1}", callback_data=f"dl_link|{short_id}")]
        )

    await wait_msg.edit_text(
        text_result,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        disable_web_page_preview=True,
    )


@dp.message(Command("sub"))
async def cmd_subscribe(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ 350 ⭐ — месяц", callback_data="buy_month")],
            [InlineKeyboardButton(text="⭐ 1000 ⭐ — 6 месяцев", callback_data="buy_6months")],
            [InlineKeyboardButton(text="⭐ 1500 ⭐ — год", callback_data="buy_year")],
        ]
    )
    await message.answer(
        "✨ <b>SaveYouTube ПЛЮС</b>\n\n"
        "• Видео в качестве 4K, 2K, FULLHD, HD, 240p, 144p\n"
        "• Субтитры и скачивание плейлистов\n"
        "• Мгновенная скорость без ограничений по весу\n\n"
        "📅 <b>Выбери период:</b>",
        reply_markup=keyboard,
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Поддержка 1", url="https://t.me/bshbmw")],
            [InlineKeyboardButton(text="👤 Поддержка 2", url="https://t.me/bunbmw")],
        ]
    )
    await message.answer(
        "💬 <b>Служба поддержки</b>\n\n"
        "Если у вас возникли вопросы по работе бота, оплате подписки или появились "
        "предложения, вы можете обратиться к нашей администрации:",
        reply_markup=keyboard,
    )


PLAN_INFO = {
    "month": (350, 30, "Подписка на 1 месяц"),
    "6months": (1000, 182, "Подписка на 6 месяцев"),
    "year": (1500, 365, "Подписка на 1 год"),
}


@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    plan = callback.data.split("_", 1)[1]
    if plan not in PLAN_INFO:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    amount, _days, title = PLAN_INFO[plan]
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title,
        description="Доступ к премиум-функциям скачивания видео (4K, 2K, 144p и др.)",
        payload=f"sub_{plan}_{callback.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)],
    )
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload  # "sub_<plan>_<user_id>"
    parts = payload.split("_")
    plan = parts[1] if len(parts) > 1 else "month"
    days = PLAN_INFO.get(plan, PLAN_INFO["month"])[1]

    user_id = message.from_user.id
    db_add_subscription(user_id, days)

    expires = datetime.now() + timedelta(days=days)
    await message.answer(
        f"🎉 Спасибо за покупку! Подписка активирована до "
        f"<b>{expires.strftime('%d.%m.%Y')}</b>."
    )


@dp.inline_query()
async def inline_search(query: types.InlineQuery):
    text = query.query.strip()
    if not text:
        return

    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "default_search": "ytsearch10",
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    def search():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch10:{text}", download=False)
            return info.get("entries", []) if info else []

    videos = await run_ytdlp(search)
    results = []

    if videos:
        for i, v in enumerate(videos):
            title = v.get("title", "Видео")
            url = v.get("url") or f"https://www.youtube.com/watch?v={v.get('id')}"
            uploader = v.get("uploader", "YouTube")
            thumbnail = v.get("thumbnail") or f"https://img.youtube.com/vi/{v.get('id')}/hqdefault.jpg"

            results.append(
                InlineQueryResultArticle(
                    id=str(i),
                    title=title,
                    description=f"Автор: {uploader} | Нажми, чтобы отправить",
                    thumbnail_url=thumbnail,
                    input_message_content=InputTextMessageContent(message_text=url),
                )
            )

    await query.answer(results, cache_time=1)


@dp.callback_query(F.data.startswith("dl_link|"))
async def callback_dl_link(callback: types.CallbackQuery):
    _, short_id = callback.data.split("|", 1)
    url = get_cached_url(short_id)
    if not url:
        await callback.answer("⌛ Ссылка устарела, выполните поиск заново.", show_alert=True)
        return
    await show_qualities(callback.message, url)
    await callback.answer()


@dp.message(F.text.contains("tiktok.com") | F.text.contains("vm.tiktok.com"))
async def handle_tiktok(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if "tiktok.com" in w), None)
    if not url:
        return

    wait_msg = await message.answer("⏳ Скачиваю видео из TikTok...")

    ydl_opts = {"quiet": True, "no_warnings": True, "format": "best"}

    def get_tiktok_link():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("url") if info else None

    direct_url = await run_ytdlp(get_tiktok_link)

    if not direct_url:
        await wait_msg.edit_text("❌ Не удалось получить видео из TikTok (сервис не ответил вовремя).")
        return

    short_id = cache_url(direct_url)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Открыть видео", url=direct_url)]
        ]
    )
    await wait_msg.edit_text(
        "✅ <b>Готово!</b> Нажми кнопку ниже, чтобы открыть видео:\n\n"
        "⚠️ Если кнопка не открывается — прямая ссылка могла устареть, "
        "отправьте ссылку на TikTok ещё раз.",
        reply_markup=keyboard,
    )


@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_url(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if w.startswith("http://") or w.startswith("https://")), None)
    if not url:
        return
    await show_qualities(message, url)


async def show_qualities(message: types.Message, url: str):
    is_sub = db_is_subscribed(message.from_user.id)
    short_id = cache_url(url)

    if is_sub:
        keyboard_buttons = [
            [InlineKeyboardButton(text="🎬 4K / 2K (Ultra HD)", callback_data=f"dl|4k|{short_id}")],
            [
                InlineKeyboardButton(text="🎬 1080p", callback_data=f"dl|1080|{short_id}"),
                InlineKeyboardButton(text="🎬 720p", callback_data=f"dl|720|{short_id}"),
            ],
            [InlineKeyboardButton(text="🎬 240p / 144p (Низкое)", callback_data=f"dl|144|{short_id}")],
            [InlineKeyboardButton(text="🎵 Аудио MP3", callback_data=f"dl|mp3|{short_id}")],
        ]
        text_info = "✨ <b>Премиум-доступ активен!</b> Выберите качество:"
    else:
        keyboard_buttons = [
            [
                InlineKeyboardButton(text="🎬 1080p", callback_data=f"dl|1080|{short_id}"),
                InlineKeyboardButton(text="🎬 720p", callback_data=f"dl|720|{short_id}"),
            ],
            [InlineKeyboardButton(text="⭐ 4K, 2K и 144p доступны в подписке (/sub)", callback_data="locked")],
        ]
        text_info = "📺 Выберите стандартное качество (для 4K, 2K и 144p оформите подписку /sub):"

    await message.answer(text_info, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))


@dp.callback_query(F.data == "locked")
async def locked_callback(callback: types.CallbackQuery):
    await callback.answer(
        "🔒 Это качество доступно только по подписке SaveYouTube ПЛЮС! Напишите /sub",
        show_alert=True,
    )


@dp.callback_query(F.data.startswith("dl|"))
async def callback_download(callback: types.CallbackQuery):
    _, quality, short_id = callback.data.split("|", 2)
    url = get_cached_url(short_id)
    if not url:
        await callback.answer("⌛ Ссылка устарела, отправьте видео заново.", show_alert=True)
        return

    await callback.message.edit_text("⏳ Обрабатываю видео...")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    format_map = {
        "4k": "best[height<=2160]/best",
        "1080": "best[height<=1080]/best",
        "720": "best[height<=720]/best",
        "144": "best[height<=144]/best",
        "mp3": "bestaudio/best",
    }
    ydl_opts["format"] = format_map.get(quality, "best")

    def get_direct_link():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            return info.get("url") or (
                info.get("entries")[0].get("url") if "entries" in info and info.get("entries") else None
            )

    direct_url = await run_ytdlp(get_direct_link)

    if not direct_url:
        await callback.message.edit_text("❌ Не удалось получить ссылку (сервис не ответил вовремя). Попробуйте другую.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📥 Открыть файл", url=direct_url)]]
    )
    await callback.message.edit_text(
        "✅ <b>Готово!</b> Нажми кнопку ниже, чтобы скачать видео:\n\n"
        "⚠️ Если ссылка не открывается — она могла устареть, отправьте видео заново.",
        reply_markup=keyboard,
    )


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
#  Веб-сервер для удержания открытого порта (например, на Render)
# ============================================================

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
    logger.info(f"Веб-сервер запущен на порту {port}")


async def main():
    db_init()
    await set_main_menu(bot)
    logger.info("Бот запущен!")

    await asyncio.gather(web_server(), dp.start_polling(bot))


if __name__ == "__main__":
    asyncio.run(main())
