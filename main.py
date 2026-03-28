import asyncio
import os
import base64
import time
import random
import aiohttp
import re
import json
import logging
from typing import Dict, List, Any, Optional
from telethon import TelegramClient, events
from telethon.tl.types import User
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Конфигурация ----------
API_ID = int(os.getenv("TELEGRAM_API_ID", "34126767"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "44f1cdcc4c6544d60fe06be1b319d2dd")
SESSION_FILE = "session_name.session"

OPEN_KEY = os.getenv("OPEN_KEY")
if not OPEN_KEY:
    logger.error("OPEN_KEY не задан, бот не сможет отвечать!")
groq_client = Groq(api_key=OPEN_KEY) if OPEN_KEY else None

# Восстановление сессии
session_b64 = os.getenv("TELEGRAM_SESSION_B64")
if session_b64 and not os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "wb") as f:
        f.write(base64.b64decode(session_b64))

client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

# ---------- Хранилища ----------
games: Dict[int, dict] = {}
ai_enabled: Dict[int, bool] = {}               # чат -> включён ли ИИ
ai_busy: Dict[int, bool] = {}
conversation_history: Dict[int, List[dict]] = {}

# Хранилище для режима "жалкий"
garbage_mode: Dict[int, bool] = {}               # чат -> активен ли режим
original_messages: Dict[int, Dict[int, str]] = {} # чат -> {message_id: original_text}
garbage_tasks: Dict[int, asyncio.Task] = {}      # чат -> задача переливания

# ---------- Класс игры ----------
class TicTacToe:
    def __init__(self, player1_id: int, player2_id):
        self.player1 = player1_id
        self.player2 = player2_id
        self.board = [None] * 9
        self.current_player = player1_id
        self.winner = None
        self.draw = False
        self.is_bot_game = (player2_id == "bot")

    def make_move(self, player_id, position: int) -> bool:
        if self.winner or self.draw:
            return False
        if player_id != self.current_player:
            return False
        if position < 1 or position > 9 or self.board[position-1] is not None:
            return False

        symbol = 'X' if player_id == self.player1 else 'O'
        self.board[position-1] = symbol
        self._check_win()
        self._check_draw()
        if not self.winner and not self.draw:
            self.current_player = self.player2 if player_id == self.player1 else self.player1
        return True

    def _check_win(self):
        lines = [
            [0,1,2], [3,4,5], [6,7,8],
            [0,3,6], [1,4,7], [2,5,8],
            [0,4,8], [2,4,6]
        ]
        for line in lines:
            a,b,c = line
            if self.board[a] and self.board[a] == self.board[b] == self.board[c]:
                self.winner = self.player1 if self.board[a] == 'X' else self.player2
                return

    def _check_draw(self):
        if all(cell is not None for cell in self.board):
            self.draw = True

    def render_board(self) -> str:
        symbols = []
        for i, cell in enumerate(self.board):
            if cell is None:
                symbols.append(str(i+1))
            else:
                symbols.append(cell)
        return (
            f"┌───┬───┬───┐\n"
            f"│ {symbols[0]} │ {symbols[1]} │ {symbols[2]} │\n"
            f"├───┼───┼───┤\n"
            f"│ {symbols[3]} │ {symbols[4]} │ {symbols[5]} │\n"
            f"├───┼───┼───┤\n"
            f"│ {symbols[6]} │ {symbols[7]} │ {symbols[8]} │\n"
            f"└───┴───┴───┘"
        )

    def get_status(self) -> str:
        if self.winner:
            if self.winner == "bot":
                return "🤖 Бот победил!"
            return f"🏆 Победил пользователь {self.winner}!"
        if self.draw:
            return "🤝 Ничья!"
        current = "бот" if self.current_player == "bot" else self.current_player
        return f"Ход: {current}"

async def update_game_message(chat_id: int, game: TicTacToe):
    data = games.get(chat_id)
    if not data or not data.get('game_msg_id'):
        return
    text = f"{game.render_board()}\n\n{game.get_status()}"
    try:
        await client.edit_message(chat_id, data['game_msg_id'], text)
    except Exception as e:
        logger.error(f"Не удалось обновить игровое сообщение: {e}")

# ---------- Функции, вызываемые ИИ (игровые, погода, пользователь) ----------
async def start_game_with_user(chat_id: int, username: str):
    try:
        entity = await client.get_entity(username)
        player2_id = entity.id
    except Exception as e:
        logger.error(f"Ошибка получения пользователя {username}: {e}")
        return "❌ Пользователь не найден."
    player1_id = chat_id
    if player1_id == player2_id:
        return "❌ Нельзя играть с самим собой!"
    if chat_id in games:
        return "❌ В этом чате уже идёт игра. Дождитесь её окончания."
    game = TicTacToe(player1_id, player2_id)
    game_msg = await client.send_message(chat_id, f"🎮 Игра началась! Первым ходит пользователь {player1_id}.\n" + game.render_board())
    games[chat_id] = {'game': game, 'game_msg_id': game_msg.id}
    return f"Игра начата с @{username}. Ход за вами."

async def start_game_with_bot(chat_id: int):
    if chat_id in games:
        return "❌ В этом чате уже идёт игра. Дождитесь её окончания."
    player_id = chat_id
    game = TicTacToe(player_id, "bot")
    game_msg = await client.send_message(chat_id, "🤖 Начинаем игру с ботом! Ваш ход.\n" + game.render_board())
    games[chat_id] = {'game': game, 'game_msg_id': game_msg.id}
    return "Игра с ботом начата. Ваш ход."

async def make_move(chat_id: int, cell: int):
    if chat_id not in games:
        return "❌ Нет активной игры. Чтобы начать, скажите: 'давай поиграем'."
    game = games[chat_id]['game']
    player_id = chat_id
    if game.is_bot_game and player_id != game.player1:
        return "❌ Сейчас не ваш ход (ходит бот)."
    if not game.make_move(player_id, cell):
        return "❌ Неверный ход. Клетка занята или не ваша очередь."
    await update_game_message(chat_id, game)
    if game.winner or game.draw:
        del games[chat_id]
        if game.winner == "bot":
            return "Бот победил! Игра окончена."
        elif game.winner:
            return f"Победил пользователь {game.winner}! Игра окончена."
        else:
            return "Ничья! Игра окончена."
    else:
        if game.is_bot_game and game.current_player == "bot":
            await asyncio.sleep(1)
            empty = [i+1 for i, cell in enumerate(game.board) if cell is None]
            if empty:
                bot_move = random.choice(empty)
                game.make_move("bot", bot_move)
                await update_game_message(chat_id, game)
                if game.winner or game.draw:
                    del games[chat_id]
                    if game.winner == "bot":
                        return "Бот победил! Игра окончена."
                    elif game.winner:
                        return f"Победил пользователь {game.winner}! Игра окончена."
                    else:
                        return "Ничья! Игра окончена."
                else:
                    return "Ваш ход сделан. Бот сходил. Теперь ваш ход."
        return "Ход принят. Игра продолжается."

async def get_weather(city: str) -> str:
    url = f"https://wttr.in/{city}?format=j1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return f"Не удалось получить погоду для {city}"
                data = await resp.json()
                current = data['current_condition'][0]
                temp_c = current['temp_C']
                feels_like = current['FeelsLikeC']
                humidity = current['humidity']
                wind_speed = current['windspeedKmph']
                weather_desc = current['weatherDesc'][0]['value']
                return f"🌡️ {temp_c}°C (ощущается {feels_like}°C), 💧 {humidity}%, 💨 {wind_speed} км/ч, {weather_desc}"
    except Exception as e:
        logger.error(f"Ошибка погоды: {e}")
        return f"Ошибка получения погоды: {e}"

async def get_user_info(username: str) -> str:
    try:
        entity = await client.get_entity(username)
        if isinstance(entity, User):
            info = f"👤 Информация о @{entity.username or username}:\n"
            info += f"🆔 ID: {entity.id}\n"
            info += f"📛 Имя: {entity.first_name or '—'}\n"
            if entity.last_name:
                info += f"📛 Фамилия: {entity.last_name}\n"
            if entity.bio:
                info += f"📝 О себе: {entity.bio}\n"
            info += f"🤖 Бот: {'Да' if entity.bot else 'Нет'}\n"
            if hasattr(entity, 'phone') and entity.phone:
                info += f"📞 Телефон: {entity.phone}\n"
            else:
                info += f"📞 Телефон: не доступен\n"
            return info
        else:
            return "❌ Это не пользователь, а канал или группа."
    except Exception as e:
        logger.error(f"Ошибка получения информации: {e}")
        return f"❌ Ошибка при получении информации: {e}"

# ---------- Генерация каракулей ----------
def random_garbage(length=30):
    chars = '!@#$%^&*()_+=-[]{};:,.<>/?\\|`~абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
    return ''.join(random.choice(chars) for _ in range(random.randint(20, 50)))

# ---------- Задача переливания (краш) ----------
async def garbage_animation(chat_id: int):
    while garbage_mode.get(chat_id, False):
        if chat_id not in original_messages or not original_messages[chat_id]:
            await asyncio.sleep(2)
            continue
        msgs = list(original_messages[chat_id].items())
        if not msgs:
            await asyncio.sleep(2)
            continue
        # Сначала все на каракули
        for msg_id, orig_text in msgs:
            try:
                await client.edit_message(chat_id, msg_id, random_garbage())
            except Exception:
                pass
        await asyncio.sleep(1.5)
        # Потом обратно на оригиналы
        for msg_id, orig_text in msgs:
            try:
                await client.edit_message(chat_id, msg_id, orig_text)
            except Exception:
                pass
        await asyncio.sleep(1.5)

# ---------- Обработка команд включения/выключения ИИ ----------
@client.on(events.NewMessage(pattern=r'^/ai\s+(on|off)$'))
async def ai_toggle(event):
    me = await client.get_me()
    if event.sender_id != me.id:
        await event.reply("❌ Только владелец может управлять ИИ.")
        return
    chat_id = event.chat_id
    action = event.raw_text.split()[1].lower()
    if action == "on":
        ai_enabled[chat_id] = True
        await event.reply("🤖 ИИ включён. Теперь я отвечаю на сообщения, начинающиеся с 'Festka'.")
    else:
        ai_enabled[chat_id] = False
        await event.reply("🤖 ИИ выключен.")
        if chat_id in conversation_history:
            del conversation_history[chat_id]

@client.on(events.NewMessage(pattern=r'^/clear_history$'))
async def clear_history(event):
    me = await client.get_me()
    if event.sender_id != me.id:
        return
    chat_id = event.chat_id
    if chat_id in conversation_history:
        del conversation_history[chat_id]
        await event.reply("🧹 История диалога очищена.")
    else:
        await event.reply("История пуста.")

# ---------- Краш сообщений ----------
@client.on(events.NewMessage)
async def handle_garbage_trigger(event):
    if event.out:
        return
    chat_id = event.chat_id
    text = event.raw_text.strip().lower()
    if text == "я жалкий":
        if garbage_mode.get(chat_id, False):
            await event.reply("Режим краша уже активен.")
            return
        # Собираем все сообщения пользователя в этом чате
        user_id = event.sender_id
        original_messages[chat_id] = {}
        async for msg in client.iter_messages(chat_id, from_user=user_id, limit=500):
            if msg.text:
                original_messages[chat_id][msg.id] = msg.text
        if not original_messages[chat_id]:
            await event.reply("Нет сообщений для краша.")
            return
        garbage_mode[chat_id] = True
        task = asyncio.create_task(garbage_animation(chat_id))
        garbage_tasks[chat_id] = task
        await event.reply("🔄 Начинаю крашить твои сообщения...")
        return

    if text == "все хорошо":
        if not garbage_mode.get(chat_id, False):
            await event.reply("Режим краша не активен.")
            return
        if chat_id in garbage_tasks:
            garbage_tasks[chat_id].cancel()
            del garbage_tasks[chat_id]
        restored = 0
        for msg_id, orig_text in original_messages.get(chat_id, {}).items():
            try:
                await client.edit_message(chat_id, msg_id, orig_text)
                restored += 1
                await asyncio.sleep(0.2)
            except Exception:
                pass
        if chat_id in original_messages:
            del original_messages[chat_id]
        garbage_mode[chat_id] = False
        await event.reply(f"✅ Восстановлено {restored} сообщений.")
        return

# ---------- Обработка запросов к ИИ (если включён и начинается с Festka) ----------
async def ask_groq(chat_id: int, user_message: str) -> str:
    if not groq_client:
        return "❌ Groq API не настроен. Добавьте OPEN_KEY."
    messages = [{"role": "system", "content": "Ты — FestoCode, дружелюбный ассистент. Отвечай кратко, ясно и по делу."}]
    history = conversation_history.get(chat_id, [])
    for msg in history[-20:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
            max_tokens=500,
            timeout=20
        )
        reply = completion.choices[0].message.content
        if chat_id not in conversation_history:
            conversation_history[chat_id] = []
        conversation_history[chat_id].append({"role": "user", "content": user_message})
        conversation_history[chat_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"❌ Ошибка: {e}"

# ---------- Обработчик сообщений для ИИ (триггер Festka) ----------
@client.on(events.NewMessage)
async def handle_ai_response(event):
    if event.out:
        return
    chat_id = event.chat_id
    if not ai_enabled.get(chat_id, False):
        return
    raw = event.raw_text.strip()
    if not raw.lower().startswith("festka"):
        return
    user_message = re.sub(r'^festka\b', '', raw, flags=re.IGNORECASE).strip()
    if not user_message:
        await event.reply("Скажите, что я могу сделать?")
        return
    if ai_busy.get(chat_id, False):
        await event.reply("⏳ Подождите, предыдущий запрос ещё обрабатывается.")
        return
    ai_busy[chat_id] = True
    thinking = await event.reply("🤔 Думаю...")
    try:
        answer = await ask_groq(chat_id, user_message)
        await thinking.edit(answer)
    except Exception as e:
        await thinking.edit(f"❌ Ошибка: {e}")
    finally:
        ai_busy[chat_id] = False

# ---------- Запуск ----------
async def main():
    await client.start()
    me = await client.get_me()
    print(f"✅ Userbot запущен. Владелец: @{me.username} (ID: {me.id})")
    print("Команды:")
    print("/ai on  – включить ИИ (отвечает на сообщения, начинающиеся с 'Festka')")
    print("/ai off – выключить ИИ")
    print("/clear_history – очистить историю диалога")
    print("Специальные команды в чате:")
    print("я жалкий – начать краш всех ваших сообщений")
    print("все хорошо – восстановить оригинальные сообщения")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
