import os
import time
import sqlite3
import logging
import requests
from flask import Flask, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DB_PATH = "moderation.db"

# Anti-flood sozlamalari
FLOOD_LIMIT = 5          # necha xabar
FLOOD_WINDOW = 10        # necha soniya ichida
FLOOD_MUTE_SECONDS = 300 # flood qilganda necha soniya mute qilinadi

# ─── DATABASE ───

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS message_log (
            chat_id INTEGER,
            user_id INTEGER,
            timestamp REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            welcome_text TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── TELEGRAM API HELPERS ───

def api(method, data=None):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data or {}, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"{method} error: {e}")
        return {}

def send(chat_id, text, reply_to=None, markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    if markup:
        data["reply_markup"] = markup
    return api("sendMessage", data)

def get_chat_member(chat_id, user_id):
    return api("getChatMember", {"chat_id": chat_id, "user_id": user_id})

def ban_chat_member(chat_id, user_id):
    return api("banChatMember", {"chat_id": chat_id, "user_id": user_id})

def unban_chat_member(chat_id, user_id):
    return api("unbanChatMember", {"chat_id": chat_id, "user_id": user_id, "only_if_banned": True})

def restrict_chat_member(chat_id, user_id, until_date=0, can_send_messages=False):
    return api("restrictChatMember", {
        "chat_id": chat_id,
        "user_id": user_id,
        "until_date": until_date,
        "permissions": {
            "can_send_messages": can_send_messages,
            "can_send_media_messages": can_send_messages,
            "can_send_other_messages": can_send_messages,
            "can_add_web_page_previews": can_send_messages
        }
    })


# ─── ADMIN TEKSHIRISH ───

def is_admin(chat_id, user_id):
    """Foydalanuvchi guruh admini yoki egasi ekanligini tekshirish"""
    result = get_chat_member(chat_id, user_id)
    if result.get("ok"):
        status = result["result"]["status"]
        return status in ["administrator", "creator"]
    return False


# ─── ANTI-FLOOD ───

def check_flood(chat_id, user_id):
    """Foydalanuvchi flood qilayotganini tekshirish. True bo'lsa flood aniqlandi."""
    now = time.time()
    conn = get_db()
    c = conn.cursor()

    # Eski yozuvlarni tozalash
    c.execute(
        "DELETE FROM message_log WHERE chat_id=? AND user_id=? AND timestamp < ?",
        (chat_id, user_id, now - FLOOD_WINDOW)
    )
    # Yangi xabarni qo'shish
    c.execute(
        "INSERT INTO message_log (chat_id, user_id, timestamp) VALUES (?, ?, ?)",
        (chat_id, user_id, now)
    )
    conn.commit()

    # Necha xabar borligini sanash
    c.execute(
        "SELECT COUNT(*) as cnt FROM message_log WHERE chat_id=? AND user_id=? AND timestamp >= ?",
        (chat_id, user_id, now - FLOOD_WINDOW)
    )
    count = c.fetchone()["cnt"]
    conn.close()

    return count > FLOOD_LIMIT


def apply_flood_mute(chat_id, user_id):
    until = int(time.time()) + FLOOD_MUTE_SECONDS
    restrict_chat_member(chat_id, user_id, until_date=until, can_send_messages=False)


# ─── WELCOME XABAR ───

def get_welcome_text(chat_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT welcome_text FROM settings WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row and row["welcome_text"]:
        return row["welcome_text"]
    return "👋 Xush kelibsiz, {name}!\n\nGuruh qoidalariga rioya qiling 🙏"

def set_welcome_text(chat_id, text):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings (chat_id, welcome_text) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET welcome_text=excluded.welcome_text",
        (chat_id, text)
    )
    conn.commit()
    conn.close()


# ─── YORDAMCHI: REPLY DAN USER OLISH ───

def get_target_user(message):
    """Reply qilingan xabardan foydalanuvchini olish"""
    reply = message.get("reply_to_message")
    if reply and "from" in reply:
        u = reply["from"]
        return u["id"], u.get("first_name", "Foydalanuvchi")
    return None, None


# ─── WEBHOOK ───

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.json
    if not update:
        return "ok"

    # ── Yangi a'zo qo'shilganda ──
    if "message" in update and "new_chat_members" in update["message"]:
        message = update["message"]
        chat_id = message["chat"]["id"]
        welcome_text = get_welcome_text(chat_id)
        for member in message["new_chat_members"]:
            name = member.get("first_name", "Foydalanuvchi")
            text = welcome_text.replace("{name}", name)
            send(chat_id, text)
        return "ok"

    message = update.get("message")
    if not message:
        return "ok"

    chat_id = message["chat"]["id"]
    chat_type = message["chat"]["type"]
    user = message.get("from", {})
    user_id = user.get("id")
    text = message.get("text", "").strip()
    message_id = message["message_id"]

    # Faqat guruhlarda ishlaydi
    if chat_type not in ["group", "supergroup"]:
        if text == "/start":
            send(chat_id, "🤖 Salom! Meni guruhga admin qilib qo'shing — moderatsiya qilaman.")
        return "ok"

    if not text:
        # Anti-flood matnli bo'lmagan xabarlar uchun ham ishlasin
        if user_id and check_flood(chat_id, user_id):
            apply_flood_mute(chat_id, user_id)
            send(chat_id, f"🔇 {user.get('first_name', 'Foydalanuvchi')} flood qilgani uchun {FLOOD_MUTE_SECONDS // 60} daqiqaga sukut qilindi.")
        return "ok"

    # ── BUYRUQLAR (faqat adminlar uchun) ──

    if text.startswith("/ban"):
        if not is_admin(chat_id, user_id):
            send(chat_id, "⛔ Bu buyruq faqat adminlar uchun!", reply_to=message_id)
            return "ok"
        target_id, target_name = get_target_user(message)
        if not target_id:
            send(chat_id, "❗ Ban qilish uchun foydalanuvchining xabariga reply qiling.\nMisol: xabarga reply qilib /ban yozing", reply_to=message_id)
            return "ok"
        ban_chat_member(chat_id, target_id)
        send(chat_id, f"🚫 {target_name} guruhdan ban qilindi.")
        return "ok"

    if text.startswith("/unban"):
        if not is_admin(chat_id, user_id):
            send(chat_id, "⛔ Bu buyruq faqat adminlar uchun!", reply_to=message_id)
            return "ok"
        target_id, target_name = get_target_user(message)
        if not target_id:
            send(chat_id, "❗ Unban qilish uchun foydalanuvchining xabariga reply qiling.", reply_to=message_id)
            return "ok"
        unban_chat_member(chat_id, target_id)
        send(chat_id, f"✅ {target_name} ban dan chiqarildi.")
        return "ok"

    if text.startswith("/mute"):
        if not is_admin(chat_id, user_id):
            send(chat_id, "⛔ Bu buyruq faqat adminlar uchun!", reply_to=message_id)
            return "ok"
        target_id, target_name = get_target_user(message)
        if not target_id:
            send(chat_id, "❗ Mute qilish uchun foydalanuvchining xabariga reply qiling.\nMisol: xabarga reply qilib /mute yozing", reply_to=message_id)
            return "ok"

        # Vaqt parametri: /mute 10 (daqiqa). Bo'lmasa doimiy.
        parts = text.split()
        until_date = 0
        if len(parts) > 1 and parts[1].isdigit():
            minutes = int(parts[1])
            until_date = int(time.time()) + minutes * 60
            duration_text = f"{minutes} daqiqaga"
        else:
            duration_text = "doimiy"

        restrict_chat_member(chat_id, target_id, until_date=until_date, can_send_messages=False)
        send(chat_id, f"🔇 {target_name} {duration_text} sukut qilindi.")
        return "ok"

    if text.startswith("/unmute"):
        if not is_admin(chat_id, user_id):
            send(chat_id, "⛔ Bu buyruq faqat adminlar uchun!", reply_to=message_id)
            return "ok"
        target_id, target_name = get_target_user(message)
        if not target_id:
            send(chat_id, "❗ Unmute qilish uchun foydalanuvchining xabariga reply qiling.", reply_to=message_id)
            return "ok"
        restrict_chat_member(chat_id, target_id, until_date=0, can_send_messages=True)
        send(chat_id, f"🔊 {target_name} gapirish huquqi qaytarildi.")
        return "ok"

    if text.startswith("/setwelcome"):
        if not is_admin(chat_id, user_id):
            send(chat_id, "⛔ Bu buyruq faqat adminlar uchun!", reply_to=message_id)
            return "ok"
        new_text = text.replace("/setwelcome", "").strip()
        if not new_text:
            send(chat_id,
                "❗ Misol: /setwelcome Xush kelibsiz, {name}!\n\n"
                "{name} o'rniga yangi a'zo ismi qo'yiladi.", reply_to=message_id)
            return "ok"
        set_welcome_text(chat_id, new_text)
        send(chat_id, "✅ Welcome xabari yangilandi!")
        return "ok"

    if text == "/help":
        send(chat_id,
            "📖 <b>Moderatsiya Bot — Buyruqlar</b>\n\n"
            "🔹 /ban — (reply) foydalanuvchini ban qilish\n"
            "🔹 /unban — (reply) banni bekor qilish\n"
            "🔹 /mute [daqiqa] — (reply) sukut qilish\n"
            "🔹 /unmute — (reply) sukutni bekor qilish\n"
            "🔹 /setwelcome [matn] — welcome xabarini sozlash\n\n"
            "<i>Barcha buyruqlar faqat adminlar uchun</i>"
        )
        return "ok"

    # ── ANTI-FLOOD TEKSHIRISH (oddiy xabarlar uchun) ──
    if user_id and not is_admin(chat_id, user_id):
        if check_flood(chat_id, user_id):
            apply_flood_mute(chat_id, user_id)
            send(chat_id, f"🔇 {user.get('first_name', 'Foydalanuvchi')} tez-tez yozgani uchun {FLOOD_MUTE_SECONDS // 60} daqiqaga sukut qilindi.")

    return "ok"


@app.route("/")
def index():
    return "Moderatsiya Bot ishlamoqda ✅"


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
