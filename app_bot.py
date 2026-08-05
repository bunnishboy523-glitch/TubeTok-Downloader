import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# Твой токен бота от BotFather
TOKEN = "ТВОЙ_ТОКЕН_БОТА"

# Ссылка на твое развернутое мини-приложение (на Render)
WEB_APP_URL = "https://t-02-1.onrender.com"

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Обработка команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Нажми на кнопку ниже, чтобы открыть поиск и скачать видео.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть ленту →", web_app=WebAppInfo(url=WEB_APP_URL))]
            ]
        )
    )

# Обработка команды /search (выдает сообщение с кнопкой-лентой, как на скриншоте)
@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть ленту →", web_app=WebAppInfo(url=WEB_APP_URL))]
        ]
    )
    await message.answer(
        "🔍 Собрал результаты в ленту — открывай.",
        reply_markup=keyboard
    )

# Обработка данных, которые пользователь выбирает и отправляет из мини-приложения
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    video_url = message.web_app_data.data
    await message.answer(f"✅ Получил ссылку из приложения:\n{video_url}\n\n*Здесь можно запустить скачивание через yt-dlp*")

# Запуск бота
async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
