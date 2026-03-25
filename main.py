import asyncio
import os
import re
import time
import aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from telethon import TelegramClient

# ---------- Чтение переменных окружения ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_ID_STR = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# Проверяем, что все необходимые переменные заданы
if not BOT_TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не установлена!")
if not TELEGRAM_API_ID_STR:
    raise ValueError("❌ Переменная окружения TELEGRAM_API_ID не установлена!")
if not TELEGRAM_API_HASH:
    raise ValueError("❌ Переменная окружения TELEGRAM_API_HASH не установлена!")

# Преобразуем API_ID в число (после проверки, что строка не пуста)
try:
    API_ID = int(TELEGRAM_API_ID_STR)
except ValueError:
    raise ValueError("❌ TELEGRAM_API_ID должно быть числом!")

# ---------- Инициализация бота ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- Настройки ----------
# Каналы, из которых будем парсить прокси (измените на реальные)
PROXY_CHANNELS = [
    "socks5_proxies",  # пример, замените на существующие каналы
    "free_proxy_list",
]

# Регулярное выражение для поиска IP:PORT
IP_PORT_REGEX = r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b"

# ---------- Клавиатуры ----------
def get_main_menu_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="🔍 Найти прокси", callback_data="find_proxy")],
        [types.InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# ---------- Получение прокси из Telegram-каналов ----------
async def fetch_proxies_from_telegram():
    """Парсит прокси из сообщений указанных каналов за последние 24 часа."""
    all_proxies = []
    async with TelegramClient("session_name", API_ID, TELEGRAM_API_HASH) as client:
        for channel in PROXY_CHANNELS:
            try:
                messages = await client.get_messages(channel, limit=100)
                for message in messages:
                    # Фильтруем только сообщения за последние сутки
                    if message.date and message.date > datetime.now() - timedelta(days=1):
                        if message.text:  # проверяем наличие текста
                            matches = re.findall(IP_PORT_REGEX, message.text)
                            for match in matches:
                                ip, port = match.split(":")
                                all_proxies.append({
                                    "ip": ip,
                                    "port": int(port),
                                    "date": message.date
                                })
            except Exception as e:
                print(f"⚠️ Ошибка при получении сообщений из канала {channel}: {e}")
    return all_proxies

# ---------- Проверка работоспособности прокси ----------
async def check_proxy(proxy_ip, proxy_port):
    """Проверяет прокси через httpbin.org (работает только для HTTP/HTTPS)."""
    start_time = time.time()
    try:
        connector = aiohttp.TCPConnector(limit=1)
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            proxy_url = f"http://{proxy_ip}:{proxy_port}"
            async with session.get('http://httpbin.org/ip', proxy=proxy_url) as resp:
                if resp.status == 200:
                    end_time = time.time()
                    speed = round(end_time - start_time, 2)
                    data = await resp.json()
                    origin_ip = data.get("origin", "")
                    is_anonymous = origin_ip != proxy_ip
                    return True, speed, is_anonymous
    except Exception:
        pass
    return False, 0, False

# ---------- Обработчики команд и callback-запросов ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Привет! Я бот, который находит и проверяет прокси.\n\n"
        "MTProto позволяет обходить блокировки и использовать Telegram быстро и безопасно.\n\n"
        "Выбери действие:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard())

@dp.callback_query(lambda c: c.data == "find_proxy")
async def process_find_proxy(callback_query: types.CallbackQuery):
    await callback_query.message.answer("🔍 Поиск прокси в Telegram...")
    proxies = await fetch_proxies_from_telegram()
    working_proxies = []

    for proxy in proxies:
        is_working, speed, is_anonymous = await check_proxy(proxy["ip"], proxy["port"])
        if is_working:
            working_proxies.append({
                "ip": proxy["ip"],
                "port": proxy["port"],
                "speed": speed,
                "is_anonymous": is_anonymous
            })

    if working_proxies:
        best_proxy = min(working_proxies, key=lambda x: x["speed"])
        response = (
            f"✅ Найден рабочий прокси:\n"
            f"🌐 IP: {best_proxy['ip']}\n"
            f"🔌 Порт: {best_proxy['port']}\n"
            f"⚡ Скорость: {best_proxy['speed']} сек\n"
            f"🔒 Анонимность: {'Да' if best_proxy['is_anonymous'] else 'Нет'}\n\n"
            f"ℹ️ Этот прокси SOCKS5. Для использования в Telegram, установите его вручную или используйте с VPN-приложением."
        )
        await callback_query.message.answer(response)
    else:
        await callback_query.message.answer("❌ Не удалось найти рабочие прокси.")

    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "about")
async def process_about(callback_query: types.CallbackQuery):
    about_text = (
        "ℹ️ **О боте:**\n\n"
        "Этот бот предоставляет SOCKS5-прокси, которые помогают обходить "
        "блокировки и использовать Telegram без ограничений.\n\n"
        "MTProto — это протокол, разработанный Telegram, который защищает "
        "трафик и позволяет подключаться даже при жёсткой цензуре."
    )
    await callback_query.message.answer(about_text, parse_mode="Markdown")
    await callback_query.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
