import asyncio
import aiohttp
import os
import re
import time                                     # <-- добавлен импорт time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Получаем токен из переменной окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Список популярных сайтов с прокси
PROXY_SITES = [
    "https://www.proxy-list.download/SOCKS5",
    "https://free-proxy-list.net/",
    "https://spys.me/socks.html",
]

# Регулярное выражение для поиска IP:PORT
IP_PORT_REGEX = r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b"

# Клавиатура с кнопками
def get_main_menu_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="🔍 Найти прокси", callback_data="find_proxy")],
        [types.InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_date_filter_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="📅 За день", callback_data="date_1")],
        [types.InlineKeyboardButton(text="📅 За 3 дня", callback_data="date_3")],
        [types.InlineKeyboardButton(text="📅 За неделю", callback_data="date_7")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

async def fetch_proxies_from_web():
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        for url in PROXY_SITES:
            try:
                async with session.get(url) as resp:
                    text = await resp.text()
                    # Ищем IP:PORT с помощью регулярного выражения
                    matches = re.findall(IP_PORT_REGEX, text)
                    for match in matches:
                        ip, port = match.split(":")
                        all_proxies.append({"ip": ip, "port": int(port), "date": datetime.now()})
            except Exception:
                continue
    return all_proxies

async def check_proxy(proxy_ip, proxy_port):
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
    await callback_query.message.answer("📅 Выберите дату публикации прокси:", reply_markup=get_date_filter_keyboard())
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("date_"))
async def process_date_filter(callback_query: types.CallbackQuery):
    days = int(callback_query.data.split("_")[1])
    date_threshold = datetime.now() - timedelta(days=days)

    await callback_query.message.answer(f"🔍 Поиск прокси за последние {days} дней...")
    
    proxies = await fetch_proxies_from_web()
    working_proxies = []                                   # <-- исправлено: перенесено на новую строку

    for proxy in proxies:
        if proxy["date"] >= date_threshold:
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
