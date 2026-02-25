import asyncio
import random
import sqlite3
import logging
import string
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.middlewares.throttling import ThrottlingMiddleware  # антиспам

# ========== НАСТРОЙКИ ==========
TOKEN = "8032635677:AAFi83m59Q8kcUxgvUwo7Y6Z13AwYAQKVpk"
REVIEW_CHAT_ID = -5235029911
MAX_COINS = 5000  # максимальное количество монет

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

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Подключаем антиспам
dp.message.middleware(ThrottlingMiddleware(rate_limit=0.5))

# ========== ХРАНИЛИЩА ИГР ==========
minesweeper_games = {}
tictactoe_games = {}
guess_games = {}
rps_games = {}
multiplayer_games = {}
farm_games = {}

# ========== МАГАЗИН ==========
SHOP_ITEMS = {
    'skin_gold': {'name': '✨ Золотой скин', 'price': 500, 'desc': 'Ваше имя в игре станет золотым'},
    'skin_rainbow': {'name': '🌈 Радужный скин', 'price': 1000, 'desc': 'Имя переливается всеми цветами'},
    'double_reward': {'name': '🎁 Удвоение награды', 'price': 300, 'desc': 'В следующих 3 играх награда x2'},
    'extra_attempt': {'name': '🔄 Дополнительная попытка', 'price': 100, 'desc': '+1 попытка в угадайке'},
}

# ========== ГЕНЕРАЦИЯ ID КОМНАТЫ ==========
def generate_room_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ========== ИГРА "ФЕРМА" ==========
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

# ========== САПЁР ==========
MS_DIFFICULTY = {
    'easy': {'rows': 5, 'cols': 5, 'mines': 5, 'name': 'Лёгкая (5x5, 5 мин)'},
    'medium': {'rows': 8, 'cols': 8, 'mines': 10, 'name': 'Средняя (8x8, 10 мин)'},
    'hard': {'rows': 10, 'cols': 10, 'mines': 15, 'name': 'Сложная (10x10, 15 мин)'}
}

def ms_new_board(rows, cols, mines, first_r, first_c):
    board = [[0 for _ in range(cols)] for _ in range(rows)]
    all_positions = [(r, c) for r in range(rows) for c in range(cols) if (r, c) != (first_r, first_c)]
    mine_positions = random.sample(all_positions, mines)
    for r, c in mine_positions:
        board[r][c] = -1
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and board[nr][nc] != -1:
                    board[nr][nc] += 1
    return board, set(mine_positions)

def ms_get_keyboard(board, opened, flags, rows, cols, mode):
    keyboard = []
    for r in range(rows):
        row_buttons = []
        for c in range(cols):
            if (r, c) in flags:
                text = '🚩'
            elif (r, c) in opened:
                cell = board[r][c]
                if cell == -1:
                    text = '💣'
                elif cell == 0:
                    text = '⬛'
                else:
                    text = str(cell)
            else:
                text = '⬜'
            callback_data = f"ms_cell_{r}_{c}"
            row_buttons.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        keyboard.append(row_buttons)
    mode_text = "⛏ Копать" if mode == 'flag' else "🚩 Флаг"
    mode_button = [InlineKeyboardButton(text=f"Режим: {mode_text}", callback_data="ms_toggle_mode")]
    keyboard.append(mode_button)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def ms_open_cell(game_key, r, c, callback):
    game = minesweeper_games[game_key]
    board = game['board']
    opened = game['opened']
    mines = game['mines']
    flags = game['flags']
    rows, cols = game['rows'], game['cols']
    mode = game['mode']

    if (r, c) in flags:
        await callback.answer("Сначала снимите флаг!")
        return

    if board is None:
        board, mines = ms_new_board(rows, cols, game['mines_count'], r, c)
        game['board'] = board
        game['mines'] = mines
        game['opened'].add((r, c))
        if board[r][c] == 0:
            await ms_open_zeros(board, opened, mines, rows, cols, r, c)
        await ms_update_board(callback, game_key)
        return

    if (r, c) in opened:
        await callback.answer("Клетка уже открыта")
        return

    if (r, c) in mines:
        opened.update(mines)
        keyboard = ms_get_keyboard(board, opened, flags, rows, cols, mode)
        await callback.message.edit_text(
            f"💥 Вы наступили на мину! Игра окончена.\n/minesweeper — новая игра",
            reply_markup=keyboard
        )
        del minesweeper_games[game_key]
        await callback.answer()
        return

    opened.add((r, c))
    if board[r][c] == 0:
        await ms_open_zeros(board, opened, mines, rows, cols, r, c)

    await ms_check_win(callback, game_key)

async def ms_open_zeros(board, opened, mines, rows, cols, r, c):
    queue = [(r, c)]
    visited = {(r, c)}
    while queue:
        cr, cc = queue.pop(0)
        for dr, dc in [(-1,-1), (-1,0), (-1,1), (0,-1), (0,1), (1,-1), (1,0), (1,1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                if (nr, nc) not in mines:
                    visited.add((nr, nc))
                    opened.add((nr, nc))
                    if board[nr][nc] == 0:
                        queue.append((nr, nc))

async def ms_check_win(callback, game_key):
    game = minesweeper_games[game_key]
    opened = game['opened']
    mines = game['mines']
    rows, cols = game['rows'], game['cols']
    total_cells = rows * cols
    safe_cells = total_cells - len(mines)

    if len(opened) == safe_cells:
        opened.update(mines)
        keyboard = ms_get_keyboard(game['board'], opened, game['flags'], rows, cols, game['mode'])
        await callback.message.edit_text(
            f"🎉 Поздравляю! Вы открыли все безопасные клетки! +50 монет",
            reply_markup=keyboard
        )
        user = callback.from_user
        update_user_stats(user.id, user.username, user.first_name, games_played=1, games_won=1)
        add_coins(user.id, 50)
        del minesweeper_games[game_key]
        await callback.answer()
        return

    await ms_update_board(callback, game_key)

async def ms_update_board(callback, game_key):
    game = minesweeper_games[game_key]
    board = game['board']
    opened = game['opened']
    flags = game['flags']
    rows, cols = game['rows'], game['cols']
    mode = game['mode']
    safe_cells = rows * cols - len(game['mines'])
    keyboard = ms_get_keyboard(board, opened, flags, rows, cols, mode)
    await callback.message.edit_text(
        f"Сапёр ({game['difficulty_name']})\n"
        f"Открыто {len(opened)}/{safe_cells} безопасных клеток",
        reply_markup=keyboard
    )
    await callback.answer()

# ========== КРЕСТИКИ-НОЛИКИ С БОТОМ ==========
def ttt_new_game():
    return [[' ' for _ in range(3)] for _ in range(3)]

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
            callback_data = f"ttt_move_{r}_{c}"
            row_buttons.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        keyboard.append(row_buttons)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def ttt_bot_move(board):
    empty = [(r, c) for r in range(3) for c in range(3) if board[r][c] == ' ']
    return random.choice(empty) if empty else None

# ========== КАМЕНЬ-НОЖНИЦЫ-БУМАГА ==========
RPS_EMOJI = {'камень': '🪨', 'ножницы': '✂️', 'бумага': '📄'}
RPS_BEATS = {'камень': 'ножницы', 'ножницы': 'бумага', 'бумага': 'камень'}

def rps_get_keyboard():
    kb = [
        [InlineKeyboardButton(text=f"{RPS_EMOJI['камень']} Камень", callback_data="rps_камень")],
        [InlineKeyboardButton(text=f"{RPS_EMOJI['ножницы']} Ножницы", callback_data="rps_ножницы")],
        [InlineKeyboardButton(text=f"{RPS_EMOJI['бумага']} Бумага", callback_data="rps_бумага")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ========== ИГРЫ (DICE, BASKETBALL и т.д.) ==========
@dp.message(Command("dice"))
async def cmd_dice(message: types.Message):
    user_roll = random.randint(1, 6)
    bot_roll = random.randint(1, 6)
    if user_roll > bot_roll:
        result = "🎉 Вы выиграли! +20 монет"
        add_coins(message.from_user.id, 20)
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1, games_won=1)
    elif user_roll < bot_roll:
        result = "🤖 Бот выиграл!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    else:
        result = "🤝 Ничья!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    await message.reply(f"🎲 Ваш бросок: {user_roll}\n🤖 Бот: {bot_roll}\n\n{result}")

@dp.message(Command("basketball"))
async def cmd_basketball(message: types.Message):
    user_score = random.randint(0, 30)
    bot_score = random.randint(0, 30)
    if user_score > bot_score:
        result = "🏆 Вы победили! +25 монет"
        add_coins(message.from_user.id, 25)
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1, games_won=1)
    elif user_score < bot_score:
        result = "🤖 Бот победил!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    else:
        result = "🤝 Ничья!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    await message.reply(f"🏀 Ваш счёт: {user_score}\n🤖 Бот: {bot_score}\n\n{result}")

@dp.message(Command("football"))
async def cmd_football(message: types.Message):
    user_goals = random.randint(0, 5)
    bot_goals = random.randint(0, 5)
    if user_goals > bot_goals:
        result = "⚽ Гол! Вы выиграли! +25 монет"
        add_coins(message.from_user.id, 25)
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1, games_won=1)
    elif user_goals < bot_goals:
        result = "🤖 Бот забил больше..."
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    else:
        result = "🤝 Ничья!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    await message.reply(f"⚽ Ваши голы: {user_goals}\n🤖 Голы бота: {bot_goals}\n\n{result}")

@dp.message(Command("bowling"))
async def cmd_bowling(message: types.Message):
    user_pins = random.randint(0, 10)
    bot_pins = random.randint(0, 10)
    if user_pins > bot_pins:
        result = "🎳 Страйк! Вы выиграли! +25 монет"
        add_coins(message.from_user.id, 25)
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1, games_won=1)
    elif user_pins < bot_pins:
        result = "🤖 Бот выбил больше..."
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    else:
        result = "🤝 Ничья!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    await message.reply(f"🎳 Ваши кегли: {user_pins}\n🤖 Кегли бота: {bot_pins}\n\n{result}")

@dp.message(Command("darts"))
async def cmd_darts(message: types.Message):
    user_score = random.randint(0, 180)
    bot_score = random.randint(0, 180)
    if user_score > bot_score:
        result = "🎯 Меткий бросок! +25 монет"
        add_coins(message.from_user.id, 25)
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1, games_won=1)
    elif user_score < bot_score:
        result = "🤖 Бот точнее..."
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    else:
        result = "🤝 Ничья!"
        update_user_stats(message.from_user.id, message.from_user.username, 
                         message.from_user.first_name, games_played=1)
    await message.reply(f"🎯 Ваши очки: {user_score}\n🤖 Очки бота: {bot_score}\n\n{result}")

# ========== ЕЖЕДНЕВНЫЙ БОНУС ==========
@dp.message(Command("daily"))
async def cmd_daily(message: types.Message):
    user_id = message.from_user.id
    if can_daily(user_id):
        new_balance = add_coins(user_id, 50)
        update_daily(user_id)
        await message.reply(f"✅ Вы получили 50 монет за ежедневный вход!\n💰 Текущий баланс: {new_balance}")
    else:
        await message.reply("⏳ Вы уже получали бонус сегодня. Приходите завтра!")

# ========== СТАТИСТИКА ==========
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    stats = get_user_stats(user_id)
    if stats:
        text = (
            f"📊 <b>Статистика {message.from_user.full_name}</b>\n\n"
            f"🎮 Игр сыграно: {stats[3]}\n"
            f"🏆 Побед: {stats[4]}\n"
            f"💰 Монет: {stats[8]}\n"
            f"🎯 Лучшее в угадайке: {stats[6] if stats[6] else '—'}"
        )
    else:
        text = "У вас пока нет статистики. Сыграйте в любую игру!"
    await message.reply(text)

# ========== РЕФЕРАЛЬНАЯ СИСТЕМА ==========
@dp.message(Command("referral"))
async def cmd_referral(message: types.Message):
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    await message.reply(
        f"🔗 Ваша реферальная ссылка:\n{referral_link}\n\n"
        f"За каждого друга, который перейдёт по ссылке и начнёт играть, вы получите 100 монет!"
    )

# ========== МАГАЗИН ==========
@dp.message(Command("shop"))
async def cmd_shop(message: types.Message):
    keyboard = []
    for item_id, item in SHOP_ITEMS.items():
        keyboard.append([InlineKeyboardButton(
            text=f"{item['name']} - {item['price']}💰",
            callback_data=f"shop_{item_id}"
        )])
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="shop_close")])
    await message.reply(
        "🛒 <b>Магазин</b>\n\nПокупайте скины и бонусы за монеты!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda c: c.data.startswith("shop_"))
async def shop_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split('_')[1]
    
    if action == "close":
        await callback.message.delete()
        await callback.answer()
        return
    
    item_id = action
    item = SHOP_ITEMS.get(item_id)
    if not item:
        await callback.answer("Товар не найден")
        return
    
    if spend_coins(user_id, item['price']):
        if item_id == 'skin_gold':
            cursor.execute('UPDATE users SET skin = ? WHERE user_id = ?', ('gold', user_id))
            conn.commit()
            await callback.message.edit_text(f"✅ Вы купили {item['name']}! Теперь ваше имя золотое.")
        elif item_id == 'skin_rainbow':
            cursor.execute('UPDATE users SET skin = ? WHERE user_id = ?', ('rainbow', user_id))
            conn.commit()
            await callback.message.edit_text(f"✅ Вы купили {item['name']}! Теперь ваше имя радужное.")
        elif item_id == 'double_reward':
            await callback.message.edit_text(f"✅ Вы купили {item['name']}! Следующие 3 игры с удвоенной наградой.")
        elif item_id == 'extra_attempt':
            cursor.execute('UPDATE users SET guess_attempts_best = guess_attempts_best + 1 WHERE user_id = ?', (user_id,))
            conn.commit()
            await callback.message.edit_text(f"✅ Вы купили {item['name']}!")
    else:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
    await callback.answer()

# ========== ПРИВАТНЫЕ КОМНАТЫ ==========
@dp.message(Command("create_room"))
async def cmd_create_room(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите тип игры: /create_room [tictactoe|rps]")
        return
    
    game_type = args[1].strip().lower()
    if game_type not in ['tictactoe', 'rps']:
        await message.reply("❌ Поддерживаются только: tictactoe, rps")
        return
    
    user_id = message.from_user.id
    room_id = generate_room_id()
    
    cursor.execute('''
        INSERT INTO rooms (room_id, owner_id, game_type, players, state)
        VALUES (?, ?, ?, ?, ?)
    ''', (room_id, user_id, game_type, f'[{user_id}]', '{}'))
    conn.commit()
    
    await message.reply(
        f"✅ Комната создана!\n"
        f"🔑 Код комнаты: <code>{room_id}</code>\n"
        f"📌 Тип игры: {game_type}\n\n"
        f"Пригласите друга: /join {room_id}"
    )

@dp.message(Command("join"))
async def cmd_join(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Укажите код комнаты: /join КОД")
        return
    
    room_id = args[1].upper()
    user_id = message.from_user.id
    
    cursor.execute('SELECT * FROM rooms WHERE room_id = ?', (room_id,))
    room = cursor.fetchone()
    if not room:
        await message.reply("❌ Комната не найдена")
        return
    
    import json
    players = json.loads(room[3])
    if len(players) >= 2:
        await message.reply("❌ В комнате уже два игрока")
        return
    
    if user_id in players:
        await message.reply("❌ Вы уже в этой комнате")
        return
    
    players.append(user_id)
    cursor.execute('UPDATE rooms SET players = ? WHERE room_id = ?', 
                   (json.dumps(players), room_id))
    conn.commit()
    
    owner_id = room[1]
    game_type = room[2]
    
    await bot.send_message(
        owner_id,
        f"🔔 Игрок {message.from_user.full_name} присоединился к вашей комнате {room_id}!\nНачинаем игру?"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Начать игру", callback_data=f"start_game_{room_id}")]
    ])
    
    await message.reply(
        f"✅ Вы присоединились к комнате {room_id}.\nОжидайте, пока владелец начнёт игру.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data.startswith("start_game_"))
async def start_game_callback(callback: CallbackQuery):
    room_id = callback.data.split('_')[2]
    user_id = callback.from_user.id
    
    cursor.execute('SELECT * FROM rooms WHERE room_id = ?', (room_id,))
    room = cursor.fetchone()
    if not room:
        await callback.answer("Комната не найдена")
        await callback.message.delete()
        return
    
    if room[1] != user_id:
        await callback.answer("Только владелец может начать игру")
        return
    
    import json
    players = json.loads(room[3])
    if len(players) < 2:
        await callback.answer("Недостаточно игроков")
        return
    
    game_type = room[2]
    
    cursor.execute('DELETE FROM rooms WHERE room_id = ?', (room_id,))
    conn.commit()
    
    if game_type == 'tictactoe':
        multiplayer_games[room_id] = {
            'players': players,
            'board': [[' ' for _ in range(3)] for _ in range(3)],
            'turn': players[0],
            'game_type': 'tictactoe'
        }
        for p in players:
            try:
                await bot.send_message(
                    p,
                    f"🎮 Игра началась! Ваш соперник: {players[1] if p == players[0] else players[0]}\n"
                    f"Ход игрока: {'❌' if multiplayer_games[room_id]['turn'] == p else '⭕'}",
                    reply_markup=ttt_get_keyboard(multiplayer_games[room_id]['board'])
                )
            except:
                pass
    elif game_type == 'rps':
        multiplayer_games[room_id] = {
            'players': players,
            'choices': {},
            'game_type': 'rps'
        }
        for p in players:
            try:
                await bot.send_message(
                    p,
                    f"🎮 Дуэль в КНБ! Ваш соперник: {players[1] if p == players[0] else players[0]}\nСделайте свой выбор:",
                    reply_markup=rps_get_keyboard()
                )
            except:
                pass
    
    await callback.message.edit_text("✅ Игра началась!")

# ========== МНОГОПОЛЬЗОВАТЕЛЬСКИЕ ИГРЫ В ГРУППАХ ==========
@dp.message(Command("duel"))
async def cmd_duel(message: types.Message):
    if message.chat.type == "private":
        await message.reply("❌ Эта команда доступна только в группах!")
        return
    
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение пользователя, с которым хотите сразиться!")
        return
    
    opponent = message.reply_to_message.from_user
    if opponent.id == message.from_user.id:
        await message.reply("❌ Нельзя сражаться с самим собой!")
        return
    
    if opponent.is_bot:
        await message.reply("❌ Нельзя сражаться с ботом! Используйте /rps для игры с ботом.")
        return
    
    room_id = generate_room_id()
    players = [message.from_user.id, opponent.id]
    multiplayer_games[room_id] = {
        'players': players,
        'choices': {},
        'game_type': 'rps',
        'chat_id': message.chat.id,
        'message_id': None
    }
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪨 Камень", callback_data=f"duel_{room_id}_камень")],
        [InlineKeyboardButton(text="✂️ Ножницы", callback_data=f"duel_{room_id}_ножницы")],
        [InlineKeyboardButton(text="📄 Бумага", callback_data=f"duel_{room_id}_бумага")]
    ])
    
    sent = await message.reply(
        f"⚔️ Дуэль между {message.from_user.full_name} и {opponent.full_name}!\nВыбирайте оружие:",
        reply_markup=keyboard
    )
    multiplayer_games[room_id]['message_id'] = sent.message_id

@dp.callback_query(lambda c: c.data.startswith("duel_"))
async def duel_callback(callback: CallbackQuery):
    data = callback.data.split('_')
    room_id = data[1]
    choice = data[2]
    user_id = callback.from_user.id
    
    game = multiplayer_games.get(room_id)
    if not game:
        await callback.answer("Игра не найдена или уже завершена")
        return
    
    if user_id not in game['players']:
        await callback.answer("Вы не участвуете в этой дуэли")
        return
    
    if user_id in game['choices']:
        await callback.answer("Вы уже сделали выбор!")
        return
    
    game['choices'][user_id] = choice
    
    if len(game['choices']) == 2:
        p1, p2 = game['players']
        c1 = game['choices'][p1]
        c2 = game['choices'][p2]
        
        if c1 == c2:
            result = "🤝 Ничья!"
            winner = None
        elif RPS_BEATS[c1] == c2:
            result = f"🎉 Победил {callback.from_user.full_name if p1 == user_id else 'противник'}!"
            winner = p1
        else:
            result = f"🎉 Победил {callback.from_user.full_name if p2 == user_id else 'противник'}!"
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
        
        del multiplayer_games[room_id]
    else:
        await callback.answer("Выбор принят, ожидаем соперника...")

@dp.message(Command("ttt"))
async def cmd_ttt_multi(message: types.Message):
    if message.chat.type == "private":
        await message.reply("❌ Эта команда доступна только в группах!")
        return
    
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение пользователя, с которым хотите сыграть!")
        return
    
    opponent = message.reply_to_message.from_user
    if opponent.id == message.from_user.id:
        await message.reply("❌ Нельзя играть с самим собой!")
        return
    
    if opponent.is_bot:
        await message.reply("❌ Нельзя играть с ботом! Используйте /tictactoe для игры с ботом.")
        return
    
    room_id = generate_room_id()
    players = [message.from_user.id, opponent.id]
    multiplayer_games[room_id] = {
        'players': players,
        'board': [[' ' for _ in range(3)] for _ in range(3)],
        'turn': players[0],
        'game_type': 'tictactoe',
        'chat_id': message.chat.id,
        'message_id': None
    }
    
    keyboard = ttt_get_keyboard(multiplayer_games[room_id]['board'])
    sent = await message.reply(
        f"🎮 Игра в крестики-нолики между {message.from_user.full_name} (❌) и {opponent.full_name} (⭕)\n"
        f"Ход игрока {message.from_user.full_name}:",
        reply_markup=keyboard
    )
    multiplayer_games[room_id]['message_id'] = sent.message_id

# ========== ОБРАБОТЧИК ДЛЯ КРЕСТИКОВ-НОЛИКОВ ==========
@dp.callback_query(lambda c: c.data.startswith("ttt_move_"))
async def ttt_move_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    game_room = None
    room_id = None
    for rid, game in multiplayer_games.items():
        if game.get('message_id') == callback.message.message_id and user_id in game['players']:
            game_room = game
            room_id = rid
            break
    
    if game_room and game_room['game_type'] == 'tictactoe':
        _, _, r_str, c_str = callback.data.split('_')
        r, c = int(r_str), int(c_str)
        
        if game_room['turn'] != user_id:
            await callback.answer("Сейчас не ваш ход!")
            return
        
        if game_room['board'][r][c] != ' ':
            await callback.answer("Клетка занята!")
            return
        
        symbol = 'X' if game_room['players'][0] == user_id else 'O'
        game_room['board'][r][c] = symbol
        
        winner = ttt_check_winner(game_room['board'])
        if winner:
            if winner == 'X':
                winner_id = game_room['players'][0]
                loser_id = game_room['players'][1]
            elif winner == 'O':
                winner_id = game_room['players'][1]
                loser_id = game_room['players'][0]
            else:
                winner_id = None
            
            if winner_id:
                update_user_stats(winner_id, None, None, games_played=1, games_won=1)
                add_coins(winner_id, 50)
                update_user_stats(loser_id, None, None, games_played=1)
                result_text = f"🎉 Победил игрок {callback.from_user.full_name if winner_id == user_id else 'противник'}!"
            else:
                for p in game_room['players']:
                    update_user_stats(p, None, None, games_played=1)
                result_text = "🤝 Ничья!"
            
            keyboard = ttt_get_keyboard(game_room['board'])
            await callback.message.edit_text(
                f"{result_text}\nНовая игра: /ttt @user",
                reply_markup=keyboard
            )
            del multiplayer_games[room_id]
            await callback.answer()
            return
        
        game_room['turn'] = game_room['players'][1] if game_room['turn'] == game_room['players'][0] else game_room['players'][0]
        
        keyboard = ttt_get_keyboard(game_room['board'])
        next_player_name = (await bot.get_chat(game_room['turn'])).first_name
        await callback.message.edit_text(
            f"Ход игрока {next_player_name}:",
            reply_markup=keyboard
        )
        await callback.answer()
        return
    
    # Если не нашли многопользовательскую - это одиночная игра с ботом
    _, _, r_str, c_str = callback.data.split('_')
    r, c = int(r_str), int(c_str)
    user_id = callback.from_user.id
    game_key = (chat_id, user_id)

    if game_key not in tictactoe_games:
        await callback.answer("Игра не найдена")
        await callback.message.delete()
        return

    game = tictactoe_games[game_key]
    board = game['board']
    if board[r][c] != ' ':
        await callback.answer("Клетка занята!")
        return
    if game['turn'] != 'player':
        await callback.answer("Сейчас не ваш ход!")
        return

    board[r][c] = 'X'
    game['turn'] = 'bot'
    result = ttt_check_winner(board)
    if result:
        await ttt_game_over(callback, game_key, result)
        return

    bot_move = ttt_bot_move(board)
    if bot_move:
        br, bc = bot_move
        board[br][bc] = 'O'
        game['turn'] = 'player'
        result = ttt_check_winner(board)
        if result:
            await ttt_game_over(callback, game_key, result)
            return
    else:
        await ttt_game_over(callback, game_key, 'draw')
        return

    keyboard = ttt_get_keyboard(board)
    await callback.message.edit_text("Ваш ход:", reply_markup=keyboard)
    await callback.answer()

async def ttt_game_over(callback, game_key, result):
    game = tictactoe_games[game_key]
    board = game['board']
    coins_reward = 0
    if result == 'X':
        text = "🎉 Вы выиграли! +30 монет"
        coins_reward = 30
        update_user_stats(callback.from_user.id, callback.from_user.username,
                         callback.from_user.first_name, games_played=1, games_won=1)
    elif result == 'O':
        text = "🤖 Бот выиграл!"
        update_user_stats(callback.from_user.id, callback.from_user.username,
                         callback.from_user.first_name, games_played=1)
    else:
        text = "🤝 Ничья!"
        update_user_stats(callback.from_user.id, callback.from_user.username,
                         callback.from_user.first_name, games_played=1)
    
    if coins_reward:
        add_coins(callback.from_user.id, coins_reward)
    
    keyboard = ttt_get_keyboard(board)
    await callback.message.edit_text(f"{text}\nНовая игра: /tictactoe", reply_markup=keyboard)
    del tictactoe_games[game_key]
    await callback.answer()

@dp.message(Command("cancel_ttt"))
async def cancel_ttt(message: types.Message):
    game_key = (message.chat.id, message.from_user.id)
    if game_key in tictactoe_games:
        del tictactoe_games[game_key]
        await message.reply("Игра отменена.")
    else:
        await message.reply("Нет активной игры.")

# ========== УГАДАЙ ЧИСЛО ==========
@dp.message(Command("guess"))
async def cmd_guess(message: types.Message):
    game_key = (message.chat.id, message.from_user.id)
    if game_key in guess_games:
        await message.reply("Вы уже угадываете число! /cancel_guess для выхода.")
        return
    number = random.randint(1, 100)
    guess_games[game_key] = {'number': number, 'attempts': 0}
    await message.reply("🔢 Я загадал число от 1 до 100. Вводите предположения.")

@dp.message(Command("cancel_guess"))
async def cancel_guess(message: types.Message):
    game_key = (message.chat.id, message.from_user.id)
    if game_key in guess_games:
        del guess_games[game_key]
        await message.reply("Игра завершена.")
    else:
        await message.reply("Нет активной игры.")

@dp.message(lambda msg: msg.text and msg.text.isdigit())
async def guess_number(message: types.Message):
    game_key = (message.chat.id, message.from_user.id)
    if game_key not in guess_games:
        return
    game = guess_games[game_key]
    guess = int(message.text)
    game['attempts'] += 1
    target = game['number']
    if guess < target:
        await message.reply("⬆️ Моё число больше!")
    elif guess > target:
        await message.reply("⬇️ Моё число меньше!")
    else:
        reward = max(50 - game['attempts'] * 2, 10)
        await message.reply(
            f"🎉 Поздравляю! Ты угадал число {target} за {game['attempts']} попыток!\n"
            f"Вы получили {reward} монет!"
        )
        user = message.from_user
        current = get_user_stats(user.id)
        if current and (current[6] is None or game['attempts'] < current[6]):
            cursor.execute('UPDATE users SET guess_attempts_best = ? WHERE user_id = ?',
                           (game['attempts'], user.id))
            conn.commit()
        update_user_stats(user.id, user.username, user.first_name, games_played=1, games_won=1)
        add_coins(user.id, reward)
        del guess_games[game_key]

# ========== КАМЕНЬ-НОЖНИЦЫ-БУМАГА С БОТОМ ==========
@dp.message(Command("rps"))
async def cmd_rps(message: types.Message):
    keyboard = rps_get_keyboard()
    await message.reply("Выберите ваш ход:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("rps_камень") or c.data.startswith("rps_ножницы") or c.data.startswith("rps_бумага"))
async def rps_bot_callback(callback: CallbackQuery):
    user_choice = callback.data.split('_')[1]
    bot_choice = random.choice(list(RPS_EMOJI.keys()))
    user_emoji = RPS_EMOJI[user_choice]
    bot_emoji = RPS_EMOJI[bot_choice]

    if user_choice == bot_choice:
        result = "Ничья!"
        win = 0
        coins = 5
    elif RPS_BEATS[user_choice] == bot_choice:
        result = "Вы выиграли! 🎉"
        win = 1
        coins = 20
    else:
        result = "Бот выиграл! 🤖"
        win = 0
        coins = 0

    text = (f"{user_emoji} Вы: {user_choice}\n"
            f"{bot_emoji} Бот: {bot_choice}\n\n"
            f"{result}")
    
    if coins:
        text += f"\n+{coins} монет"
        add_coins(callback.from_user.id, coins)
    
    await callback.message.edit_text(text)
    update_user_stats(callback.from_user.id, callback.from_user.username,
                     callback.from_user.first_name, games_played=1, games_won=win)
    await callback.answer()

# ========== ТАБЛИЦА ЛИДЕРОВ ==========
@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
    cursor.execute('''
        SELECT first_name, games_won, coins FROM users
        ORDER BY games_won DESC
        LIMIT 10
    ''')
    top = cursor.fetchall()
    if not top:
        await message.reply("Пока нет данных для таблицы лидеров.")
        return
    text = "🏆 <b>Таблица лидеров (по победам)</b>\n\n"
    for i, (name, wins, coins) in enumerate(top, 1):
        text += f"{i}. {name} — {wins} побед | {coins}💰\n"
    await message.reply(text)

# ========== ОТЗЫВЫ ==========
@dp.message(Command("review"))
async def cmd_review(message: types.Message):
    await message.reply(
        "📝 Пожалуйста, отправьте одним сообщением ваш отзыв и оценку от 1 до 5.\n"
        "Пример: <code>Отличный бот! 5</code>\n\n"
        "⚠️ Можно оставлять только 1 отзыв в день."
    )

@dp.message(lambda msg: msg.text and not msg.text.startswith('/'))
async def handle_review(message: types.Message):
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
        await message.reply("❌ Пожалуйста, укажите оценку от 1 до 5 в сообщении.")
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

# ========== САПЁР (ОСНОВНЫЕ КОМАНДЫ) ==========
@dp.message(Command("minesweeper"))
async def cmd_minesweeper(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=MS_DIFFICULTY['easy']['name'], callback_data="ms_diff_easy")],
        [InlineKeyboardButton(text=MS_DIFFICULTY['medium']['name'], callback_data="ms_diff_medium")],
        [InlineKeyboardButton(text=MS_DIFFICULTY['hard']['name'], callback_data="ms_diff_hard")]
    ])
    await message.reply("Выберите сложность:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("ms_diff_"))
async def ms_difficulty_callback(callback: CallbackQuery):
    diff = callback.data.split('_')[2]
    config = MS_DIFFICULTY[diff]
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    game_key = (chat_id, user_id)

    minesweeper_games[game_key] = {
        'board': None,
        'opened': set(),
        'flags': set(),
        'mines': None,
        'rows': config['rows'],
        'cols': config['cols'],
        'mines_count': config['mines'],
        'difficulty_name': config['name'],
        'mode': 'dig'
    }

    dummy_board = [[0 for _ in range(config['cols'])] for _ in range(config['rows'])]
    keyboard = ms_get_keyboard(dummy_board, set(), set(), config['rows'], config['cols'], 'dig')
    await callback.message.edit_text(
        f"Сапёр ({config['name']})\n"
        "Нажмите на любую клетку, чтобы начать (первый ход безопасен).",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("ms_cell_"))
async def ms_cell_callback(callback: CallbackQuery):
    _, _, r_str, c_str = callback.data.split('_')
    r, c = int(r_str), int(c_str)
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    game_key = (chat_id, user_id)

    if game_key not in minesweeper_games:
        await callback.answer("Игра не найдена. Начните /minesweeper")
        await callback.message.delete()
        return

    game = minesweeper_games[game_key]
    if game['mode'] == 'dig':
        await ms_open_cell(game_key, r, c, callback)
    else:
        flags = game['flags']
        if (r, c) in game['opened']:
            await callback.answer("Нельзя ставить флаг на открытую клетку!")
            return
        if (r, c) in flags:
            flags.remove((r, c))
        else:
            flags.add((r, c))
        await ms_update_board(callback, game_key)

@dp.callback_query(lambda c: c.data == "ms_toggle_mode")
async def ms_toggle_mode(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    game_key = (chat_id, user_id)
    if game_key in minesweeper_games:
        game = minesweeper_games[game_key]
        game['mode'] = 'flag' if game['mode'] == 'dig' else 'dig'
        await ms_update_board(callback, game_key)
    else:
        await callback.answer("Нет активной игры")

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
        "👥 <b>Многопользовательские игры (в группах):</b>\n"
        "• /duel @user - Дуэль в КНБ\n"
        "• /ttt @user - Крестики-нолики вдвоём\n\n"
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