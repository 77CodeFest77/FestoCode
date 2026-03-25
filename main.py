"""
Telegram бот:
- поиск SOCKS5 прокси
- поиск VPN ботов с проверкой пробного периода
- получение информации о пользователе по username
"""

import asyncio
import os
import re
import time
import sys
import base64
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import aiohttp
from aiohttp_socks import SocksConnector
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- Переменные окружения ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_ID_STR = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
USE_TELEGRAM_SOURCES = os.getenv("USE_TELEGRAM_SOURCES", "true").lower() == "true"
MAX_PROXIES_TO_CHECK = int(os.getenv("MAX_PROXIES_TO_CHECK", "20"))
CONCURRENT_CHECKS = int(os.getenv("CONCURRENT_CHECKS", "5"))
PROXY_CHECK_TIMEOUT = int(os.getenv("PROXY_CHECK_TIMEOUT", "10"))
PROXY_CHECK_URL = os.getenv("PROXY_CHECK_URL", "http://httpbin.org/ip")
MAX_VPN_BOTS_TO_CHECK = int(os.getenv("MAX_VPN_BOTS_TO_CHECK", "10"))

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан!")
if USE_TELEGRAM_SOURCES:
    if not TELEGRAM_API_ID_STR or not TELEGRAM_API_HASH:
        raise ValueError("❌ Для Telegram источников нужны TELEGRAM_API_ID и TELEGRAM_API_HASH")
    API_ID = int(TELEGRAM_API_ID_STR)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- FSM состояния ----------
class UserInfoState(StatesGroup):
    waiting_for_username = State()

# ---------- Конфигурация ----------
WEB_PROXY_SOURCES = [
    {"url": "https://www.proxy-list.download/api/v1/get?type=socks5", "parser": "line_ip_port"},
    {"url": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all", "parser": "line_ip_port"},
    {"url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt", "parser": "line_ip_port"},
]

TELEGRAM_PROXY_CHANNELS = [
    "socks5_proxies",          # замените на реальные
    "free_proxy_list",
]

VPN_BOT_CHANNELS = [
    "vpn_bot_list",            # замените на реальные
    "free_vpn_bots",
]

IP_PORT_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b")
BOT_LINK_REGEX = re.compile(r"@[a-zA-Z0-9_]{5,32}\b|https?://t\.me/[a-zA-Z0-9_]{5,32}\b")
VPN_KEYWORDS = re.compile(r"(VPN|vpn|пробный|бесплатный|free|trial|demo|тестовый|промо)", re.IGNORECASE)

SESSION_FILE = "session_name.session"

# ---------- Управление сессией Telethon ----------
async def get_telegram_client() -> TelegramClient:
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

    if os.getenv("GITHUB_ACTIONS") == "true":
        raise RuntimeError("Сессия Telethon не найдена. Добавьте секрет TELEGRAM_SESSION_B64.")

    print("\n📱 Сессия не найдена. Создаём новую...")
    client = TelegramClient(SESSION_FILE, API_ID, TELEGRAM_API_HASH)
    await client.start()
    print("✅ Сессия создана. Перезапустите бота.")
    await client.disconnect()
    sys.exit(0)

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

# ---------- Поиск VPN ботов ----------
async def fetch_vpn_bots_from_telegram() -> List[Dict]:
    client = await get_telegram_client()
    async with client:
        bots = []
        for channel in VPN_BOT_CHANNELS:
            try:
                messages = await client.get_messages(channel, limit=200)
                for msg in messages:
                    if not msg.date or msg.date < datetime.now() - timedelta(days=7):
                        continue
                    if msg.text and VPN_KEYWORDS.search(msg.text):
                        links = BOT_LINK_REGEX.findall(msg.text)
                        for link in links:
                            if link.startswith("https://t.me/"):
                                username = link.split("/")[-1]
                                link = f"@{username}"
                            bots.append({
                                "link": link,
                                "source": f"telegram:{channel}",
                                "date": msg.date,
                                "text": msg.text[:200]
                            })
            except Exception as e:
                logger.error(f"Ошибка получения из {channel}: {e}")
        unique = {}
        for b in bots:
            key = b["link"]
            if key not in unique:
                unique[key] = b
        return list(unique.values())

async def check_one_vpn_bot(bot_link: str, client: TelegramClient) -> Tuple[str, str, bool]:
    try:
        entity = await client.get_entity(bot_link)
        async with client.conversation(entity, timeout=30) as conv:
            await conv.send_message('/start')
            response = await conv.get_response()
            response_text = response.text if response.text else "(нет текста)"
            has_trial = bool(VPN_KEYWORDS.search(response_text))
            return bot_link, response_text, has_trial
    except FloodWaitError as e:
        logger.warning(f"Flood wait {e.seconds} seconds for bot {bot_link}")
        await asyncio.sleep(e.seconds)
        return bot_link, "Flood wait", False
    except Exception as e:
        logger.error(f"Ошибка при проверке бота {bot_link}: {e}")
        return bot_link, f"Ошибка: {e}", False

async def check_vpn_bots(bots: List[Dict], progress_callback=None) -> List[Dict]:
    client = await get_telegram_client()
    async with client:
        results = []
        for i, bot_info in enumerate(bots[:MAX_VPN_BOTS_TO_CHECK]):
            link = bot_info["link"]
            logger.info(f"Проверяю бота {link}...")
            link, resp, has_trial = await check_one_vpn_bot(link, client)
            results.append({
                "link": link,
                "response": resp,
                "has_trial": has_trial,
                "source": bot_info["source"]
            })
            if progress_callback:
                await progress_callback(i+1, len(bots[:MAX_VPN_BOTS_TO_CHECK]))
            await asyncio.sleep(1)
        return results

# ---------- Проверка прокси (SOCKS5) ----------
async def check_proxy_socks5(proxy_ip: str, proxy_port: int) -> Tuple[bool, float, bool]:
    start = time.time()
    connector = SocksConnector.from_url(f"socks5://{proxy_ip}:{proxy_port}")
    timeout = aiohttp.ClientTimeout(total=PROXY_CHECK_TIMEOUT)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(PROXY_CHECK_URL) as resp:
                if resp.status == 200:
                    elapsed = round(time.time() - start, 2)
                    data = await resp.json()
                    origin_ip = data.get("origin", "")
                    is_anonymous = origin_ip != proxy_ip
                    return True, elapsed, is_anonymous
    except Exception:
        pass
    return False, 0, False

async def check_proxies_batch(proxies: List[Dict], progress_callback=None) -> List[Dict]:
    semaphore = asyncio.Semaphore(CONCURRENT_CHECKS)
    tasks = []
    for p in proxies:
        async def task(proxy):
            async with semaphore:
                ok, speed, anon = await check_proxy_socks5(proxy["ip"], proxy["port"])
                if ok:
                    return {
                        "ip": proxy["ip"],
                        "port": proxy["port"],
                        "speed": speed,
                        "anonymous": anon,
                        "source": proxy.get("source", "unknown")
                    }
                return None
        tasks.append(task(p))

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
        [InlineKeyboardButton(text="🤖 Найти VPN ботов", callback_data="find_vpn")],
        [InlineKeyboardButton(text="👤 Инфо о пользователе", callback_data="user_info")],
        [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
    ])

# ---------- Обработчики ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я могу:\n"
        "• Найти рабочие SOCKS5 прокси\n"
        "• Найти VPN ботов в Telegram и проверить пробный период\n"
        "• Показать информацию о пользователе по username\n\n"
        "Выбери действие:",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(lambda c: c.data == "find_proxy")
async def callback_find_proxy(callback: types.CallbackQuery):
    await callback.answer("🔍 Начинаю поиск прокси...")
    status_msg = await callback.message.answer("🔍 Собираю прокси из источников...")
    asyncio.create_task(search_and_send_proxy(callback.from_user.id, status_msg.chat.id, status_msg.message_id))

@dp.callback_query(lambda c: c.data == "find_vpn")
async def callback_find_vpn(callback: types.CallbackQuery):
    await callback.answer("🤖 Ищу VPN ботов...")
    status_msg = await callback.message.answer("🔍 Поиск VPN ботов в Telegram...")
    asyncio.create_task(search_and_send_vpn(callback.from_user.id, status_msg.chat.id, status_msg.message_id))

@dp.callback_query(lambda c: c.data == "user_info")
async def callback_user_info(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите username пользователя (с @ или без):")
    await state.set_state(UserInfoState.waiting_for_username)

@dp.callback_query(lambda c: c.data == "about")
async def callback_about(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "ℹ️ **О боте**\n\n"
        "**Прокси:** собираются из веб-источников и Telegram-каналов.\n"
        "Проверяются SOCKS5 прокси на скорость и анонимность.\n\n"
        "**VPN боты:** собираются из Telegram-каналов, затем каждому отправляется `/start`.\n"
        "Если в ответе есть ключевые слова (пробный, бесплатный, trial и т.п.), бот отмечается как имеющий пробный период.\n\n"
        "**Информация о пользователе:** по username получает публичные данные: ID, имя, био, статус и т.д.\n\n"
        "⚠️ *Примечания:*\n"
        "- Номер телефона виден только если пользователь в ваших контактах.\n"
        "- Онлайн-статус доступен только для контактов.\n"
        "- Нельзя узнать список групп, в которых состоит пользователь.\n\n"
        "⚙️ Настройки:\n"
        f"• Максимум прокси для проверки: {MAX_PROXIES_TO_CHECK}\n"
        f"• Максимум VPN ботов для проверки: {MAX_VPN_BOTS_TO_CHECK}\n"
        f"• Параллельных проверок: {CONCURRENT_CHECKS}\n"
        f"• Таймаут проверки: {PROXY_CHECK_TIMEOUT} сек\n\n"
        "Исходный код в репозитории.",
        parse_mode="Markdown"
    )

# ---------- Поиск прокси (фоновая задача) ----------
async def update_progress(chat_id: int, msg_id: int, current: int, total: int):
    text = f"🔍 Проверено {current} из {total} прокси..."
    await bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id)

async def search_and_send_proxy(user_id: int, chat_id: int, status_msg_id: int):
    try:
        await bot.edit_message_text(
            text="🌐 Сбор прокси из всех источников...",
            chat_id=chat_id,
            message_id=status_msg_id
        )
        all_proxies = await fetch_proxies_from_web()
        if USE_TELEGRAM_SOURCES:
            tg_proxies = await fetch_proxies_from_telegram()
            all_proxies.extend(tg_proxies)

        if not all_proxies:
            await bot.edit_message_text(
                text="❌ Не найдено ни одного прокси. Проверьте источники.",
                chat_id=chat_id,
                message_id=status_msg_id
            )
            return

        unique = {}
        for p in all_proxies:
            key = f"{p['ip']}:{p['port']}"
            if key not in unique:
                unique[key] = p
        all_proxies = list(unique.values())

        proxies_to_check = all_proxies[:MAX_PROXIES_TO_CHECK]
        await bot.edit_message_text(
            text=f"📦 Найдено {len(all_proxies)} прокси, проверяю {len(proxies_to_check)}...",
            chat_id=chat_id,
            message_id=status_msg_id
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
                f"ℹ️ Это SOCKS5 прокси. Используйте для обхода блокировок."
            )
            if len(working) > 1:
                response += f"\n\n💡 **Другие варианты:**\n"
                for p in working[1:4]:
                    response += f"`{p['ip']}:{p['port']}` – {p['speed']} сек\n"
            await bot.edit_message_text(
                text=response,
                chat_id=chat_id,
                message_id=status_msg_id,
                parse_mode="Markdown"
            )
        else:
            await bot.edit_message_text(
                text="❌ Не удалось найти ни одного рабочего прокси.",
                chat_id=chat_id,
                message_id=status_msg_id
            )

    except Exception as e:
        logger.exception("Ошибка в search_and_send_proxy")
        await bot.edit_message_text(
            text=f"❌ Ошибка: {e}",
            chat_id=chat_id,
            message_id=status_msg_id
        )

# ---------- Поиск и проверка VPN ботов ----------
async def update_vpn_progress(chat_id: int, msg_id: int, current: int, total: int):
    text = f"🤖 Проверено {current} из {total} ботов..."
    await bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id)

async def search_and_send_vpn(user_id: int, chat_id: int, status_msg_id: int):
    try:
        await bot.edit_message_text(
            text="🤖 Ищу VPN ботов в Telegram...",
            chat_id=chat_id,
            message_id=status_msg_id
        )
        if not USE_TELEGRAM_SOURCES:
            await bot.edit_message_text(
                text="❌ Поиск VPN ботов возможен только при включённых Telegram-источниках (USE_TELEGRAM_SOURCES=true).",
                chat_id=chat_id,
                message_id=status_msg_id
            )
            return

        bots = await fetch_vpn_bots_from_telegram()
        if not bots:
            await bot.edit_message_text(
                text="❌ Не найдено VPN ботов в указанных каналах. Проверьте настройки каналов.",
                chat_id=chat_id,
                message_id=status_msg_id
            )
            return

        bots_to_check = bots[:MAX_VPN_BOTS_TO_CHECK]
        await bot.edit_message_text(
            text=f"📦 Найдено {len(bots)} ботов, проверяю {len(bots_to_check)}...",
            chat_id=chat_id,
            message_id=status_msg_id
        )

        results = await check_vpn_bots(
            bots_to_check,
            progress_callback=lambda cur, total: update_vpn_progress(chat_id, status_msg_id, cur, total)
        )

        if results:
            response = "🤖 **Результаты проверки VPN ботов:**\n\n"
            for r in results:
                emoji = "✅" if r["has_trial"] else "❌"
                response += f"{emoji} {r['link']}\n"
                if r["has_trial"]:
                    short_response = r["response"][:100].replace("\n", " ")
                    response += f"   📝 *Ответ:* {short_response}...\n"
                response += f"   📡 *Источник:* {r['source']}\n\n"
            response += "⚠️ *Примечание:* Проверка проводилась отправкой `/start`. "
            response += "Если бот использует кнопки, ответ может быть неполным."
        else:
            response = "❌ Не удалось проверить ни одного бота."

        await bot.edit_message_text(
            text=response,
            chat_id=chat_id,
            message_id=status_msg_id,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.exception("Ошибка в search_and_send_vpn")
        await bot.edit_message_text(
            text=f"❌ Ошибка: {e}",
            chat_id=chat_id,
            message_id=status_msg_id
        )

# ---------- Обработчик ввода username для информации о пользователе ----------
@dp.message(UserInfoState.waiting_for_username)
async def process_username(message: types.Message, state: FSMContext):
    username = message.text.strip().lstrip('@')
    if not username:
        await message.answer("❌ Вы не ввели username.")
        await state.clear()
        return

    status_msg = await message.answer(f"🔍 Ищу пользователя @{username}...")

    try:
        client = await get_telegram_client()
        async with client:
            user = await client.get_entity(username)
    except ValueError:
        await status_msg.edit_text(f"❌ Пользователь @{username} не найден.")
        await state.clear()
        return
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        await state.clear()
        return

    # Формируем ответ
    info = f"👤 **Информация о пользователе @{user.username}**\n\n"
    info += f"🆔 ID: `{user.id}`\n"
    info += f"📛 Имя: {user.first_name or '—'}\n"
    if user.last_name:
        info += f"📛 Фамилия: {user.last_name}\n"
    if user.bio:
        info += f"📝 О себе: {user.bio}\n"
    info += f"🤖 Бот: {'Да' if user.bot else 'Нет'}\n"
    if hasattr(user, 'phone') and user.phone:
        info += f"📞 Телефон: `{user.phone}`\n"
    if user.photo:
        # Получаем ссылку на фото (только если есть)
        info += f"🖼️ Фото: есть (не показывается в тексте)\n"
    if hasattr(user, 'status') and user.status:
        info += f"🟢 Статус: {user.status}\n"

    await status_msg.edit_text(info, parse_mode="Markdown")
    await state.clear()

# ---------- Команда /userinfo ----------
@dp.message(Command("userinfo"))
async def cmd_userinfo(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /userinfo @username\nили нажмите кнопку 'Инфо о пользователе' и введите username.")
        return
    username = args[1].lstrip('@')
    if not username:
        await message.answer("❌ Вы не ввели username.")
        return

    status_msg = await message.answer(f"🔍 Ищу пользователя @{username}...")

    try:
        client = await get_telegram_client()
        async with client:
            user = await client.get_entity(username)
    except ValueError:
        await status_msg.edit_text(f"❌ Пользователь @{username} не найден.")
        return
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        return

    info = f"👤 **Информация о пользователе @{user.username}**\n\n"
    info += f"🆔 ID: `{user.id}`\n"
    info += f"📛 Имя: {user.first_name or '—'}\n"
    if user.last_name:
        info += f"📛 Фамилия: {user.last_name}\n"
    if user.bio:
        info += f"📝 О себе: {user.bio}\n"
    info += f"🤖 Бот: {'Да' if user.bot else 'Нет'}\n"
    if hasattr(user, 'phone') and user.phone:
        info += f"📞 Телефон: `{user.phone}`\n"
    if user.photo:
        info += f"🖼️ Фото: есть (не показывается в тексте)\n"
    if hasattr(user, 'status') and user.status:
        info += f"🟢 Статус: {user.status}\n"

    await status_msg.edit_text(info, parse_mode="Markdown")

# ---------- Запуск ----------
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
