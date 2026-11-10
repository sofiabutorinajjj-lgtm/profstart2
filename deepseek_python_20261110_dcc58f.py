import os, sys, asyncio, html, logging
from datetime import datetime
from pathlib import Path

try:
    import aiosqlite
    from aiogram import Bot, Dispatcher, F, Router
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "aiogram>=3.13,<4", "aiosqlite>=0.20,<1"])

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CITY = os.getenv("CITY", "Ленинск-Кузнецкий")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = Path(os.getenv("DB_PATH", "data/profstart.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("profstart")
PAGE_SIZE = 5

MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="💼 Вакансии"), KeyboardButton(text="🔎 Поиск")],
    [KeyboardButton(text="🧭 Профориентация"), KeyboardButton(text="⭐ Избранное")],
    [KeyboardButton(text="📬 Мои отклики"), KeyboardButton(text="🎓 Обучение")],
    [KeyboardButton(text="📅 Мероприятия"), KeyboardButton(text="👤 Моя анкета")],
    [KeyboardButton(text="✉️ Обратная связь"), KeyboardButton(text="ℹ️ Помощь")]
], resize_keyboard=True)
CANCEL_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

class Form(StatesGroup):
    name = State(); age = State(); contact = State(); goal = State()
    feedback = State(); search = State(); apply_message = State(); broadcast = State()

class Quiz(StatesGroup):
    q1 = State(); q2 = State(); q3 = State(); q4 = State(); q5 = State()

def esc(value) -> str:
    return html.escape(str(value or ""))

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users(tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT, age TEXT, contact TEXT, goal TEXT, direction TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS vacancies(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, company TEXT, salary TEXT, description TEXT, contact TEXT, category TEXT, active INTEGER DEFAULT 1, created_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, date_text TEXT, place TEXT, description TEXT, active INTEGER DEFAULT 1, created_at TEXT);
        CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT, vacancy_id INTEGER, tg_id INTEGER, message TEXT, status TEXT DEFAULT 'new', created_at TEXT, UNIQUE(vacancy_id, tg_id));
        CREATE TABLE IF NOT EXISTS favorites(tg_id INTEGER, vacancy_id INTEGER, PRIMARY KEY(tg_id, vacancy_id));
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, message TEXT, created_at TEXT);
        """)
        await db.commit()

async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def send_admins(bot: Bot, text, reply_markup=None):
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception: pass

def paginate_kb(prefix: str, page: int, total_pages: int):
    nav = []
    if page > 1: nav.append(InlineKeyboardButton(text="◀", callback_data=f"{prefix}:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton(text="▶", callback_data=f"{prefix}:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])

def vacancy_card(row) -> str:
    cat = {"worker": "⚙️", "tech": "🛠", "engineer": "📈", "intern": "🎓", "other": "•"}.get(row["category"] or "other", "•")
    return f"{cat} <b>{esc(row['title'])}</b>\n🏢 {esc(row['company'])}\n💰 {esc(row['salary']) or 'уточняется'}\n{esc(row['description'])}\n📞 {esc(row['contact']) or 'уточняется'}"

def vacancy_kb(vac_id: int, in_fav: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Откликнуться", callback_data=f"apply:{vac_id}")],
        [InlineKeyboardButton(text="💔 Убрать" if in_fav else "⭐ В избранное", callback_data=f"fav:{vac_id}")]
    ])

common_router, vac_router, profile_router, quiz_router, admin_router = Router(), Router(), Router(), Router(), Router()

@common_router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Привет! Я — <b>ПрофСтарт</b> 🏭\nПомогаю молодёжи {esc(CITY)} найти работу и стартовать в карьере.", reply_markup=MENU)

@common_router.message(Command("help"))
@common_router.message(F.text == "ℹ️ Помощь")
async def help_message(message: Message):
    await message.answer("<b>Помощь</b>\n💼 Вакансии, 🔎 Поиск, ⭐ Избранное, 📬 Отклики, 🧭 Тест, 👤 Анкета.\n\nАдмин: /addvacancy /addevent /applications /stats /broadcast")

@common_router.message(Command("cancel"))
@common_router.message(F.text == "❌ Отмена")
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=MENU)

@common_router.message(F.text == "🎓 Обучение")
async def learning(message: Message):
    await message.answer("🎓 <b>Обучение</b>\nСмотри вакансии в категории «Стажировки» или пиши в обратную связь.")

@common_router.message(F.text == "📅 Мероприятия")
async def show_events(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM events WHERE active=1 ORDER BY id DESC LIMIT 10")).fetchall()
    if not rows: return await message.answer("Пока нет мероприятий.")
    for row in rows:
        await message.answer(f"📅 <b>{esc(row['title'])}</b>\nКогда: {esc(row['date_text'])}\nГде: {esc(row['place'])}\n{esc(row['description'])}")

@vac_router.message(F.text == "💼 Вакансии")
async def vacancies_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все", callback_data="vlist:all:1")],
        [InlineKeyboardButton(text="⚙️ Рабочие", callback_data="vlist:worker:1"), InlineKeyboardButton(text="🛠 Технические", callback_data="vlist:tech:1")],
        [InlineKeyboardButton(text="📈 Инженерные", callback_data="vlist:engineer:1"), InlineKeyboardButton(text="🎓 Стажировки", callback_data="vlist:intern:1")]
    ])
    await message.answer("Выбери категорию:", reply_markup=kb)

@vac_router.callback_query(F.data.startswith("vlist:"))
async def vacancies_list(callback: CallbackQuery):
    _, cat, page_s = callback.data.split(":")
    page = int(page_s)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where, params = ("active=1", []) if cat == "all" else ("active=1 AND category=?", [cat])
        total = (await (await db.execute(f"SELECT COUNT(*) FROM vacancies WHERE {where}", params)).fetchone())[0]
        if total == 0: return await callback.answer("Нет вакансий", show_alert=True)
        tp = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, tp)
        rows = await (await db.execute(f"SELECT * FROM vacancies WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))).fetchall()
    await callback.message.edit_text(f"Найдено: <b>{total}</b> (стр. {page}/{tp})", reply_markup=paginate_kb(f"vlist:{cat}", page, tp))
    for row in rows:
        async with aiosqlite.connect(DB_PATH) as db:
            in_fav = bool(await (await db.execute("SELECT 1 FROM favorites WHERE tg_id=? AND vacancy_id=?", (callback.from_user.id, row["id"]))).fetchone())
        await callback.message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], in_fav))
    await callback.answer()

@vac_router.message(F.text == "🔎 Поиск")
async def search_start(message: Message, state: FSMContext):
    await state.set_state(Form.search)
    await message.answer("Введи слово для поиска:", reply_markup=CANCEL_KB)

@vac_router.message(Form.search, F.text)
async def search_run(message: Message, state: FSMContext):
    q = message.text.strip()
    await state.clear()
    like = f"%{q}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM vacancies WHERE active=1 AND (title LIKE ? OR description LIKE ?) ORDER BY id DESC LIMIT 10", (like, like))).fetchall()
    if not rows: return await message.answer("Ничего не найдено.", reply_markup=MENU)
    await message.answer(f"Найдено: <b>{len(rows)}</b>", reply_markup=MENU)
    for row in rows: await message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], False))

@vac_router.callback_query(F.data.startswith("fav:"))
async def fav_toggle(callback: CallbackQuery):
    vac_id = int(callback.data.split(":")[1])
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        exists = await (await db.execute("SELECT 1 FROM favorites WHERE tg_id=? AND vacancy_id=?", (uid, vac_id))).fetchone()
        if exists: await db.execute("DELETE FROM favorites WHERE tg_id=? AND vacancy_id=?", (uid, vac_id))
        else: await db.execute("INSERT INTO favorites(tg_id, vacancy_id) VALUES(?,?)", (uid, vac_id))
        await db.commit()
    await callback.answer("Убрано" if exists else "Добавлено")
    await callback.message.edit_reply_markup(reply_markup=vacancy_kb(vac_id, not exists))

@vac_router.message(F.text == "⭐ Избранное")
async def favorites_list(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT v.* FROM favorites f JOIN vacancies v ON v.id=f.vacancy_id WHERE f.tg_id=? AND v.active=1", (message.from_user.id,))).fetchall()
    if not rows: return await message.answer("В избранном пусто.")
    for row in rows: await message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], True))

@vac_router.callback_query(F.data.startswith("apply:"))
async def apply_start(callback: CallbackQuery, state: FSMContext):
    vac_id = int(callback.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        if await (await db.execute("SELECT 1 FROM applications WHERE vacancy_id=? AND tg_id=?", (vac_id, callback.from_user.id))).fetchone():
            return await callback.answer("Ты уже откликался", show_alert=True)
    await state.update_data(apply_vac_id=vac_id)
    await state.set_state(Form.apply_message)
    await callback.answer()
    await callback.message.answer("Напиши пару слов о себе (или отправь «-»):", reply_markup=CANCEL_KB)

@vac_router.message(Form.apply_message, F.text)
async def apply_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    text = "" if message.text.strip() in ("-", "нет") else message.text[:1000]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT title FROM vacancies WHERE id=?", (data.get("apply_vac_id"),))).fetchone()
        try:
            await db.execute("INSERT INTO applications(vacancy_id, tg_id, message, status, created_at) VALUES(?,?,?,?,?)", (data.get("apply_vac_id"), message.from_user.id, text, "new", datetime.now().isoformat(timespec="minutes")))
            await db.commit()
        except aiosqlite.IntegrityError: return await message.answer("Уже откликался.", reply_markup=MENU)
    await state.clear()
    await message.answer("Отклик отправлен ✅", reply_markup=MENU)
    await send_admins(bot, f"📨 <b>Отклик</b> на «{esc(row['title'])}»\n👤 @{esc(message.from_user.username)}\n💬 {esc(text)}\n/applications")

@vac_router.message(F.text == "📬 Мои отклики")
async def my_applications(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT a.*, v.title FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.tg_id=? ORDER BY a.id DESC", (message.from_user.id,))).fetchall()
    if not rows: return await message.answer("Откликов нет.")
    await message.answer("\n".join([f"• {esc(r['title'])} ({r['status']})" for r in rows]))

QUIZ_QUESTIONS = [
    ("Что ближе?", [("Руками", "worker"), ("Техника", "tech"), ("Анализ", "engineer"), ("Пробовать", "intern")]),
    ("Физический труд?", [("Да", "worker"), ("В меру", "tech"), ("Нет", "engineer"), ("Готов", "intern")]),
    ("Учёба?", [("Сразу работать", "worker"), ("Курсы", "intern"), ("Колледж", "tech"), ("Вуз", "engineer")]),
    ("Что важнее?", [("Стабильность", "worker"), ("Рост", "tech"), ("Сложность", "engineer"), ("Опыт", "intern")]),
    ("Где комфортнее?", [("Цех", "worker"), ("Объект", "tech"), ("Офис", "engineer"), ("Везде", "intern")]),
]
DIRECTION_TEXT = {"worker": "⚙️ Рабочие профессии.", "tech": "🛠 Технические направления.", "engineer": "📈 Инженерные.", "intern": "🎓 Стажировки."}

@quiz_router.message(F.text == "🧭 Профориентация")
async def quiz_start(message: Message, state: FSMContext):
    await state.set_state(Quiz.q1)
    await state.update_data(score={})
    await message.answer(f"🧭 Тест (1/5). <b>{QUIZ_QUESTIONS[0][0]}</b>", reply_markup=_quiz_kb(0))

def _quiz_kb(q_idx: int):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=l, callback_data=f"quiz:{q_idx}:{i}")] for i, (l, _) in enumerate(QUIZ_QUESTIONS[q_idx][1])])

@quiz_router.callback_query(F.data.startswith("quiz:"))
async def quiz_answer(callback: CallbackQuery, state: FSMContext):
    _, q_idx_s, ans_idx_s = callback.data.split(":")
    q_idx, ans_idx = int(q_idx_s), int(ans_idx_s)
    if await state.get_state() != f"Quiz:q{q_idx+1}": return await callback.answer("Неактуально", show_alert=True)
    score = (await state.get_data()).get("score", {})
    direction = QUIZ_QUESTIONS[q_idx][1][ans_idx][1]
    score[direction] = score.get(direction, 0) + 1
    await state.update_data(score=score)
    if q_idx + 1 < len(QUIZ_QUESTIONS):
        await state.set_state(getattr(Quiz, f"q{q_idx+2}"))
        await callback.message.edit_text(f"<b>{q_idx+2}/5. {QUIZ_QUESTIONS[q_idx+1][0]}</b>", reply_markup=_quiz_kb(q_idx+1))
    else:
        await state.clear()
        winner = max(score.items(), key=lambda kv: kv[1])[0]
        await callback.message.edit_text(f"🎯 Твоё направление: <b>{esc(winner)}</b>\n\n{esc(DIRECTION_TEXT[winner])}")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO users(tg_id, direction, updated_at) VALUES(?,?,?) ON CONFLICT(tg_id) DO UPDATE SET direction=excluded.direction", (callback.from_user.id, winner, datetime.now().isoformat(timespec="minutes")))
            await db.commit()
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM vacancies WHERE active=1 AND category=? LIMIT 3", (winner,))).fetchall()
        for row in rows: await callback.message.answer(vacancy_card(row), reply_markup=vacancy_kb(row["id"], False))
    await callback.answer()

@profile_router.message(F.text == "👤 Моя анкета")
async def my_profile(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM users WHERE tg_id=?", (message.from_user.id,))).fetchone()
    if not row: return await message.answer("Анкеты нет.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Заполнить", callback_data="profile")]]))
    await message.answer(f"<b>Анкета</b>\nИмя: {esc(row['name'])}\nВозраст: {esc(row['age'])}\nКонтакты: {esc(row['contact'])}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Обновить", callback_data="profile")]]))

@profile_router.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await profile_begin(callback.message, state)

@profile_router.message(F.text == "Заполнить")
async def profile_begin(message: Message, state: FSMContext):
    await state.clear(); await state.set_state(Form.name)
    await message.answer("Как тебя зовут?", reply_markup=CANCEL_KB)

@profile_router.message(Form.name, F.text)
async def profile_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text[:100]); await state.set_state(Form.age)
    await message.answer("Сколько тебе лет?", reply_markup=CANCEL_KB)

@profile_router.message(Form.age, F.text)
async def profile_age(message: Message, state: FSMContext):
    await state.update_data(age=message.text[:20]); await state.set_state(Form.contact)
    await message.answer("Контакт (телефон или @ник):", reply_markup=CANCEL_KB)

@profile_router.message(Form.contact, F.text)
async def profile_contact(message: Message, state: FSMContext):
    await state.update_data(contact=message.text[:100]); await state.set_state(Form.goal)
    await message.answer("Что тебе интересно?", reply_markup=CANCEL_KB)

@profile_router.message(Form.goal, F.text)
async def profile_goal(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(tg_id, username, name, age, contact, goal, updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name, age=excluded.age, contact=excluded.contact, goal=excluded.goal", (message.from_user.id, message.from_user.username, data.get("name"), data.get("age"), data.get("contact"), message.text[:500], datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await state.clear()
    await message.answer("Анкета сохранена ✅", reply_markup=MENU)

@profile_router.message(F.text == "✉️ Обратная связь")
async def feedback_start(message: Message, state: FSMContext):
    await state.set_state(Form.feedback)
    await message.answer("Напиши сообщение:", reply_markup=CANCEL_KB)

@profile_router.message(Form.feedback, F.text)
async def feedback_save(message: Message, state: FSMContext, bot: Bot):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO feedback(tg_id, message, created_at) VALUES(?,?,?)", (message.from_user.id, message.text[:1500], datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await state.clear()
    await message.answer("Спасибо ✅", reply_markup=MENU)
    await send_admins(bot, f"✉️ От {message.from_user.id}:\n{esc(message.text)}")

@admin_router.message(Command("addvacancy"))
async def add_vacancy(message: Message):
    if not await is_admin(message.from_user.id): return
    parts = message.text.partition(" ")[2].split("|")
    if len(parts) < 5: return await message.answer("Формат:\n/addvacancy Должность | Работодатель | Зарплата | Описание | Контакт")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO vacancies(title,company,salary,description,contact,category,created_at) VALUES(?,?,?,?,?,'other',?)", (*[p.strip() for p in parts[:5]], datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await message.answer("Добавлено.")

@admin_router.message(Command("addevent"))
async def add_event(message: Message):
    if not await is_admin(message.from_user.id): return
    parts = message.text.partition(" ")[2].split("|")
    if len(parts) < 4: return await message.answer("Формат:\n/addevent Название | Дата | Место | Описание")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO events(title,date_text,place,description,created_at) VALUES(?,?,?,?,?)", (*[p.strip() for p in parts[:4]], datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await message.answer("Добавлено.")

@admin_router.message(Command("applications"))
async def admin_apps(message: Message):
    if not await is_admin(message.from_user.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT a.*, v.title FROM applications a JOIN vacancies v ON v.id=a.vacancy_id ORDER BY a.id DESC LIMIT 10")).fetchall()
    if not rows: return await message.answer("Нет откликов.")
    for r in rows:
        await message.answer(f"<b>{esc(r['title'])}</b>\nID: {r['tg_id']}\n💬 {esc(r['message'])}\nСтатус: {r['status']}")

@admin_router.message(Command("stats"))
async def stats(message: Message):
    if not await is_admin(message.from_user.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        u = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        v = (await (await db.execute("SELECT COUNT(*) FROM vacancies")).fetchone())[0]
    await message.answer(f"📊 Анкеты: {u}\nВакансии: {v}")

@admin_router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id): return
    await state.set_state(Form.broadcast)
    await message.answer("Отправь текст для рассылки:", reply_markup=CANCEL_KB)

@admin_router.message(Form.broadcast, F.text)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(message.from_user.id): return
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        users = await (await db.execute("SELECT tg_id FROM users")).fetchall()
    ok = 0
    for (uid,) in users:
        try: await bot.send_message(uid, message.text); ok += 1
        except: pass
    await message.answer(f"Отправлено: {ok}", reply_markup=MENU)

async def main():
    if not BOT_TOKEN: raise RuntimeError("Нет BOT_TOKEN")
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(common_router, vac_router, profile_router, quiz_router, admin_router)
    log.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())