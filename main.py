"""
Telegram бот для поиска и проверки прокси (SOCKS5/HTTP).
Поддерживает парсинг из Telegram-каналов и веб-сайтов.
Автоматически создаёт сессию Telethon при локальном запуске.
"""

import asyncio
import os
import re
import time
import sys
import base64
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from telethon import TelegramClient

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
USE_TELEGRAM_SOURCES = os.getenv("USE_TELEGRAM_SOURCES", "true").lower() == "true"
MAX_PROXIES_TO_CHECK = int(os.getenv("MAX_PROXIES_TO_CHECK", "20"))
CONCURRENT_CHECKS = int(os.getenv("CONCURRENT_CHECKS", "5"))
PROXY_CHECK_TIMEOUT = int(os.getenv("PROXY_CHECK_TIMEOUT", "10"))
PROXY_CHECK_URL = os.getenv("PROXY_CHECK_URL", "http://httpbin.org/ip")

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
WEB_PROXY_SOURCES = [
    {"url": "https://www.proxy-list.download/api/v1/get?type=socks5", "parser": "line_ip_port"},
    {"url": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all", "parser": "line_ip_port"},
    {"url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt", "parser": "line_ip_port"},
    {"url": "https://free-proxy-list.net/", "parser": "html_table"},
]

TELEGRAM_PROXY_CHANNELS = [
    "socks5_proxies",   # замените на реальные
    "free_proxy_list",
]

IP_PORT_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b")
SESSION_FILE = "session_name.session"

# ---------- Управление сессией Telethon ----------
async def get_telegram_client() -> TelegramClient:
    """
    Возвращает клиент Telethon с существующей сессией.
    Если сессии нет:
      - в GitHub Actions: выбрасывает ошибку
      - локально: запускает интерактивное создание сессии и завершает бота
    """
    # Если задана переменная окружения с base64 сессией – восстанавливаем
    session_b64 = os.getenv("TELEGRAM_SESSION_B64")
    if session_b64 and not os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "wb") as f:
                f.write(base64.b64decode(session_b64))
            logger.info("Сессия восстановлена из переменной TELEGRAM_SESSION_B64")
        except Exception as e:
            logger.error(f"Не удалось восстановить сессию: {e}")

    if os.path.exists(SESSION_FILE):
        return TelegramClient(SESSION_FILE, API_ID, TELEGRAM_API_HASH)

    # Сессии нет
    if os.getenv("GITHUB_ACTIONS") == "true":
        raise RuntimeError(
            "Сессия Telethon не найдена в GitHub Actions. "
            "Добавьте секрет TELEGRAM_SESSION_B64 с base64-кодированным файлом сессии."
        )

    # Локальный режим – интерактивное создание сессии
    print("\n📱 Сессия Telethon не найдена. Создаём новую...")
    client = TelegramClient(SESSION_FILE, API_ID, TELEGRAM_API_HASH)
    await client.start()
    print("✅ Сессия создана. Перезапустите бота.")
    await client.disconnect()
    sys.exit(0)  # Выходим, чтобы бот перезапустился с готовой сессией

# ---------- Парсинг прокси ----------
async def parse_web_source(session: aiohttp.ClientSession, source: Dict) -> List[Dict]:
    proxies = []
    try:
        async with session.get(source["url"], timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
            if source["parser"] == "line_ip_port":
                for line in text.strip().splitlines():
                    line = line.strip()
                    if ":" in line:
                        parts = line.split(":")
                        if len(parts) >= 2 and parts[1].isdigit():
                            proxies.append({"ip": parts[0], "port": int(parts[1]), "source": source["url"]})
            elif source["parser"] == "html_table":
                for match in IP_PORT_REGEX.finditer(text):
                    ip, port = match.group().split(":")
                    proxies.append({"ip": ip, "port": int(port), "source": source["url"]})
    except Exception as e:
        logger.error(f"Ошибка при парсинге {source['url']}: {e}")
    return proxies

async def fetch_proxies_from_web() -> List[Dict]:
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [parse_web_source(session, src) for src in WEB_PROXY_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, list):
                all_proxies.extend(res)
    # Удаление дубликатов
    unique = {}
    for p in all_proxies:
        key = f"{p['ip']}:{p['port']}"
        if key not in unique:
            unique[key] = p
    return list(unique.values())

async def fetch_proxies_from_telegram() -> List[Dict]:
    client = await get_telegram_client()
    async with client:
        all_proxies = []
        for channel in TELEGRAM_PROXY_CHANNELS:
            try:
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
                logger.error(f"Ошибка получения из {channel}: {e}")
        return all_proxies

async def fetch_all_proxies() -> List[Dict]:
    proxies = await fetch_proxies_from_web()
    if USE_TELEGRAM_SOURCES:
        tg_proxies = await fetch_proxies_from_telegram()
        proxies.extend(tg_proxies)
    # Удаление дубликатов
    unique = {}
    for p in proxies:
        key = f"{p['ip']}:{p['port']}"
        if key not in unique:
            unique[key] = p
    return list(unique.values())

# ---------- Проверка прокси ----------
async def check_one_proxy(session: aiohttp.ClientSession, proxy: Dict, semaphore: asyncio.Semaphore) -> Optional[Dict]:
    async with semaphore:
        ip = proxy["ip"]
        port = proxy["port"]
        start = time.time()
        try:
            proxy_url = f"http://{ip}:{port}"
            timeout = aiohttp.ClientTimeout(total=PROXY_CHECK_TIMEOUT)
            async with session.get(PROXY_CHECK_URL, proxy=proxy_url, timeout=timeout) as resp:
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
        except Exception:
            pass
    return None

async def check_proxies_batch(proxies: List[Dict], progress_callback=None) -> List[Dict]:
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
    await callback.answer("🔍 Начинаю поиск...")
    status_msg = await callback.message.answer("🔍 Собираю прокси из источников...")
    asyncio.create_task(search_and_send(callback.from_user.id, status_msg.chat.id, status_msg.message_id))

async def update_progress(chat_id: int, msg_id: int, current: int, total: int):
    text = f"🔍 Проверено {current} из {total} прокси..."
    await bot.edit_message_text(text, chat_id, msg_id)

async def search_and_send(user_id: int, chat_id: int, status_msg_id: int):
    try:
        await bot.edit_message_text("🌐 Сбор прокси из всех источников...", chat_id, status_msg_id)
        all_proxies = await fetch_all_proxies()
        if not all_proxies:
            await bot.edit_message_text("❌ Не найдено ни одного прокси. Проверьте источники.", chat_id, status_msg_id)
            return

        proxies_to_check = all_proxies[:MAX_PROXIES_TO_CHECK]
        await bot.edit_message_text(
            f"📦 Найдено {len(all_proxies)} прокси, проверяю {len(proxies_to_check)}...",
            chat_id, status_msg_id
        )

        working = await check_proxies_batch(
            proxies_to_check,
            progress_callback=lambda cur, total: update_progress(chat_id, status_msg_id, cur, total)
        )

        if working:
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
