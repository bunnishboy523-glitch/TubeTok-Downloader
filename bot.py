import asyncio
from aiogram import types, F
from aiogram.exceptions import TelegramBadRequest

@router.message(F.text) # Или твой фильтр / состояние FSM
async def handle_phone(message: types.Message):
    phone = message.text.strip()
    
    # Твоя проверка номера (например, если нет плюса или не цифры)
    if not phone.startswith("+") or not phone[1:].isdigit():
        
        # 1. Удаляем сообщение пользователя с неверным номером
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
            
        # 2. Отправляем сообщение с предупреждением
        warning_msg = await message.answer(
            "❌ Неверный формат номера! Пожалуйста, укажи номер в правильном формате."
        )
        
        # 3. Ждем 5 секунд
        await asyncio.sleep(5)
        
        # 4. Удаляем сообщение бота
        try:
            await warning_msg.delete()
        except TelegramBadRequest:
            pass
            
        return

    # Если номер верный
    await message.answer("✅ Номер успешно сохранен!")
