"""
ПрофСтарт — Telegram-бот для поиска работы, обучения и карьерного старта
Ленинск-Кузнецкий.

Запуск:
  BOT_TOKEN=... python main.py

Переменные окружения:
  BOT_TOKEN  — токен бота от @BotFather (обязательно)
  ADMIN_IDS  — Telegram ID администраторов через запятую (необязательно)
  CITY       — город (по умолчанию Ленинск-Кузнецкий)

Для Railway:
  Build: автоматически определяется как Python
  Start Command: python main.py
  Variables: BOT_TOKEN, ADMIN_IDS
"""

import os
import sys
import subprocess

# Самодостаточная установка зависимостей для варианта с одним файлом.
subprocess.check_call([
    sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
    "aiogram>=3.13,<4", "aiosqlite>=0.20,<1"
])

import asyncio
import html
import logging
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CITY = os.getenv("CITY", "Ленинск-Кузнецкий")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = Path(os.getenv("DB_PATH", "data/profstart.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("profstart")

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💼 Вакансии"), KeyboardButton(text="🧭 Профориентация")],
        [KeyboardButton(text="🎓 Обучение и стажировки"), KeyboardButton(text="🏭 О предприятиях")],
        [KeyboardButton(text="📅 Мероприятия"), KeyboardButton(text="👤 Моя анкета")],
        [KeyboardButton(text="✉️ Обратная связь"), KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True
)

class Form(StatesGroup):
    name = State()
    age = State()
    contact = State()
    goal = State()
    feedback = State()

def esc(value):
    return html.escape(str(value or ""))

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT, age TEXT,
            contact TEXT, goal TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vacancies(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            company TEXT NOT NULL, salary TEXT DEFAULT '', description TEXT DEFAULT '',
            contact TEXT DEFAULT '', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            date_text TEXT DEFAULT '', place TEXT DEFAULT '', description TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, vacancy_id INTEGER,
            tg_id INTEGER, message TEXT DEFAULT '', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER,
            message TEXT, created_at TEXT
        );
        """)
        await db.commit()

async def is_admin(user_id):
    return user_id in ADMIN_IDS

async def send_admins(bot, text):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            log.exception("Не удалось отправить сообщение администратору %s", admin_id)

async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Привет! Я — <b>ПрофСтарт</b> 🏭\n\n"
        f"Помогаю молодёжи {esc(CITY)} найти работу, узнать о производственных "
        "профессиях, обучении, стажировках и мероприятиях.\n\n"
        "Выбери раздел в меню. Чтобы подобрать возможности точнее, заполни анкету.",
        reply_markup=MENU
    )

async def show_vacancies(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM vacancies ORDER BY id DESC LIMIT 20")
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Пока вакансий нет. Загляни позже — новые предложения будут появляться здесь.")
        return
    for row in rows:
        text = (
            f"<b>{esc(row['title'])}</b>\n"
            f"Работодатель: {esc(row['company'])}\n"
            f"Зарплата: {esc(row['salary']) or 'уточняется'}\n"
            f"{esc(row['description'])}\n"
            f"Контакт: {esc(row['contact']) or 'уточняется'}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Откликнуться", callback_data=f"apply:{row['id']}")
        ]])
        await message.answer(text, reply_markup=kb)

async def show_events(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 20")
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Ближайшие мероприятия пока не опубликованы.")
        return
    for row in rows:
        await message.answer(
            f"📅 <b>{esc(row['title'])}</b>\n"
            f"Когда: {esc(row['date_text']) or 'уточняется'}\n"
            f"Где: {esc(row['place']) or 'уточняется'}\n{esc(row['description'])}"
        )

async def begin_profile(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Form.name)
    await message.answer("Как тебя зовут? (Можно указать только имя)")

async def profile_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text[:100])
    await state.set_state(Form.age)
    await message.answer("Сколько тебе лет?")

async def profile_age(message: Message, state: FSMContext):
    await state.update_data(age=message.text[:20])
    await state.set_state(Form.contact)
    await message.answer("Оставь контакт для связи: @username или телефон. Можно написать «пропустить».")

async def profile_contact(message: Message, state: FSMContext):
    contact = "" if message.text.lower().strip() in ("пропустить", "нет", "-") else message.text[:100]
    await state.update_data(contact=contact)
    await state.set_state(Form.goal)
    await message.answer("Что тебе интересно? Например: работа, обучение, стажировка, выбор профессии.")

async def profile_goal(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    goal = message.text[:500]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users(tg_id, username, name, age, contact, goal, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username,
            name=excluded.name, age=excluded.age, contact=excluded.contact,
            goal=excluded.goal, updated_at=excluded.updated_at
        """, (
            message.from_user.id, message.from_user.username,
            data.get("name", ""), data.get("age", ""), data.get("contact", ""),
            goal, datetime.now().isoformat(timespec="minutes")
        ))
        await db.commit()
    await state.clear()
    await message.answer("Анкета сохранена! Она поможет ориентироваться в возможностях проекта.", reply_markup=MENU)
    await send_admins(bot, f"📝 Новая/обновлённая анкета\nИмя: {esc(data.get('name'))}\nВозраст: {esc(data.get('age'))}\n"
                           f"Контакт: {esc(data.get('contact'))}\nИнтерес: {esc(goal)}\nTelegram ID: {message.from_user.id}")

async def my_profile(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (message.from_user.id,))
        row = await cur.fetchone()
    if not row:
        await message.answer("Анкеты пока нет. Нажми «Заполнить анкету».", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Заполнить анкету", callback_data="profile")]]))
        return
    await message.answer(
        f"<b>Твоя анкета</b>\nИмя: {esc(row['name'])}\nВозраст: {esc(row['age'])}\n"
        f"Контакт: {esc(row['contact']) or 'не указан'}\nИнтересы: {esc(row['goal'])}\n\n"
        "Чтобы изменить данные, заполни анкету заново."
    )

async def apply_callback(callback, bot: Bot):
    vacancy_id = int(callback.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title, company FROM vacancies WHERE id=?", (vacancy_id,))
        row = await cur.fetchone()
        if not row:
            await callback.answer("Вакансия уже недоступна", show_alert=True)
            return
        await db.execute(
            "INSERT INTO applications(vacancy_id,tg_id,created_at) VALUES(?,?,?)",
            (vacancy_id, callback.from_user.id, datetime.now().isoformat(timespec="minutes"))
        )
        await db.commit()
    await callback.answer("Отклик отправлен!")
    await callback.message.answer("Твой отклик принят. Администратор проекта сможет связаться с тобой.")
    await send_admins(bot, f"📨 Отклик на вакансию: {esc(row[0])} — {esc(row[1])}\n"
                           f"Пользователь: @{esc(callback.from_user.username or 'не указан')} "
                           f"(ID {callback.from_user.id})")

async def feedback_start(message: Message, state: FSMContext):
    await state.set_state(Form.feedback)
    await message.answer("Напиши сообщение для команды проекта. Оно будет передано администратору.")

async def feedback_save(message: Message, state: FSMContext, bot: Bot):
    text = message.text[:1500]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO feedback(tg_id,message,created_at) VALUES(?,?,?)",
                         (message.from_user.id, text, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await state.clear()
    await message.answer("Спасибо! Сообщение передано команде проекта.", reply_markup=MENU)
    await send_admins(bot, f"✉️ Обратная связь от {message.from_user.id}:\n{esc(text)}")

async def add_vacancy(message: Message):
    # Формат: /addvacancy Название | Работодатель | Зарплата | Описание | Контакт
    if not await is_admin(message.from_user.id):
        return await message.answer("Команда доступна только администратору.")
    parts = message.text.partition(" ")[2].split("|")
    if len(parts) < 5:
        return await message.answer("Формат:\n/addvacancy Должность | Работодатель | Зарплата | Описание | Контакт")
    vals = [p.strip() for p in parts[:5]]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO vacancies(title,company,salary,description,contact,created_at) VALUES(?,?,?,?,?,?)",
                         (*vals, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await message.answer("Вакансия опубликована.")

async def add_event(message: Message):
    # Формат: /addevent Название | Дата | Место | Описание
    if not await is_admin(message.from_user.id):
        return await message.answer("Команда доступна только администратору.")
    parts = message.text.partition(" ")[2].split("|")
    if len(parts) < 4:
        return await message.answer("Формат:\n/addevent Название | Дата | Место | Описание")
    vals = [p.strip() for p in parts[:4]]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO events(title,date_text,place,description,created_at) VALUES(?,?,?,?,?)",
                         (*vals, datetime.now().isoformat(timespec="minutes")))
        await db.commit()
    await message.answer("Мероприятие опубликовано.")

async def stats(message: Message):
    if not await is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        vacancies = (await (await db.execute("SELECT COUNT(*) FROM vacancies")).fetchone())[0]
        applications = (await (await db.execute("SELECT COUNT(*) FROM applications")).fetchone())[0]
        events = (await (await db.execute("SELECT COUNT(*) FROM events")).fetchone())[0]
    await message.answer(f"📊 Статистика\nАнкеты: {users}\nВакансии: {vacancies}\nОтклики: {applications}\nМероприятия: {events}")

async def profile_callback(callback, state: FSMContext):
    await callback.answer()
    await begin_profile(callback.message, state)

async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=MENU)

async def help_message(message: Message):
    await message.answer(
        "<b>ПрофСтарт — помощь</b>\n"
        "💼 Вакансии — предложения работодателей и отклики.\n"
        "🧭 Профориентация — выбери направление и узнай, с чего начать.\n"
        "🎓 Обучение и стажировки — информация о старте карьеры.\n"
        "🏭 О предприятиях — знакомство с производственными направлениями.\n"
        "📅 Мероприятия — экскурсии, встречи и ярмарки вакансий.\n"
        "👤 Моя анкета — твои интересы и контакты.\n\n"
        "Администратор может добавлять вакансии и мероприятия командами /addvacancy и /addevent."
    )

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Добавь токен в переменные окружения.")
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.message.register(start, CommandStart())
    dp.message.register(add_vacancy, Command("addvacancy"))
    dp.message.register(add_event, Command("addevent"))
    dp.message.register(stats, Command("stats"))
    dp.message.register(cancel_command, Command("cancel"))

    dp.callback_query.register(apply_callback, F.data.startswith("apply:"))
    dp.callback_query.register(profile_callback, F.data == "profile")

    dp.message.register(profile_name, Form.name)
    dp.message.register(profile_age, Form.age)
    dp.message.register(profile_contact, Form.contact)
    dp.message.register(profile_goal, Form.goal)
    dp.message.register(feedback_save, Form.feedback)

    dp.message.register(show_vacancies, F.text == "💼 Вакансии")
    dp.message.register(lambda m: m.answer(
        "Выбери, что ближе:\n\n"
        "⚙️ Рабочие профессии: оператор, слесарь, электромонтёр, сварщик.\n"
        "🛠 Технические направления: механик, технолог, наладчик.\n"
        "📈 Инженерные направления: инженер, мастер, специалист по качеству.\n\n"
        "Посмотри вакансии и раздел обучения или напиши команде проекта."
    ), F.text == "🧭 Профориентация")
    dp.message.register(lambda m: m.answer(
        "Ищи программы обучения при работодателях, ученические места, практику и стажировки. "
        "Актуальные предложения можно уточнить через обратную связь."
    ), F.text == "🎓 Обучение и стажировки")
    dp.message.register(lambda m: m.answer(
        "В проекте будут публиковаться карточки предприятий, экскурсии и встречи с представителями производства. "
        "Информация появится по мере добавления материалов администратора."
    ), F.text == "🏭 О предприятиях")
    dp.message.register(show_events, F.text == "📅 Мероприятия")
    dp.message.register(my_profile, F.text == "👤 Моя анкета")
    dp.message.register(begin_profile, F.text == "Заполнить анкету")
    dp.message.register(feedback_start, F.text == "✉️ Обратная связь")
    dp.message.register(help_message, F.text == "ℹ️ Помощь")

    log.info("ПрофСтарт запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
