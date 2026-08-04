import os
import logging
import re
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Токен берется из переменных окружения Render
TOKEN = os.getenv("BOT_TOKEN")

# Исправленная инициализация бота
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

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
