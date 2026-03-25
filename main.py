import asyncio
import aiohttp
import time
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp_socks import SocksConnector  # правильно: aiohttp_socks

# Получаем токен из переменной окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Источники прокси (только SOCKS5)
PROXY_SOURCES = [
    "https://www.proxy-list.download/api/v1/get?type=socks5",
    "https://api.proxyscrape.com/v2/?request=getcountry&country=RU&protocol=socks5&timeout=1000",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
]

def get_main_menu_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="🔍 Найти прокси", callback_data="find_proxy")],
        [types.InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

async def fetch_proxies_from_sources():
    all_proxies = set()
    async with aiohttp.ClientSession() as session:
        for url in PROXY_SOURCES:
            try:
                async with session.get(url) as resp:
                    text = await resp.text()
                    lines = text.strip().splitlines()
                    for line in lines:
                        line = line.strip()
                        if ":" in line:
                            parts = line.split(":")
                            if len(parts) >= 2:
                                ip = parts[0].strip()
                                port_str = parts[1].strip()
                                if port_str.isdigit():
                                    all_proxies.add((ip, int(port_str)))
            except Exception:
                continue
    return list(all_proxies)

async def check_proxy_speed(proxy_ip, proxy_port):
    start_time = time.time()
    connector = SocksConnector.from_url(f"socks5://{proxy_ip}:{proxy_port}")
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get('http://httpbin.org/ip') as resp:
                if resp.status == 200:
                    end_time = time.time()
                    speed = round(end_time - start_time, 2)
                    return True, speed
    except Exception:
        pass
    return False, 0

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
    await callback_query.message.answer("🔍 Поиск и проверка прокси...")
    
    proxies = await fetch_proxies_from_sources()
    working_proxies = []

    for ip, port in proxies[:10]:
        is_working, speed = await check_proxy_speed(ip, port)
        if is_working:
            working_proxies.append({"ip": ip, "port": port, "speed": speed})

    if working_proxies:
        best_proxy = min(working_proxies, key=lambda x: x["speed"])
        response = (
            f"✅ Найден рабочий прокси:\n"
            f"🌐 IP: {best_proxy['ip']}\n"
            f"🔌 Порт: {best_proxy['port']}\n"
            f"⚡ Скорость: {best_proxy['speed']} сек\n\n"
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
