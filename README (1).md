# 🤖 Telegram Moderatsiya Boti

## O'rnatish

```bash
pip install -r requirements.txt
```

## Sozlash

`bot.py` faylida quyidagi qatorni o'zgartiring:
```python
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
```

## Ishga tushirish

```bash
python bot.py
```

## Botni guruhga qo'shish

1. Botni guruhga admin qilib qo'shing
2. Quyidagi ruxsatlarni bering:
   - ✅ Foydalanuvchilarni ban qilish
   - ✅ Xabarlarni o'chirish
   - ✅ A'zolarni cheklash (restrict)

## Anti-flood sozlamalari (bot.py ichida)

```python
FLOOD_LIMIT    = 5   # nechta xabar
FLOOD_INTERVAL = 5   # soniya ichida
MUTE_DURATION  = 60  # mute vaqti (soniya)
```

## Buyruqlar

| Buyruq | Tavsif |
|--------|--------|
| `/ban [sabab]` | Reply qilingan userni ban qilish |
| `/unban` | Banni olib tashlash |
| `/mute [daqiqa] [sabab]` | Mute qilish |
| `/unmute` | Muteni olib tashlash |
| `/help` | Yordam xabari |

## Ma'lumotlar bazasi

SQLite (`moderation.db`) avtomatik yaratiladi. Jadvallar:
- `banned_users` — ban qilinganlar
- `muted_users` — mute qilinganlar  
- `warnings` — ogohlantirishlar (kelajak uchun)
