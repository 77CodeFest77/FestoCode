import asyncio
import os
import base64
import random
import re
import logging
from telethon import TelegramClient, events
from telethon.tl.types import User
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("TELEGRAM_API_ID", "34126767"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "44f1cdcc4c6544d60fe06be1b319d2dd")
OPEN_KEY = os.getenv("OPEN_KEY")
groq_client = Groq(api_key=OPEN_KEY) if OPEN_KEY else None

# Без прокси – прямое подключение
SESSION_FILE = "session_name.session"
session_b64 = os.getenv("TELEGRAM_SESSION_B64")
if session_b64 and not os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "wb") as f:
        f.write(base64.b64decode(session_b64))

client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

# ---------- Хранилища ----------
games = {}
pending_invites = {}
invite_tasks = {}
garbage_mode = {}
original_msgs = {}
garbage_tasks = {}
ai_enabled = False

# ---------- Класс игры ----------
class TicTacToe:
    def __init__(self, p1, p2):
        self.p1 = p1
        self.p2 = p2
        self.board = [None]*9
        self.turn = p1
        self.winner = None
        self.draw = False
        self.bot = (p2 == "bot")

    def move(self, pid, pos):
        if self.winner or self.draw:
            return False
        if pid != self.turn:
            return False
        if pos < 1 or pos > 9 or self.board[pos-1]:
            return False
        self.board[pos-1] = 'X' if pid == self.p1 else 'O'
        self._check()
        if not self.winner and not self.draw:
            self.turn = self.p2 if pid == self.p1 else self.p1
        return True

    def _check(self):
        lines = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]]
        for a,b,c in lines:
            if self.board[a] and self.board[a]==self.board[b]==self.board[c]:
                self.winner = self.p1 if self.board[a]=='X' else self.p2
                return
        if all(x is not None for x in self.board):
            self.draw = True

    def render(self):
        s = [str(i+1) if x is None else x for i,x in enumerate(self.board)]
        return (f"┌───┬───┬───┐\n│ {s[0]} │ {s[1]} │ {s[2]} │\n"
                f"├───┼───┼───┤\n│ {s[3]} │ {s[4]} │ {s[5]} │\n"
                f"├───┼───┼───┤\n│ {s[6]} │ {s[7]} │ {s[8]} │\n└───┴───┴───┘")

    def status(self):
        if self.winner:
            return f"🏆 Победил {'бот' if self.winner=='bot' else self.winner}!"
        if self.draw:
            return "🤝 Ничья!"
        return f"Ход: {'бот' if self.turn=='bot' else self.turn}"

# ---------- Игровые команды ----------
@client.on(events.NewMessage(pattern=r'^/game\s+(@?\w+)'))
async def game_cmd(event):
    args = event.raw_text.split()
    if len(args)<2:
        await event.reply("❌ /game @username")
        return
    target = args[1].lstrip('@')
    try:
        user = await client.get_entity(target)
        p2 = user.id
    except:
        await event.reply("❌ Пользователь не найден")
        return
    p1 = event.sender_id
    if p1 == p2:
        await event.reply("❌ Сам с собой?")
        return
    cid = event.chat_id
    if cid in games:
        await event.reply("❌ Уже игра")
        return
    msg = await event.reply(f"🎮 @{target}, /join")
    start = time.time()
    pending_invites[cid] = {'p1':p1, 'p2':p2, 'msg':msg.id, 'start':start}
    async def timer():
        while cid in pending_invites:
            left = max(0, 300 - int(time.time()-start))
            if left <= 0:
                if cid in pending_invites:
                    del pending_invites[cid]
                try:
                    await client.edit_message(cid, msg.id, "⏰ Время вышло")
                except: pass
                break
            bar = '█' * int(10*left/300) + '░' * (10 - int(10*left/300))
            await client.edit_message(cid, msg.id, f"🎮 {left//60:02d}:{left%60:02d}\n[{bar}]\n/join")
            await asyncio.sleep(1)
    invite_tasks[cid] = asyncio.create_task(timer())

@client.on(events.NewMessage(pattern=r'^/join$'))
async def join_cmd(event):
    cid = event.chat_id
    if cid not in pending_invites:
        await event.reply("❌ Нет приглашения")
        return
    inv = pending_invites[cid]
    if event.sender_id != inv['p2']:
        await event.reply("❌ Не для вас")
        return
    if cid in invite_tasks:
        invite_tasks[cid].cancel()
        del invite_tasks[cid]
    try:
        await client.delete_messages(cid, inv['msg'])
    except: pass
    game = TicTacToe(inv['p1'], inv['p2'])
    msg = await client.send_message(cid, f"🎮 Игра!\n{game.render()}\n{game.status()}")
    games[cid] = {'game':game, 'msg':msg.id}

@client.on(events.NewMessage(pattern=r'^/game_bot$'))
async def bot_game(event):
    cid = event.chat_id
    if cid in games:
        await event.reply("❌ Уже игра")
        return
    game = TicTacToe(event.sender_id, "bot")
    msg = await event.reply(f"🤖 Бот\n{game.render()}\n{game.status()}")
    games[cid] = {'game':game, 'msg':msg.id}

@client.on(events.NewMessage(pattern=r'^/cancel$'))
async def cancel_cmd(event):
    cid = event.chat_id
    if cid in pending_invites:
        if cid in invite_tasks:
            invite_tasks[cid].cancel()
            del invite_tasks[cid]
        try:
            await client.delete_messages(cid, pending_invites[cid]['msg'])
        except: pass
        del pending_invites[cid]
        await event.reply("❌ Отменено")
    elif cid in games:
        try:
            await client.delete_messages(cid, games[cid]['msg'])
        except: pass
        del games[cid]
        await event.reply("❌ Игра завершена")

@client.on(events.NewMessage)
async def move(event):
    cid = event.chat_id
    if cid not in games:
        return
    g = games[cid]['game']
    if not re.match(r'^[1-9]$', event.raw_text.strip()):
        return
    pos = int(event.raw_text)
    pid = event.sender_id
    if not g.move(pid, pos):
        return
    await client.edit_message(cid, games[cid]['msg'], f"{g.render()}\n{g.status()}")
    if g.winner or g.draw:
        del games[cid]
    if g.bot and not g.winner and not g.draw and g.turn == "bot":
        await asyncio.sleep(1)
        empty = [i+1 for i,c in enumerate(g.board) if c is None]
        if empty:
            g.move("bot", random.choice(empty))
            await client.edit_message(cid, games[cid]['msg'], f"{g.render()}\n{g.status()}")
            if g.winner or g.draw:
                del games[cid]

# ---------- Groq AI ----------
async def groq_answer(msg):
    if not groq_client:
        return "❌ Нет ключа"
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":msg}],
            temperature=0.7,
            max_tokens=500
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"❌ Ошибка: {e}"

@client.on(events.NewMessage(pattern=r'^/ai\s+(on|off)$'))
async def ai_toggle(event):
    global ai_enabled
    me = await client.get_me()
    if event.sender_id != me.id:
        return
    if event.raw_text.split()[1] == "on":
        ai_enabled = True
        await event.reply("✅ ИИ включён")
    else:
        ai_enabled = False
        await event.reply("❌ ИИ выключен")

@client.on(events.NewMessage)
async def ai_reply(event):
    if event.out or not ai_enabled:
        return
    txt = event.raw_text.strip()
    if not txt.lower().startswith("festka"):
        return
    q = re.sub(r'^festka\s*', '', txt, flags=re.I).strip()
    if not q:
        return
    await event.reply("🤔 Думаю...")
    ans = await groq_answer(q)
    await event.reply(ans)

# ---------- Краш сообщений ----------
def garbage():
    chars = '!@#$%^&*()_+=-[]{};:,.<>/?\\|`~абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
    return ''.join(random.choice(chars) for _ in range(random.randint(20,50)))

async def anim(cid):
    while garbage_mode.get(cid, False):
        if cid not in original_msgs or not original_msgs[cid]:
            await asyncio.sleep(2)
            continue
        msgs = list(original_msgs[cid].items())
        if not msgs:
            await asyncio.sleep(2)
            continue
        for mid,_ in msgs:
            try:
                await client.edit_message(cid, mid, garbage())
            except: pass
        await asyncio.sleep(1.5)
        for mid,orig in msgs:
            try:
                await client.edit_message(cid, mid, orig)
            except: pass
        await asyncio.sleep(1.5)

@client.on(events.NewMessage(pattern=r'^/cr$'))
async def cr_cmd(event):
    await event.delete()
    cid = event.chat_id
    if garbage_mode.get(cid, False):
        return
    uid = event.sender_id
    original_msgs[cid] = {}
    async for msg in client.iter_messages(cid, from_user=uid, limit=500):
        if msg.text:
            original_msgs[cid][msg.id] = msg.text
    if not original_msgs[cid]:
        await event.reply("Нет сообщений")
        return
    garbage_mode[cid] = True
    garbage_tasks[cid] = asyncio.create_task(anim(cid))
    await event.reply("🔄 Краш активирован")

@client.on(events.NewMessage(pattern=r'^/restore$'))
async def restore_cmd(event):
    await event.delete()
    cid = event.chat_id
    if not garbage_mode.get(cid, False):
        return
    if cid in garbage_tasks:
        garbage_tasks[cid].cancel()
        del garbage_tasks[cid]
    cnt = 0
    for mid,orig in original_msgs.get(cid, {}).items():
        try:
            await client.edit_message(cid, mid, orig)
            cnt += 1
            await asyncio.sleep(0.2)
        except: pass
    if cid in original_msgs:
        del original_msgs[cid]
    garbage_mode[cid] = False
    await event.reply(f"✅ Восстановлено {cnt}")

# ---------- Информация о пользователе ----------
@client.on(events.NewMessage(pattern=r'^/gti\s*(?:@(\w+))?$'))
async def gti(event):
    await event.delete()
    txt = event.raw_text
    m = re.search(r'@(\w+)', txt)
    if m:
        un = m.group(1)
        try:
            u = await client.get_entity(un)
        except:
            await event.reply("❌ Не найден")
            return
    else:
        reply = await event.get_reply_message()
        if reply:
            u = reply.sender_id
            try:
                u = await client.get_entity(u)
            except:
                await event.reply("❌ Ошибка")
                return
        else:
            await event.reply("❌ Укажи @username или ответь")
            return
    if not isinstance(u, User):
        await event.reply("❌ Не пользователь")
        return
    info = f"👤 @{u.username or '—'}\n🆔 {u.id}\n📛 {u.first_name or '—'}"
    if u.last_name: info += f" {u.last_name}"
    if u.bio: info += f"\n📝 {u.bio}"
    info += f"\n🤖 {'Бот' if u.bot else 'Человек'}"
    if hasattr(u,'phone') and u.phone: info += f"\n📞 {u.phone}"
    await event.reply(info)

# ---------- Генератор ключевых слов для VPN ----------
def generate_vpn_keywords():
    adjectives = [
        "быстрый", "бесплатный", "пробный", "секретный", "скрытый", "защищенный", "анонимный",
        "fast", "free", "trial", "secret", "hidden", "secure", "anonymous", "unlimited", "premium",
        "express", "ultra", "super", "mega", "turbo", "lightning", "rocket", "shadow"
    ]
    nouns = [
        "vpn", "vpn сервис", "vpn бот", "впн", "впн сервис", "впн бот", "прокси", "proxy",
        "tunnel", "туннель", "shield", "щит", "guard", "страж", "protection", "защита",
        "connection", "соединение", "access", "доступ", "unblock", "разблокировка",
        "net", "сеть", "gateway", "шлюз", "bridge", "мост", "fly", "полет", "speed", "скорость"
    ]
    colors = ["красный", "синий", "зеленый", "желтый", "черный", "белый", "фиолетовый", "оранжевый",
              "red", "blue", "green", "yellow", "black", "white", "purple", "orange"]
    animals = ["лиса", "волк", "дракон", "орел", "лев", "тигр", "медведь", "сокол",
               "fox", "wolf", "dragon", "eagle", "lion", "tiger", "bear", "hawk"]
    random_words = [
        "кристалл", "молния", "ветер", "огонь", "вода", "земля", "небо", "звезда", "космос",
        "crystal", "lightning", "wind", "fire", "water", "earth", "sky", "star", "space",
        "дрифт", "цвет", "скорость", "секрет", "тень", "волна", "вихрь", "драйв"
    ]
    
    keywords = set()
    for n in nouns[:10]:
        keywords.add(n)
        keywords.add(f"{n} бот")
        keywords.add(f"{n} канал")
    for adj in adjectives[:20]:
        for n in nouns[:8]:
            keywords.add(f"{adj} {n}")
            keywords.add(f"{adj} vpn")
            keywords.add(f"{adj} впн")
    for c in colors:
        keywords.add(f"{c} vpn")
        keywords.add(f"{c} впн")
        keywords.add(f"{c} proxy")
        keywords.add(f"{c} прокси")
    for a in animals:
        keywords.add(f"{a} vpn")
        keywords.add(f"{a} впн")
        keywords.add(f"{a} proxy")
    for rw in random_words:
        keywords.add(f"{rw} vpn")
        keywords.add(f"{rw} впн")
        keywords.add(f"vpn {rw}")
    eng_variants = ["vpn bot", "free vpn", "trial vpn", "vpn service", "vpn channel", 
                    "vpn proxy", "best vpn", "fast vpn", "secure vpn", "unlimited vpn",
                    "vpn telegram", "telegram vpn", "vpn free trial", "premium vpn free"]
    for ev in eng_variants:
        keywords.add(ev)
    ru_variants = ["впн бот", "бесплатный впн", "пробный впн", "впн сервис", "впн канал",
                   "лучший впн", "быстрый впн", "безопасный впн", "впн телеграм", "телеграм впн"]
    for rv in ru_variants:
        keywords.add(rv)
    return list(keywords)

VPN_CHANNELS = [
    "vpn_bot_list",
    "free_vpn_bots",
    "vpn_offers",
    "vpntrial",
    "best_vpn_bots",
    "vpn_channel",
    "vpn_service",
    "vpn_proxy_list"
]

async def search_vpn_bots():
    found = {}
    keywords = generate_vpn_keywords()
    random.shuffle(keywords)
    logger.info(f"Сгенерировано {len(keywords)} ключевых слов для поиска")
    for channel in VPN_CHANNELS:
        try:
            for kw in keywords[:40]:
                try:
                    async for msg in client.iter_messages(channel, search=kw, limit=20):
                        if msg.text:
                            links = re.findall(r'@[a-zA-Z0-9_]{5,32}\b|https?://t\.me/[a-zA-Z0-9_]{5,32}\b', msg.text)
                            for link in links:
                                if link.startswith("https://t.me/"):
                                    link = "@" + link.split("/")[-1]
                                if link not in found:
                                    found[link] = {
                                        "link": link,
                                        "source": channel,
                                        "text": msg.text[:150],
                                        "keyword": kw
                                    }
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Ошибка в канале {channel}: {e}")
    results = list(found.values())
    results.sort(key=lambda x: 0 if re.search(r'vpn|впн', x['text'].lower()) else 1)
    return results[:15]

@client.on(events.NewMessage(pattern=r'^/vpns$'))
async def vpn_search_command(event):
    await event.delete()
    status = await event.reply("🔍 Ищу VPN ботов... Генерирую миллионы слов...\nЭто может занять 30-60 секунд")
    try:
        bots = await search_vpn_bots()
        if not bots:
            await status.edit("❌ Не найдено VPN ботов в указанных каналах.")
            return
        response = "🤖 **НАЙДЕННЫЕ VPN БОТЫ**\n\n"
        for i, b in enumerate(bots[:15], 1):
            response += f"{i}. 🔗 {b['link']}\n"
            response += f"   📡 *Канал:* {b['source']}\n"
            response += f"   📝 *Найдено по:* {b['keyword']}\n"
            response += f"   📄 {b['text'][:100]}...\n\n"
        response += "⚠️ Боты могут иметь пробный период. Уточняйте условия."
        await status.edit(response, parse_mode='markdown')
    except Exception as e:
        await status.edit(f"❌ Ошибка: {e}")
        logger.exception("Ошибка поиска VPN")

# ---------- Запуск ----------
async def main():
    await client.start()
    me = await client.get_me()
    print(f"✅ Userbot запущен. Владелец: @{me.username}")
    print("Доступные команды:")
    print("/ai on/off - включить/выключить ИИ")
    print("/game @username - играть с другом")
    print("/game_bot - играть с ботом")
    print("/join - присоединиться к игре")
    print("/cancel - отменить игру")
    print("/cr - краш сообщений")
    print("/restore - восстановить сообщения")
    print("/gti @username - информация о пользователе")
    print("/vpns - найти VPN ботов (миллионы слов)")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())