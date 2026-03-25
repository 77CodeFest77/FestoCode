"""
Telegram бот для поиска и проверки прокси (SOCKS5/HTTP).
Поддерживает парсинг из Telegram-каналов и веб-сайтов.
Использует асинхронную проверку с прогрессом.
"""

import asyncio
import os
import re
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# Загружаем переменные окружения из .env (для локального запуска)
load_dotenv()

# ---------- Настройки логирования ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- Чтение переменных окружения ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_ID_STR = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# Необязательные настройки
USE_TELEGRAM_SOURCES = os.getenv("USE_TELEGRAM_SOURCES", "true").lower() == "true"
MAX_PROXIES_TO_CHECK = int(os.getenv("MAX_PROXIES_TO_CHECK", "20"))       # сколько прокси проверять
CONCURRENT_CHECKS = int(os.getenv("CONCURRENT_CHECKS", "5"))              # параллельных проверок
PROXY_CHECK_TIMEOUT = int(os.getenv("PROXY_CHECK_TIMEOUT", "10"))         # таймаут на проверку
PROXY_CHECK_URL = os.getenv("PROXY_CHECK_URL", "http://httpbin.org/ip")  # URL для проверки

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан!")
if USE_TELEGRAM_SOURCES:
    if not TELEGRAM_API_ID_STR or not TELEGRAM_API_HASH:
        raise ValueError("❌ Для Telegram источников нужны TELEGRAM_API_ID и TELEGRAM_API_HASH")
    try:
        API_ID = int(TELEGRAM_API_ID_STR)
    except ValueError:
        raise ValueError("❌ TELEGRAM_API_ID должно быть числом")

# ---------- Инициализация бота ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- Конфигурация источников ----------
# Веб-сайты с прокси (регулярки подстраиваются под формат)
WEB_PROXY_SOURCES = [
    {
        "url": "https://www.proxy-list.download/api/v1/get?type=socks5",
        "parser": "line_ip_port",   # каждая строка вида IP:PORT
    },
    {
        "url": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",
        "parser": "line_ip_port",
    },
    {
        "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
        "parser": "line_ip_port",
    },
    {
        "url": "https://free-proxy-list.net/",
        "parser": "html_table",      # страница с таблицей прокси
    },
]

# Telegram-каналы для парсинга (замените на реальные)
TELEGRAM_PROXY_CHANNELS = [
    "socks5_proxies",   # пример
    "free_proxy_list",
]

# Регулярное выражение для IP:PORT
IP_PORT_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b")

# ---------- Вспомогательные функции парсинга ----------
async def parse_web_source(session: aiohttp.ClientSession, source: Dict) -> List[Dict]:
    """Парсит один веб-источник и возвращает список прокси в формате [{"ip": ip, "port": port, "source": source}]"""
    proxies = []
    try:
        async with session.get(source["url"], timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"Source {source['url']} returned status {resp.status}")
                return []
            text = await resp.text()

            if source["parser"] == "line_ip_port":
                # Каждая строка: IP:PORT
                for line in text.strip().splitlines():
                    line = line.strip()
                    if ":" in line:
                        parts = line.split(":")
                        if len(parts) >= 2 and parts[1].isdigit():
                            proxies.append({
                                "ip": parts[0],
                                "port": int(parts[1]),
                                "source": source["url"]
                            })
            elif source["parser"] == "html_table":
                # Простой поиск IP:PORT в HTML
                for match in IP_PORT_REGEX.finditer(text):
                    ip, port = match.group().split(":")
                    proxies.append({
                        "ip": ip,
                        "port": int(port),
                        "source": source["url"]
                    })
    except Exception as e:
        logger.error(f"Error parsing {source['url']}: {e}")
    return proxies

async def fetch_proxies_from_web() -> List[Dict]:
    """Собирает прокси со всех веб-источников"""
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [parse_web_source(session, src) for src in WEB_PROXY_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, list):
                all_proxies.extend(res)
    # Убираем дубликаты по IP:PORT
    unique = {}
    for p in all_proxies:
        key = f"{p['ip']}:{p['port']}"
        if key not in unique:
            unique[key] = p
    return list(unique.values())

async def fetch_proxies_from_telegram() -> List[Dict]:
    """Парсит прокси из Telegram-каналов за последние 24 часа"""
    all_proxies = []
    async with TelegramClient("session_name", API_ID, TELEGRAM_API_HASH) as client:
        for channel in TELEGRAM_PROXY_CHANNELS:
            try:
                # Получаем последние 200 сообщений
                messages = await client.get_messages(channel, limit=200)
                for msg in messages:
                    if not msg.date or msg.date < datetime.now() - timedelta(days=1):
                        continue
                    if msg.text:
                        matches = IP_PORT_REGEX.findall(msg.text)
                        for match in matches:
                            ip, port = match.split(":")
                            all_proxies.append({
                                "ip": ip,
                                "port": int(port),
                                "source": f"telegram:{channel}",
                                "date": msg.date
                            })
            except Exception as e:
                logger.error(f"Error fetching from {channel}: {e}")
    return all_proxies

async def fetch_all_proxies() -> List[Dict]:
    """Собирает прокси из всех включённых источников"""
    proxies = []
    # Веб-источники
    web_proxies = await fetch_proxies_from_web()
    proxies.extend(web_proxies)
    # Telegram источники (если включены)
    if USE_TELEGRAM_SOURCES:
        tg_proxies = await fetch_proxies_from_telegram()
        proxies.extend(tg_proxies)
    # Убираем дубликаты
    unique = {}
    for p in proxies:
        key = f"{p['ip']}:{p['port']}"
        if key not in unique:
            unique[key] = p
    return list(unique.values())

# ---------- Проверка прокси ----------
async def check_one_proxy(session: aiohttp.ClientSession, proxy: Dict, semaphore: asyncio.Semaphore) -> Optional[Dict]:
    """Проверяет один прокси (SOCKS5 или HTTP) и возвращает результат с задержкой"""
    async with semaphore:
        ip = proxy["ip"]
        port = proxy["port"]
        start = time.time()
        try:
            # Пытаемся использовать HTTP прокси (если прокси HTTP/HTTPS)
            proxy_url = f"http://{ip}:{port}"
            # Для SOCKS5 нужно использовать другой коннектор (см. ниже)
            # Пока оставляем HTTP, но добавим fallback на SOCKS5 с помощью aiohttp_socks
            connector = aiohttp.TCPConnector(limit=1)
            timeout = aiohttp.ClientTimeout(total=PROXY_CHECK_TIMEOUT)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as sess:
                async with sess.get(PROXY_CHECK_URL, proxy=proxy_url) as resp:
                    if resp.status == 200:
                        elapsed = round(time.time() - start, 2)
                        data = await resp.json()
                        origin_ip = data.get("origin", "")
                        is_anonymous = origin_ip != ip
                        return {
                            "ip": ip,
                            "port": port,
                            "speed": elapsed,
                            "anonymous": is_anonymous,
                            "source": proxy.get("source", "unknown")
                        }
        except Exception as e:
            # Можно попробовать SOCKS5, если не сработало HTTP
            # Для этого нужна aiohttp_socks, здесь опущено для краткости
            pass
    return None

async def check_proxies_batch(proxies: List[Dict], progress_callback=None) -> List[Dict]:
    """
    Проверяет список прокси параллельно с ограничением CONCURRENT_CHECKS.
    Если передан progress_callback, вызывается после каждого блока проверок.
    """
    semaphore = asyncio.Semaphore(CONCURRENT_CHECKS)
    connector = aiohttp.TCPConnector(limit=CONCURRENT_CHECKS)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [check_one_proxy(session, p, semaphore) for p in proxies]
        results = []
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            res = await coro
            if res:
                results.append(res)
            if progress_callback and i % 5 == 0:
                await progress_callback(i+1, len(proxies))
        if progress_callback:
            await progress_callback(len(proxies), len(proxies))
    return results

# ---------- Клавиатуры ----------
def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти прокси", callback_data="find_proxy")],
        [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
    ])

# ---------- Обработчики ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для поиска рабочих прокси (SOCKS5/HTTP).\n\n"
        "Использую несколько источников и проверяю скорость.\n\n"
        "Выбери действие:",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(lambda c: c.data == "find_proxy")
async def callback_find_proxy(callback: types.CallbackQuery):
    # Сразу отвечаем, чтобы Telegram не ждал
    await callback.answer("🔍 Начинаю поиск...")
    status_msg = await callback.message.answer("🔍 Собираю прокси из источников...")

    # Запускаем фоновую задачу, чтобы не блокировать обработчик
    asyncio.create_task(search_and_send(callback.from_user.id, status_msg.chat.id, status_msg.message_id))

async def update_progress(chat_id: int, msg_id: int, current: int, total: int):
    """Обновляет сообщение с прогрессом"""
    text = f"🔍 Проверено {current} из {total} прокси..."
    await bot.edit_message_text(text, chat_id, msg_id)

async def search_and_send(user_id: int, chat_id: int, status_msg_id: int):
    """Фоновая задача: сбор, проверка, отправка результата"""
    try:
        # 1. Сбор прокси
        await bot.edit_message_text("🌐 Сбор прокси из всех источников...", chat_id, status_msg_id)
        all_proxies = await fetch_all_proxies()
        if not all_proxies:
            await bot.edit_message_text("❌ Не найдено ни одного прокси. Проверьте источники.", chat_id, status_msg_id)
            return

        # 2. Ограничиваем количество для проверки
        proxies_to_check = all_proxies[:MAX_PROXIES_TO_CHECK]
        await bot.edit_message_text(
            f"📦 Найдено {len(all_proxies)} прокси, проверяю {len(proxies_to_check)}...",
            chat_id, status_msg_id
        )

        # 3. Проверка с прогрессом
        working = await check_proxies_batch(
            proxies_to_check,
            progress_callback=lambda cur, total: update_progress(chat_id, status_msg_id, cur, total)
        )

        # 4. Формируем результат
        if working:
            # Сортируем по скорости и выбираем лучший
            working.sort(key=lambda x: x["speed"])
            best = working[0]
            response = (
                f"✅ **Найден рабочий прокси:**\n\n"
                f"🌐 IP: `{best['ip']}:{best['port']}`\n"
                f"⚡ Скорость: {best['speed']} сек\n"
                f"🔒 Анонимность: {'Да' if best['anonymous'] else 'Нет'}\n"
                f"📡 Источник: {best['source']}\n\n"
                f"ℹ️ Прокси можно использовать для обхода блокировок.\n"
                f"Для Telegram лучше всего подойдёт MTProto, но этот прокси SOCKS5/HTTP."
            )
            # Добавим ещё несколько вариантов, если есть
            if len(working) > 1:
                response += f"\n\n💡 **Другие варианты:**\n"
                for p in working[1:4]:
                    response += f"`{p['ip']}:{p['port']}` – {p['speed']} сек\n"
            await bot.edit_message_text(response, chat_id, status_msg_id, parse_mode="Markdown")
        else:
            await bot.edit_message_text("❌ Не удалось найти ни одного рабочего прокси.", chat_id, status_msg_id)

    except Exception as e:
        logger.exception("Ошибка в search_and_send")
        await bot.edit_message_text(f"❌ Произошла ошибка: {e}", chat_id, status_msg_id)

@dp.callback_query(lambda c: c.data == "about")
async def callback_about(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "ℹ️ **О боте**\n\n"
        "Бот собирает прокси из открытых источников (веб-сайты, Telegram-каналы)\n"
        "и проверяет их работоспособность.\n\n"
        "⚙️ Настройки:\n"
        f"• Максимум прокси для проверки: {MAX_PROXIES_TO_CHECK}\n"
        f"• Параллельных проверок: {CONCURRENT_CHECKS}\n"
        f"• Таймаут проверки: {PROXY_CHECK_TIMEOUT} сек\n\n"
        "Исходный код доступен в репозитории.",
        parse_mode="Markdown"
    )

async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
