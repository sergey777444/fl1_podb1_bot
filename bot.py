import asyncio, json, os
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

bot = Bot(os.environ["BOT_TOKEN"])
dp = Dispatcher()

def load():
    with open("info.json", encoding="utf-8") as f:
        return json.load(f)

@dp.message(Command("start"))
async def start(m: Message):
    cmds = " ".join("/" + k for k in load())
    await m.answer(f"Привет! Команды: {cmds}")

@dp.message(Command("schedule", "teachers", "deadlines"))
async def info(m: Message):
    cmd = m.text.split()[0][1:].split("@")[0]
    await m.answer(load().get(cmd, "Пока пусто 😭"))

async def main():
    await dp.start_polling(bot)

asyncio.run(main())
