import os, json, time
from datetime import datetime
import requests
from telebot import TeleBot, types

# ====== НАСТРОЙКИ (можно через ENV, можно прямо тут) ======
TOKEN = os.getenv("TOKEN") or "ВСТАВЬ_ТОКЕН_БОТА"
BOT_USERNAME = os.getenv("BOT_USERNAME") or "filehosting_bot"  # без @
ADMIN_ID = int(os.getenv("ADMIN_ID") or "1401800532")

CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN") or "233286:AA3VfnMNKVA00YnPBCpWartKmgh40RySrnu"
CRYPTO_API = "https://pay.crypt.bot/api/"

SUB_PRICE_USD = float(os.getenv("SUB_PRICE_USD") or "0.5")
DB_FILE = "database.json"

# ====== ЗАЩИТА ОТ ПУСТОГО TOKEN ======
if not TOKEN or ":" not in TOKEN:
    raise SystemExit("❌ TOKEN не задан. Вставь токен в код или добавь переменную окружения TOKEN.")

bot = TeleBot(TOKEN, parse_mode="HTML")

# ====== БАЗА (СОЗДАЁТСЯ САМА ВСЕГДА) ======
def load_db():
    if not os.path.exists(DB_FILE):
        db = {}
    else:
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except:
            db = {}

    db.setdefault("users", {})
    db.setdefault("files", {})
    db.setdefault("invoices", {})
    db.setdefault("settings", {"price": SUB_PRICE_USD})

    # если файл был старого формата (просто code->file), миграция:
    if "users" not in db or "files" not in db:
        db = {"users": {}, "files": db if isinstance(db, dict) else {}, "invoices": {}, "settings": {"price": SUB_PRICE_USD}}

    save_db(db)
    return db

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def ensure_user(u):
    db = load_db()
    uid = str(u.id)
    if uid not in db["users"]:
        db["users"][uid] = {"id": u.id, "username": u.username or "", "sub_until": 0}
        save_db(db)

def is_admin(user_id): return int(user_id) == int(ADMIN_ID)

def has_sub(user_id):
    db = load_db()
    return db["users"].get(str(user_id), {}).get("sub_until", 0) > time.time()

# ====== CRYPTO PAY ======
def crypto_create_invoice():
    price = float(load_db()["settings"].get("price", SUB_PRICE_USD))
    payload = {"amount": f"{price:.2f}", "currency_type": "fiat", "fiat": "USD", "description": "Подписка на 1 месяц"}
    r = requests.post(
        CRYPTO_API + "createInvoice",
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        json=payload, timeout=20
    ).json()
    if not r.get("ok"):
        raise RuntimeError(str(r))
    inv = r["result"]
    return str(inv["invoice_id"]), inv["pay_url"]

def crypto_check_invoice(invoice_id: str):
    r = requests.post(
        CRYPTO_API + "getInvoices",
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        json={"invoice_ids": invoice_id}, timeout=20
    ).json()
    if not r.get("ok"):
        raise RuntimeError(str(r))
    items = r["result"].get("items", [])
    return items[0] if items else None

def pay_kb(invoice_id: str, pay_url: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 Оплатить в CryptoBot", url=pay_url))
    kb.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"chk:{invoice_id}"))
    return kb

# ====== START ======
@bot.message_handler(commands=["start"])
def start(message):
    ensure_user(message.from_user)
    db = load_db()

    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        p = args[1].strip()

        # СКАЧКА (из WebApp по ссылке)
        if p.startswith("dl_"):
            code = p[3:]
            f = db["files"].get(code)
            if not f:
                bot.send_message(message.chat.id, "❌ Файл не найден")
                return
            bot.send_document(message.chat.id, f["file_id"], caption="📥 Ваш файл")
            return

        # КАРТОЧКА ФАЙЛА (показываем кнопку на WebApp)
        if p.startswith("file_"):
            code = p[5:]
            f = db["files"].get(code)
            if not f:
                bot.send_message(message.chat.id, "❌ Файл не найден")
                return

            # ⚠️ WEBAPP_URL должен быть на статике (GitHub Pages / Cloudflare Pages и т.п.)
            WEBAPP_URL = db["settings"].get("webapp_url", "")

            if not WEBAPP_URL:
                bot.send_message(message.chat.id, "❌ Web-App не настроен. Админ: /setwebapp <url>")
                return

            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📄 Открыть файл (Web-App)", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?file={code}")))

            bot.send_message(
                message.chat.id,
                f"📄 <b>{f['name']}</b>\n"
                f"👤 Отправитель: <code>{f['sender']}</code>\n"
                f"📦 Тип: <code>{f.get('type','')}</code>\n",
                reply_markup=kb
            )
            return

        # ОПЛАТА (из WebApp или просто вручную)
        if p == "sub":
            try:
                invoice_id, pay_url = crypto_create_invoice()
                db["invoices"][invoice_id] = {"user_id": message.from_user.id, "status": "pending"}
                save_db(db)
                bot.send_message(message.chat.id, "💳 Оплата подписки:", reply_markup=pay_kb(invoice_id, pay_url))
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Не могу создать счёт\n<code>{e}</code>")
            return

    # обычный вход
    WEBAPP_URL = db["settings"].get("webapp_url", "")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if WEBAPP_URL:
        kb.add(types.InlineKeyboardButton("💳 Подписка (Web-App)", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    kb.add(types.InlineKeyboardButton("💳 Подписка (в боте)", url=f"https://t.me/{BOT_USERNAME}?start=sub"))
    if is_admin(message.from_user.id):
        kb.add(types.InlineKeyboardButton("🛠 Админ", callback_data="admin"))

    bot.send_message(message.chat.id, "👋 Меню", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("chk:"))
def cb_chk(call):
    ensure_user(call.from_user)
    invoice_id = call.data.split("chk:", 1)[1].strip()
    db = load_db()
    try:
        inv = crypto_check_invoice(invoice_id)
        if not inv:
            bot.answer_callback_query(call.id, "Инвойс не найден", show_alert=True)
            return
        status = inv.get("status")
        if status == "paid":
            uid = str(call.from_user.id)
            db["users"][uid]["sub_until"] = time.time() + 30*24*60*60
            db["invoices"].setdefault(invoice_id, {})
            db["invoices"][invoice_id]["status"] = "paid"
            save_db(db)
            bot.answer_callback_query(call.id, "Оплата найдена ✅", show_alert=True)
            bot.send_message(call.message.chat.id, "✅ Подписка активирована на 1 месяц.")
        else:
            bot.answer_callback_query(call.id, f"Статус: {status}", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка", show_alert=True)
        bot.send_message(call.message.chat.id, f"❌ Ошибка проверки\n<code>{e}</code>")

# ====== ADMIN ======
@bot.callback_query_handler(func=lambda c: c.data == "admin")
def admin_cb(call):
    if not is_admin(call.from_user.id):
        return
    db = load_db()
    bot.send_message(
        call.message.chat.id,
        "🛠 <b>Админ</b>\n"
        f"👥 Пользователей: <b>{len(db['users'])}</b>\n"
        f"📁 Файлов: <b>{len(db['files'])}</b>\n"
        f"💵 Цена: <b>${float(db['settings'].get('price', SUB_PRICE_USD)):.2f}</b>\n"
        f"🌐 WebApp: <code>{db['settings'].get('webapp_url','—')}</code>\n\n"
        "Команды:\n"
        "<code>/setwebapp https://....</code>\n"
        "<code>/setprice 0.5</code>\n"
        "<code>/users</code>"
    )

@bot.message_handler(commands=["setwebapp"])
def setwebapp(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setwebapp https://USERNAME.github.io/REPO/</code>")
        return
    url = parts[1].strip()
    db = load_db()
    db["settings"]["webapp_url"] = url
    save_db(db)
    bot.send_message(message.chat.id, f"✅ WebApp URL сохранён:\n<code>{url}</code>")

@bot.message_handler(commands=["setprice"])
def setprice(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setprice 0.5</code>")
        return
    try:
        price = float(parts[1].replace(",", "."))
        if price <= 0:
            raise ValueError
        db = load_db()
        db["settings"]["price"] = price
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Цена обновлена: <b>${price:.2f}</b>")
    except:
        bot.send_message(message.chat.id, "❌ Неверная цена")

@bot.message_handler(commands=["users"])
def users_cmd(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    db = load_db()
    lines = ["👥 <b>Пользователи</b>"]
    for u in db["users"].values():
        tag = ("@" + u["username"]) if u.get("username") else "—"
        lines.append(f"• {tag} — <code>{u.get('id')}</code>")
    txt = "\n".join(lines)
    for i in range(0, len(txt), 3500):
        bot.send_message(message.chat.id, txt[i:i+3500])

# ====== UPLOAD ======
@bot.message_handler(content_types=["document"])
def upload(message):
    ensure_user(message.from_user)
    if not (is_admin(message.from_user.id) or has_sub(message.from_user.id)):
        bot.send_message(message.chat.id, "🔒 Нужна подписка. Оформи: https://t.me/%s?start=sub" % BOT_USERNAME)
        return

    db = load_db()
    code = str(int(time.time()))
    db["files"][code] = {
        "file_id": message.document.file_id,
        "name": message.document.file_name,
        "type": message.document.mime_type or "",
        "sender": ("@" + message.from_user.username) if message.from_user.username else str(message.from_user.id),
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    }
    save_db(db)

    link = f"https://t.me/{BOT_USERNAME}?start=file_{code}"
    bot.send_message(message.chat.id, f"✅ Загружено\n🔗 <code>{link}</code>")

print("BOT STARTED")
bot.infinity_polling(skip_pending=True)            WEBAPP_URL = db["settings"].get("webapp_url", "")

            if not WEBAPP_URL:
                bot.send_message(message.chat.id, "❌ Web-App не настроен. Админ: /setwebapp <url>")
                return

            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📄 Открыть файл (Web-App)", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?file={code}")))

            bot.send_message(
                message.chat.id,
                f"📄 <b>{f['name']}</b>\n"
                f"👤 Отправитель: <code>{f['sender']}</code>\n"
                f"📦 Тип: <code>{f.get('type','')}</code>\n",
                reply_markup=kb
            )
            return

        # ОПЛАТА (из WebApp или просто вручную)
        if p == "sub":
            try:
                invoice_id, pay_url = crypto_create_invoice()
                db["invoices"][invoice_id] = {"user_id": message.from_user.id, "status": "pending"}
                save_db(db)
                bot.send_message(message.chat.id, "💳 Оплата подписки:", reply_markup=pay_kb(invoice_id, pay_url))
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Не могу создать счёт\n<code>{e}</code>")
            return

    # обычный вход
    WEBAPP_URL = db["settings"].get("webapp_url", "")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if WEBAPP_URL:
        kb.add(types.InlineKeyboardButton("💳 Подписка (Web-App)", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    kb.add(types.InlineKeyboardButton("💳 Подписка (в боте)", url=f"https://t.me/{BOT_USERNAME}?start=sub"))
    if is_admin(message.from_user.id):
        kb.add(types.InlineKeyboardButton("🛠 Админ", callback_data="admin"))

    bot.send_message(message.chat.id, "👋 Меню", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("chk:"))
def cb_chk(call):
    ensure_user(call.from_user)
    invoice_id = call.data.split("chk:", 1)[1].strip()
    db = load_db()
    try:
        inv = crypto_check_invoice(invoice_id)
        if not inv:
            bot.answer_callback_query(call.id, "Инвойс не найден", show_alert=True)
            return
        status = inv.get("status")
        if status == "paid":
            uid = str(call.from_user.id)
            db["users"][uid]["sub_until"] = time.time() + 30*24*60*60
            db["invoices"].setdefault(invoice_id, {})
            db["invoices"][invoice_id]["status"] = "paid"
            save_db(db)
            bot.answer_callback_query(call.id, "Оплата найдена ✅", show_alert=True)
            bot.send_message(call.message.chat.id, "✅ Подписка активирована на 1 месяц.")
        else:
            bot.answer_callback_query(call.id, f"Статус: {status}", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка", show_alert=True)
        bot.send_message(call.message.chat.id, f"❌ Ошибка проверки\n<code>{e}</code>")

# ====== ADMIN ======
@bot.callback_query_handler(func=lambda c: c.data == "admin")
def admin_cb(call):
    if not is_admin(call.from_user.id):
        return
    db = load_db()
    bot.send_message(
        call.message.chat.id,
        "🛠 <b>Админ</b>\n"
        f"👥 Пользователей: <b>{len(db['users'])}</b>\n"
        f"📁 Файлов: <b>{len(db['files'])}</b>\n"
        f"💵 Цена: <b>${float(db['settings'].get('price', SUB_PRICE_USD)):.2f}</b>\n"
        f"🌐 WebApp: <code>{db['settings'].get('webapp_url','—')}</code>\n\n"
        "Команды:\n"
        "<code>/setwebapp https://....</code>\n"
        "<code>/setprice 0.5</code>\n"
        "<code>/users</code>"
    )

@bot.message_handler(commands=["setwebapp"])
def setwebapp(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setwebapp https://USERNAME.github.io/REPO/</code>")
        return
    url = parts[1].strip()
    db = load_db()
    db["settings"]["webapp_url"] = url
    save_db(db)
    bot.send_message(message.chat.id, f"✅ WebApp URL сохранён:\n<code>{url}</code>")

@bot.message_handler(commands=["setprice"])
def setprice(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setprice 0.5</code>")
        return
    try:
        price = float(parts[1].replace(",", "."))
        if price <= 0:
            raise ValueError
        db = load_db()
        db["settings"]["price"] = price
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Цена обновлена: <b>${price:.2f}</b>")
    except:
        bot.send_message(message.chat.id, "❌ Неверная цена")

@bot.message_handler(commands=["users"])
def users_cmd(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    db = load_db()
    lines = ["👥 <b>Пользователи</b>"]
    for u in db["users"].values():
        tag = ("@" + u["username"]) if u.get("username") else "—"
        lines.append(f"• {tag} — <code>{u.get('id')}</code>")
    txt = "\n".join(lines)
    for i in range(0, len(txt), 3500):
        bot.send_message(message.chat.id, txt[i:i+3500])

# ====== UPLOAD ======
@bot.message_handler(content_types=["document"])
def upload(message):
    ensure_user(message.from_user)
    if not (is_admin(message.from_user.id) or has_sub(message.from_user.id)):
        bot.send_message(message.chat.id, "🔒 Нужна подписка. Оформи: https://t.me/%s?start=sub" % BOT_USERNAME)
        return

    db = load_db()
    code = str(int(time.time()))
    db["files"][code] = {
        "file_id": message.document.file_id,
        "name": message.document.file_name,
        "type": message.document.mime_type or "",
        "sender": ("@" + message.from_user.username) if message.from_user.username else str(message.from_user.id),
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    }
    save_db(db)

    link = f"https://t.me/{BOT_USERNAME}?start=file_{code}"
    bot.send_message(message.chat.id, f"✅ Загружено\n🔗 <code>{link}</code>")

print("BOT STARTED")
bot.infinity_polling(skip_pending=True)import os, json, time
from datetime import datetime
import requests
from telebot import TeleBot, types

# ====== НАСТРОЙКИ (можно через ENV, можно прямо тут) ======
TOKEN = os.getenv("TOKEN") or "ВСТАВЬ_ТОКЕН_БОТА"
BOT_USERNAME = os.getenv("BOT_USERNAME") or "YourBotUsername"  # без @
ADMIN_ID = int(os.getenv("ADMIN_ID") or "1401800532")

CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN") or "ВСТАВЬ_CRYPTO_PAY_API_TOKEN"
CRYPTO_API = "https://pay.crypt.bot/api/"

SUB_PRICE_USD = float(os.getenv("SUB_PRICE_USD") or "0.5")
DB_FILE = "database.json"

# ====== ЗАЩИТА ОТ ПУСТОГО TOKEN ======
if not TOKEN or ":" not in TOKEN:
    raise SystemExit("❌ TOKEN не задан. Вставь токен в код или добавь переменную окружения TOKEN.")

bot = TeleBot(TOKEN, parse_mode="HTML")

# ====== БАЗА (СОЗДАЁТСЯ САМА ВСЕГДА) ======
def load_db():
    if not os.path.exists(DB_FILE):
        db = {}
    else:
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except:
            db = {}

    db.setdefault("users", {})
    db.setdefault("files", {})
    db.setdefault("invoices", {})
    db.setdefault("settings", {"price": SUB_PRICE_USD})

    # если файл был старого формата (просто code->file), миграция:
    if "users" not in db or "files" not in db:
        db = {"users": {}, "files": db if isinstance(db, dict) else {}, "invoices": {}, "settings": {"price": SUB_PRICE_USD}}

    save_db(db)
    return db

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def ensure_user(u):
    db = load_db()
    uid = str(u.id)
    if uid not in db["users"]:
        db["users"][uid] = {"id": u.id, "username": u.username or "", "sub_until": 0}
        save_db(db)

def is_admin(user_id): return int(user_id) == int(ADMIN_ID)

def has_sub(user_id):
    db = load_db()
    return db["users"].get(str(user_id), {}).get("sub_until", 0) > time.time()

# ====== CRYPTO PAY ======
def crypto_create_invoice():
    price = float(load_db()["settings"].get("price", SUB_PRICE_USD))
    payload = {"amount": f"{price:.2f}", "currency_type": "fiat", "fiat": "USD", "description": "Подписка на 1 месяц"}
    r = requests.post(
        CRYPTO_API + "createInvoice",
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        json=payload, timeout=20
    ).json()
    if not r.get("ok"):
        raise RuntimeError(str(r))
    inv = r["result"]
    return str(inv["invoice_id"]), inv["pay_url"]

def crypto_check_invoice(invoice_id: str):
    r = requests.post(
        CRYPTO_API + "getInvoices",
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        json={"invoice_ids": invoice_id}, timeout=20
    ).json()
    if not r.get("ok"):
        raise RuntimeError(str(r))
    items = r["result"].get("items", [])
    return items[0] if items else None

def pay_kb(invoice_id: str, pay_url: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 Оплатить в CryptoBot", url=pay_url))
    kb.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"chk:{invoice_id}"))
    return kb

# ====== START ======
@bot.message_handler(commands=["start"])
def start(message):
    ensure_user(message.from_user)
    db = load_db()

    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        p = args[1].strip()

        # СКАЧКА (из WebApp по ссылке)
        if p.startswith("dl_"):
            code = p[3:]
            f = db["files"].get(code)
            if not f:
                bot.send_message(message.chat.id, "❌ Файл не найден")
                return
            bot.send_document(message.chat.id, f["file_id"], caption="📥 Ваш файл")
            return

        # КАРТОЧКА ФАЙЛА (показываем кнопку на WebApp)
        if p.startswith("file_"):
            code = p[5:]
            f = db["files"].get(code)
            if not f:
                bot.send_message(message.chat.id, "❌ Файл не найден")
                return

            # ⚠️ WEBAPP_URL должен быть на статике (GitHub Pages / Cloudflare Pages и т.п.)
            WEBAPP_URL = db["settings"].get("webapp_url", "")

            if not WEBAPP_URL:
                bot.send_message(message.chat.id, "❌ Web-App не настроен. Админ: /setwebapp <url>")
                return

            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📄 Открыть файл (Web-App)", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?file={code}")))

            bot.send_message(
                message.chat.id,
                f"📄 <b>{f['name']}</b>\n"
                f"👤 Отправитель: <code>{f['sender']}</code>\n"
                f"📦 Тип: <code>{f.get('type','')}</code>\n",
                reply_markup=kb
            )
            return

        # ОПЛАТА (из WebApp или просто вручную)
        if p == "sub":
            try:
                invoice_id, pay_url = crypto_create_invoice()
                db["invoices"][invoice_id] = {"user_id": message.from_user.id, "status": "pending"}
                save_db(db)
                bot.send_message(message.chat.id, "💳 Оплата подписки:", reply_markup=pay_kb(invoice_id, pay_url))
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Не могу создать счёт\n<code>{e}</code>")
            return

    # обычный вход
    WEBAPP_URL = db["settings"].get("webapp_url", "")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if WEBAPP_URL:
        kb.add(types.InlineKeyboardButton("💳 Подписка (Web-App)", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    kb.add(types.InlineKeyboardButton("💳 Подписка (в боте)", url=f"https://t.me/{BOT_USERNAME}?start=sub"))
    if is_admin(message.from_user.id):
        kb.add(types.InlineKeyboardButton("🛠 Админ", callback_data="admin"))

    bot.send_message(message.chat.id, "👋 Меню", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("chk:"))
def cb_chk(call):
    ensure_user(call.from_user)
    invoice_id = call.data.split("chk:", 1)[1].strip()
    db = load_db()
    try:
        inv = crypto_check_invoice(invoice_id)
        if not inv:
            bot.answer_callback_query(call.id, "Инвойс не найден", show_alert=True)
            return
        status = inv.get("status")
        if status == "paid":
            uid = str(call.from_user.id)
            db["users"][uid]["sub_until"] = time.time() + 30*24*60*60
            db["invoices"].setdefault(invoice_id, {})
            db["invoices"][invoice_id]["status"] = "paid"
            save_db(db)
            bot.answer_callback_query(call.id, "Оплата найдена ✅", show_alert=True)
            bot.send_message(call.message.chat.id, "✅ Подписка активирована на 1 месяц.")
        else:
            bot.answer_callback_query(call.id, f"Статус: {status}", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка", show_alert=True)
        bot.send_message(call.message.chat.id, f"❌ Ошибка проверки\n<code>{e}</code>")

# ====== ADMIN ======
@bot.callback_query_handler(func=lambda c: c.data == "admin")
def admin_cb(call):
    if not is_admin(call.from_user.id):
        return
    db = load_db()
    bot.send_message(
        call.message.chat.id,
        "🛠 <b>Админ</b>\n"
        f"👥 Пользователей: <b>{len(db['users'])}</b>\n"
        f"📁 Файлов: <b>{len(db['files'])}</b>\n"
        f"💵 Цена: <b>${float(db['settings'].get('price', SUB_PRICE_USD)):.2f}</b>\n"
        f"🌐 WebApp: <code>{db['settings'].get('webapp_url','—')}</code>\n\n"
        "Команды:\n"
        "<code>/setwebapp https://....</code>\n"
        "<code>/setprice 0.5</code>\n"
        "<code>/users</code>"
    )

@bot.message_handler(commands=["setwebapp"])
def setwebapp(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setwebapp https://USERNAME.github.io/REPO/</code>")
        return
    url = parts[1].strip()
    db = load_db()
    db["settings"]["webapp_url"] = url
    save_db(db)
    bot.send_message(message.chat.id, f"✅ WebApp URL сохранён:\n<code>{url}</code>")

@bot.message_handler(commands=["setprice"])
def setprice(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setprice 0.5</code>")
        return
    try:
        price = float(parts[1].replace(",", "."))
        if price <= 0:
            raise ValueError
        db = load_db()
        db["settings"]["price"] = price
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Цена обновлена: <b>${price:.2f}</b>")
    except:
        bot.send_message(message.chat.id, "❌ Неверная цена")

@bot.message_handler(commands=["users"])
def users_cmd(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    db = load_db()
    lines = ["👥 <b>Пользователи</b>"]
    for u in db["users"].values():
        tag = ("@" + u["username"]) if u.get("username") else "—"
        lines.append(f"• {tag} — <code>{u.get('id')}</code>")
    txt = "\n".join(lines)
    for i in range(0, len(txt), 3500):
        bot.send_message(message.chat.id, txt[i:i+3500])

# ====== UPLOAD ======
@bot.message_handler(content_types=["document"])
def upload(message):
    ensure_user(message.from_user)
    if not (is_admin(message.from_user.id) or has_sub(message.from_user.id)):
        bot.send_message(message.chat.id, "🔒 Нужна подписка. Оформи: https://t.me/%s?start=sub" % BOT_USERNAME)
        return

    db = load_db()
    code = str(int(time.time()))
    db["files"][code] = {
        "file_id": message.document.file_id,
        "name": message.document.file_name,
        "type": message.document.mime_type or "",
        "sender": ("@" + message.from_user.username) if message.from_user.username else str(message.from_user.id),
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    }
    save_db(db)

    link = f"https://t.me/{BOT_USERNAME}?start=file_{code}"
    bot.send_message(message.chat.id, f"✅ Загружено\n🔗 <code>{link}</code>")

print("BOT STARTED")
bot.infinity_polling(skip_pending=True)def file_card_kb(code: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📄 Открыть файл", web_app=types.WebAppInfo(url=f"{BASE_URL}/?file={code}")))
    return kb

def pay_kb(invoice_id: str, pay_url: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 Оплатить в CryptoBot", url=pay_url))
    kb.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"chk:{invoice_id}"))
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(message):
    ensure_user(message.from_user)
    db = load_db()

    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        payload = args[1].strip()

        # скачка из WebApp через редирект
        if payload.startswith("dl_"):
            code = payload[3:]
            f = db["files"].get(code)
            if not f:
                bot.send_message(message.chat.id, "❌ Файл не найден")
                return
            bot.send_document(message.chat.id, f["file_id"], caption="📥 Ваш файл")
            return

        # ссылка на файл
        if payload.startswith("file_"):
            code = payload[5:]
            f = db["files"].get(code)
            if not f:
                bot.send_message(message.chat.id, "❌ Файл не найден")
                return

            bot.send_message(
                message.chat.id,
                f"📄 <b>{f['name']}</b>\n"
                f"👤 Отправитель: <code>{f['sender']}</code>\n"
                f"📦 Тип: <code>{f.get('type','')}</code>\n",
                reply_markup=file_card_kb(code)
            )
            return

        # открыть оплату напрямую
        if payload == "sub":
            try:
                invoice_id, pay_url = crypto_create_invoice()
                db["invoices"][str(invoice_id)] = {"user_id": message.from_user.id, "status": "pending"}
                save_db(db)
                bot.send_message(message.chat.id, "💳 Оплата подписки:", reply_markup=pay_kb(str(invoice_id), pay_url))
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Не могу создать счёт\n<code>{e}</code>")
            return

    bot.send_message(message.chat.id, "👋 Меню:", reply_markup=main_menu_kb())

@bot.callback_query_handler(func=lambda c: c.data.startswith("chk:"))
def cb_check(call):
    ensure_user(call.from_user)
    invoice_id = call.data.split("chk:", 1)[1].strip()
    db = load_db()

    try:
        inv = crypto_check_invoice(invoice_id)
        if not inv:
            bot.answer_callback_query(call.id, "Инвойс не найден", show_alert=True)
            return
        status = inv.get("status")
        if status == "paid":
            uid = str(call.from_user.id)
            db["users"].setdefault(uid, {"id": call.from_user.id, "username": call.from_user.username or "", "sub_until": 0})
            db["users"][uid]["sub_until"] = time.time() + 30 * 24 * 60 * 60
            db["invoices"].setdefault(invoice_id, {})
            db["invoices"][invoice_id]["status"] = "paid"
            save_db(db)
            bot.answer_callback_query(call.id, "Оплата найдена ✅", show_alert=True)
            bot.send_message(call.message.chat.id, "✅ Подписка активирована на 1 месяц.")
        else:
            bot.answer_callback_query(call.id, f"Статус: {status}", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка проверки", show_alert=True)
        bot.send_message(call.message.chat.id, f"❌ Ошибка проверки\n<code>{e}</code>")

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    db = load_db()
    bot.send_message(
        message.chat.id,
        "🛠 <b>Админ</b>\n"
        f"👥 Пользователей: <b>{len(db['users'])}</b>\n"
        f"📁 Файлов: <b>{len(db['files'])}</b>\n"
        f"💵 Цена: <b>${float(db['settings'].get('price', SUB_PRICE_USD)):.2f}</b>\n\n"
        "Изменить цену: <code>/setprice 0.5</code>\n"
        "Показать пользователей: <code>/users</code>"
    )

@bot.message_handler(commands=["setprice"])
def cmd_setprice(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Пример: <code>/setprice 0.5</code>")
        return
    try:
        price = float(parts[1].replace(",", "."))
        if price <= 0:
            raise ValueError
        db = load_db()
        db["settings"]["price"] = price
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Цена обновлена: <b>${price:.2f}</b>")
    except:
        bot.send_message(message.chat.id, "❌ Неверная цена")

@bot.message_handler(commands=["users"])
def cmd_users(message):
    ensure_user(message.from_user)
    if not is_admin(message.from_user.id):
        return
    db = load_db()
    lines = ["👥 <b>Пользователи</b>"]
    for u in db["users"].values():
        tag = ("@" + u["username"]) if u.get("username") else "—"
        lines.append(f"• {tag} — <code>{u.get('id')}</code>")
    text = "\n".join(lines)
    if len(text) > 3500:
        for i in range(0, len(text), 3500):
            bot.send_message(message.chat.id, text[i:i+3500])
    else:
        bot.send_message(message.chat.id, text)

@bot.message_handler(content_types=["document"])
def upload(message):
    ensure_user(message.from_user)

    if not (is_admin(message.from_user.id) or has_sub(message.from_user.id)):
        bot.send_message(message.chat.id, "🔒 Нужна подписка. Открой Web-App и оформи.")
        return

    db = load_db()
    code = str(int(time.time()))  # уникально по времени

    db["files"][code] = {
        "file_id": message.document.file_id,
        "name": message.document.file_name,
        "type": message.document.mime_type or "",
        "sender": ("@" + message.from_user.username) if message.from_user.username else str(message.from_user.id),
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    }
    save_db(db)

    link = f"https://t.me/{BOT_USERNAME}?start=file_{code}"
    bot.send_message(message.chat.id, f"✅ Загружено\n🔗 <code>{link}</code>")

def run_bot():
    bot.infinity_polling(skip_pending=True)

# ========== WEB APP (Flask) ==========
app = Flask(__name__)

INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Hosting</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  body{margin:0;background:#0b0f17;color:#fff;font-family:system-ui;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:18px}
  .card{width:min(520px,100%);background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
  border-radius:22px;padding:18px}
  h1{margin:0 0 10px;font-size:18px}
  p{margin:8px 0;color:rgba(255,255,255,.75)}
  .row{margin-top:12px;padding:12px;border-radius:16px;border:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.12)}
  button,a.btn{display:block;width:100%;text-align:center;text-decoration:none;margin-top:12px;
  padding:14px;border-radius:16px;border:0;background:#2ea6ff;color:#fff;font-weight:700;font-size:16px}
  a.btn{box-sizing:border-box}
  .secondary{background:transparent;border:1px solid rgba(255,255,255,.18)}
  code{font-family:ui-monospace,Menlo,Consolas,monospace}
</style>
</head>
<body>
<div class="card" id="app"></div>

<script>
  const tg = window.Telegram?.WebApp;
  if (tg) tg.expand();

  const params = new URLSearchParams(location.search);
  const file = params.get("file");

  const BOT = "%BOT_USERNAME%";
  const PRICE = "%PRICE%";

  const el = document.getElementById("app");

  if (file){
    el.innerHTML = `
      <h1>📄 Файл готов</h1>
      <p>Нажми «Скачать» — тебя перекинет в бота и он сразу отправит файл.</p>
      <div class="row">Код файла: <code>${file}</code></div>
      <a class="btn" href="https://t.me/${BOT}?start=dl_${file}">🔽 Скачать</a>
      <a class="btn secondary" href="https://t.me/${BOT}?start=file_${file}">↩️ Назад к карточке</a>
    `;
  } else {
    el.innerHTML = `
      <h1>💳 Подписка на 1 месяц</h1>
      <p>Цена: <b>$${PRICE}</b></p>
      <p>Нажми «Оплатить» — бот пришлёт ссылку на CryptoBot.</p>
      <a class="btn" href="https://t.me/${BOT}?start=sub">Оплатить</a>
    `;
  }
</script>
</body>
</html>"""

@app.get("/")
def index():
    html = INDEX_HTML.replace("%BOT_USERNAME%", BOT_USERNAME).replace("%PRICE%", f"{SUB_PRICE_USD:.2f}")
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.get("/health")
def health():
    return jsonify({"ok": True})

# ========== ENTRY ==========
if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
