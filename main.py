import asyncio
import os
import base64
from typing import Dict
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "34126767"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "44f1cdcc4c6544d60fe06be1b319d2dd")
SESSION_FILE = "session_name.session"

# Восстанавливаем сессию из секрета, если есть
session_b64 = os.getenv("TELEGRAM_SESSION_B64")
if session_b64 and not os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "wb") as f:
        f.write(base64.b64decode(session_b64))

client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

games: Dict[int, 'TicTacToe'] = {}
pending_invites: Dict[int, dict] = {}

class TicTacToe:
    def __init__(self, player1_id: int, player2_id: int):
        self.player1 = player1_id
        self.player2 = player2_id
        self.board = [None] * 9
        self.current_player = player1_id
        self.winner = None
        self.draw = False

    def make_move(self, player_id: int, position: int) -> bool:
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
            return f"🏆 Победил пользователь {self.winner}!"
        if self.draw:
            return "🤝 Ничья!"
        return f"Ход пользователя {self.current_player}"

@client.on(events.NewMessage(pattern=r'^/game\s+(@?\w+)'))
async def game_command(event):
    args = event.raw_text.split(maxsplit=1)
    if len(args) < 2:
        await event.reply("❌ Укажите второго игрока: /game @username")
        return
    target = args[1].strip().lstrip('@')
    try:
        user = await client.get_entity(target)
        player2_id = user.id
    except Exception:
        await event.reply("❌ Пользователь не найден.")
        return
    player1_id = event.sender_id
    if player1_id == player2_id:
        await event.reply("❌ Нельзя играть с самим собой!")
        return
    chat_id = event.chat_id
    if chat_id in games:
        await event.reply("В этом чате уже идёт игра. Дождитесь её окончания.")
        return
    pending_invites[chat_id] = {
        'player1': player1_id,
        'player2': player2_id,
        'inviter': player1_id
    }
    await event.reply(
        f"🎮 Вы пригласили @{target} сыграть в крестики-нолики.\n"
        f"@{target}, если согласны, введите /join\n"
        f"Если передумаете – введите /cancel"
    )

@client.on(events.NewMessage(pattern=r'^/join$'))
async def join_command(event):
    chat_id = event.chat_id
    if chat_id not in pending_invites:
        await event.reply("Сейчас нет активного приглашения. Начните игру командой /game")
        return
    invite = pending_invites[chat_id]
    if event.sender_id != invite['player2']:
        await event.reply("Это приглашение не для вас.")
        return
    game = TicTacToe(invite['player1'], invite['player2'])
    games[chat_id] = game
    del pending_invites[chat_id]
    await client.send_message(chat_id, f"🎉 Игра началась! Первым ходит пользователь {invite['player1']}.")
    await client.send_message(chat_id, game.render_board())
    await client.send_message(chat_id, game.get_status())

@client.on(events.NewMessage(pattern=r'^/cancel$'))
async def cancel_command(event):
    chat_id = event.chat_id
    if chat_id in pending_invites:
        del pending_invites[chat_id]
        await event.reply("Приглашение отменено.")
    elif chat_id in games:
        del games[chat_id]
        await event.reply("Игра отменена.")
    else:
        await event.reply("Нет активной игры или приглашения.")

@client.on(events.NewMessage)
async def handle_move(event):
    chat_id = event.chat_id
    if chat_id not in games:
        return
    game = games[chat_id]
    player_id = event.sender_id
    if player_id not in (game.player1, game.player2):
        await event.reply("Вы не участвуете в текущей игре.")
        return
    if game.winner or game.draw:
        del games[chat_id]
        await event.reply("Игра уже закончена. Чтобы начать новую, введите /game @username")
        return
    try:
        pos = int(event.raw_text.strip())
        if pos < 1 or pos > 9:
            raise ValueError
    except ValueError:
        await event.reply("Введите число от 1 до 9, соответствующее клетке.")
        return
    if not game.make_move(player_id, pos):
        await event.reply("Неверный ход. Либо не ваша очередь, либо клетка занята.")
        return
    await event.reply(game.render_board())
    status = game.get_status()
    await event.reply(status)
    if game.winner or game.draw:
        del games[chat_id]

async def main():
    await client.start()
    print("✅ Userbot запущен. Играйте командами /game, /join, /cancel")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
