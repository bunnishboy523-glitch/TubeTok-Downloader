import os
import logging
import re
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Токен берется из переменных окружения Render
TOKEN = os.getenv("BOT_TOKEN")

# Инициализация бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

class BotStates(StatesGroup):
    waiting_for_phone = State()

PHONE_REGEX = r"\+998\d{9}"

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Привет! Напишите мне номер телефона в формате <b>+99812345678</b>"
    )
    await state.set_state(BotStates.waiting_for_phone)

@dp.message(BotStates.waiting_for_phone, F.text)
async def process_phone(message: types.Message, state: FSMContext):
    text = message.text.strip()
    match = re.search(PHONE_REGEX, text)
    
    if match:
        phone_number = match.group(0)
        response_text = f"Напишите мне в телеграмм с этого номера {phone_number}"
        await message.answer(response_text)
        await state.clear()
    else:
        await message.answer(
            "❌ Неверный формат. Пожалуйста, отправьте номер в формате: <code>+99812345678</code>"
        )

# Фиктивный веб-сервер для удовлетворения требований бесплатного Web Service на Render
async def handle_health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render автоматически передает порт в переменную PORT
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    # Запускаем веб-сервер параллельно с ботом
    await start_web_server()
    # Запускаем Long Polling бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
