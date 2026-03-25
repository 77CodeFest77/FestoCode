import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Получаем токен из переменной окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Клавиатура с кнопками
def get_main_menu_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="📋 Получить MTProto-прокси", callback_data="get_proxy")],
        [types.InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")],
        [types.InlineKeyboardButton(text="🔄 Обновить прокси", callback_data="refresh_proxy")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# Пример списка прокси (в реальной версии будет из файла/БД)
PROXY_LIST = [
    {"ip": "1.2.3.4", "port": 443, "secret": "dd1234567890abcdef"},
    {"ip": "5.6.7.8", "port": 443, "secret": "dd0987654321fedcba"},
]

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Привет! Я бот, который поможет тебе получить MTProto-прокси.\n\n"
        "MTProto позволяет обходить блокировки и использовать Telegram быстро и безопасно.\n\n"
        "Выбери действие:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard())

@dp.callback_query(lambda c: c.data == "get_proxy")
async def process_get_proxy(callback_query: types.CallbackQuery):
    if PROXY_LIST:
        proxy = PROXY_LIST[0]
        link = f"tg://proxy?server={proxy['ip']}&port={proxy['port']}&secret={proxy['secret']}"
        await callback_query.message.answer(f"🔗 Вот ваша MTProto-ссылка:\n{link}")
    else:
        await callback_query.message.answer("❌ Нет доступных прокси.")
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "about")
async def process_about(callback_query: types.CallbackQuery):
    about_text = (
        "ℹ️ **О боте:**\n\n"
        "Этот бот предоставляет MTProto-прокси, которые помогают обходить "
        "блокировки и использовать Telegram без ограничений.\n\n"
        "MTProto — это протокол, разработанный Telegram, который защищает "
        "трафик и позволяет подключаться даже при жёсткой цензуре."
    )
    await callback_query.message.answer(about_text, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "refresh_proxy")
async def process_refresh_proxy(callback_query: types.CallbackQuery):
    # Здесь ты можешь запустить процесс проверки прокси
    await callback_query.message.answer("🔄 Обновляем список прокси...")
    # В реальной версии: вызов функции проверки прокси
    await callback_query.message.answer("✅ Прокси обновлены!", reply_markup=get_main_menu_keyboard())
    await callback_query.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
