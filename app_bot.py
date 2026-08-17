import logging
import asyncio
import os
import sqlite3
import time
import uuid
import shutil
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, BotCommand,
    InlineQueryResultArticle, InputTextMessageContent, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
import yt_dlp
from aiohttp import web

# ==== Токен читаем из переменной окружения, а не храним в коде ====
TOKEN = os.environ.get("BOT_TOKEN")
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
YTDLP_TIMEOUT = 25  # секунд — для быстрых операций (метаданные, прямые ссылки)
DOWNLOAD_TIMEOUT = 300  # секунд — для тяжёлого скачивания+склейки видео с нужным звуком
LINK_TTL = 3600      # сколько секунд хранить ссылку в кэше по короткому id
SEARCH_RESULTS_LIMIT = 25  # сколько результатов показывать в поиске

TEMP_DOWNLOAD_DIR = "/tmp/bot_downloads"
os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)

# Официальный лимит Telegram Bot API на отправку файлов ботом (без
# self-hosted Bot API сервера) — 50 МБ.
MAX_TELEGRAM_FILE_MB = 50

# Путь к файлу cookies (экспортированному из браузера) для обхода
# блокировки YouTube "Sign in to confirm you're not a bot" на облачных IP.
# Если файла нет — бот продолжит работать без cookies (может ловить эту ошибку).
COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", "cookies.txt")


def ytdlp_base_opts() -> dict:
    """Общие опции yt-dlp, включая cookies, если файл существует."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


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


# short_id -> выбранный код языка озвучки (None = не выбирали / язык один)
_audio_lang_cache: dict[str, str] = {}

# Отображение кода языка -> подпись на кнопке (как в примере со скрина)
LANGUAGE_LABELS = {
    "ru": "🇷🇺 RU", "en": "🇺🇸 EN", "ar": "🇸🇦 AR", "de": "🇩🇪 DE",
    "es": "🇪🇸 ES", "fr": "🇫🇷 FR", "it": "🇮🇹 IT", "ko": "🇰🇷 KO",
    "pt": "🇵🇹 PT", "ja": "🇯🇵 JA", "zh": "🇨🇳 ZH", "hi": "🇮🇳 HI",
    "tr": "🇹🇷 TR", "uz": "🇺🇿 UZ",
}


def get_audio_languages(url: str) -> list[str]:
    """Возвращает список уникальных кодов языка озвучки, если видео
    содержит несколько аудиодорожек (мультиязычный дубляж на YouTube).
    Пустой список — если языков несколько не найдено (обычное видео)."""
    ydl_opts = {**ytdlp_base_opts(), "extract_flat": False}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []

    formats = info.get("formats") or []
    langs = set()
    for f in formats:
        # Аудиодорожка: есть звук, видео нет (acodec задан, vcodec == 'none')
        if f.get("acodec") not in (None, "none") and f.get("vcodec") == "none":
            lang = f.get("language")
            if lang:
                langs.add(lang.split("-")[0].lower())

    return sorted(langs)


async def maybe_ask_language(message: types.Message, url: str) -> bool:
    """Если у видео несколько языковых дорожек — спрашивает выбор языка
    и возвращает True (дальнейшую обработку продолжит колбэк выбора языка).
    Если языков ≤1 (или проверка не удалась) — возвращает False,
    и вызывающий код должен сам продолжить показом качества."""
    try:
        langs = await run_ytdlp(lambda: get_audio_languages(url))
    except YtDlpFailure:
        return False  # не смогли проверить — просто идём дальше как обычно

    if not langs or len(langs) < 2:
        return False

    short_id = cache_url(url)
    buttons = []
    row = []
    for lang in langs:
        label = LANGUAGE_LABELS.get(lang, lang.upper())
        row.append(InlineKeyboardButton(text=label, callback_data=f"lang|{lang}|{short_id}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await message.answer(
        "🌐 <b>У этого видео несколько языков озвучки.</b>\nВыберите язык:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    return True


@dp.callback_query(F.data.startswith("lang|"))
async def callback_choose_language(callback: types.CallbackQuery):
    _, lang, short_id = callback.data.split("|", 2)
    url = get_cached_url(short_id)
    if not url:
        await callback.answer("⌛ Ссылка устарела, отправьте видео заново.", show_alert=True)
        return

    _audio_lang_cache[short_id] = lang
    await callback.answer(f"Выбран язык: {LANGUAGE_LABELS.get(lang, lang.upper())}")
    await show_qualities(callback.message, url, short_id_override=short_id)


class BotStates(StatesGroup):
    pass  # заготовка на случай будущих FSM-сценариев


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

class YtDlpFailure(Exception):
    """Понятная для пользователя причина отказа yt-dlp."""
    def __init__(self, user_message: str):
        self.user_message = user_message
        super().__init__(user_message)


async def run_ytdlp(func):
    """Выполняет блокирующую функцию yt-dlp в executor с таймаутом,
    чтобы бот не зависал, если YouTube/TikTok не отвечает.

    Ловит ЛЮБУЮ ошибку yt-dlp (не только таймаут) и превращает её
    в понятное сообщение — иначе исключение "вылетает" необработанным,
    и бот навсегда зависает на "Обрабатываю видео...".
    """
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func), timeout=YTDLP_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning("yt-dlp: превышено время ожидания")
        return None
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        logger.warning(f"yt-dlp DownloadError: {msg}")
        if "Sign in to confirm" in msg or "not a bot" in msg:
            raise YtDlpFailure(
                "❌ YouTube требует подтверждение, что вы не бот "
                "(это ограничение YouTube для облачных серверов). "
                "Попробуйте другое видео или ссылку на TikTok."
            )
        raise YtDlpFailure("❌ Не удалось обработать это видео. Попробуйте другую ссылку.")
    except Exception as e:
        logger.exception(f"yt-dlp: непредвиденная ошибка: {e}")
        raise YtDlpFailure("❌ Произошла ошибка при обработке видео. Попробуйте позже.")


async def run_ytdlp_download(func):
    """Как run_ytdlp, но с бОльшим таймаутом — для операций, которые
    реально скачивают и склеивают файл (а не просто получают ссылку)."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func), timeout=DOWNLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning("yt-dlp: скачивание+склейка превысили лимит времени")
        raise YtDlpFailure(
            "❌ Скачивание и склейка видео заняли слишком много времени. "
            "Попробуйте более низкое качество."
        )
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        logger.warning(f"yt-dlp DownloadError (download): {msg}")
        if "Sign in to confirm" in msg or "not a bot" in msg:
            raise YtDlpFailure(
                "❌ YouTube требует подтверждение, что вы не бот. "
                "Попробуйте другое видео."
            )
        raise YtDlpFailure("❌ Не удалось скачать это видео. Попробуйте другую ссылку или качество.")
    except Exception as e:
        logger.exception(f"yt-dlp: непредвиденная ошибка при скачивании: {e}")
        raise YtDlpFailure("❌ Произошла ошибка при скачивании видео. Попробуйте позже.")


def download_and_merge(url: str, height: int, lang: str, on_status=None) -> str:
    """Скачивает видео нужного качества + аудио на нужном языке и
    склеивает их через ffmpeg. Возвращает путь к готовому файлу.

    on_status(stage) вызывается при реальной смене этапа:
    'downloading' — идёт скачивание, 'converting' — идёт склейка через ffmpeg.
    Вызывается из фонового потока — сам callback должен быть потокобезопасным.

    Требует установленный ffmpeg в системе (см. Dockerfile)."""
    out_id = uuid.uuid4().hex[:12]
    out_template = os.path.join(TEMP_DOWNLOAD_DIR, f"{out_id}.%(ext)s")

    def progress_hook(d):
        if on_status and d.get("status") == "downloading":
            on_status("downloading")

    def postprocessor_hook(d):
        if on_status and d.get("status") == "started":
            on_status("converting")

    ydl_opts = {
        **ytdlp_base_opts(),
        "format": f"bestvideo[height<={height}]+bestaudio[language={lang}]/best[height<={height}]",
        "merge_output_format": "mp4",
        "outtmpl": out_template,
        "noplaylist": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # После скачивания+склейки yt-dlp кладёт финальный путь в requested_downloads
        if info.get("requested_downloads"):
            return info["requested_downloads"][0]["filepath"]
        # Фоллбэк: ищем файл по маске, если поле не заполнено
        final_path = ydl.prepare_filename(info)
        return final_path


def cleanup_file(path: str):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        logger.warning(f"Не удалось удалить временный файл: {path}")


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
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ YouTube", callback_data="src_youtube")],
            [InlineKeyboardButton(text="🎵 TikTok", callback_data="src_tiktok")],
        ]
    )
    await message.answer("Мне скачивать с чего?", reply_markup=keyboard)


SOURCE_LABELS = {"youtube": "YouTube", "tiktok": "TikTok"}


@dp.callback_query(F.data.startswith("src_"))
async def process_source_choice(callback: types.CallbackQuery):
    source = callback.data.split("_", 1)[1]
    label = SOURCE_LABELS.get(source)
    if not label:
        await callback.answer("Неизвестный источник", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔍 Ввести запрос для {label}",
                    switch_inline_query_current_chat=f"{label} ",
                )
            ]
        ]
    )
    note = (
        "" if source == "youtube" else
        "\n\n⚠️ Поиск по TikTok работает нестабильно (у TikTok нет "
        "официального API поиска) — если результатов не будет, просто "
        "пришли прямую ссылку на видео."
    )
    await callback.message.edit_text(
        f"Выбрано: <b>{label}</b>\n"
        f"Нажми на кнопку ниже — в поле ввода появится подсказка, "
        f"допиши после неё, что искать.{note}",
        reply_markup=keyboard,
    )
    await callback.answer()


PLAN_INFO = {
    "month": (150, 30, "Подписка на 1 месяц"),
    "6months": (500, 182, "Подписка на 6 месяцев"),
    "year": (800, 365, "Подписка на 1 год"),
}

PLAN_BUTTON_LABELS = {
    "month": "месяц",
    "6months": "6 месяцев",
    "year": "год",
}


@dp.message(Command("sub"))
async def cmd_subscribe(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ {price} ⭐ — {PLAN_BUTTON_LABELS[plan]}",
                    callback_data=f"buy_{plan}",
                )
            ]
            for plan, (price, _days, _title) in PLAN_INFO.items()
        ]
    )
    await message.answer(
        "✨ <b>SaveYouTube ПЛЮС</b>\n\n"
        "• Видео в качестве 4K, 2K, FULLHD, HD, 480p, 360p, 240p\n"
        "• Субтитры и скачивание плейлистов\n"
        "• Мгновенная скорость без ограничений по весу\n\n"
        "📅 <b>Выбери период:</b>",
        reply_markup=keyboard,
    )


ADMIN_IDS = {
    123456789,  # <-- твой Telegram user_id (узнать у @userinfobot)
    987654321,  # <-- user_id второго аккаунта (узнать у @userinfobot)
}


@dp.message(Command("grant"))
async def cmd_grant(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return  # обычные пользователи даже не узнают, что команда существует

    parts = message.text.split()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 365
    db_add_subscription(message.from_user.id, days)

    expires = datetime.now() + timedelta(days=days)
    await message.answer(
        f"✅ Тебе выдана подписка до <b>{expires.strftime('%d.%m.%Y')}</b> (админ-режим)."
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
    raw_text = query.query.strip()
    if not raw_text:
        return

    # Определяем источник по префиксу (подставляется кнопкой из /search):
    # "YouTube запрос..." или "TikTok запрос..."
    source = "youtube"
    text = raw_text
    lowered = raw_text.lower()
    if lowered.startswith("tiktok"):
        source = "tiktok"
        text = raw_text[len("tiktok"):].strip()
    elif lowered.startswith("youtube"):
        source = "youtube"
        text = raw_text[len("youtube"):].strip()

    if not text:
        return

    if source == "tiktok":
        # ВНИМАНИЕ: у TikTok нет официального API поиска, эта функция
        # yt-dlp неофициальная и может не работать / отвалиться в любой момент.
        search_prefix = f"tiktoksearch{SEARCH_RESULTS_LIMIT}:"
        default_thumb = None
    else:
        search_prefix = f"ytsearch{SEARCH_RESULTS_LIMIT}:"
        default_thumb = None

    ydl_opts = {**ytdlp_base_opts(), "extract_flat": True}

    def search():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"{search_prefix}{text}", download=False)
            return info.get("entries", []) if info else []

    try:
        videos = await run_ytdlp(search)
    except YtDlpFailure:
        videos = None

    results = []

    if videos:
        for i, v in enumerate(videos):
            if not v:
                continue
            title = v.get("title", "Видео")
            url = v.get("url") or v.get("webpage_url") or (
                f"https://www.youtube.com/watch?v={v.get('id')}" if source == "youtube" else ""
            )
            if not url:
                continue
            uploader = v.get("uploader") or ("YouTube" if source == "youtube" else "TikTok")
            thumbnail = v.get("thumbnail") or default_thumb or (
                f"https://img.youtube.com/vi/{v.get('id')}/hqdefault.jpg" if source == "youtube" else None
            )

            results.append(
                InlineQueryResultArticle(
                    id=str(i),
                    title=title,
                    description=f"Автор: {uploader} | Нажми, чтобы отправить",
                    thumbnail_url=thumbnail,
                    input_message_content=InputTextMessageContent(message_text=url),
                )
            )

    if not results and source == "tiktok":
        await query.answer(
            results,
            cache_time=1,
            switch_pm_text="Поиск по TikTok не сработал — пришли ссылку напрямую",
            switch_pm_parameter="start",
        )
        return

    await query.answer(results, cache_time=1)


@dp.callback_query(F.data.startswith("dl_link|"))
async def callback_dl_link(callback: types.CallbackQuery):
    _, short_id = callback.data.split("|", 1)
    url = get_cached_url(short_id)
    if not url:
        await callback.answer("⌛ Ссылка устарела, выполните поиск заново.", show_alert=True)
        return
    await callback.answer()
    asked = await maybe_ask_language(callback.message, url)
    if not asked:
        await show_qualities(callback.message, url)


@dp.message(F.text.contains("tiktok.com") | F.text.contains("vm.tiktok.com"))
async def handle_tiktok(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if "tiktok.com" in w), None)
    if not url:
        return

    wait_msg = await message.answer("⏳ Скачиваю видео из TikTok...")

    ydl_opts = {**ytdlp_base_opts(), "format": "best"}

    def get_tiktok_link():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("url") if info else None

    try:
        direct_url = await run_ytdlp(get_tiktok_link)
    except YtDlpFailure as e:
        await wait_msg.edit_text(e.user_message)
        return

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
    asked = await maybe_ask_language(message, url)
    if not asked:
        await show_qualities(message, url)


async def show_qualities(message: types.Message, url: str, short_id_override: str | None = None):
    is_sub = db_is_subscribed(message.from_user.id)
    short_id = short_id_override or cache_url(url)

    if is_sub:
        keyboard_buttons = [
            [
                InlineKeyboardButton(text="🎬 240p", callback_data=f"dl|240|{short_id}"),
                InlineKeyboardButton(text="🎬 360p", callback_data=f"dl|360|{short_id}"),
                InlineKeyboardButton(text="🎬 480p", callback_data=f"dl|480|{short_id}"),
            ],
            [
                InlineKeyboardButton(text="🎬 720p", callback_data=f"dl|720|{short_id}"),
                InlineKeyboardButton(text="🎬 1080p", callback_data=f"dl|1080|{short_id}"),
            ],
            [InlineKeyboardButton(text="🎬 4K / 2K (Ultra HD)", callback_data=f"dl|4k|{short_id}")],
            [InlineKeyboardButton(text="🎵 Аудио MP3", callback_data=f"dl|mp3|{short_id}")],
            [InlineKeyboardButton(text="🖼 Превью (обложка)", callback_data=f"dl|preview|{short_id}")],
        ]
        text_info = "✨ <b>Премиум-доступ активен!</b> Выберите формат:"
    else:
        keyboard_buttons = [
            [
                InlineKeyboardButton(text="🎬 720p", callback_data=f"dl|720|{short_id}"),
                InlineKeyboardButton(text="🎬 1080p", callback_data=f"dl|1080|{short_id}"),
            ],
            [InlineKeyboardButton(text="⭐ Больше форматов (240p–4K, MP3, превью) в подписке (/sub)", callback_data="locked")],
        ]
        text_info = "📺 Выберите стандартное качество (остальные форматы — по подписке /sub):"

    if short_id in _audio_lang_cache:
        lang = _audio_lang_cache[short_id]
        label = LANGUAGE_LABELS.get(lang, lang.upper())
        text_info += (
            f"\n\n🌐 Выбранный язык: <b>{label}</b>. Для видео скачивание "
            f"и склейка со звуком на этом языке займут больше времени, "
            f"чем обычно — бот пришлёт готовый файл, а не ссылку."
        )

    await message.answer(text_info, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))


@dp.callback_query(F.data == "locked")
async def locked_callback(callback: types.CallbackQuery):
    await callback.answer(
        "🔒 Это качество доступно только по подписке Tubetok Downloader ПЛЮС! Напишите /sub",
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

    # "Превью" — это не видео-формат, а обложка (thumbnail), обрабатываем отдельно
    if quality == "preview":
        ydl_opts = ytdlp_base_opts()

        def get_thumbnail():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None
                return info.get("thumbnail")

        try:
            thumb_url = await run_ytdlp(get_thumbnail)
        except YtDlpFailure as e:
            await callback.message.edit_text(e.user_message)
            return

        if not thumb_url:
            await callback.message.edit_text("❌ Не удалось получить превью для этого видео.")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🖼 Открыть превью", url=thumb_url)]]
        )
        await callback.message.edit_text("✅ <b>Готово!</b> Нажми, чтобы открыть превью:", reply_markup=keyboard)
        return

    ydl_opts = ytdlp_base_opts()

    chosen_lang = _audio_lang_cache.get(short_id)

    quality_heights = {"4k": 2160, "1080": 1080, "720": 720, "480": 480, "360": 360, "240": 240}

    # Если выбран язык озвучки И это видео-формат (не MP3) — единственный
    # способ дать видео именно с этим языком — скачать видео и аудио
    # отдельно, склеить через ffmpeg и отправить готовый файл в Telegram
    # (а не давать прямую ссылку, как для остальных случаев).
    if chosen_lang and quality in quality_heights:
        height = quality_heights[quality]
        await callback.message.edit_text("⏳ Обрабатываю...")

        # download_and_merge() выполняется в отдельном потоке (через executor),
        # поэтому редактируем сообщение потокобезопасно через
        # run_coroutine_threadsafe в основной event loop.
        loop = asyncio.get_running_loop()
        last_stage = {"value": None}

        STAGE_TEXTS = {
            "downloading": "📥 Скачиваю видео и звук на выбранном языке...",
            "converting": "🔄 Конвертирую (объединяю видео и звук)...",
        }

        def on_status(stage: str):
            if last_stage["value"] == stage:
                return  # не спамим повторными правками на каждый прогресс-тик
            last_stage["value"] = stage
            text = STAGE_TEXTS.get(stage)
            if not text:
                return

            async def _edit():
                try:
                    await callback.message.edit_text(text)
                except Exception:
                    pass  # сообщение могло не измениться / гонка — не critично

            asyncio.run_coroutine_threadsafe(_edit(), loop)

        local_path = None
        try:
            local_path = await run_ytdlp_download(
                lambda: download_and_merge(url, height, chosen_lang, on_status)
            )
        except YtDlpFailure as e:
            await callback.message.edit_text(e.user_message)
            return

        if not local_path or not os.path.isfile(local_path):
            await callback.message.edit_text("❌ Не удалось подготовить файл. Попробуйте другое качество.")
            return

        try:
            size_mb = os.path.getsize(local_path) / (1024 * 1024)
            if size_mb > MAX_TELEGRAM_FILE_MB:
                await callback.message.edit_text(
                    f"❌ Готовый файл весит {size_mb:.0f} МБ — это больше лимита "
                    f"Telegram на отправку файлов ботом ({MAX_TELEGRAM_FILE_MB} МБ). "
                    f"Попробуйте более низкое качество."
                )
                return

            await callback.message.edit_text("📤 Отправляю...")
            await callback.message.answer_video(
                FSInputFile(local_path),
                caption=f"✅ Готово! Язык звука: {LANGUAGE_LABELS.get(chosen_lang, chosen_lang.upper())}",
            )
            await callback.message.delete()
        finally:
            cleanup_file(local_path)
        return

    format_map = {
        "4k": "best[height<=2160]/best",
        "1080": "best[height<=1080]/best",
        "720": "best[height<=720]/best",
        "480": "best[height<=480]/best",
        "360": "best[height<=360]/best",
        "240": "best[height<=240]/best",
        "mp3": "bestaudio/best",
    }
    fmt = format_map.get(quality, "best")

    # Для MP3 язык можно применить напрямую (это одна аудиодорожка,
    # без слияния) — видео с выбранным языком уже обработано веткой выше.
    if chosen_lang and quality == "mp3":
        fmt = f"bestaudio[language={chosen_lang}]/bestaudio/best"

    ydl_opts["format"] = fmt

    def get_direct_link():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            return info.get("url") or (
                info.get("entries")[0].get("url") if "entries" in info and info.get("entries") else None
            )

    try:
        direct_url = await run_ytdlp(get_direct_link)
    except YtDlpFailure as e:
        await callback.message.edit_text(e.user_message)
        return

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


COOKIE_REFRESH_INTERVAL = 20 * 3600  # раз в 20 часов
ADMIN_NOTIFY_IDS = ADMIN_IDS  # кому слать уведомление, если авто-обновление не удалось


async def cookie_refresh_loop():
    """Раз в COOKIE_REFRESH_INTERVAL секунд пытается обновить cookies.
    Если YT_EMAIL/YT_PASSWORD не заданы — просто ничего не делает."""
    if not os.environ.get("YT_EMAIL") or not os.environ.get("YT_PASSWORD"):
        logger.info("YT_EMAIL/YT_PASSWORD не заданы — авто-обновление cookies выключено")
        return

    from cookie_refresher import refresh_cookies

    loop = asyncio.get_running_loop()
    while True:
        try:
            success = await loop.run_in_executor(None, refresh_cookies)
            if not success:
                for admin_id in ADMIN_NOTIFY_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            "⚠️ Не удалось автоматически обновить YouTube cookies "
                            "(возможно, Google запросил капчу или 2FA). "
                            "Может понадобиться ручное обновление.",
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.exception(f"Ошибка в cookie_refresh_loop: {e}")

        await asyncio.sleep(COOKIE_REFRESH_INTERVAL)


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

    await asyncio.gather(web_server(), dp.start_polling(bot), cookie_refresh_loop())


if __name__ == "__main__":
    asyncio.run(main())
