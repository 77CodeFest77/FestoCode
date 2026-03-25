import asyncio
import os
import logging
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- Состояния FSM ----------
class GameStates(StatesGroup):
    waiting_for_second_player = State()   # ожидание второго игрока
    playing = State()                      # игра идёт

# Хранилище активных игр
# ключ = chat_id, значение = объект игры
games = {}

class TicTacToe:
    def __init__(self, player1_id: int, player2_id: int):
        self.player1 = player1_id
        self.player2 = player2_id
        self.board = [None] * 9          # None = пусто, 'X' или 'O'
        self.current_player = player1_id  # начинает player1
        self.winner = None
        self.draw = False

    def make_move(self, player_id: int, position: int) -> bool:
        """Возвращает True, если ход успешен, иначе False"""
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
        # смена хода
        if not self.winner and not self.draw:
            self.current_player = self.player2 if player_id == self.player1 else self.player1
        return True

    def _check_win(self):
        lines = [
            [0,1,2], [3,4,5], [6,7,8],  # строки
            [0,3,6], [1,4,7], [2,5,8],  # столбцы
            [0,4,8], [2,4,6]            # диагонали
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
        """Возвращает текстовое представление поля"""
        symbols = []
        for i, cell in enumerate(self.board):
            if cell is None:
                symbols.append(str(i+1))
            else:
                symbols.append(cell)
        board = (
            f"┌───┬───┬───┐\n"
            f"│ {symbols[0]} │ {symbols[1]} │ {symbols[2]} │\n"
            f"├───┼───┼───┤\n"
            f"│ {symbols[3]} │ {symbols[4]} │ {symbols[5]} │\n"
            f"├───┼───┼───┤\n"
            f"│ {symbols[6]} │ {symbols[7]} │ {symbols[8]} │\n"
            f"└───┴───┴───┘"
        )
        return board

    def get_status(self) -> str:
        if self.winner:
            return f"🏆 Победил пользователь {self.winner}!"
        elif self.draw:
            return "🤝 Ничья!"
        else:
            return f"Ход пользователя {self.current_player}"

# ---------- Клавиатура с кнопками для выхода ----------
def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Завершить игру", callback_data="cancel_game")]
    ])

# ---------- Команда /start ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот для игры в крестики-нолики.\n"
        "Используй команду /game @username, чтобы начать игру с другим пользователем.\n"
        "Во время игры просто отправляй номер клетки (1–9)."
    )

# ---------- Команда /game ----------
@dp.message(Command("game"))
async def cmd_game(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Укажите второго игрока: /game @username")
        return

    target = args[1].strip()
    # извлекаем username
    if target.startswith('@'):
        username = target[1:]
    else:
        username = target

    try:
        # получаем информацию о пользователе
        user = await bot.get_chat(username)
        if user.type == "private":
            player2_id = user.id
        else:
            # если это не пользователь, а канал или группа
            await message.answer("❌ Можно приглашать только обычных пользователей.")
            return
    except Exception:
        await message.answer("❌ Пользователь не найден. Проверьте правильность username.")
        return

    player1_id = message.from_user.id
    if player1_id == player2_id:
        await message.answer("❌ Нельзя играть с самим собой!")
        return

    chat_id = message.chat.id
    if chat_id in games:
        await message.answer("В этом чате уже идёт игра. Дождитесь её окончания.")
        return

    # Предлагаем второму игроку присоединиться
    await state.set_state(GameStates.waiting_for_second_player)
    await state.update_data(player1=player1_id, player2=player2_id, inviter=message.from_user.id)
    await message.answer(
        f"🎮 {message.from_user.first_name} приглашает @{username} сыграть в крестики-нолики.\n"
        f"@{username}, если согласны, введите /join\n"
        f"Если передумаете – нажмите /cancel",
        reply_markup=get_cancel_keyboard()
    )

# ---------- Команда /join ----------
@dp.message(Command("join"))
async def cmd_join(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("inviter") is None:
        await message.answer("Сейчас нет активного приглашения. Начните игру командой /game")
        return

    if message.from_user.id != data.get("player2"):
        await message.answer("Это приглашение не для вас.")
        return

    player1_id = data["player1"]
    player2_id = data["player2"]
    chat_id = message.chat.id

    # Создаём игру
    game = TicTacToe(player1_id, player2_id)
    games[chat_id] = game

    # Уведомляем обоих игроков
    await bot.send_message(chat_id, f"🎉 Игра началась! Первым ходит {message.from_user.first_name}.")
    await bot.send_message(chat_id, game.render_board(), parse_mode="HTML")
    await bot.send_message(chat_id, game.get_status(), reply_markup=get_cancel_keyboard())

    await state.clear()

# ---------- Обработка ходов ----------
@dp.message(GameStates.playing)
async def process_move(message: types.Message, state: FSMContext):
    # этот обработчик вызывается, если состояние playing, но мы будем обрабатывать ходы в любом состоянии
    pass

# Мы будем обрабатывать ходы в любом сообщении, если игра активна и сообщение от участника
@dp.message()
async def handle_move(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in games:
        return  # игры нет

    game = games[chat_id]
    player_id = message.from_user.id

    # Проверяем, участвует ли игрок в этой игре
    if player_id not in (game.player1, game.player2):
        await message.answer("Вы не участвуете в текущей игре.")
        return

    # Проверяем, не закончена ли игра
    if game.winner or game.draw:
        # Удаляем игру
        del games[chat_id]
        await message.answer("Игра уже закончена. Чтобы начать новую, введите /game @username")
        return

    # Парсим ход
    try:
        pos = int(message.text.strip())
        if pos < 1 or pos > 9:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 9, соответствующее клетке.")
        return

    # Делаем ход
    success = game.make_move(player_id, pos)
    if not success:
        await message.answer("Неверный ход. Либо не ваша очередь, либо клетка занята.")
        return

    # Обновляем отображение
    await message.answer(game.render_board(), parse_mode="HTML")
    status = game.get_status()
    await message.answer(status, reply_markup=get_cancel_keyboard())

    # Если игра закончена, удаляем из хранилища
    if game.winner or game.draw:
        del games[chat_id]

# ---------- Отмена игры через кнопку ----------
@dp.callback_query(lambda c: c.data == "cancel_game")
async def cancel_game_callback(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    if chat_id in games:
        del games[chat_id]
        await callback.message.edit_text("Игра отменена.")
    else:
        await callback.answer("Игра не активна.")
    await callback.answer()

# ---------- Команда /cancel для отмены приглашения ----------
@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == GameStates.waiting_for_second_player.state:
        await state.clear()
        await message.answer("Приглашение отменено.")
    else:
        await message.answer("Сейчас нет активного приглашения или игры.")

# ---------- Запуск ----------
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
