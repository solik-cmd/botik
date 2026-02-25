import asyncio
import random
import sqlite3
import logging
import string
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.middlewares.throttling import ThrottlingMiddleware

# ========== НАСТРОЙКИ ==========
TOKEN = "8032635677:AAFi83m59Q8kcUxgvUwo7Y6Z13AwYAQKVpk"
REVIEW_CHAT_ID = -5235029911
MAX_COINS = 5000

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ SQLITE ==========
conn = sqlite3.connect('game_bot.db')
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    games_played INTEGER DEFAULT 0,
    games_won INTEGER DEFAULT 0,
    minesweeper_best_time REAL DEFAULT NULL,
    guess_attempts_best INTEGER DEFAULT NULL,
    rps_wins INTEGER DEFAULT 0,
    coins INTEGER DEFAULT 100,
    daily_last TIMESTAMP DEFAULT NULL,
    referrer_id INTEGER DEFAULT NULL,
    skin TEXT DEFAULT 'default'
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    review_text TEXT,
    rating INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    referred_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    owner_id INTEGER,
    game_type TEXT,
    players TEXT,
    state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS lobbies (
    lobby_id TEXT PRIMARY KEY,
    name TEXT,
    owner_id INTEGER,
    game_type TEXT,
    players TEXT,
    status TEXT DEFAULT 'waiting',
    chat_id INTEGER,
    message_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ==========
def update_user_stats(user_id, username, first_name, **kwargs):
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)',
                   (user_id, username, first_name))
    for key, value in kwargs.items():
        cursor.execute(f'UPDATE users SET {key} = {key} + ? WHERE user_id = ?', (value, user_id))
    conn.commit()

def get_user_stats(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    return cursor.fetchone()

def can_daily(user_id):
    cursor.execute('SELECT daily_last FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return True
    last = datetime.fromisoformat(row[0])
    return datetime.now() - last > timedelta(days=1)

def update_daily(user_id):
    cursor.execute('UPDATE users SET daily_last = ? WHERE user_id = ?', 
                   (datetime.now().isoformat(), user_id))
    conn.commit()

def add_coins(user_id, amount):
    cursor.execute('SELECT coins FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    current = result[0] if result else 0
    new_balance = min(current + amount, MAX_COINS)
    cursor.execute('UPDATE users SET coins = ? WHERE user_id = ?', (new_balance, user_id))
    conn.commit()
    return new_balance

def spend_coins(user_id, amount):
    cursor.execute('SELECT coins FROM users WHERE user_id = ?', (user_id,))
    coins = cursor.fetchone()[0]
    if coins >= amount:
        cursor.execute('UPDATE users SET coins = coins - ? WHERE user_id = ?', (amount, user_id))
        conn.commit()
        return True
    return False

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Антиспам
dp.message.middleware(ThrottlingMiddleware(rate_limit=0.5))

# ========== ХРАНИЛИЩА ИГР ==========
minesweeper_games = {}
tictactoe_games = {}
guess_games = {}
rps_games = {}
multiplayer_games = {}
farm_games = {}
lobby_games = {}  # активные игры из лобби

# ========== МАГАЗИН ==========
SHOP_ITEMS = {
    'skin_gold': {'name': '✨ Золотой скин', 'price': 500, 'desc': 'Ваше имя в игре станет золотым'},
    'skin_rainbow': {'name': '🌈 Радужный скин', 'price': 1000, 'desc': 'Имя переливается всеми цветами'},
    'double_reward': {'name': '🎁 Удвоение награды', 'price': 300, 'desc': 'В следующих 3 играх награда x2'},
    'extra_attempt': {'name': '🔄 Дополнительная попытка', 'price': 100, 'desc': '+1 попытка в угадайке'},
}

# ========== ФЕРМА ==========
class Plant:
    def __init__(self):
        self.planted_time = datetime.now()
        self.growth_time = 30

    def is_ready(self):
        return (datetime.now() - self.planted_time).total_seconds() >= self.growth_time

class Farm:
    def __init__(self):
        self.fields = [None] * 4
        self.coins = 0
        self.seeds = 5

def get_farm(user_id):
    if user_id not in farm_games:
        farm_games[user_id] = Farm()
    return farm_games[user_id]

def farm_keyboard(user_id):
    farm = get_farm(user_id)
    kb = []
    for i in range(4):
        field = farm.fields[i]
        if field is None:
            text = f"🌱 Поле {i+1} (пусто)"
        elif field.is_ready():
            text = f"🌾 Поле {i+1} (созрело!)"
        else:
            text = f"🌿 Поле {i+1} (растёт...)"
        kb.append([InlineKeyboardButton(text=text, callback_data=f"farm_field_{i}")])
    kb.append([
        InlineKeyboardButton(text="🌽 Купить семена (5💰)", callback_data="farm_buy_seed"),
        InlineKeyboardButton(text="💰 Продать всё", callback_data="farm_sell_all")
    ])
    kb.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="farm_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(Command("farm"))
async def cmd_farm(message: types.Message):
    user_id = message.from_user.id
    farm = get_farm(user_id)
    text = (f"🌾 <b>Твоя ферма</b>\n"
            f"💰 Монет: {farm.coins}\n"
            f"🌱 Семян: {farm.seeds}\n"
            f"Выбери действие:")
    await message.reply(text, reply_markup=farm_keyboard(user_id))

@dp.callback_query(lambda c: c.data.startswith("farm_"))
async def farm_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    farm = get_farm(user_id)
    action = callback.data.split('_')[1]

    if action == "field":
        field_num = int(callback.data.split('_')[2])
        field = farm.fields[field_num]
        if field is None:
            if farm.seeds <= 0:
                await callback.answer("❌ Нет семян! Купи в магазине.")
                return
            farm.fields[field_num] = Plant()
            farm.seeds -= 1
            await callback.answer("🌱 Посажено! Жди 30 секунд.")
        else:
            if field.is_ready():
                farm.fields[field_num] = None
                farm.coins += 10
                await callback.answer("✅ Урожай собран! +10 монет.")
            else:
                await callback.answer("⏳ Ещё растёт, подожди.")
        await callback.message.edit_text(
            f"🌾 Твоя ферма\n💰 Монет: {farm.coins}\n🌱 Семян: {farm.seeds}",
            reply_markup=farm_keyboard(user_id)
        )

    elif action == "buy_seed":
        if farm.coins >= 5:
            farm.coins -= 5
            farm.seeds += 1
            await callback.answer("✅ Куплено 1 семя.")
        else:
            await callback.answer("❌ Недостаточно монет.")
        await callback.message.edit_text(
            f"🌾 Твоя ферма\n💰 Монет: {farm.coins}\n🌱 Семян: {farm.seeds}",
            reply_markup=farm_keyboard(user_id)
        )

    elif action == "sell_all":
        harvested = 0
        for i in range(4):
            field = farm.fields[i]
            if field and field.is_ready():
                farm.fields[i] = None
                harvested += 1
        if harvested:
            farm.coins += harvested * 10
            await callback.answer(f"✅ Продано {harvested} урожаев. +{harvested*10} монет.")
        else:
            await callback.answer("❌ Нет созревших полей.")
        await callback.message.edit_text(
            f"🌾 Твоя ферма\n💰 Монет: {farm.coins}\n🌱 Семян: {farm.seeds}",
            reply_markup=farm_keyboard(user_id)
        )

    elif action == "refresh":
        await callback.answer()
        await callback.message.edit_text(
            f"🌾 Твоя ферма\n💰 Монет: {farm.coins}\n🌱 Семян: {farm.seeds}",
            reply_markup=farm_keyboard(user_id)
        )

# ========== СИСТЕМА ЛОББИ ==========
@dp.message(Command("lobby"))
async def cmd_lobby(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply(
            "🏠 <b>Команды лобби:</b>\n"
            "• /lobby create [название] [игра] - создать лобби (игры: ttt, rps, duel)\n"
            "• /lobby list - список активных лобби\n"
            "• /lobby join [код] - присоединиться к лобби\n"
            "• /lobby leave - выйти из лобби\n"
            "• /lobby start - начать игру (только создатель)"
        )
        return

    subcmd = args[1].lower()
    
    if subcmd == "create":
        if len(args) < 4:
            await message.reply("❌ Укажите название и тип игры: /lobby create Название ttt")
            return
        name = args[2]
        game_type = args[3].lower()
        if game_type not in ['ttt', 'rps', 'duel']:
            await message.reply("❌ Поддерживаются только: ttt, rps, duel")
            return
        
        lobby_id = generate_code()
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        cursor.execute('''
            INSERT INTO lobbies (lobby_id, name, owner_id, game_type, players, chat_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (lobby_id, name, user_id, game_type, json.dumps([user_id]), chat_id))
        conn.commit()
        
        await message.reply(
            f"✅ Лобби <b>{name}</b> создано!\n"
            f"📌 Код: <code>{lobby_id}</code>\n"
            f"🎮 Игра: {game_type}\n"
            f"👤 Создатель: {message.from_user.full_name}\n\n"
            f"Приглашайте игроков: /lobby join {lobby_id}"
        )
    
    elif subcmd == "list":
        cursor.execute('SELECT lobby_id, name, game_type, players, owner_id FROM lobbies WHERE status = "waiting"')
        lobbies = cursor.fetchall()
        if not lobbies:
            await message.reply("📭 Нет активных лобби.")
            return
        text = "🏠 <b>Активные лобби:</b>\n\n"
        for lobby_id, name, game_type, players_json, owner_id in lobbies:
            players = json.loads(players_json)
            text += f"• <b>{name}</b> ({game_type})\n  Код: <code>{lobby_id}</code>\n  Игроков: {len(players)}/2\n"
        await message.reply(text)
    
    elif subcmd == "join":
        if len(args) < 3:
            await message.reply("❌ Укажите код лобби: /lobby join КОД")
            return
        lobby_id = args[2].upper()
        user_id = message.from_user.id
        
        cursor.execute('SELECT * FROM lobbies WHERE lobby_id = ? AND status = "waiting"', (lobby_id,))
        lobby = cursor.fetchone()
        if not lobby:
            await message.reply("❌ Лобби не найдено или уже заполнено.")
            return
        
        players = json.loads(lobby[4])
        if len(players) >= 2:
            await message.reply("❌ В лобби уже два игрока.")
            return
        
        if user_id in players:
            await message.reply("❌ Вы уже в этом лобби.")
            return
        
        players.append(user_id)
        cursor.execute('UPDATE lobbies SET players = ? WHERE lobby_id = ?', 
                      (json.dumps(players), lobby_id))
        conn.commit()
        
        # Уведомляем создателя
        owner_id = lobby[2]
        await bot.send_message(
            owner_id,
            f"🔔 Игрок {message.from_user.full_name} присоединился к вашему лобби <b>{lobby[1]}</b>!\n"
            f"Чтобы начать игру: /lobby start {lobby_id}"
        )
        
        await message.reply(f"✅ Вы присоединились к лобби <b>{lobby[1]}</b>!")
    
    elif subcmd == "leave":
        user_id = message.from_user.id
        cursor.execute('SELECT * FROM lobbies WHERE players LIKE ? AND status = "waiting"', 
                      (f'%{user_id}%',))
        lobby = cursor.fetchone()
        if not lobby:
            await message.reply("❌ Вы не в активном лобби.")
            return
        
        lobby_id = lobby[0]
        players = json.loads(lobby[4])
        players.remove(user_id)
        
        if not players:
            cursor.execute('DELETE FROM lobbies WHERE lobby_id = ?', (lobby_id,))
            await message.reply("🏠 Лобби удалено (все вышли).")
        else:
            cursor.execute('UPDATE lobbies SET players = ? WHERE lobby_id = ?', 
                          (json.dumps(players), lobby_id))
            await message.reply("✅ Вы вышли из лобби.")
        conn.commit()
    
    elif subcmd == "start":
        if len(args) < 3:
            await message.reply("❌ Укажите код лобби: /lobby start КОД")
            return
        lobby_id = args[2].upper()
        user_id = message.from_user.id
        
        cursor.execute('SELECT * FROM lobbies WHERE lobby_id = ? AND status = "waiting"', (lobby_id,))
        lobby = cursor.fetchone()
        if not lobby:
            await message.reply("❌ Лобби не найдено.")
            return
        
        if lobby[2] != user_id:
            await message.reply("❌ Только создатель может начать игру.")
            return
        
        players = json.loads(lobby[4])
        if len(players) < 2:
            await message.reply("❌ Недостаточно игроков (нужно 2).")
            return
        
        game_type = lobby[3]
        chat_id = lobby[6]
        
        # Удаляем лобби из БД
        cursor.execute('DELETE FROM lobbies WHERE lobby_id = ?', (lobby_id,))
        conn.commit()
        
        # Создаём игру
        if game_type == 'ttt':
            game_id = f"lobby_{lobby_id}"
            lobby_games[game_id] = {
                'players': players,
                'board': [[' ' for _ in range(3)] for _ in range(3)],
                'turn': players[0],
                'game_type': 'tictactoe',
                'chat_id': chat_id,
                'message_id': None
            }
            
            keyboard = ttt_get_keyboard(lobby_games[game_id]['board'])
            sent = await bot.send_message(
                chat_id,
                f"🎮 Игра в крестики-нолики началась!\n"
                f"Игрок 1: {players[0]} (❌)\n"
                f"Игрок 2: {players[1]} (⭕)\n"
                f"Ход первого игрока:",
                reply_markup=keyboard
            )
            lobby_games[game_id]['message_id'] = sent.message_id
        
        elif game_type in ['rps', 'duel']:
            game_id = f"lobby_{lobby_id}"
            lobby_games[game_id] = {
                'players': players,
                'choices': {},
                'game_type': 'rps',
                'chat_id': chat_id,
                'message_id': None
            }
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🪨 Камень", callback_data=f"lobby_rps_{game_id}_камень")],
                [InlineKeyboardButton(text="✂️ Ножницы", callback_data=f"lobby_rps_{game_id}_ножницы")],
                [InlineKeyboardButton(text="📄 Бумага", callback_data=f"lobby_rps_{game_id}_бумага")]
            ])
            
            sent = await bot.send_message(
                chat_id,
                f"⚔️ Дуэль в КНБ началась!\n"
                f"Участники: {players[0]} vs {players[1]}\n"
                f"Выбирайте оружие:",
                reply_markup=keyboard
            )
            lobby_games[game_id]['message_id'] = sent.message_id

# ========== ОБРАБОТЧИК ДЛЯ ИГР ИЗ ЛОББИ ==========
@dp.callback_query(lambda c: c.data.startswith("lobby_"))
async def lobby_game_callback(callback: CallbackQuery):
    data = callback.data.split('_')
    game_id = data[2]
    action = data[3]
    user_id = callback.from_user.id
    
    game = lobby_games.get(game_id)
    if not game:
        await callback.answer("Игра не найдена или уже завершена")
        return
    
    # Проверяем, участвует ли пользователь
    if user_id not in game['players']:
        await callback.answer("❌ Вы не участвуете в этой игре!", show_alert=True)
        return
    
    if game['game_type'] == 'rps':
        if user_id in game['choices']:
            await callback.answer("Вы уже сделали выбор!")
            return
        
        game['choices'][user_id] = action
        
        if len(game['choices']) == 2:
            p1, p2 = game['players']
            c1 = game['choices'][p1]
            c2 = game['choices'][p2]
            
            RPS_EMOJI = {'камень': '🪨', 'ножницы': '✂️', 'бумага': '📄'}
            RPS_BEATS = {'камень': 'ножницы', 'ножницы': 'бумага', 'бумага': 'камень'}
            
            if c1 == c2:
                result = "🤝 Ничья!"
                winner = None
            elif RPS_BEATS[c1] == c2:
                result = f"🎉 Победил {p1}!"
                winner = p1
            else:
                result = f"🎉 Победил {p2}!"
                winner = p2
            
            if winner:
                update_user_stats(winner, None, None, games_played=1, games_won=1)
                add_coins(winner, 30)
            for player in game['players']:
                update_user_stats(player, None, None, games_played=1)
            
            await callback.message.edit_text(
                f"⚔️ Результаты дуэли:\n"
                f"Игрок 1: {RPS_EMOJI[c1]} {c1}\n"
                f"Игрок 2: {RPS_EMOJI[c2]} {c2}\n\n"
                f"{result}"
            )
            
            del lobby_games[game_id]
        else:
            await callback.answer("Выбор принят, ожидаем соперника...")
    
    elif game['game_type'] == 'tictactoe':
        # Обработка хода в крестики-нолики
        if game['turn'] != user_id:
            await callback.answer("Сейчас не ваш ход!")
            return
        
        try:
            r = int(data[4])
            c = int(data[5])
        except:
            await callback.answer("Неверный формат хода")
            return
        
        if game['board'][r][c] != ' ':
            await callback.answer("Клетка занята!")
            return
        
        symbol = 'X' if game['players'][0] == user_id else 'O'
        game['board'][r][c] = symbol
        
        # Проверка победы
        winner = ttt_check_winner(game['board'])
        if winner:
            if winner == 'X':
                winner_id = game['players'][0]
                loser_id = game['players'][1]
            elif winner == 'O':
                winner_id = game['players'][1]
                loser_id = game['players'][0]
            else:
                winner_id = None
            
            if winner_id:
                update_user_stats(winner_id, None, None, games_played=1, games_won=1)
                add_coins(winner_id, 50)
                update_user_stats(loser_id, None, None, games_played=1)
                result_text = f"🎉 Победил игрок {winner_id}!"
            else:
                for p in game['players']:
                    update_user_stats(p, None, None, games_played=1)
                result_text = "🤝 Ничья!"
            
            keyboard = ttt_get_keyboard(game['board'])
            await callback.message.edit_text(
                f"{result_text}\nНовая игра: /lobby create",
                reply_markup=keyboard
            )
            del lobby_games[game_id]
            await callback.answer()
            return
        
        # Меняем ход
        game['turn'] = game['players'][1] if game['turn'] == game['players'][0] else game['players'][0]
        
        keyboard = ttt_get_keyboard(game['board'])
        await callback.message.edit_text(
            f"Ход игрока {game['turn']}:",
            reply_markup=keyboard
        )
        await callback.answer()

# ========== САПЁР, КРЕСТИКИ-НОЛИКИ, КНБ, ИГРЫ ==========
# (здесь идут все функции из предыдущего кода без изменений)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ИГР ==========
def ttt_check_winner(board):
    for i in range(3):
        if board[i][0] == board[i][1] == board[i][2] != ' ':
            return board[i][0]
        if board[0][i] == board[1][i] == board[2][i] != ' ':
            return board[0][i]
    if board[0][0] == board[1][1] == board[2][2] != ' ':
        return board[0][0]
    if board[0][2] == board[1][1] == board[2][0] != ' ':
        return board[0][2]
    if all(cell != ' ' for row in board for cell in row):
        return 'draw'
    return None

def ttt_get_keyboard(board):
    keyboard = []
    for r in range(3):
        row_buttons = []
        for c in range(3):
            text = board[r][c]
            if text == ' ':
                text = '⬜'
            elif text == 'X':
                text = '❌'
            elif text == 'O':
                text = '⭕'
            callback_data = f"lobby_ttt_move_{r}_{c}"
            row_buttons.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        keyboard.append(row_buttons)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ========== ОТЗЫВЫ (исправлено) ==========
@dp.message(Command("review"))
async def cmd_review(message: types.Message):
    await message.reply(
        "📝 Пожалуйста, отправьте одним сообщением ваш отзыв и оценку от 1 до 5.\n"
        "Пример: <code>Отличный бот! 5</code>\n\n"
        "⚠️ Можно оставлять только 1 отзыв в день."
    )

@dp.message(lambda msg: msg.text and not msg.text.startswith('/') and ('отзыв' in msg.text.lower() or 'review' in msg.text.lower()))
async def handle_review(message: types.Message):
    """Обрабатывает только сообщения, содержащие слово 'отзыв' или 'review'"""
    user_id = message.from_user.id
    text = message.text
    
    cursor.execute('''
        SELECT COUNT(*) FROM reviews 
        WHERE user_id = ? AND date(created_at) = date('now')
    ''', (user_id,))
    today_reviews = cursor.fetchone()[0]
    
    if today_reviews >= 1:
        await message.reply("⏳ Вы уже оставляли отзыв сегодня. Попробуйте завтра!")
        return
    
    words = text.split()
    rating = None
    for word in words:
        if word.isdigit() and 1 <= int(word) <= 5:
            rating = int(word)
            break
    
    if not rating:
        await message.reply("❌ Пожалуйста, укажите оценку от 1 до 5 в сообщении (например: 'Отличный бот! 5')")
        return
    
    cursor.execute('''
        INSERT INTO reviews (user_id, username, review_text, rating)
        VALUES (?, ?, ?, ?)
    ''', (user_id, message.from_user.username or "no_username", text, rating))
    conn.commit()
    
    if REVIEW_CHAT_ID:
        try:
            await bot.send_message(
                REVIEW_CHAT_ID,
                f"⭐ <b>Новый отзыв</b>\n"
                f"От: {message.from_user.full_name} (@{message.from_user.username})\n"
                f"Оценка: {rating}/5\n"
                f"Текст: {text}"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить отзыв в канал: {e}")
    
    new_balance = add_coins(user_id, 30)
    await message.reply(f"✅ Спасибо за отзыв! Вы получили 30 монет. Баланс: {new_balance}")

# ========== КОМАНДА START ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    args = message.text.split()
    user_id = message.from_user.id

    if len(args) > 1 and args[1].startswith('ref_'):
        referrer_id = int(args[1].split('_')[1])
        if referrer_id != user_id:
            cursor.execute('INSERT OR IGNORE INTO referrals (user_id, referred_id) VALUES (?, ?)',
                          (referrer_id, user_id))
            cursor.execute('UPDATE users SET referrer_id = ? WHERE user_id = ?',
                          (referrer_id, user_id))
            add_coins(referrer_id, 100)
            conn.commit()
            await bot.send_message(referrer_id, f"🎉 По вашей ссылке зарегистрировался новый пользователь! +100 монет")

    await message.reply(
        "👋 Привет! Я игровой бот.\n\n"
        "🎮 <b>Одиночные игры:</b>\n"
        "• /minesweeper - Сапёр\n"
        "• /tictactoe - Крестики-нолики (с ботом)\n"
        "• /guess - Угадай число\n"
        "• /rps - Камень-ножницы-бумага\n"
        "• /dice - Кости\n"
        "• /basketball - Баскетбол\n"
        "• /football - Футбол\n"
        "• /bowling - Боулинг\n"
        "• /darts - Дротики\n"
        "• /farm - Ферма (сажай и продавай!)\n\n"
        "👥 <b>Многопользовательские игры (через лобби):</b>\n"
        "• /lobby create [название] [ttt/rps/duel] - создать лобби\n"
        "• /lobby list - список лобби\n"
        "• /lobby join [код] - присоединиться\n"
        "• /lobby start [код] - начать игру\n\n"
        "🏠 <b>Приватные комнаты:</b>\n"
        "• /create_room [тип игры] - Создать комнату\n"
        "• /join [код] - Присоединиться к комнате\n\n"
        "💰 <b>Магазин и бонусы:</b>\n"
        "• /daily - Ежедневный бонус\n"
        "• /shop - Магазин\n"
        "• /referral - Реферальная ссылка\n"
        "• /stats - Моя статистика\n"
        "• /leaderboard - Таблица лидеров\n"
        "• /review - Оставить отзыв"
    )

# ========== ПРИВЕТСТВИЕ В ГРУППЕ ==========
@dp.my_chat_member()
async def on_bot_added(event: types.ChatMemberUpdated):
    if event.new_chat_member.status == "member":
        chat = event.chat
        await bot.send_message(
            chat.id,
            f"👋 Привет, {chat.title}! Я игровой бот. Список игр: /start"
        )

# ========== ЗАПУСК ==========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())