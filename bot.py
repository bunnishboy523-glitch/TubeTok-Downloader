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

# Регулярное выражение: необязательный +998, затем верный код оператора и 7 цифр
PHONE_REGEX = r"(?:\+998)?(33|50|77|88|90|91|93|94|95|97|98|99)\d{7}"

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Привет! Напишите мне номер телефона <b>с +998 или без него</b> (например: <code>+998901234567</code> или <code>901234567</code>)"
    )
    await state.set_state(BotStates.waiting_for_phone)

@dp.message(BotStates.waiting_for_phone, F.text)
async def process_phone(message: types.Message, state: FSMContext):
    text = message.text.strip()
    match = re.search(PHONE_REGEX, text)
    
    if match:
        # Извлекаем найденные группы: код оператора и оставшиеся 7 цифр
        operator_code = match.group(1)
        # Получаем полный хвост из 9 цифр (код оператора + 7 цифр номера)
        full_number = operator_code + text[-7:]
        
        response_text = f"Всё готово ! https://t.me/+998{full_number}"
        await message.answer(response_text)
        # Состояние не сбрасываем, можно отправлять следующие номера
    else:
        await message.answer(
            "❌ Неверный номер или код оператора. Пожалуйста, отправьте номер в формате "
            "<code>+998901234567</code> или просто <code>901234567</code>"
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
