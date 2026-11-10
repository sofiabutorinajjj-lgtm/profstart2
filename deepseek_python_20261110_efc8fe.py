"""
ПрофСтарт — Telegram-бот для поиска работы, обучения и карьерного старта
Ленинск-Кузнецкий.
"""

import os
import sys
import csv
import io
import asyncio
import html
import logging
from datetime import datetime
from pathlib import Path

# --- автозависимости ---
try:
    import aiosqlite
    from aiogram import Bot, Dispatcher, F, Router
except ImportError:
    import subprocess
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "aiogram>=3.13,<4", "aiosqlite>=0.20,<1"
    ])

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ============================================================
# КОНФИГ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CITY = os.getenv("CITY", "Ленинск-Кузнецкий")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = Path(os.getenv("DB_PATH", "data/profstart.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("profstart")
PAGE_SIZE = 5

# ============================================================
# КЛАВИАТУРЫ
# ============================================================
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💼 Вакансии"), KeyboardButton(text="🔎 Поиск")],
        [KeyboardButton(text="🧭 Профориентация"), KeyboardButton(text="⭐ Избранное")],
        [KeyboardButton(text="📬 Мои отклики"), KeyboardButton(text="🎓 Обучение")],
        [KeyboardButton(text="📅 Мероприятия"), KeyboardButton(text="👤 Моя анкета")],
        [KeyboardButton(text="✉️ Обратная связь"), KeyboardButton(text="ℹ️ Помощь")],
    ], resize_keyboard=True
)
CANCEL_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

# ============================================================
# FSM
# ============================================================
class Form(StatesGroup):
    name = State()
    age = State()
    contact = State()
    goal = State()
    feedback = State()
    search = State()
    apply_message = State()
    broadcast = State()

class Quiz(StatesGroup):
    q1 = State(); q2 = State(); q3 = State(); q4 = State(); q5 = State()

def esc(value) -> str:
    return html.escape(str(value or ""))

# ============================================================
# БАЗА ДАННЫХ
# ============================================================
SCHEMA_VERSION = 3

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT, age TEXT,
            contact TEXT, goal TEXT, direction TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vacancies(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            company TEXT NOT NULL, salary TEXT DEFAULT '', description TEXT DEFAULT '',
            contact TEXT DEFAULT '', category TEXT DEFAULT 'other',
            active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            date_text TEXT DEFAULT '', place TEXT DEFAULT '', description TEXT DEFAULT '',
            active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, vacancy_id INTEGER,
            tg_id INTEGER, message TEXT DEFAULT '', status TEXT DEFAULT 'new',
            created_at TEXT, UNIQUE(vacancy_id, tg_id)
        );
        CREATE TABLE IF NOT EXISTS favorites(
            tg_id INTEGER, vacancy_id INTEGER, PRIMARY KEY(tg_id, vacancy_id)
        );
        CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, message TEXT, created_at TEXT
        );
        """)
        await db.commit()

async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def send_admins(bot: Bot, text, reply_markup=None):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception:
            log.exception("Не удалось отправить сообщение администратору %s", admin_id)

def paginate_kb(prefix: str, page: int, total_pages: int):
    rows = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"{prefix}:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"{prefix}:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def vacancy_card(row) -> str:
    cat = {"worker": "⚙️ рабочая", "tech": "🛠 техническая", "engineer": "📈 инженерная", "intern": "🎓 стажировка", "other": "• прочее"}.get(row["category"] or "other", "• прочее")
    return f"<b>{esc(row['title'])}</b>  <i>({cat})</i>\n🏢 {esc(row['company'])}\n💰 {esc(row['salary']) or 'уточняется'}\n{esc(row['description'])}\n📞 {esc(row['contact']) or 'уточняется'}"

def vacancy_kb(vac_id: int, in_fav: bool) -> InlineKeyboardMarkup:
    fav_text = "💔 Убрать из избранного" if in_fav else "⭐ В избранное"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Откликнуться", callback_data=f"apply:{vac_id}")],
        [InlineKeyboardButton(text=fav_text, callback_data=f"fav:{vac_id}")]
    ])

# ============================================================
# РОУТЕРЫ
# ============================================================
common_router = Router()
vac_router = Router()
profile_router = Router()
quiz_router = Router()
admin_router = Router()

# --- Базовые команды ---
@common_router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Привет! Я — <b>ПрофСтарт</b> 🏭\n\n"
        f"Помогаю молодёжи {esc(CITY)} найти работу, узнать о производственных профессиях, обучении, стажировках и мероприятиях.\n\n"
        "Выбери раздел в меню. Не знаешь с чего начать — загляни в «🧭 Профориентация».",
        reply_markup=MENU)

@common_router.message(Command("help"))
@common_router.message(F.text == "ℹ️ Помощь")
async def help_message(message: Message):
    await message.answer(
        "<b>ПрофСтарт — помощь</b>\n"
        "💼 Вакансии — листай подборку, фильтруй, откликайся.\n"
        "🔎 Поиск — найди вакансию по слову.\n"
        "⭐ Избранное — сохранённые вакансии.\n"
        "📬 Мои отклики — куда ты уже откликнулся.\n"
        "🧭 Профориентация — короткий тест и подбор вакансий.\n"
        "🎓 Обучение — старт карьеры.\n"
        "📅 Мероприятия — экскурсии и встречи.\n"
        "👤 Моя анкета — твои данные.\n\n"
        "Админ-команды: /addvacancy /addevent /applications /vacancies /broadcast /export /stats")

@common_router.message(Command("cancel"))
@common_router.message(F.text == "❌ Отмена")
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=MENU)

@common_router.message(F.text == "🎓 Обучение")
async def learning(message: Message):
    await message.answer("🎓 <b>Обучение и стажировки</b>\n\nИщи программы обучения при работодателях, ученические места, практику и стажировки. Актуальные предложения уточняй через «✉️ Обратная связь».")

@common_router.message(F.text == "📅 Мероприятия")
async def show_events(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM events WHERE active=1 ORDER BY id DESC LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Ближайшие мероприятия пока не опубликованы.")
        return
    for row in rows:
        await message.answer(
            f"📅 <b>{esc(row['title'])}</b>\n"
            f"Когда: {esc(row['date_text']) or 'уточняется'}\n"
            f"Где: {esc(row['place']) or 'уточняется'}\n{esc(row['description'])}")

# ============================================================
# ВАКАНСИИ
# ============================================================
@vac_router.message(F.text == "💼 Вакансии")
async def vacancies_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все", callback_data="vlist:all:1")],
        [InlineKeyboardButton(text="⚙️ Рабочие", callback_data="vlist:worker:1"), InlineKeyboardButton(text="🛠 Технические", callback_data="vlist:tech:1")],
        [InlineKeyboardButton(text="📈 Инженерные", callback_data="vlist:engineer:1"), InlineKeyboardButton(text="🎓 Стажировки", callback_data="vlist:intern:1")],
    ])
    await message.answer("Выбери категорию или посмотри все вакансии:", reply_markup=kb)

@vac_router.callback_query(F.data.startswith("vlist:"))
async def vacancies_list(callback: CallbackQuery):
    _, category, page_s = callback.data.split(":")
    page = int(page_s)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = "active=1"
        params = []
        if category != "all":
            where += " AND category=?"
            params.append(category)
        cur = await db.execute(f"SELECT COUNT(*) FROM vacancies WHERE {where}", params)
        total = (await cur.fetchone())[0]
        if total == 0:
            await callback.answer("Пока нет вакансий в этой категории", show_alert=True)
            return
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        offset = (page - 1) * PAGE_SIZE
        cur = await db.execute(f"SELECT * FROM vacancies WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, PAGE_SIZE, offset))
        rows = await cur.fetchall()

    await callback.message.edit_text(f"Найдено вакансий: <b>{total}</b> (стр. {page}/{total_pages})", reply_markup=paginate_kb(f"vlist:{category}", page, total_pages))
    for row in rows:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT 1 FROM favorites WHERE tg_id=? AND vacancy_id=?", (callback.from_user.id, row["id"]))
            in_fav = bool(await cur.fetchone())
        await callback.message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], in_fav))
    await callback.answer()

@vac_router.message(F.text == "🔎 Поиск")
async def search_start(message: Message, state: FSMContext):
    await state.set_state(Form.search)
    await message.answer("Напиши слово для поиска:", reply_markup=CANCEL_KB)

@vac_router.message(Form.search, F.text)
async def search_run(message: Message, state: FSMContext):
    q = message.text.strip()[:100]
    await state.clear()
    like = f"%{q}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM vacancies WHERE active=1 AND (title LIKE ? OR company LIKE ? OR description LIKE ?) ORDER BY id DESC LIMIT 20", (like, like, like))
        rows = await cur.fetchall()
    if not rows:
        await message.answer(f"По запросу «{esc(q)}» ничего не нашлось.", reply_markup=MENU)
        return
    await message.answer(f"По запросу «{esc(q)}» найдено: <b>{len(rows)}</b>", reply_markup=MENU)
    for row in rows:
        await message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], False))

@vac_router.callback_query(F.data.startswith("fav:"))
async def fav_toggle(callback: CallbackQuery):
    vac_id = int(callback.data.split(":")[1])
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM favorites WHERE tg_id=? AND vacancy_id=?", (uid, vac_id))
        exists = await cur.fetchone()
        if exists:
            await db.execute("DELETE FROM favorites WHERE tg_id=? AND vacancy_id=?", (uid, vac_id))
            msg = "Убрано из избранного"
        else:
            await db.execute("INSERT INTO favorites(tg_id, vacancy_id) VALUES(?,?)", (uid, vac_id))
            msg = "Добавлено в избранное"
        await db.commit()
    await callback.answer(msg)
    await callback.message.edit_reply_markup(reply_markup=vacancy_kb(vac_id, not exists))

@vac_router.message(F.text == "⭐ Избранное")
async def favorites_list(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT v.* FROM favorites f JOIN vacancies v ON v.id=f.vacancy_id WHERE f.tg_id=? AND v.active=1 ORDER BY f.rowid DESC", (message.from_user.id,))
        rows = await cur.fetchall()
    if not rows:
        await message.answer("В избранном пусто. Открывай вакансии и жми ⭐.")
        return
    await message.answer(f"⭐ В избранном: <b>{len(rows)}</b>")
    for row in rows:
        await message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], True))

# --- Отклики ---
@vac_router.callback_query(F.data.startswith("apply:"))
async def apply_start(callback: CallbackQuery, state: FSMContext):
    vac_id = int(callback.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM applications WHERE vacancy_id=? AND tg_id=?", (vac_id, callback.from_user.id))
        if await cur.fetchone():
            await callback.answer("Ты уже откликался на эту вакансию", show_alert=True)
            return
    await state.update_data(apply_vac_id=vac_id)
    await state.set_state(Form.apply_message)
    await callback.answer()
    await callback.message.answer("Напиши пару слов о себе для работодателя. Или отправь «-», чтобы откликнуться без текста.", reply_markup=CANCEL_KB)

@vac_router.message(Form.apply_message, F.text)
async def apply_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    vac_id = data.get("apply_vac_id")
    text = "" if message.text.strip() in ("-", "нет", "пропустить") else message.text[:1000]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT title, company FROM vacancies WHERE id=?", (vac_id,))
        row = await cur.fetchone()
        if not row:
            await state.clear()
            await message.answer("Вакансия больше недоступна.", reply_markup=MENU)
            return
        try:
            await db.execute("INSERT INTO applications(vacancy_id, tg_id, message, status, created_at) VALUES(?,?,?,?,?)", (vac_id, message.from_user.id, text, "new", datetime.now().isoformat(timespec="minutes")))
            await db.commit()
        except aiosqlite.IntegrityError:
            await state.clear()
            await message.answer("Ты уже откликался на эту вакансию.", reply_markup=MENU)
            return
    await state.clear()
    await message.answer("Отклик отправлен ✅ Администратор свяжется с тобой.", reply_markup=MENU)
    await send_admins(bot, f"📨 <b>Отклик</b> на «{esc(row['title'])}»\n👤 @{esc(message.from_user.username or '—')} (ID {message.from_user.id})\n💬 {esc(text) or '<i>без сообщения</i>'}\n\nУправление: /applications")

@vac_router.message(F.text == "📬 Мои отклики")
async def my_applications(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT a.*, v.title, v.company FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.tg_id=? ORDER BY a.id DESC LIMIT 20", (message.from_user.id,))
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Ты пока никуда не откликался.")
        return
    status_map = {"new": "🆕 новый", "in_progress": "⏳ в работе", "accepted": "✅ принят", "rejected": "❌ отказ"}
    lines = ["<b>Мои отклики</b>"]
    for r in rows:
        lines.append(f"• {esc(r['title'])} — {esc(r['company'])} <i>({status_map.get(r['status'], r['status'])})</i>")
    await message.answer("\n".join(lines))

# ============================================================
# ПРОФОРИЕНТАЦИЯ
# ============================================================
QUIZ_QUESTIONS = [
    ("Что тебе ближе?", [("Работать руками", "worker"), ("С техникой", "tech"), ("Считать/анализировать", "engineer"), ("Хочу попробовать", "intern")]),
    ("Нравится физический труд?", [("Да", "worker"), ("В меру", "tech"), ("Нет", "engineer"), ("Готов(а)", "intern")]),
    ("Готов(а) учиться?", [("Сразу работать", "worker"), ("Короткие курсы", "intern"), ("Колледж", "tech"), ("Вуз", "engineer")]),
    ("Что важнее?", [("Стабильность", "worker"), ("Рост", "tech"), ("Сложные задачи", "engineer"), ("Первый опыт", "intern")]),
    ("Комфортнее работать...", [("В цеху", "worker"), ("На объекте", "tech"), ("В офисе", "engineer"), ("Где угодно", "intern")]),
]
DIRECTION_TEXT = {"worker": "⚙️ Рабочие профессии.", "tech": "🛠 Технические направления.", "engineer": "📈 Инженерные направления.", "intern": "🎓 Стажировки."}

@quiz_router.message(F.text == "🧭 Профориентация")
async def quiz_start(message: Message, state: FSMContext):
    await state.set_state(Quiz.q1)
    await state.update_data(score={})
    await message.answer(f"🧭 Тест (5 вопросов).\n\n<b>1. {QUIZ_QUESTIONS[0][0]}</b>", reply_markup=_quiz_kb(0))

def _quiz_kb(q_idx: int):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"quiz:{q_idx}:{i}")] for i, (label, _) in enumerate(QUIZ_QUESTIONS[q_idx][1])])

@quiz_router.callback_query(F.data.startswith("quiz:"))
async def quiz_answer(callback: CallbackQuery, state: FSMContext):
    _, q_idx_s, ans_idx_s = callback.data.split(":")
    q_idx = int(q_idx_s)
    ans_idx = int(ans_idx_s)
    if await state.get_state() != f"Quiz:q{q_idx+1}":
        await callback.answer("Вопрос неактуален", show_alert=True)
        return
    data = await state.get_data()
    score = data.get("score", {})
    direction = QUIZ_QUESTIONS[q_idx][1][ans_idx][1]
    score[direction] = score.get(direction, 0) + 1
    await state.update_data(score=score)

    if q_idx + 1 < len(QUIZ_QUESTIONS):
        next_idx = q_idx + 1
        await state.set_state(getattr(Quiz, f"q{next_idx+1}"))
        await callback.message.edit_text(f"<b>{next_idx+1}. {QUIZ_QUESTIONS[next_idx][0]}</b>", reply_markup=_quiz_kb(next_idx))
    else:
        await state.clear()
        winner = max(score.items(), key=lambda kv: kv[1])[0]
        await callback.message.edit_text(f"🎯 Твоё направление: <b>{esc(winner)}</b>\n\n{esc(DIRECTION_TEXT[winner])}")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO users(tg_id, direction, updated_at) VALUES(?,?,?) ON CONFLICT(tg_id) DO UPDATE SET direction=excluded.direction, updated_at=excluded.updated_at", (callback.from_user.id, winner, datetime.now().isoformat(timespec="minutes")))
            await db.commit()
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM vacancies WHERE active=1 AND category=? ORDER BY id DESC LIMIT 3", (winner,))
            rows = await cur.fetchall()
        if rows:
            await callback.message.answer("Вот что можно посмотреть прямо сейчас:")
            for row in rows:
                await callback.message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], False))
        else:
            await callback.message.answer("Пока нет активных вакансий в этом направлении.")
    await callback.answer()

# ============================================================
# ПРОФИЛЬ И ОБРАТНАЯ СВЯЗЬ
# ============================================================
@profile_router.message(F.text == "👤 Моя анкета")
async def my_profile(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (message.from_user.id,))
        row = await cur.fetchone()
    if not row:
        await message.answer("Анкеты пока нет.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Заполнить анкету", callback_data="profile")]]))
        return
    await message.answer(f"<b>Твоя анкета</b>\nИмя: {esc(row['name'])}\nВозраст: {esc(row['age'])}\nКонтакт: {esc(row['contact']) or 'не указан'}\nИнтересы: {esc(row['goal'])}\nНаправление: {esc(row['direction']) or '—'}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Обновить", callback_data="profile")]]))

@profile_router.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await profile_begin(callback.message, state)

@profile_router.message(F.text == "Заполнить анкету")
async def profile_begin(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Form.name)
    await message.answer("Как тебя зовут?", reply_markup=CANCEL_KB)

@profile_router.message(Form.name, F.text)
async def profile_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text[:100])
    await state.set_state(Form.age)
    await message.answer("Сколько тебе лет?", reply_markup=CANCEL_KB)

@profile_router.message(Form.age, F.text)
async def profile_age(message: Message, state: FSMContext):
    await state.update_data(age=message.text[:20])
    await state.set_state(Form.contact)
    await message.answer("Контакт: @username или телефон. Можно «-».", reply_markup=CANCEL_KB)

@profile_router.message(Form.contact, F.text)
async def profile_contact(message: Message, state: FSMContext):
    val = "" if message.text.strip().lower() in ("-", "нет") else message.text[:100]
    await state.update_data(contact=val)
    await state.set_state(Form.goal)
    await message.answer("Что тебе интересно?", reply_markup=CANCEL_KB)

@profile_router.message(Form.goal, F.text)
async def profile_goal(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    goal = message.text[:500]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(tg_id, username, name, age, contact, goal, updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, name=excluded.name, age=excluded.age, contact=excluded.contact, goal=excluded.goal, updated_at=excluded.updated_at", (message.from_user.id, message.from_user.username, data.get("name", ""), data.get("age", ""), data.get("contact", ""), goal, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await state.clear()
    await message.answer("Анкета сохранена ✅", reply_markup=MENU)

@profile_router.message(F.text == "✉️ Обратная связь")
async def feedback_start(message: Message, state: FSMContext):
    await state.set_state(Form.feedback)
    await message.answer("Напиши сообщение для команды проекта.", reply_markup=CANCEL_KB)

@profile_router.message(Form.feedback, F.text)
async def feedback_save(message: Message, state: FSMContext, bot: Bot):
    text = message.text[:1500]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO feedback(tg_id, message, created_at) VALUES(?,?,?)", (message.from_user.id, text, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await state.clear()
    await message.answer("Спасибо! Передано команде ✅", reply_markup=MENU)
    await send_admins(bot, f"✉️ Обратная связь от {message.from_user.id}:\n{esc(text)}")

# ============================================================
# АДМИНКА
# ============================================================
@admin_router.message(Command("addvacancy"))
async def add_vacancy(message: Message):
    if not await is_admin(message.from_user.id): return
    parts = message.text.partition(" ")[2].split("|")
    if len(parts) < 6: return await message.answer("Формат:\n/addvacancy Должность | Работодатель | Зарплата | Описание | Контакт | Категория(worker/tech/engineer/intern)")
    vals = [p.strip() for p in parts[:6]]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO vacancies(title,company,salary,description,contact,category,created_at) VALUES(?,?,?,?,?,?,?)", (*vals, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await message.answer("Вакансия опубликована.")

@admin_router.message(Command("addevent"))
async def add_event(message: Message):
    if not await is_admin(message.from_user.id): return
    parts = message.text.partition(" ")[2].split("|")
    if len(parts) < 4: return await message.answer("Формат:\n/addevent Название | Дата | Место | Описание")
    vals = [p.strip() for p in parts[:4]]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO events(title,date_text,place,description,created_at) VALUES(?,?,?,?,?)", (*vals, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await message.answer("Мероприятие опубликовано.")

@admin_router.message(Command("applications"))
async def admin_apps(message: Message):
    if not await is_admin(message.from_user.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT a.*, v.title FROM applications a JOIN vacancies v ON v.id=a.vacancy_id ORDER BY a.id DESC LIMIT 15")
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Откликов нет.")
        return
    for r in rows:
        st = {"new": "🆕", "in_progress": "⏳", "accepted": "✅", "rejected": "❌"}.get(r["status"], "")
        await message.answer(f"{st} <b>{esc(r['title'])}</b>\nОт: {r['tg_id']}\n💬 {esc(r['message']) or '<i>без сообщения</i>'}\nСтатус: {r['status']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="В работу", callback_data=f"appst:{r['id']}:in_progress"), InlineKeyboardButton(text="Принять", callback_data=f"appst:{r['id']}:accepted")],
                [InlineKeyboardButton(text="Отказ", callback_data=f"appst:{r['id']}:rejected")]
            ]))

@admin_router.callback_query(F.data.startswith("appst:"))
async def admin_app_status(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return
    _, app_id, status = callback.data.split(":")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE applications SET status=? WHERE id=?", (status, app_id))
        await db.commit()
    await callback.answer("Статус обновлён")
    await callback.message.edit_reply_markup(reply_markup=None)

@admin_router.message(Command("stats"))
async def stats(message: Message):
    if not await is_admin(message.from_user.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        u = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        v = (await (await db.execute("SELECT COUNT(*) FROM vacancies WHERE active=1")).fetchone())[0]
        a = (await (await db.execute("SELECT COUNT(*) FROM applications")).fetchone())[0]
    await message.answer(f"📊 Анкеты: {u}\nВакансии: {v}\nОтклики: {a}")

@admin_router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id): return
    await state.set_state(Form.broadcast)
    await message.answer("Отправь текст для рассылки всем пользователям:", reply_markup=CANCEL_KB)

@admin_router.message(Form.broadcast, F.text)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(message.from_user.id): return
    text = message.text
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tg_id FROM users")
        users = await cur.fetchall()
    ok, fail = 0, 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"Рассылка завершена. Достав