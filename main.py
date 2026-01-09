# -*- coding: utf-8 -*-
import json
import os
import random
import string
import time
from datetime import datetime, timedelta, timezone

import requests
import telebot
from telebot import types

CONFIG_PATH = "config.json"
DB_PATH = "database.json"

# =======================
# CONFIG
# =======================
def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit("❌ Нет config.json рядом с main.py")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    token = cfg.get("TOKEN")
    if not token or ":" not in str(token):
        raise SystemExit("❌ В config.json не задан TOKEN")

    admin_id = cfg.get("ADMIN_ID")
    if admin_id is None:
        raise SystemExit("❌ В config.json не задан ADMIN_ID")

    crypto_token = cfg.get("CRYPTOPAY_TOKEN")
    if not crypto_token:
        raise SystemExit("❌ В config.json не задан CRYPTOPAY_TOKEN (Crypto Pay API)")

    channel_id = str(cfg.get("CHANNEL_ID", "")).strip()
    if not channel_id:
        raise SystemExit("❌ В config.json не задан CHANNEL_ID (группа/канал для бэкапа файлов).")

    cfg["CHANNEL_ID"] = channel_id
    cfg.setdefault("CRYPTOPAY_BASE", "https://pay.crypt.bot/api/")
    cfg.setdefault("DEFAULT_PRICE_USD", 0.5)

    return cfg

config = load_config()

TOKEN = str(config["TOKEN"])
ADMIN_ID = int(config["ADMIN_ID"])
CHANNEL_ID = str(config["CHANNEL_ID"]).strip()
CRYPTOPAY_TOKEN = str(config["CRYPTOPAY_TOKEN"])
CRYPTOPAY_BASE = str(config.get("CRYPTOPAY_BASE", "https://pay.crypt.bot/api/")).rstrip("/") + "/"
DEFAULT_PRICE_USD = float(config.get("DEFAULT_PRICE_USD", 0.5))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# =======================
# TIME
# =======================
def now_utc():
    return datetime.now(timezone.utc)

def dt_to_iso(dt: datetime | None):
    return dt.astimezone(timezone.utc).isoformat() if dt else None

def iso_to_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

# =======================
# DB (auto-create + migration)
# =======================
def save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def load_db():
    if not os.path.exists(DB_PATH):
        db = {}
    else:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            db = {}

    # Migration: old format {code: file_info}
    if isinstance(db, dict) and "files" not in db and any(isinstance(v, dict) and "file_id" in v for v in db.values()):
        old_files = db
        db = {"files": old_files, "users": {}, "invoices": {}, "settings": {}}

    if not isinstance(db, dict):
        db = {}

    db.setdefault("files", {})
    db.setdefault("users", {})
    db.setdefault("invoices", {})
    db.setdefault("settings", {})
    db["settings"].setdefault("monthly_price_usd", DEFAULT_PRICE_USD)

    save_db(db)
    return db

db = load_db()

# =======================
# USERS / SUBS
# =======================
def ensure_user(u: types.User):
    uid = str(u.id)
    user = db["users"].get(uid, {})
    user["id"] = u.id
    user["username"] = u.username or ""
    user["tag"] = ("@" + u.username) if u.username else "—"
    user["first_name"] = u.first_name or ""
    user["last_name"] = u.last_name or ""
    user["last_seen"] = dt_to_iso(now_utc())
    user.setdefault("sub_until", None)      # ISO
    user.setdefault("last_invoice", None)   # invoice_id
    db["users"][uid] = user
    save_db(db)
    return user

def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)

def get_price() -> float:
    try:
        return float(db["settings"].get("monthly_price_usd", DEFAULT_PRICE_USD))
    except Exception:
        return DEFAULT_PRICE_USD

def sub_until_dt(user: dict):
    return iso_to_dt(user.get("sub_until"))

def has_active_sub(user: dict) -> bool:
    until = sub_until_dt(user)
    return bool(until and until > now_utc())

def extend_sub(user_id: int, days: int = 30):
    uid = str(user_id)
    user = db["users"].get(uid) or {"id": user_id}
    cur = sub_until_dt(user)
    base = cur if (cur and cur > now_utc()) else now_utc()
    new_until = base + timedelta(days=days)
    user["sub_until"] = dt_to_iso(new_until)
    db["users"][uid] = user
    save_db(db)
    return new_until

def fmt_sub(user: dict) -> str:
    until = sub_until_dt(user)
    if until and until > now_utc():
        return f"✅ Активна до: <code>{until.strftime('%Y-%m-%d %H:%M UTC')}</code>"
    if until:
        return f"❌ Истекла: <code>{until.strftime('%Y-%m-%d %H:%M UTC')}</code>"
    return "❌ Не активна"

# =======================
# FILES
# =======================
def gen_code(n=16):
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

# =======================
# CRYPTO PAY (CryptoBot)
# =======================
def crypto_request(method: str, payload: dict):
    url = CRYPTOPAY_BASE + method
    headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data))
    return data["result"]

def create_invoice_for_month(user_id: int):
    price = get_price()
    payload = {
        "amount": f"{price:.2f}",
        "currency_type": "fiat",
        "fiat": "USD",
        "description": "Подписка FileHosting на 1 месяц",
        "expires_in": 900,  # 15 минут
        "allow_anonymous": False,
        "allow_comments": False,
    }
    inv = crypto_request("createInvoice", payload)
    invoice_id = str(inv.get("invoice_id"))
    pay_url = inv.get("pay_url") or inv.get("bot_invoice_url") or ""
    db["invoices"][invoice_id] = {
        "invoice_id": invoice_id,
        "user_id": user_id,
        "created_at": dt_to_iso(now_utc()),
        "status": inv.get("status", "active"),
        "amount_usd": price,
        "pay_url": pay_url,
    }
    save_db(db)
    return invoice_id, pay_url

def get_invoice_status(invoice_id: str):
    res = crypto_request("getInvoices", {"invoice_ids": invoice_id})
    items = res.get("items", [])
    if not items:
        return None
    return items[0]

# =======================
# UI (no payment buttons anywhere except /pay)
# =======================
def menu_kb(user: dict):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👤 Профиль", callback_data="profile"))
    if is_admin(user["id"]):
        kb.add(types.InlineKeyboardButton("🛠 Админ-панель", callback_data="admin"))
    return kb

def pay_kb(invoice_id: str, pay_url: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=pay_url))
    kb.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"chk:{invoice_id}"))
    return kb

def admin_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Пользователи", callback_data="adm:users"),
        types.InlineKeyboardButton("💵 Цена", callback_data="adm:price"),
    )
    kb.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="adm:stats"),
        types.InlineKeyboardButton("🎫 Выдать подписку", callback_data="adm:grant"),
    )
    return kb

admin_state = {}  # chat_id -> state

def chunk(text: str, limit=3500):
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts

# =======================
# COMMANDS
# =======================
@bot.message_handler(commands=["start"])
def on_start(message: types.Message):
    user = ensure_user(message.from_user)

    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        code = args[1].strip()
        f = db["files"].get(code)
        if not f:
            bot.send_message(message.chat.id, "⚠️ Файл не найден или ссылка устарела.", reply_markup=menu_kb(user))
            return

        bot.send_message(
            message.chat.id,
            "ℹ️ <b>Файл</b>\n"
            f"Имя: <code>{f.get('file_name','')}</code>\n"
            f"Тип: <code>{f.get('mime_type','')}</code>\n"
            f"Отправитель: <code>{f.get('u_tag','—')}</code>\n"
            f"Дата: <code>{f.get('created_at','')}</code>\n\n"
            "📦 Отправляю файл…"
        )
        bot.send_document(message.chat.id, f["file_id"])
        return

    bot.send_message(
        message.chat.id,
        "👋 <b>File Hosting</b>\n\n"
        "• Отправь мне файл — я дам ссылку.\n"
        "• По ссылке файл можно скачать.\n"
        "• Для загрузки файлов нужна подписка.\n\n"
        "💳 Оплата подписки: <code>/pay</code>\n"
        "ℹ️ Инфо по файлу: <code>/file CODE</code>",
        reply_markup=menu_kb(user),
    )

@bot.message_handler(commands=["pay"])
def on_pay(message: types.Message):
    user = ensure_user(message.from_user)
    try:
        invoice_id, pay_url = create_invoice_for_month(user["id"])
        db["users"][str(user["id"])]["last_invoice"] = invoice_id
        save_db(db)

        bot.send_message(
            message.chat.id,
            f"💳 <b>Подписка на 1 месяц</b>\n"
            f"Сумма: <b>${get_price():.2f}</b>\n\n"
            "Нажми «Оплатить», после оплаты — «Проверить оплату».",
            reply_markup=pay_kb(invoice_id, pay_url)
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Не могу создать счёт.\n<code>{e}</code>")

@bot.message_handler(commands=["file"])
def on_file(message: types.Message):
    user = ensure_user(message.from_user)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/file CODE</code>")
        return
    code = parts[1].strip()
    f = db["files"].get(code)
    if not f:
        bot.send_message(message.chat.id, "❌ Файл не найден.")
        return

    bot.send_message(
        message.chat.id,
        "ℹ️ <b>Информация о файле</b>\n"
        f"Код: <code>{code}</code>\n"
        f"Имя: <code>{f.get('file_name','')}</code>\n"
        f"Тип: <code>{f.get('mime_type','')}</code>\n"
        f"Отправитель: <code>{f.get('u_tag','—')}</code>\n"
        f"ID отправителя: <code>{f.get('u_id','')}</code>\n"
        f"Дата: <code>{f.get('created_at','')}</code>"
    )

@bot.message_handler(commands=["admin"])
def on_admin_cmd(message: types.Message):
    user = ensure_user(message.from_user)
    if not is_admin(user["id"]):
        return
    bot.send_message(message.chat.id, "🛠 <b>Админ-панель</b>", reply_markup=admin_kb())

@bot.message_handler(commands=["info"])
def on_info(message: types.Message):
    user = ensure_user(message.from_user)
    if not is_admin(user["id"]):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/info CODE</code>")
        return
    code = parts[1].strip()
    f = db["files"].get(code)
    if not f:
        bot.send_message(message.chat.id, "❌ Файл не найден.")
        return
    bot.send_message(
        message.chat.id,
        "ℹ️ <b>Информация о файле (админ)</b>\n"
        f"Код: <code>{code}</code>\n"
        f"Имя: <code>{f.get('file_name','')}</code>\n"
        f"Тип: <code>{f.get('mime_type','')}</code>\n"
        f"Отправитель: <code>{f.get('u_tag','—')}</code>\n"
        f"ID отправителя: <code>{f.get('u_id','')}</code>\n"
        f"Дата: <code>{f.get('created_at','')}</code>\n"
        f"Backup msg_id: <code>{f.get('backup_msg_id','—')}</code>"
    )

# =======================
# CALLBACKS
# =======================
@bot.callback_query_handler(func=lambda c: True)
def on_cb(call: types.CallbackQuery):
    user = ensure_user(call.from_user)
    data = call.data or ""

    # payment check (only from /pay message button)
    if data.startswith("chk:"):
        invoice_id = data.split("chk:", 1)[1].strip()
        try:
            inv = get_invoice_status(invoice_id)
            if not inv:
                bot.answer_callback_query(call.id, "Счёт не найден", show_alert=True)
                return

            status = inv.get("status", "unknown")
            db["invoices"].setdefault(invoice_id, {})
            db["invoices"][invoice_id]["status"] = status
            db["invoices"][invoice_id]["checked_at"] = dt_to_iso(now_utc())
            save_db(db)

            if status == "paid":
                extend_sub(user["id"], days=30)
                u = db["users"].get(str(user["id"]), user)
                bot.answer_callback_query(call.id, "Оплата найдена ✅", show_alert=True)
                bot.send_message(call.message.chat.id, f"✅ Подписка активирована.\n{fmt_sub(u)}")
            else:
                bot.answer_callback_query(call.id, f"Статус: {status}", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, "Ошибка проверки", show_alert=True)
            bot.send_message(call.message.chat.id, f"❌ Ошибка проверки.\n<code>{e}</code>")
        return

    if data == "profile":
        bot.answer_callback_query(call.id)
        u = db["users"].get(str(user["id"]), user)
        bot.send_message(
            call.message.chat.id,
            "👤 <b>Профиль</b>\n"
            f"ID: <code>{u['id']}</code>\n"
            f"Тег: <code>{u.get('tag','—')}</code>\n"
            f"Подписка: {fmt_sub(u)}\n\n"
            "💳 Оплата подписки: <code>/pay</code>",
            reply_markup=menu_kb(u),
        )
        return

    if data == "admin":
        if not is_admin(user["id"]):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🛠 <b>Админ-панель</b>", reply_markup=admin_kb())
        return

    if data == "adm:users":
        if not is_admin(user["id"]):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        users = list(db["users"].values())
        users.sort(key=lambda x: int(x.get("id", 0)))
        lines = ["👥 <b>Пользователи</b>"]
        for u in users[:3000]:
            lines.append(f"• {u.get('tag','—')} — <code>{u.get('id')}</code>")
        txt = "\n".join(lines) if users else "Пользователей нет."
        for part in chunk(txt):
            bot.send_message(call.message.chat.id, part)
        return

    if data == "adm:stats":
        if not is_admin(user["id"]):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        users = list(db["users"].values())
        active = 0
        for u in users:
            until = iso_to_dt(u.get("sub_until"))
            if until and until > now_utc():
                active += 1
        bot.send_message(
            call.message.chat.id,
            "📊 <b>Статистика</b>\n"
            f"Пользователей: <b>{len(users)}</b>\n"
            f"Активных подписок: <b>{active}</b>\n"
            f"Файлов: <b>{len(db['files'])}</b>\n"
            f"Инвойсов: <b>{len(db['invoices'])}</b>\n"
            f"Цена: <b>${get_price():.2f}</b>/мес"
        )
        return

    if data == "adm:price":
        if not is_admin(user["id"]):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        admin_state[str(call.message.chat.id)] = "await_price"
        bot.send_message(
            call.message.chat.id,
            f"💵 Текущая цена: <b>${get_price():.2f}</b>\n"
            "Отправь новую цену числом (пример: <code>0.5</code>)"
        )
        return

    if data == "adm:grant":
        if not is_admin(user["id"]):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        admin_state[str(call.message.chat.id)] = "await_grant_user"
        bot.send_message(call.message.chat.id, "ID пользователя, кому выдать подписку (30 дней).")
        return

    bot.answer_callback_query(call.id)

# =======================
# ADMIN INPUT
# =======================
@bot.message_handler(content_types=["text"])
def on_text(message: types.Message):
    user = ensure_user(message.from_user)
    st = admin_state.get(str(message.chat.id))

    if st == "await_price" and is_admin(user["id"]):
        try:
            price = float(message.text.strip().replace(",", "."))
            if price <= 0:
                raise ValueError
            db["settings"]["monthly_price_usd"] = price
            save_db(db)
            admin_state.pop(str(message.chat.id), None)
            bot.send_message(message.chat.id, f"✅ Цена обновлена: <b>${price:.2f}</b> / мес", reply_markup=admin_kb())
        except Exception:
            bot.send_message(message.chat.id, "❌ Не понял цену. Пример: <code>0.5</code>")
        return

    if st == "await_grant_user" and is_admin(user["id"]):
        try:
            target_id = int(message.text.strip())
            target = db["users"].get(str(target_id)) or {
                "id": target_id, "username": "", "tag": "—", "sub_until": None, "last_invoice": None
            }
            db["users"][str(target_id)] = target
            save_db(db)
            until = extend_sub(target_id, days=30)
            admin_state.pop(str(message.chat.id), None)
            bot.send_message(
                message.chat.id,
                f"✅ Подписка выдана <code>{target_id}</code> до <code>{until.strftime('%Y-%m-%d %H:%M UTC')}</code>",
                reply_markup=admin_kb()
            )
        except Exception:
            bot.send_message(message.chat.id, "❌ Неверный ID. Пришли числом.")
        return

    if message.text.strip().lower() in ("меню", "/menu"):
        bot.send_message(message.chat.id, "Меню:", reply_markup=menu_kb(user))
        return

# =======================
# UPLOAD (backup to group/channel is mandatory)
# =======================
@bot.message_handler(content_types=["document"])
def on_upload(message: types.Message):
    user = ensure_user(message.from_user)

    if not (is_admin(user["id"]) or has_active_sub(user)):
        bot.send_message(
            message.chat.id,
            "🔒 <b>Загрузка доступна по подписке.</b>\n"
            f"Цена: <b>${get_price():.2f}</b> / месяц\n\n"
            "Оплата подписки: <code>/pay</code>"
        )
        return

    code = gen_code()
    doc = message.document

    # 1) MUST forward to backup chat/channel
    try:
        forwarded = bot.forward_message(CHANNEL_ID, message.chat.id, message.message_id)
    except Exception as e:
        bot.send_message(
            message.chat.id,
            "❌ Не смог переслать файл в backup-группу/канал.\n"
            "Проверь: бот добавлен в группу/канал и имеет права.\n\n"
            f"<code>{e}</code>"
        )
        return

    # 2) store file_id (prefer forwarded document file_id)
    backup_file_id = None
    try:
        if getattr(forwarded, "document", None):
            backup_file_id = forwarded.document.file_id
    except Exception:
        backup_file_id = None

    file_id_to_store = backup_file_id or doc.file_id

    db["files"][code] = {
        "file_id": file_id_to_store,
        "file_name": doc.file_name or "",
        "mime_type": doc.mime_type or "",
        "u_id": user["id"],
        "u_tag": user.get("tag", "—"),
        "created_at": now_utc().strftime("%Y-%m-%d %H:%M UTC"),
        "backup_chat": CHANNEL_ID,
        "backup_msg_id": getattr(forwarded, "message_id", None),
    }
    save_db(db)

    link = f"https://t.me/{bot.get_me().username}?start={code}"
    bot.send_message(
        message.chat.id,
        "✅ <b>Файл сохранён.</b>\n\n"
        f"🔗 Ссылка:\n<code>{link}</code>\n\n"
        "По ссылке файл можно скачать."
    )

print("BOT STARTED")
bot.infinity_polling(skip_pending=True)
