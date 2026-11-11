import asyncio
import html
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ---------- настройки (переменные окружения) ----------
TOKEN = os.environ["BOT_TOKEN"]
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))  # id чата группы, 0 = без ограничений
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Yekaterinburg"))  # Челябинск
MORNING_TIME = os.environ.get("MORNING_TIME", "07:30")
HW_URL = os.environ.get("HW_URL", "https://api.npoint.io/ac41837c39a8785f2ce4")
DATA_DIR = os.environ.get("DATA_DIR", ".")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

MOODLE = "https://moodle.uio.csu.ru/course/view.php?id="
ANCHOR = date(2026, 9, 14)  # понедельник нечётной недели

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "info.json"), encoding="utf-8") as f:
    DATA = json.load(f)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]

# ---------- дни рождения ----------
BIRTHDAYS = [  # (день, месяц, имя)
    (1, 1, "Алина Шакирова"),
    (24, 1, "Алёна Боровикова"),
    (7, 3, "Влад и ещё один именинник"),  # поправь имя
    (12, 5, "Алина Карелина"),
    (11, 7, "Арина Анохина"),
    (4, 8, "Серёжа"),
    (13, 8, "Софа"),
    (9, 9, "Алёна Истомина"),
    (10, 9, "Сабина"),
    (12, 11, "Влада"),
    (5, 12, "Саша"),
    (18, 12, "Арина Арахланова"),
]
BD_AMOUNT = 200
# реквизиты лучше держать в переменной Railway, а не в публичном репозитории
PAY_INFO = os.environ.get("PAY_INFO", "Алёна Боровикова, Райффайзен")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
dp = Dispatcher()


# ---------- доступ только для своей группы ----------
class Access(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not GROUP_ID:
            return await handler(event, data)
        msg = event if isinstance(event, Message) else event.message
        if msg is None:
            return None
        if msg.chat.id == GROUP_ID:
            return await handler(event, data)
        if msg.chat.type == "private":
            try:
                member = await bot.get_chat_member(GROUP_ID, event.from_user.id)
                if member.status in ("member", "administrator", "creator"):
                    return await handler(event, data)
            except Exception:
                pass
        return None


dp.message.outer_middleware(Access())
dp.callback_query.outer_middleware(Access())


# ---------- расписание ----------
def week_type(d: date) -> str:
    monday = d - timedelta(days=d.weekday())
    weeks = (monday - ANCHOR).days // 7
    return "odd" if weeks % 2 == 0 else "even"


def pairs_on(d: date) -> list:
    if d.isoweekday() > 6:
        return []
    return DATA["schedule"][week_type(d)].get(str(d.isoweekday()), [])


def head(d: date, icon: str, label: str) -> str:
    wt = "Нечётная" if week_type(d) == "odd" else "Чётная"
    return f"{icon} <b>{label}</b> · {WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}\n{wt} неделя"


def room_line(room: str, lec) -> str:
    parts = []
    if room:
        parts.append(("💻 " if room.lower().startswith("онлайн") else "🚪 ") + html.escape(room))
    if lec:
        parts.append("📚 лекция")
    return " · ".join(parts)


def day_text(d: date, label: str) -> str:
    pairs = pairs_on(d)
    text = head(d, "📅", label)
    if not pairs:
        return text + "\n\nПар нет 🎉"
    lines = []
    for num, title, room, *lec in pairs:
        s, e = DATA["calls"][str(num)]
        extra = room_line(room, lec)
        lines.append(f"{num}) {s}–{e} · {html.escape(title)}" + ("\n" + extra if extra else ""))
    return text + "\n\n" + "\n\n".join(lines)


@dp.message(Command("start", "help"))
async def cmd_start(m: Message):
    await m.answer(
        "Привет! 👋 Я бот группы ФЛ1-ПОДб-11\n\n"
        "/today — пары сегодня\n"
        "/tomorrow — пары завтра\n"
        "/calls — звонки\n"
        "/subject — предметы и преподаватели\n"
        "/hw — ДЗ (например /hw 15.11 на дату)\n"
        "/addhw — добавить ДЗ\n"
        "/bd — дни рождения\n"
        "/bday on|off — напоминания о днях рождения\n"
        "/notify on|off — уведомления о начале и конце пар\n"
        "/morning on|off — утренняя рассылка с парами"
    )


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"id этого чата: <code>{m.chat.id}</code>")


@dp.message(Command("today"))
async def cmd_today(m: Message):
    await m.answer(day_text(datetime.now(TZ).date(), "Сегодня"))


@dp.message(Command("tomorrow"))
async def cmd_tomorrow(m: Message):
    await m.answer(day_text(datetime.now(TZ).date() + timedelta(days=1), "Завтра"))


@dp.message(Command("calls"))
async def cmd_calls(m: Message):
    rows = [f"{n}) {s}–{e}" for n, (s, e) in DATA["calls"].items()]
    await m.answer("⏰ <b>Расписание звонков</b>\n\n" + "\n".join(rows)
                   + "\n\n🔔 Уведомления о парах: /notify on")


# ---------- предметы ----------
def subj_kb():
    b = InlineKeyboardBuilder()
    for i, s in enumerate(DATA["subjects"]):
        b.button(text=f"{s['icon']} {s['short']}", callback_data=f"s:{i}")
    b.adjust(2)
    return b.as_markup()


def subject_text(s: dict) -> str:
    out = [f"{s['icon']} <b>{html.escape(s['name'])}</b>"]
    if s.get("teacher"):
        out.append(f"👤 {html.escape(s['teacher'])}")
    if s.get("mail"):
        out.append(f"✉️ {html.escape(s['mail'])}")
    links = []
    if s.get("moodle"):
        links.append(f'<a href="{MOODLE}{s["moodle"]}">🎓 Moodle</a>')
    for label, url in s.get("links", []):
        links.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
    if links:
        out.append("\n" + " · ".join(links))
    if s.get("info"):
        out.append("\n" + "\n".join("✎ " + html.escape(t) for t in s["info"]))
    return "\n".join(out)


@dp.message(Command("subject"))
async def cmd_subject(m: Message):
    await m.answer("Выбери предмет 👇", reply_markup=subj_kb())


@dp.callback_query(F.data.startswith("s:"))
async def cb_subject(c: CallbackQuery):
    try:
        s = DATA["subjects"][int(c.data[2:])]
        await c.message.edit_text(subject_text(s), reply_markup=subj_kb())
    except (IndexError, ValueError, TelegramBadRequest):
        pass
    await c.answer()


# ---------- домашние задания ----------
async def fetch_hw() -> dict:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(HW_URL) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
    if not isinstance(data, dict):
        raise ValueError("неожиданный формат ДЗ")
    return data


def parse_date(arg: str, today: date):
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            d = datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
        return d.replace(year=today.year) if fmt == "%d.%m" else d
    return None


@dp.message(Command("hw"))
async def cmd_hw(m: Message, command: CommandObject):
    today = datetime.now(TZ).date()
    arg = (command.args or "").strip()
    if arg:
        d = parse_date(arg, today)
        if d is None:
            await m.answer("Дата в формате ДД.ММ, например: /hw 15.11")
            return
    else:  # ближайший учебный день после сегодня
        d = next((today + timedelta(days=i) for i in range(1, 9) if pairs_on(today + timedelta(days=i))),
                 today + timedelta(days=1))
    pairs = pairs_on(d)
    if not pairs:
        await m.answer(head(d, "📝", "ДЗ на") + "\n\nПар нет 🎉")
        return
    try:
        hw = await get_hw()
    except Exception as e:
        print("hw error:", e)
        await m.answer("Не получилось загрузить ДЗ 😭 Попробуй чуть позже")
        return
    titles = []
    for _, title, *_ in pairs:
        if title not in titles:
            titles.append(title)
    lines = []
    for title in titles:
        text = hw.get(f"{d.isoformat()}_{title}")
        lines.append(f"<b>{html.escape(title)}</b>\n{html.escape(str(text)) if text else '— нет задания'}")
    await m.answer(head(d, "📝", "ДЗ на") + "\n\n" + "\n\n".join(lines))


# ---------- добавление ДЗ (может любой) ----------
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
LOCAL_HW_FILE = os.path.join(DATA_DIR, "hw_local.json")


class AddHW(StatesGroup):
    text = State()


def load_local_hw() -> dict:
    try:
        with open(LOCAL_HW_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_local_hw(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOCAL_HW_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


async def get_hw() -> dict:
    """Общая база ДЗ + то, что бот сохранил у себя, если база была недоступна."""
    local = load_local_hw()
    try:
        hw = await fetch_hw()
    except Exception as e:
        print("hw fetch error:", e)
        if not local:
            raise
        hw = {}
    for k, v in local.items():
        hw[k] = f"{hw[k]}\n{v}" if hw.get(k) else v
    return hw


async def save_hw(key: str, text: str) -> bool:
    """True — сохранено в общую базу, False — только в самом боте."""
    try:
        data = await fetch_hw()
        old = data.get(key)
        data[key] = f"{old}\n{text}" if old else text
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(HW_URL, json=data) as r:
                r.raise_for_status()
        return True
    except Exception as e:
        print("hw save error:", e)
        local = load_local_hw()
        old = local.get(key)
        local[key] = f"{old}\n{text}" if old else text
        save_local_hw(local)
        return False


def study_days(n: int = 6) -> list:
    today = datetime.now(TZ).date()
    days = []
    for i in range(21):
        d = today + timedelta(days=i)
        if pairs_on(d):
            days.append(d)
        if len(days) == n:
            break
    return days


def unique_titles(d: date) -> list:
    titles = []
    for _, title, *_ in pairs_on(d):
        if title not in titles:
            titles.append(title)
    return titles


@dp.message(Command("addhw"))
async def cmd_addhw(m: Message, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder()
    for d in study_days():
        b.button(text=f"{WEEKDAYS_SHORT[d.weekday()]} {d.strftime('%d.%m')}", callback_data=f"ad:{d.isoformat()}")
    b.adjust(3)
    await m.answer("📝 К какому занятию добавляем ДЗ? Выбери дату 👇", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("ad:"))
async def cb_hw_date(c: CallbackQuery):
    try:
        d = date.fromisoformat(c.data[3:])
        b = InlineKeyboardBuilder()
        for i, t in enumerate(unique_titles(d)):
            b.button(text=t, callback_data=f"as:{d.isoformat()}:{i}")
        b.adjust(1)
        await c.message.edit_text(head(d, "📝", "ДЗ на") + "\n\nПо какому предмету? 👇", reply_markup=b.as_markup())
    except (ValueError, TelegramBadRequest):
        pass
    await c.answer()


@dp.callback_query(F.data.startswith("as:"))
async def cb_hw_subject(c: CallbackQuery, state: FSMContext):
    try:
        _, ds, idx = c.data.split(":")
        d = date.fromisoformat(ds)
        title = unique_titles(d)[int(idx)]
    except (ValueError, IndexError):
        await c.answer()
        return
    await state.set_state(AddHW.text)
    await state.update_data(key=f"{d.isoformat()}_{title}")
    await c.message.edit_text(
        f"📝 <b>{html.escape(title)}</b> · {d.day} {MONTHS[d.month - 1]}\n\n"
        "Напиши текст задания одним сообщением.\nОтмена: /cancel"
    )
    await c.answer()


@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменила ✅")


@dp.message(AddHW.text, F.text, ~F.text.startswith("/"))
async def got_hw(m: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    shared = await save_hw(data["key"], m.text.strip()[:1000])
    if shared:
        await m.answer("Готово, ДЗ добавлено ✅ Посмотреть: /hw")
    else:
        await m.answer("Общая база ДЗ сейчас недоступна, поэтому сохранила только в боте ⚠️ "
                       "В /hw оно будет видно, а на сайте нет")


# ---------- вкл/выкл уведомлений и рассылки ----------
def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)


async def toggle(m: Message, command: CommandObject, key: str, name: str):
    arg = (command.args or "").strip().lower()
    s = load_settings()
    st = s.setdefault(str(m.chat.id), {})
    if arg in ("on", "вкл"):
        st[key] = True
    elif arg in ("off", "выкл"):
        st[key] = False
    else:
        state = "ВКЛ ✅" if st.get(key) else "ВЫКЛ ❌"
        await m.answer(f"{name}: {state}\n\nВключить: /{command.command} on\nВыключить: /{command.command} off")
        return
    save_settings(s)
    await m.answer(f"{name}: {'ВКЛ ✅' if st[key] else 'ВЫКЛ ❌'}")


@dp.message(Command("notify"))
async def cmd_notify(m: Message, command: CommandObject):
    await toggle(m, command, "pairs", "🔔 Уведомления о начале и конце пар")


@dp.message(Command("morning"))
async def cmd_morning(m: Message, command: CommandObject):
    await toggle(m, command, "morning", "☀️ Утренняя рассылка")


# ---------- дни рождения: команды ----------
def upcoming_bd(today: date) -> list:
    res = []
    for day, month, name in BIRTHDAYS:
        dt = date(today.year, month, day)
        if dt < today:
            dt = date(today.year + 1, month, day)
        res.append((dt, name))
    return sorted(res)


def left_text(n: int) -> str:
    return "сегодня 🎂" if n == 0 else "завтра" if n == 1 else f"через {n} дн."


def pay_text() -> str:
    return (f"💸 Скидываемся по {BD_AMOUNT} ₽ с каждого\n"
            f"Переводим: {html.escape(PAY_INFO)}")


@dp.message(Command("bd", "birthdays"))
async def cmd_bd(m: Message):
    today = datetime.now(TZ).date()
    lines = []
    for i, (dt, name) in enumerate(upcoming_bd(today)):
        mark = " ⬅️" if i == 0 else ""
        lines.append(f"{dt.day} {MONTHS[dt.month - 1]} — {html.escape(name)}"
                     f" ({left_text((dt - today).days)}){mark}")
    await m.answer("🎂 <b>Дни рождения</b>\n\n" + "\n".join(lines)
                   + "\n\n⬅️ — ближайший\n\n" + pay_text()
                   + "\n\n🔔 Напоминания: /bday on")


@dp.message(Command("bday"))
async def cmd_bday(m: Message, command: CommandObject):
    await toggle(m, command, "bday", "🎂 Напоминания о днях рождения")


async def send_bday_reminders(d: date, chats: dict):
    """В сам день — поздравить, за день до — напомнить про сбор денег. Обходит всех, не только ближайшего."""
    for dt, name in upcoming_bd(d):
        n = (dt - d).days
        if n == 0:
            text = f"🎉 Сегодня день рождения: <b>{html.escape(name)}</b>!\nНе забудь поздравить 🥳"
        elif n == 1:
            text = (f"🎂 Завтра ({dt.day} {MONTHS[dt.month - 1]}) день рождения: "
                    f"<b>{html.escape(name)}</b>\n\n" + pay_text())
        else:
            continue
        for cid, st in chats.items():
            if st.get("bday"):
                await safe_send(cid, text)


# ---------- планировщик ----------
async def safe_send(chat_id, text: str):
    try:
        await bot.send_message(int(chat_id), text)
    except Exception as e:
        print("send error:", chat_id, e)


async def tick(now: datetime):
    d = now.date()
    hhmm = now.strftime("%H:%M")
    chats = load_settings()

    if hhmm == MORNING_TIME:
        await send_bday_reminders(d, chats)  # работает и в дни без пар

    pairs = pairs_on(d)
    if not pairs:
        return

    if hhmm == MORNING_TIME:
        text = "☀️ Доброе утро!\n\n" + day_text(d, "Сегодня")
        for cid, st in chats.items():
            if st.get("morning"):
                await safe_send(cid, text)

    for i, (num, title, room, *lec) in enumerate(pairs):
        s, e = DATA["calls"][str(num)]
        name = html.escape(title) + (" 📚" if lec else "")
        where = "\n" + room_line(room, []) if room else ""
        if hhmm == s:
            msg = f"🔔 Началась {num}-я пара: <b>{name}</b>{where}\nДо {e}"
        elif hhmm == e:
            if i + 1 < len(pairs):
                n_num, n_title, n_room, *_ = pairs[i + 1]
                n_start = DATA["calls"][str(n_num)][0]
                n_where = " · " + room_line(n_room, []) if n_room else ""
                msg = (f"✅ Закончилась {num}-я пара: {name}\n"
                       f"Дальше: {n_num}-я в {n_start} · <b>{html.escape(n_title)}</b>{n_where}")
            else:
                msg = f"✅ Закончилась {num}-я пара: {name}\nНа сегодня всё 🎉"
        else:
            continue
        for cid, st in chats.items():
            if st.get("pairs"):
                await safe_send(cid, msg)


async def scheduler():
    while True:
        now = datetime.now(TZ)
        await asyncio.sleep(60 - now.second - now.microsecond / 1e6 + 0.5)
        try:
            await tick(datetime.now(TZ))
        except Exception as e:
            print("tick error:", e)


async def main():
    await bot.set_my_commands([
        BotCommand(command="today", description="Пары сегодня"),
        BotCommand(command="tomorrow", description="Пары завтра"),
        BotCommand(command="calls", description="Расписание звонков"),
        BotCommand(command="subject", description="Предметы и преподаватели"),
        BotCommand(command="hw", description="Домашние задания"),
        BotCommand(command="addhw", description="Добавить ДЗ"),
        BotCommand(command="bd", description="Дни рождения"),
        BotCommand(command="bday", description="Напоминания о ДР вкл/выкл"),
        BotCommand(command="notify", description="Уведомления о парах вкл/выкл"),
        BotCommand(command="morning", description="Утренняя рассылка вкл/выкл"),
    ])
    task = asyncio.create_task(scheduler())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
