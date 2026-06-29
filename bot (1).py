import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.types import ChatMemberUpdated, ChatPermissions
from aiogram.exceptions import TelegramBadRequest

# ── Sozlamalar ──────────────────────────────────────────────────────────────
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # @BotFather dan olingan token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ── Anti-flood sozlamalari ───────────────────────────────────────────────────
FLOOD_LIMIT = 5          # xabarlar soni
FLOOD_INTERVAL = 5       # soniya ichida
MUTE_DURATION = 60       # flood uchun mute (soniya)

flood_tracker: dict[int, list[datetime]] = defaultdict(list)  # {user_id: [vaqtlar]}


# ── Ma'lumotlar bazasi ───────────────────────────────────────────────────────
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect("moderation.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                banned_by  INTEGER,
                reason     TEXT,
                banned_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS muted_users (
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                muted_by   INTEGER,
                until      TEXT,
                reason     TEXT,
                muted_at   TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS warnings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                reason     TEXT,
                warned_at  TEXT DEFAULT (datetime('now'))
            );
        """)


# ── Yordamchi funksiyalar ────────────────────────────────────────────────────
async def is_admin(chat_id: int, user_id: int) -> bool:
    """Foydalanuvchi admin yoki creator ekanligini tekshiradi."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramBadRequest:
        return False


async def get_target(message: types.Message) -> types.User | None:
    """Reply qilingan xabardagi foydalanuvchini qaytaradi."""
    if message.reply_to_message:
        return message.reply_to_message.from_user
    return None


def db_add_ban(user_id: int, chat_id: int, banned_by: int, reason: str | None):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, chat_id, banned_by, reason) VALUES (?,?,?,?)",
            (user_id, chat_id, banned_by, reason),
        )


def db_remove_ban(user_id: int, chat_id: int):
    with db_connect() as conn:
        conn.execute(
            "DELETE FROM banned_users WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )


def db_add_mute(user_id: int, chat_id: int, muted_by: int, until: str, reason: str | None):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO muted_users (user_id, chat_id, muted_by, until, reason) VALUES (?,?,?,?,?)",
            (user_id, chat_id, muted_by, until, reason),
        )


def db_remove_mute(user_id: int, chat_id: int):
    with db_connect() as conn:
        conn.execute(
            "DELETE FROM muted_users WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )


# ── Buyruqlar ────────────────────────────────────────────────────────────────

@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni ban qilishni ko'rsating (reply orqali).")

    if await is_admin(message.chat.id, target.id):
        return await message.reply("❌ Adminni ban qilib bo'lmaydi.")

    reason = message.text.partition(" ")[2].strip() or "Sabab ko'rsatilmagan"

    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        db_add_ban(target.id, message.chat.id, message.from_user.id, reason)
        await message.reply(
            f"🚫 <b>{target.full_name}</b> ban qilindi.\n"
            f"📝 Sabab: {reason}",
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni unban qilishni ko'rsating (reply orqali).")

    try:
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
        db_remove_ban(target.id, message.chat.id)
        await message.reply(f"✅ <b>{target.full_name}</b> ban ro'yxatidan chiqarildi.", parse_mode="HTML")
    except TelegramBadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


@dp.message(Command("mute"))
async def cmd_mute(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni mute qilishni ko'rsating (reply orqali).")

    if await is_admin(message.chat.id, target.id):
        return await message.reply("❌ Adminni mute qilib bo'lmaydi.")

    # Buyruq formatidan daqiqa olish: /mute 30 sabab
    args = message.text.split()[1:]
    minutes = 10  # standart 10 daqiqa
    reason = "Sabab ko'rsatilmagan"

    if args:
        try:
            minutes = int(args[0])
            reason = " ".join(args[1:]) or reason
        except ValueError:
            reason = " ".join(args)

    until_dt = datetime.now() + timedelta(minutes=minutes)

    no_send = ChatPermissions(can_send_messages=False)

    try:
        await bot.restrict_chat_member(
            message.chat.id, target.id,
            permissions=no_send,
            until_date=until_dt,
        )
        db_add_mute(target.id, message.chat.id, message.from_user.id, until_dt.isoformat(), reason)
        await message.reply(
            f"🔇 <b>{target.full_name}</b> {minutes} daqiqaga mute qilindi.\n"
            f"📝 Sabab: {reason}",
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


@dp.message(Command("unmute"))
async def cmd_unmute(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Faqat adminlar uchun.")

    target = await get_target(message)
    if not target:
        return await message.reply("⚠️ Kimni unmute qilishni ko'rsating (reply orqali).")

    all_perms = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )

    try:
        await bot.restrict_chat_member(message.chat.id, target.id, permissions=all_perms)
        db_remove_mute(target.id, message.chat.id)
        await message.reply(f"🔊 <b>{target.full_name}</b> unmute qilindi.", parse_mode="HTML")
    except TelegramBadRequest as e:
        await message.reply(f"❌ Xatolik: {e}")


# ── Welcome xabari ───────────────────────────────────────────────────────────
@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_new_member(event: ChatMemberUpdated):
    user = event.new_chat_member.user
    mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
    await event.answer(
        f"👋 Xush kelibsiz, {mention}!\n"
        f"🏠 <b>{event.chat.title}</b> guruhiga qo'shildingiz.\n"
        f"📜 Iltimos, guruh qoidalari bilan tanishing.",
        parse_mode="HTML",
    )


# ── Anti-flood ───────────────────────────────────────────────────────────────
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def anti_flood(message: types.Message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    now = datetime.now()

    # Admin bo'lsa tekshirmaymiz
    if await is_admin(chat_id, user_id):
        return

    # Eski yozuvlarni tozalash
    flood_tracker[user_id] = [
        t for t in flood_tracker[user_id]
        if (now - t).total_seconds() < FLOOD_INTERVAL
    ]
    flood_tracker[user_id].append(now)

    if len(flood_tracker[user_id]) >= FLOOD_LIMIT:
        until_dt = now + timedelta(seconds=MUTE_DURATION)
        no_send = ChatPermissions(can_send_messages=False)

        try:
            await bot.restrict_chat_member(chat_id, user_id, permissions=no_send, until_date=until_dt)
            await message.reply(
                f"⚠️ <b>{message.from_user.full_name}</b>, flood aniqlandi!\n"
                f"🔇 {MUTE_DURATION // 60} daqiqaga mute qilindingiz.",
                parse_mode="HTML",
            )
            flood_tracker[user_id].clear()
            db_add_mute(user_id, chat_id, bot.id, until_dt.isoformat(), "Anti-flood")
        except TelegramBadRequest:
            pass


# ── /help buyrug'i ───────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "🤖 <b>Moderatsiya boti buyruqlari</b>\n\n"
        "<b>Admin buyruqlari (reply orqali):</b>\n"
        "/ban [sabab] — Foydalanuvchini ban qilish\n"
        "/unban — Ban olib tashlash\n"
        "/mute [daqiqa] [sabab] — Mute qilish (standart: 10 daq)\n"
        "/unmute — Mute olib tashlash\n\n"
        "<b>Avtomatik:</b>\n"
        f"🚫 Anti-flood: {FLOOD_INTERVAL} soniyada {FLOOD_LIMIT}+ xabar → {MUTE_DURATION}s mute\n"
        "👋 Yangi a'zo kirsa welcome xabari yuboriladi"
    )
    await message.reply(text, parse_mode="HTML")


# ── Ishga tushirish ──────────────────────────────────────────────────────────
async def main():
    init_db()
    logger.info("Bot ishga tushdi...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
