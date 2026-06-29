import asyncio
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ChatPermissions
from aiogram.utils.exceptions import BadRequest, Unauthorized

# ── Sozlamalar ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot)

# ── Anti-flood sozlamalari ───────────────────────────────────────────────────
FLOOD_LIMIT    = 5   # xabarlar soni
FLOOD_INTERVAL = 5   # soniya ichida
MUTE_DURATION  = 60  # flood uchun mute (soniya)

flood_tracker = defaultdict(list)   # {user_id: [datetime, ...]}


# ── Ma'lumotlar bazasi ───────────────────────────────────────────────────────
def db_connect():
    conn = sqlite3.connect("moderation.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id   INTEGER NOT NULL,
                chat_id   INTEGER NOT NULL,
                banned_by INTEGER,
                reason    TEXT,
                banned_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS muted_users (
                user_id  INTEGER NOT NULL,
                chat_id  INTEGER NOT NULL,
                muted_by INTEGER,
                until    TEXT,
                reason   TEXT,
                muted_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS warnings (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                chat_id  INTEGER NOT NULL,
                reason   TEXT,
                warned_at TEXT DEFAULT (datetime('now'))
            );
        """)


def db_add_ban(user_id, chat_id, banned_by, reason):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, chat_id, banned_by, reason) VALUES (?,?,?,?)",
            (user_id, chat_id, banned_by, reason),
        )


def db_remove_ban(user_id, chat_id):
    with db_connect() as conn:
        conn.execute("DELETE FROM banned_users WHERE user_id=? AND chat_id=?", (user_id, chat_id))


def db_add_mute(user_id, chat_id, muted_by, until, reason):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO muted_users (user_id, chat_id, muted_by, until, reason) VALUES (?,?,?,?,?)",
            (user_id, chat_id, muted_by, until, reason),
        )


def db_remove_mute(user_id, chat_id):
    with db_connect() as conn:
        conn.execute("DELETE FROM muted_users WHERE user_id=? AND chat_id=?", (user_id, chat_id))


# ── Yordamchi funksiyalar ────────────────────────────────────────────────────
async def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except (BadRequest, Unauthorized):
        return False


async def get_target(message: types.Message):
    if message.reply_to_message:
        return message.reply_to_message.from_user
    return None


# ── /ban ─────────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["ban"], content_types=types.ContentTypes.TEXT)
async def cmd_ban(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni ban qilishni ko'rsating (reply orqali).")

    if await is_admin(message.chat.id, target.id):
        return await message.reply("❌ Adminni ban qilib bo'lmaydi.")

    args = message.text.split(maxsplit=1)
    reason = args[1] if len(args) > 1 else "Sabab ko'rsatilmagan"

    try:
        await bot.kick_chat_member(message.chat.id, target.id)
        db_add_ban(target.id, message.chat.id, message.from_user.id, reason)
        await message.reply(
            f"🚫 <b>{target.full_name}</b> ban qilindi.\n📝 Sabab: {reason}"
        )
    except BadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


# ── /unban ────────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["unban"], content_types=types.ContentTypes.TEXT)
async def cmd_unban(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni unban qilishni ko'rsating (reply orqali).")

    try:
        await bot.unban_chat_member(message.chat.id, target.id)
        db_remove_ban(target.id, message.chat.id)
        await message.reply(f"✅ <b>{target.full_name}</b> ban ro'yxatidan chiqarildi.")
    except BadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


# ── /mute ─────────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["mute"], content_types=types.ContentTypes.TEXT)
async def cmd_mute(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni mute qilishni ko'rsating (reply orqali).")

    if await is_admin(message.chat.id, target.id):
        return await message.reply("❌ Adminni mute qilib bo'lmaydi.")

    args = message.text.split()[1:]
    minutes = 10
    reason  = "Sabab ko'rsatilmagan"

    if args:
        try:
            minutes = int(args[0])
            reason  = " ".join(args[1:]) or reason
        except ValueError:
            reason = " ".join(args)

    until_dt = datetime.now() + timedelta(minutes=minutes)

    try:
        await bot.restrict_chat_member(
            message.chat.id, target.id,
            until_date=until_dt,
            permissions=ChatPermissions(can_send_messages=False),
        )
        db_add_mute(target.id, message.chat.id, message.from_user.id, until_dt.isoformat(), reason)
        await message.reply(
            f"🔇 <b>{target.full_name}</b> {minutes} daqiqaga mute qilindi.\n📝 Sabab: {reason}"
        )
    except BadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


# ── /unmute ───────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["unmute"], content_types=types.ContentTypes.TEXT)
async def cmd_unmute(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni unmute qilishni ko'rsating (reply orqali).")

    try:
        await bot.restrict_chat_member(
            message.chat.id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        db_remove_mute(target.id, message.chat.id)
        await message.reply(f"🔊 <b>{target.full_name}</b> unmute qilindi.")
    except BadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


# ── /help ─────────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["help"])
async def cmd_help(message: types.Message):
    await message.reply(
        "🤖 <b>Moderatsiya boti buyruqlari</b>\n\n"
        "<b>Admin buyruqlari (reply orqali):</b>\n"
        "/ban [sabab] — Ban qilish\n"
        "/unban — Banni olib tashlash\n"
        "/mute [daqiqa] [sabab] — Mute (standart: 10 daq)\n"
        "/unmute — Muteni olib tashlash\n\n"
        "<b>Avtomatik:</b>\n"
        f"🚫 Anti-flood: {FLOOD_INTERVAL}s da {FLOOD_LIMIT}+ xabar → {MUTE_DURATION}s mute\n"
        "👋 Yangi a'zo kirsa welcome xabari"
    )


# ── Welcome ───────────────────────────────────────────────────────────────────
@dp.message_handler(content_types=types.ContentTypes.NEW_CHAT_MEMBERS)
async def on_new_member(message: types.Message):
    for user in message.new_chat_members:
        mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
        await message.reply(
            f"👋 Xush kelibsiz, {mention}!\n"
            f"🏠 <b>{message.chat.title}</b> guruhiga qo'shildingiz.\n"
            f"📜 Iltimos, guruh qoidalari bilan tanishing."
        )


# ── Anti-flood ────────────────────────────────────────────────────────────────
@dp.message_handler(content_types=types.ContentTypes.TEXT)
async def anti_flood(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    if not message.from_user:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    now     = datetime.now()

    if await is_admin(chat_id, user_id):
        return

    flood_tracker[user_id] = [
        t for t in flood_tracker[user_id]
        if (now - t).total_seconds() < FLOOD_INTERVAL
    ]
    flood_tracker[user_id].append(now)

    if len(flood_tracker[user_id]) >= FLOOD_LIMIT:
        until_dt = now + timedelta(seconds=MUTE_DURATION)
        try:
            await bot.restrict_chat_member(
                chat_id, user_id,
                until_date=until_dt,
                permissions=ChatPermissions(can_send_messages=False),
            )
            await message.reply(
                f"⚠️ <b>{message.from_user.full_name}</b>, flood aniqlandi!\n"
                f"🔇 {MUTE_DURATION // 60} daqiqaga mute qilindingiz."
            )
            flood_tracker[user_id].clear()
            db_add_mute(user_id, chat_id, 0, until_dt.isoformat(), "Anti-flood")
        except BadRequest:
            pass


# ── Ishga tushirish ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    logger.info("Bot ishga tushdi...")
    executor.start_polling(dp, skip_updates=True)
