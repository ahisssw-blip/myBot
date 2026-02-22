#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║         بوت الاشتراكات الاحترافي - النسخة المتطورة      ║
║         تطوير: Manus AI  |  v2.0 Professional           ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import json
import base64
import sqlite3
import logging
import asyncio
import datetime
from functools import wraps
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─────────────────────────────────────────────
#  تحميل الإعدادات من ملف config.json
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

CFG = load_config()

TOKEN        = CFG["TOKEN"]
ADMIN_ID     = int(CFG["ADMIN_ID"])
CHANNEL_ID   = int(CFG["CHANNEL_ID"])
CHANNEL_LINK = CFG["CHANNEL_LINK"]
SUPPORT      = CFG["SUPPORT"]
WALLETS      = CFG["WALLETS"]
SUBS         = CFG["SUBSCRIPTIONS"]
BTNS         = CFG["BUTTONS"]
REF_PTS      = int(CFG.get("REFERRAL_POINTS", 5))

# ─────────────────────────────────────────────
#  إعداد السجل (Logging)
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CHANNELS_FILE = Path(__file__).parent / "channels.json"

def load_channels():
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("channels", [])

# ─────────────────────────────────────────────
#  قاعدة البيانات SQLite
# ─────────────────────────────────────────────
DB_PATH = BASE_DIR / "database.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            last_name   TEXT,
            phone       TEXT,
            points      INTEGER DEFAULT 0,
            referred_by INTEGER,
            join_date   TEXT,
            last_seen   TEXT,
            is_banned   INTEGER DEFAULT 0,
            sub_status  TEXT DEFAULT 'none',
            sub_type    TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            sub_type    TEXT,
            pay_method  TEXT,
            pay_code    TEXT,
            phone       TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS messages_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            username    TEXT,
            first_name  TEXT,
            message     TEXT,
            msg_type    TEXT DEFAULT 'text',
            created_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS broadcast_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER,
            message     TEXT,
            target      TEXT,
            sent_count  INTEGER DEFAULT 0,
            fail_count  INTEGER DEFAULT 0,
            created_at  TEXT
        );
    """)
    conn.commit()
    conn.close()
    logger.info("✅ قاعدة البيانات جاهزة")

# ─────────────────────────────────────────────
#  دوال قاعدة البيانات
# ─────────────────────────────────────────────
def db_get_user(user_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def db_upsert_user(user, referred_by=None):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO users (user_id,username,first_name,last_name,join_date,last_seen,referred_by) VALUES (?,?,?,?,?,?,?)",
            (user.id, user.username or "", user.first_name or "", user.last_name or "", now, now, referred_by)
        )
        conn.commit()
        conn.close()
        return True   # مستخدم جديد
    else:
        conn.execute(
            "UPDATE users SET username=?,first_name=?,last_name=?,last_seen=? WHERE user_id=?",
            (user.username or "", user.first_name or "", user.last_name or "", now, user.id)
        )
        conn.commit()
        conn.close()
        return False  # مستخدم موجود

def db_get_points(user_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["points"] if row else 0

def db_add_points(user_id: int, pts: int):
    conn = get_db()
    conn.execute("UPDATE users SET points=points+? WHERE user_id=?", (pts, user_id))
    conn.commit()
    conn.close()

def db_all_users() -> list:
    conn = get_db()
    rows = conn.execute("SELECT * FROM users WHERE is_banned=0").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_count_users() -> int:
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n

def db_log_message(user_id, username, first_name, message, msg_type="text"):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO messages_log (user_id,username,first_name,message,msg_type,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, username or "", first_name or "", message, msg_type, now)
    )
    conn.commit()
    conn.close()

def db_save_payment(user_id, sub_type, pay_method, pay_code, phone=""):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO payments (user_id,sub_type,pay_method,pay_code,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, sub_type, pay_method, pay_code, phone, now, now)
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return pid

def db_update_payment_status(payment_id, status):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute("UPDATE payments SET status=?,updated_at=? WHERE id=?", (status, now, payment_id))
    conn.commit()
    conn.close()

def db_update_sub_status(user_id, sub_type):
    conn = get_db()
    conn.execute("UPDATE users SET sub_status='active',sub_type=? WHERE user_id=?", (sub_type, user_id))
    conn.commit()
    conn.close()

def db_ban_user(user_id: int, ban: bool = True):
    conn = get_db()
    conn.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if ban else 0, user_id))
    conn.commit()
    conn.close()

def db_search_user(query: str) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ?",
        (f"%{query}%", f"%{query}%", f"%{query}%")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_recent_messages(limit=20) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_get_stats() -> dict:
    conn = get_db()
    total   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active  = conn.execute("SELECT COUNT(*) FROM users WHERE sub_status='active'").fetchone()[0]
    banned  = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM payments WHERE status='pending'").fetchone()[0]
    today   = datetime.date.today().isoformat()
    new_today = conn.execute("SELECT COUNT(*) FROM users WHERE join_date LIKE ?", (f"{today}%",)).fetchone()[0]
    conn.close()
    return {"total": total, "active": active, "banned": banned, "pending": pending, "new_today": new_today}

# ─────────────────────────────────────────────
#  مساعدات عامة
# ─────────────────────────────────────────────
def encode_ref(user_id: int) -> str:
    return base64.urlsafe_b64encode(str(user_id).encode()).decode().rstrip("=")

def decode_ref(code: str) -> int | None:
    try:
        padded = code + "=" * (4 - len(code) % 4)
        return int(base64.urlsafe_b64decode(padded).decode())
    except Exception:
        return None

async def is_subscribed(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("member", "creator", "administrator")
    except TelegramError:
        return False

def admin_only(func):
    """ديكوريتر: يقيّد الأمر للأدمن فقط"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("🚫 هذا الأمر للمشرف فقط.")
            return
        return await func(update, context)
    return wrapper

def build_back_btn(cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(BTNS["btn_back"], callback_data=cb)]])

async def safe_send(bot, chat_id, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, **kwargs)
    except TelegramError as e:
        logger.warning(f"فشل الإرسال لـ {chat_id}: {e}")
        return None

# ─────────────────────────────────────────────
#  نصوص الدفع (مولّدة ديناميكياً من config)
# ─────────────────────────────────────────────
def get_sham_text(sub_key: str) -> str:
    s = SUBS[sub_key]
    return (
        f"💳 *شام كاش — {s['label']}*\n\n"
        f"قم بتحويل مبلغ *{s['price_usd']}$* أو *{s['price_syp']} ل.س جديدة* على:\n\n"
        f"_(انقر فوق عنوان المحفظة للنسخ المباشر)_\n"
        f"`{WALLETS['sham_cash']}`\n\n"
        f"اسم الحساب: *{WALLETS['sham_account_name']}*\n\n"
        f"ثم أدخل رقم العملية 👇\n\n"
        f"يمكنك الضغط على /start لإعادة البدء"
    )

def get_syriatel_text(sub_key: str) -> str:
    s = SUBS[sub_key]
    return (
        f"📱 *سيريتل كاش — {s['label']}*\n\n"
        f"قم بتحويل مبلغ *{s['price_syp']} ل.س جديدة* على:\n\n"
        f"_(انقر فوق الكود للنسخ المباشر)_\n"
        f"`{WALLETS['syriatel_cash']}`\n"
        f"بطريقة التحويل اليدوي\n\n"
        f"ثم أدخل رقم العملية 👇\n\n"
        f"يمكنك الضغط على /start لإعادة البدء"
    )

def get_usdt_text(sub_key: str) -> str:
    s = SUBS[sub_key]
    return (
        f"🪙 *USDT — {s['label']}*\n\n"
        f"قم بتحويل مبلغ *{s['price_usd']}$* على:\n\n"
        f"_(انقر فوق عنوان المحفظة للنسخ المباشر)_\n\n"
        f"🔹 BEP20:\n`{WALLETS['usdt_bep20']}`\n\n"
        f"🔹 TRC20:\n`{WALLETS['usdt_trc20']}`\n\n"
        f"ثم أدخل *(TxID)* العملية 👇\n\n"
        f"_(يمكنك التحويل المباشر خارج السلسلة على بينانس - سي والت - كوين اكس - تراست والت - TON وأي محفظة أخرى وذلك بالتواصل المباشر مع الدعم)_\n\n"
        f"يمكنك الضغط على /start لإعادة البدء"
    )

def get_other_text() -> str:
    return (
        f"⚠️ *طرق دفع أخرى*\n\n"
        f"تواصل مع الدعم لأي وسيلة أخرى.\n"
        f"نستطيع تأمين أي طريقة دفع من كل أنحاء العالم 🔥👌\n\n"
        f"👉 [الحساب 1 — واتساب](https://wa.me/{SUPPORT['whatsapp1']})\n"
        f"👉 [الحساب 2 — واتساب](https://wa.me/{SUPPORT['whatsapp2']})"
    )

# ─────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # استخراج المُحيل من الرابط
    referrer_id = None
    if args:
        referrer_id = decode_ref(args[0])
        if referrer_id == user.id:
            referrer_id = None

    is_new = db_upsert_user(user, referred_by=referrer_id)

    # التحقق من الاشتراك في القناة
    subscribed = await is_subscribed(context.bot, user.id)
    if not subscribed:
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 اشترك في قناة البوت 🔥", url=CHANNEL_LINK)
        ]])
        await update.message.reply_text(
            "⚠️ *للمتابعة يجب عليك الاشتراك في قناة البوت أولاً* 🚫\n\n"
            "بعد الاشتراك اضغط /start مجدداً 👇",
            reply_markup=kbd,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # نقاط الإحالة للمستخدم الجديد
    if is_new and referrer_id:
        ref_user = db_get_user(referrer_id)
        if ref_user:
            db_add_points(user.id, REF_PTS)
            db_add_points(referrer_id, REF_PTS)
            await safe_send(
                context.bot, referrer_id,
                text=f"🔔 *مبروك!* دخل شخص جديد برابطك وربحتَ *{REF_PTS} نقاط* 🎁",
                parse_mode=ParseMode.MARKDOWN
            )

    # إشعار الأدمن بمستخدم جديد
    if is_new:
        await safe_send(
            context.bot, ADMIN_ID,
            text=(
                f"🆕 *مستخدم جديد انضم للبوت!*\n"
                f"👤 الاسم: {user.first_name} {user.last_name or ''}\n"
                f"🔖 المعرف: @{user.username or 'لا يوجد'}\n"
                f"🆔 الآيدي: `{user.id}`\n"
                f"📅 التاريخ: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

    # رسالة الترحيب
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS["btn_next"], callback_data="main_menu")],
        [InlineKeyboardButton("قـنـواتـنـا الـعـامـة", callback_data="public_channels")]
    ])
    await update.message.reply_text(
        f"✨ *أهلاً وسهلاً {user.first_name}!*\n\n"
        f"تم التحقق من اشتراكك في قناة البوت بنجاح ✅\n\n"
        f"\n\n"
        f"اضغط على زر *الــتــالــي* للدخول إلى خيارات البوت 👇",
        reply_markup=kbd,
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  القائمة الرئيسية
# ─────────────────────────────────────────────
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS["btn_subscriptions"], callback_data="sub_menu")],
        [InlineKeyboardButton(BTNS["btn_referrals"],     callback_data="earn_menu")],
        [InlineKeyboardButton(BTNS["btn_support"],       callback_data="support")],
        [InlineKeyboardButton(BTNS["btn_end"],           callback_data="end")]
    ])
    text = (
        f"*مــرحــبــاً {user.first_name}* ✨\n\n"
        f"في البوت الخاص بالاشتراك بقنواتنا الخاصة ومجموعاتنا الخاصة 🔥\n\n"
        f"يرجى اختيار ما تريد:\n\n"
        f"1️⃣  للاشتراك 🔥\n"
        f"2️⃣  الإحالات والربح 🎁\n"
        f"3️⃣  للتواصل المباشر معنا ❤️\n"
        f"4️⃣  لإنهاء الجلسة 🙁"
    )
    q = update.callback_query
    if q:
        await q.edit_message_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  قائمة الاشتراكات
# ─────────────────────────────────────────────
async def show_sub_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS["btn_sub1"], callback_data="pay_Sub1")],
        [InlineKeyboardButton(BTNS["btn_sub2"], callback_data="pay_Sub2")],
        [InlineKeyboardButton(BTNS["btn_vip"],  callback_data="pay_VIP")],
        [InlineKeyboardButton(BTNS["btn_back"], callback_data="main_menu")]
    ])
    await update.callback_query.edit_message_text(
        "💯🔥🎁 *اختر فئة الاشتراك المطلوبة* 🎁🔥💯\n\n"
        "_(تفاصيل كل فئة اشتراك في الداخل)_",
        reply_markup=kbd,
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  قائمة طرق الدفع لاشتراك معين
# ─────────────────────────────────────────────
async def show_pay_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_key: str):
    context.user_data["sub_type"] = sub_key
    s = SUBS[sub_key]
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS["btn_sham"],     callback_data="meth_sham"),
         InlineKeyboardButton(BTNS["btn_syriatel"], callback_data="meth_syria")],
        [InlineKeyboardButton(BTNS["btn_usdt"],     callback_data="meth_usdt")],
        [InlineKeyboardButton(BTNS["btn_other"],    callback_data="meth_other"),
         InlineKeyboardButton(BTNS["btn_details"],  callback_data="details")],
        [InlineKeyboardButton(BTNS["btn_back"],     callback_data="sub_menu")]
    ])
    await update.callback_query.edit_message_text(
        f"💎 *الفئة:* {s['label']}\n"
        f"💰 *التكلفة:* {s['price_usd']}$ / {s['price_syp']} ل.س جديدة\n\n"
        f"اختر وسيلة الدفع 👇",
        reply_markup=kbd,
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  قائمة الإحالات
# ─────────────────────────────────────────────
async def show_earn_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS["btn_ref_link"],  callback_data="ref_link"),
         InlineKeyboardButton(BTNS["btn_my_points"], callback_data="my_points")],
        [InlineKeyboardButton(BTNS["btn_redeem"],    callback_data="redeem"),
         InlineKeyboardButton(BTNS["btn_ref_info"],  callback_data="ref_info")],
        [InlineKeyboardButton(BTNS["btn_back"],      callback_data="main_menu")]
    ])
    await update.callback_query.edit_message_text(
        "🎁 *قسم الإحالات والربح*\n\n"
        f"ادعُ أصدقاءك واربح *{REF_PTS} نقاط* عن كل شخص يسجّل عبر رابطك! 💰",
        reply_markup=kbd,
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  معالج الضغطات (Callback Handler)
# ─────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    # ── فحص الاشتراك في القناة ──
    if data not in ("end",):
        subscribed = await is_subscribed(context.bot, user.id)
        if not subscribed:
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 اشترك في القناة 🔥", url=CHANNEL_LINK)
            ]])
            await query.edit_message_text(
                "⚠️ *يجب الاشتراك في قناة البوت أولاً* 🚫\n\nبعد الاشتراك اضغط /start",
                reply_markup=kbd,
                parse_mode=ParseMode.MARKDOWN
            )
            return

    # ── التوجيه ──
    if data == "main_menu":
        await show_main_menu(update, context)

    elif data == "sub_menu":
        await show_sub_menu(update, context)

    elif data == "earn_menu":
        await show_earn_menu(update, context)

    elif data == "ref_link":
        bot_info = await context.bot.get_me()
        ref_code = encode_ref(user.id)
        link = f"https://t.me/{bot_info.username}?start={ref_code}"
        await query.message.reply_text(
            f"🔗 *رابط الإحالة الخاص بك:*\n\n`{link}`\n\n"
            f"قم بنسخه وأرسله لأصدقائك 👏\n"
            f"كل شخص يسجّل عبر رابطك تربح *{REF_PTS} نقاط* 💎",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "my_points":
        pts = db_get_points(user.id)
        await query.message.reply_text(
            f"✨ *رصيدك الحالي:* `{pts}` نقطة 💰",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "ref_info":
        await query.message.reply_text(
            f"📜 *نظام الإحالات:*\n\n"
            f"• اربح *{REF_PTS} نقاط* عن كل شخص يشترك عبر رابطك.\n"
            f"• الشخص الجديد يحصل أيضاً على *{REF_PTS} نقاط* هدية.\n"
            f"• يمكن استبدال النقاط كالتالي:.\n\n"
            f"• مئة نقطة:اشتراك أول , او عشرة دولار.\n\n"
            f"• مئتان نقطة:اشتراك ثاني , او عشرون دولار.\n\n"
            f"• ثلاثمئة نقطة:اشتراك VIP الـمـمـيـز أو ثلاثون دولار.\n\n"
            f"شارك رابطك الآن وابدأ بالربح! 🚀",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "public_channels":
        channels = load_channels()  # استدعاء القنوات من channels.json
        kbd_channels = InlineKeyboardMarkup([
            [InlineKeyboardButton(ch["name"], url=ch["url"])] for ch in channels
        ])

        await query.message.reply_text(
        "📺 *قنواتنا العامة:*",
        reply_markup=kbd_channels,
        parse_mode=ParseMode.MARKDOWN
    )
    
    elif data == "redeem":
        await query.message.reply_text(
            "🎁 *استبدال النقاط:*\n\n"
            "تواصل مع الدعم لاستبدال نقاطك والحصول على المكافئة الخاصة بك 💬",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("pay_"):
        sub_key = data.replace("pay_", "")
        if sub_key in SUBS:
            await show_pay_menu(update, context, sub_key)

    elif data == "support":
        kbd = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💬 {SUPPORT['label1']} — واتساب", url=f"https://wa.me/{SUPPORT['whatsapp1']}")],
            [InlineKeyboardButton(f"💬 {SUPPORT['label2']} — واتساب", url=f"https://wa.me/{SUPPORT['whatsapp2']}")],
            [InlineKeyboardButton(BTNS["btn_back"], callback_data="main_menu")]
        ])
        await query.edit_message_text(
            "📞 *التواصل المباشر مع الدعم الفني*\n\n"
            "اضغط على أحد الأزرار أدناه للتواصل عبر واتساب 👇\n\n"
            "سيتم الرد عليك خلال وقت قصير ❤️",
            reply_markup=kbd,
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "details":
        sub_key = context.user_data.get("sub_type", "Sub1")
        back_cb = f"pay_{sub_key}"
        details_text = SUBS.get(sub_key, {}).get("details", "لا توجد تفاصيل.")
        await query.edit_message_text(
            details_text,
            reply_markup=build_back_btn(back_cb),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("meth_"):
        sub_key = context.user_data.get("sub_type", "Sub1")
        method  = data.replace("meth_", "")
        back_cb = f"pay_{sub_key}"
        back_kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع لطرق الدفع", callback_data=back_cb)]])

        context.user_data["waiting_code"]  = True
        context.user_data["pay_method"]    = method

        if method == "sham":
            sham_img = BASE_DIR / "sham.jpg"
            try:
                await query.message.reply_photo(
                    photo=open(sham_img, "rb"),
                    caption=get_sham_text(sub_key),
                    parse_mode=ParseMode.MARKDOWN
                )
                await query.message.reply_text("استخدم الزر للعودة 👇", reply_markup=back_kbd)
            except Exception:
                await query.edit_message_text(get_sham_text(sub_key), reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)

        elif method == "syria":
            await query.edit_message_text(get_syriatel_text(sub_key), reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)

        elif method == "usdt":
            await query.edit_message_text(get_usdt_text(sub_key), reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)

        elif method == "other":
            await query.edit_message_text(get_other_text(), reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("adm_"):
        # أزرار القبول والرفض للأدمن
        parts = data.split("_")
        action    = parts[1]
        target_id = int(parts[2])
        pay_id    = int(parts[3]) if len(parts) > 3 else 0

        if user.id != ADMIN_ID:
            await query.answer("🚫 غير مصرح لك!", show_alert=True)
            return

        if action == "ok":
            sub_key = context.bot_data.get(f"pending_{target_id}_sub", "")
            db_update_sub_status(target_id, sub_key)
            if pay_id:
                db_update_payment_status(pay_id, "approved")
            await safe_send(
                context.bot, target_id,
                text="✅ *تهانينا! تم التأكد من الدفع وتفعيل اشتراكك بنجاح* 🎉\n\nشكراً لثقتك بنا! سيتم إضافتك للقنوات والمجموعات الخاصة خلال دقائق.",
                parse_mode=ParseMode.MARKDOWN
            )
            await query.edit_message_text(
                query.message.text + "\n\n🟢 ✅ *تم القبول وتفعيل الحساب*",
                parse_mode=ParseMode.MARKDOWN
            )

        elif action == "no":
            if pay_id:
                db_update_payment_status(pay_id, "rejected")
            await safe_send(
                context.bot, target_id,
                text="❌ *نعتذر، تم رفض طلبك.*\n\nيرجى التأكد من كود التحويل أو التواصل مع الدعم للمساعدة.",
                parse_mode=ParseMode.MARKDOWN
            )
            await query.edit_message_text(
                query.message.text + "\n\n🔴 ❌ *تم الرفض وتنبيه المستخدم*",
                parse_mode=ParseMode.MARKDOWN
            )

    elif data == "end":
        await query.edit_message_text(
            "👋 *تم إغلاق الجلسة.*\n\nشكراً لك! يمكنك العودة في أي وقت بالضغط على /start",
            parse_mode=ParseMode.MARKDOWN
        )

# ─────────────────────────────────────────────
#  معالج الرسائل النصية
# ─────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    text    = update.message.text
    user_id = user.id

    # تسجيل الرسالة في قاعدة البيانات
    db_log_message(user_id, user.username, user.first_name, text)

    # إرسال نسخة للأدمن (نظام المراقبة)
    if user_id != ADMIN_ID:
        await safe_send(
            context.bot, ADMIN_ID,
            text=(
                f"👁 *رسالة واردة*\n"
                f"👤 {user.first_name} {user.last_name or ''}\n"
                f"🔖 @{user.username or 'لا يوجد'}\n"
                f"🆔 `{user_id}`\n"
                f"💬 {text}"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── أوامر الأدمن ──
    if user_id == ADMIN_ID:
        await handle_admin_text(update, context, text)
        return

    # ── كود التحويل من المستخدم ──
    if context.user_data.get("waiting_code") and len(text) >= 5:
        context.user_data["pay_code"]     = text
        context.user_data["waiting_code"] = False

        kbd = ReplyKeyboardMarkup(
            [[KeyboardButton("✅ تــأكــيــد ✅", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text(
            "✅ *تم استلام كود التحويل*\n\n"
            "الآن اضغط على زر تـأكـيـد في الأسفل لإتمام الطلب ، سوف يصلك الرد خلال وقت قصير 👇",
            reply_markup=kbd,
            parse_mode=ParseMode.MARKDOWN
        )

# ─────────────────────────────────────────────
#  أوامر الأدمن النصية
# ─────────────────────────────────────────────
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    msg = update.message

    # ── رد على مستخدم: رد [id] [نص] ──
    if text.startswith("رد "):
        parts = text.split(" ", 2)
        if len(parts) == 3:
            try:
                await context.bot.send_message(
                    chat_id=int(parts[1]),
                    text=f"💬 *رسالة من الإدارة:*\n\n{parts[2]}",
                    parse_mode=ParseMode.MARKDOWN
                )
                await msg.reply_text(f"✅ تم إرسال ردك للمستخدم `{parts[1]}`", parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                await msg.reply_text(f"❌ خطأ: {e}\n\nالصيغة: رد [الآيدي] [النص]")
        else:
            await msg.reply_text("❌ الصيغة الصحيحة: رد [الآيدي] [النص]")
        return

    # ── بحث عن مستخدم: بحث [id أو اسم] ──
    if text.startswith("بحث "):
        query_str = text.replace("بحث ", "").strip()
        results = db_search_user(query_str)
        if not results:
            await msg.reply_text("🔍 لم يُعثر على أي مستخدم.")
            return
        lines = []
        for u in results[:10]:
            lines.append(
                f"👤 {u['first_name']} {u['last_name'] or ''}\n"
                f"🔖 @{u['username'] or 'لا يوجد'}\n"
                f"🆔 `{u['user_id']}`\n"
                f"💰 نقاط: {u['points']}\n"
                f"📅 انضم: {u['join_date'][:10] if u['join_date'] else 'غير معروف'}\n"
                f"🏷 الاشتراك: {u['sub_type'] or 'لا يوجد'}\n"
                f"─────────────"
            )
        await msg.reply_text("🔍 *نتائج البحث:*\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # ── حظر مستخدم: حظر [id] ──
    if text.startswith("حظر "):
        uid = text.replace("حظر ", "").strip()
        try:
            db_ban_user(int(uid), True)
            await msg.reply_text(f"🚫 تم حظر المستخدم `{uid}`", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await msg.reply_text(f"❌ خطأ: {e}")
        return

    # ── رفع الحظر: رفع حظر [id] ──
    if text.startswith("رفع حظر "):
        uid = text.replace("رفع حظر ", "").strip()
        try:
            db_ban_user(int(uid), False)
            await msg.reply_text(f"✅ تم رفع الحظر عن المستخدم `{uid}`", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await msg.reply_text(f"❌ خطأ: {e}")
        return

    # ── إضافة نقاط: نقاط [id] [عدد] ──
    if text.startswith("نقاط "):
        parts = text.split()
        if len(parts) == 3:
            try:
                db_add_points(int(parts[1]), int(parts[2]))
                await msg.reply_text(f"✅ تمت إضافة {parts[2]} نقطة للمستخدم `{parts[1]}`", parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                await msg.reply_text(f"❌ خطأ: {e}")
        else:
            await msg.reply_text("❌ الصيغة: نقاط [الآيدي] [العدد]")
        return

    # ── رسالة جماعية: بث [نص] ──
    if text.startswith("بث "):
        broadcast_text = text.replace("بث ", "").strip()
        users = db_all_users()
        sent = 0
        failed = 0
        await msg.reply_text(f"📡 جارٍ إرسال الرسالة لـ {len(users)} مستخدم...")
        for u in users:
            result = await safe_send(
                context.bot, u["user_id"],
                text=f"📢 *رسالة من الإدارة:*\n\n{broadcast_text}",
                parse_mode=ParseMode.MARKDOWN
            )
            if result:
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(0.05)  # تجنب حد المعدل
        await msg.reply_text(f"✅ *اكتمل البث!*\n\n📤 تم الإرسال: {sent}\n❌ فشل: {failed}", parse_mode=ParseMode.MARKDOWN)
        return

    # ── إحصائيات ──
    if text == "الاحصائيات" or text == "إحصائيات":
        stats = db_get_stats()
        await msg.reply_text(
            f"📊 *إحصائيات البوت*\n\n"
            f"👥 إجمالي المستخدمين: `{stats['total']}`\n"
            f"🆕 جدد اليوم: `{stats['new_today']}`\n"
            f"✅ مشتركون نشطون: `{stats['active']}`\n"
            f"⏳ طلبات معلّقة: `{stats['pending']}`\n"
            f"🚫 محظورون: `{stats['banned']}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── نسخة احتياطية ──
    if text == "نسخة احتياطية" or text == "نسخ احتياطي":
        try:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=open(DB_PATH, "rb"),
                caption="📦 قاعدة البيانات الكاملة"
            )
        except Exception as e:
            await msg.reply_text(f"❌ خطأ: {e}")
        return

    # ── آخر الرسائل الواردة ──
    if text == "آخر الرسائل" or text == "الرسائل":
        logs = db_recent_messages(15)
        if not logs:
            await msg.reply_text("📭 لا توجد رسائل مسجّلة.")
            return
        lines = []
        for log in logs:
            lines.append(
                f"👤 {log['first_name']} | 🆔 `{log['user_id']}`\n"
                f"💬 {log['message'][:80]}\n"
                f"🕐 {log['created_at'][:16]}\n─────"
            )
        await msg.reply_text("📋 *آخر الرسائل:*\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # ── تحديث شام كاش ──
    if text.startswith("تحديث شام "):
        new_val = text.replace("تحديث شام ", "").strip()
        CFG["WALLETS"]["sham_cash"] = new_val
        WALLETS["sham_cash"] = new_val
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=2)
        await msg.reply_text(f"✅ تم تحديث محفظة شام كاش إلى:\n`{new_val}`", parse_mode=ParseMode.MARKDOWN)
        return

    # ── تحديث سيريتل ──
    if text.startswith("تحديث سيريتل "):
        new_val = text.replace("تحديث سيريتل ", "").strip()
        CFG["WALLETS"]["syriatel_cash"] = new_val
        WALLETS["syriatel_cash"] = new_val
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=2)
        await msg.reply_text(f"✅ تم تحديث رقم سيريتل كاش إلى:\n`{new_val}`", parse_mode=ParseMode.MARKDOWN)
        return

    # ── مساعدة الأدمن ──
    if text in ("مساعدة", "help", "/help"):
        await show_admin_help(msg)
        return

# ─────────────────────────────────────────────
#  /admin - لوحة التحكم
# ─────────────────────────────────────────────
@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db_get_stats()
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات",       callback_data="adm_stats"),
         InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="adm_users")],
        [InlineKeyboardButton("📋 آخر الرسائل",      callback_data="adm_msgs"),
         InlineKeyboardButton("⏳ الطلبات المعلّقة", callback_data="adm_pending")],
        [InlineKeyboardButton("📡 رسالة جماعية",     callback_data="adm_broadcast"),
         InlineKeyboardButton("💾 نسخة احتياطية",    callback_data="adm_backup")],
        [InlineKeyboardButton("❓ أوامر الأدمن",     callback_data="adm_help")]
    ])
    await update.message.reply_text(
        f"🛡 *لوحة تحكم المشرف*\n\n"
        f"👥 المستخدمون: `{stats['total']}`\n"
        f"🆕 جدد اليوم: `{stats['new_today']}`\n"
        f"✅ مشتركون نشطون: `{stats['active']}`\n"
        f"⏳ طلبات معلّقة: `{stats['pending']}`\n\n"
        f"اختر ما تريد 👇",
        reply_markup=kbd,
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  معالج ضغطات الأدمن
# ─────────────────────────────────────────────
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if user.id != ADMIN_ID:
        await query.answer("🚫 غير مصرح لك!", show_alert=True)
        return

    if data == "adm_stats":
        stats = db_get_stats()
        await query.edit_message_text(
            f"📊 *إحصائيات البوت*\n\n"
            f"👥 إجمالي المستخدمين: `{stats['total']}`\n"
            f"🆕 جدد اليوم: `{stats['new_today']}`\n"
            f"✅ مشتركون نشطون: `{stats['active']}`\n"
            f"⏳ طلبات معلّقة: `{stats['pending']}`\n"
            f"🚫 محظورون: `{stats['banned']}`",
            reply_markup=build_back_btn("adm_panel"),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "adm_users":
        users = db_all_users()
        lines = []
        for u in users[:20]:
            lines.append(f"• {u['first_name']} | 🆔 `{u['user_id']}` | نقاط: {u['points']}")
        text = "👥 *آخر 20 مستخدم:*\n\n" + "\n".join(lines) if lines else "لا يوجد مستخدمون."
        await query.edit_message_text(text, reply_markup=build_back_btn("adm_panel"), parse_mode=ParseMode.MARKDOWN)

    elif data == "adm_msgs":
        logs = db_recent_messages(10)
        lines = []
        for log in logs:
            lines.append(f"👤 {log['first_name']} `{log['user_id']}`\n💬 {log['message'][:60]}\n🕐 {log['created_at'][:16]}\n─────")
        text = "📋 *آخر الرسائل:*\n\n" + "\n".join(lines) if lines else "لا توجد رسائل."
        await query.edit_message_text(text, reply_markup=build_back_btn("adm_panel"), parse_mode=ParseMode.MARKDOWN)

    elif data == "adm_backup":
        try:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=open(DB_PATH, "rb"),
                caption="📦 قاعدة البيانات الكاملة"
            )
            await query.answer("✅ تم إرسال النسخة الاحتياطية!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ خطأ: {e}", show_alert=True)

    elif data == "adm_help":
        await show_admin_help_cb(query)

    elif data == "adm_panel":
        stats = db_get_stats()
        kbd = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 الإحصائيات",       callback_data="adm_stats"),
             InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="adm_users")],
            [InlineKeyboardButton("📋 آخر الرسائل",      callback_data="adm_msgs"),
             InlineKeyboardButton("⏳ الطلبات المعلّقة", callback_data="adm_pending")],
            [InlineKeyboardButton("📡 رسالة جماعية",     callback_data="adm_broadcast"),
             InlineKeyboardButton("💾 نسخة احتياطية",    callback_data="adm_backup")],
            [InlineKeyboardButton("❓ أوامر الأدمن",     callback_data="adm_help")]
        ])
        await query.edit_message_text(
            f"🛡 *لوحة تحكم المشرف*\n\n"
            f"👥 المستخدمون: `{stats['total']}`\n"
            f"🆕 جدد اليوم: `{stats['new_today']}`\n"
            f"✅ مشتركون نشطون: `{stats['active']}`\n"
            f"⏳ طلبات معلّقة: `{stats['pending']}`",
            reply_markup=kbd,
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "adm_pending":
        conn = get_db()
        rows = conn.execute(
            "SELECT p.*, u.first_name, u.username FROM payments p "
            "LEFT JOIN users u ON p.user_id=u.user_id "
            "WHERE p.status='pending' ORDER BY p.created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        if not rows:
            await query.edit_message_text("✅ لا توجد طلبات معلّقة.", reply_markup=build_back_btn("adm_panel"))
            return
        for row in rows:
            row = dict(row)
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ قبول وتفعيل", callback_data=f"adm_ok_{row['user_id']}_{row['id']}"),
                InlineKeyboardButton("❌ رفض الطلب",  callback_data=f"adm_no_{row['user_id']}_{row['id']}")
            ]])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⏳ *طلب معلّق*\n"
                    f"👤 {row['first_name']} | @{row['username'] or 'لا يوجد'}\n"
                    f"🆔 `{row['user_id']}`\n"
                    f"💎 الاشتراك: {row['sub_type']}\n"
                    f"💳 طريقة الدفع: {row['pay_method']}\n"
                    f"🔑 الكود: `{row['pay_code']}`\n"
                    f"📅 {row['created_at'][:16]}"
                ),
                reply_markup=kbd,
                parse_mode=ParseMode.MARKDOWN
            )
        await query.answer("✅ تم إرسال الطلبات المعلّقة!", show_alert=True)

async def show_admin_help(msg):
    help_text = (
        "🛡 *أوامر لوحة التحكم — الأدمن*\n\n"
        "📩 *الرسائل والردود:*\n"
        "`رد [id] [نص]` — إرسال رسالة لمستخدم\n"
        "`بث [نص]` — رسالة جماعية لجميع المستخدمين\n\n"
        "🔍 *البحث والإدارة:*\n"
        "`بحث [id أو اسم]` — البحث عن مستخدم\n"
        "`حظر [id]` — حظر مستخدم\n"
        "`رفع حظر [id]` — رفع الحظر\n"
        "`نقاط [id] [عدد]` — إضافة نقاط\n\n"
        "📊 *الإحصائيات والنسخ:*\n"
        "`الاحصائيات` — عرض إحصائيات البوت\n"
        "`نسخة احتياطية` — إرسال قاعدة البيانات\n"
        "`آخر الرسائل` — عرض آخر رسائل المستخدمين\n\n"
        "⚙️ *تحديث الإعدادات:*\n"
        "`تحديث شام [رقم]` — تحديث محفظة شام كاش\n"
        "`تحديث سيريتل [رقم]` — تحديث رقم سيريتل\n\n"
        "🔧 *لوحة التحكم:* /admin"
    )
    await msg.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def show_admin_help_cb(query):
    help_text = (
        "🛡 *أوامر لوحة التحكم — الأدمن*\n\n"
        "📩 *الرسائل والردود:*\n"
        "`رد [id] [نص]` — إرسال رسالة لمستخدم\n"
        "`بث [نص]` — رسالة جماعية لجميع المستخدمين\n\n"
        "🔍 *البحث والإدارة:*\n"
        "`بحث [id أو اسم]` — البحث عن مستخدم\n"
        "`حظر [id]` — حظر مستخدم\n"
        "`رفع حظر [id]` — رفع الحظر\n"
        "`نقاط [id] [عدد]` — إضافة نقاط\n\n"
        "📊 *الإحصائيات والنسخ:*\n"
        "`الاحصائيات` — عرض إحصائيات البوت\n"
        "`نسخة احتياطية` — إرسال قاعدة البيانات\n"
        "`آخر الرسائل` — عرض آخر رسائل المستخدمين\n\n"
        "⚙️ *تحديث الإعدادات:*\n"
        "`تحديث شام [رقم]` — تحديث محفظة شام كاش\n"
        "`تحديث سيريتل [رقم]` — تحديث رقم سيريتل\n\n"
        "🔧 *لوحة التحكم:* /admin"
    )
    await query.edit_message_text(help_text, reply_markup=build_back_btn("adm_panel"), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  معالج جهة الاتصال (إتمام الطلب)
# ─────────────────────────────────────────────
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    contact = update.message.contact
    sub_key = context.user_data.get("sub_type", "غير محدد")
    pay_method = context.user_data.get("pay_method", "غير محدد")
    pay_code   = context.user_data.get("pay_code", "غير مُدخل")

    # حفظ الطلب في قاعدة البيانات
    pay_id = db_save_payment(user.id, sub_key, pay_method, pay_code, contact.phone_number)
    context.bot_data[f"pending_{user.id}_sub"] = sub_key

    # تحديث رقم الهاتف في بيانات المستخدم
    conn = get_db()
    conn.execute("UPDATE users SET phone=? WHERE user_id=?", (contact.phone_number, user.id))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎉 *تم استلام طلبك بنجاح!*\n\n"
        "سيتم مراجعة الدفع وتفعيل حسابك خلال دقائق ⚡\n\n"
        "شكراً لثقتك بنا ❤️",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )

    # إشعار الأدمن
    s = SUBS.get(sub_key, {})
    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول وتفعيل", callback_data=f"adm_ok_{user.id}_{pay_id}"),
        InlineKeyboardButton("❌ رفض الطلب",  callback_data=f"adm_no_{user.id}_{pay_id}")
    ]])
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🔔 *طلب تفعيل جديد!*\n\n"
            f"👤 الاسم: {contact.first_name} {contact.last_name or ''}\n"
            f"📱 الرقم: `{contact.phone_number}`\n"
            f"🔖 المعرف: @{user.username or 'لا يوجد'}\n"
            f"🆔 الآيدي: `{user.id}`\n"
            f"💎 الاشتراك: {s.get('label', sub_key)}\n"
            f"💳 طريقة الدفع: {pay_method}\n"
            f"🔑 كود التحويل: `{pay_code}`\n"
            f"🆔 رقم الطلب: #{pay_id}"
        ),
        reply_markup=kbd,
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  /users - قائمة المستخدمين (أدمن)
# ─────────────────────────────────────────────
@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db_get_stats()
    users = db_all_users()
    lines = []
    for u in users[:30]:
        lines.append(f"• {u['first_name']} | 🆔 `{u['user_id']}` | نقاط: {u['points']} | {u['sub_type'] or 'لا اشتراك'}")
    text = (
        f"👥 *قائمة المستخدمين* (إجمالي: {stats['total']})\n\n"
        + "\n".join(lines)
        + ("\n\n_(يُعرض أول 30 مستخدم فقط)_" if len(users) > 30 else "")
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  /stats - إحصائيات (أدمن)
# ─────────────────────────────────────────────
@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db_get_stats()
    await update.message.reply_text(
        f"📊 *إحصائيات البوت*\n\n"
        f"👥 إجمالي المستخدمين: `{stats['total']}`\n"
        f"🆕 جدد اليوم: `{stats['new_today']}`\n"
        f"✅ مشتركون نشطون: `{stats['active']}`\n"
        f"⏳ طلبات معلّقة: `{stats['pending']}`\n"
        f"🚫 محظورون: `{stats['banned']}`",
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  /broadcast - رسالة جماعية (أدمن)
# ─────────────────────────────────────────────
@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "📡 *الرسائل الجماعية*\n\n"
            "الصيغة: `/broadcast [النص]`\n\n"
            "أو أرسل: `بث [النص]`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    broadcast_text = " ".join(context.args)
    users = db_all_users()
    sent = 0
    failed = 0
    await update.message.reply_text(f"📡 جارٍ الإرسال لـ {len(users)} مستخدم...")
    for u in users:
        result = await safe_send(
            context.bot, u["user_id"],
            text=f"📢 *رسالة من الإدارة:*\n\n{broadcast_text}",
            parse_mode=ParseMode.MARKDOWN
        )
        if result:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(
        f"✅ *اكتمل البث!*\n\n📤 تم الإرسال: {sent}\n❌ فشل: {failed}",
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  /send - إرسال لمستخدم بعينه (أدمن)
# ─────────────────────────────────────────────
@admin_only
async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "📩 الصيغة: `/send [الآيدي] [النص]`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    target_id = context.args[0]
    text = " ".join(context.args[1:])
    try:
        await context.bot.send_message(
            chat_id=int(target_id),
            text=f"💬 *رسالة من الإدارة:*\n\n{text}",
            parse_mode=ParseMode.MARKDOWN
        )
        await update.message.reply_text(f"✅ تم الإرسال للمستخدم `{target_id}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ─────────────────────────────────────────────
#  /userinfo - معلومات مستخدم (أدمن)
# ─────────────────────────────────────────────
@admin_only
async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الصيغة: `/userinfo [الآيدي]`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = int(context.args[0])
    u = db_get_user(uid)
    if not u:
        await update.message.reply_text("❌ المستخدم غير موجود في قاعدة البيانات.")
        return
    await update.message.reply_text(
        f"👤 *معلومات المستخدم*\n\n"
        f"الاسم: {u['first_name']} {u['last_name'] or ''}\n"
        f"المعرف: @{u['username'] or 'لا يوجد'}\n"
        f"الآيدي: `{u['user_id']}`\n"
        f"الهاتف: `{u['phone'] or 'غير مُسجّل'}`\n"
        f"النقاط: `{u['points']}`\n"
        f"الاشتراك: {u['sub_type'] or 'لا يوجد'}\n"
        f"الحالة: {'🚫 محظور' if u['is_banned'] else '✅ نشط'}\n"
        f"تاريخ الانضمام: {u['join_date'][:10] if u['join_date'] else 'غير معروف'}\n"
        f"آخر ظهور: {u['last_seen'][:16] if u['last_seen'] else 'غير معروف'}\n"
        f"المُحيل: `{u['referred_by'] or 'لا يوجد'}`",
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  /reload - إعادة تحميل الإعدادات (أدمن)
# ─────────────────────────────────────────────
@admin_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CFG, TOKEN, ADMIN_ID, CHANNEL_ID, CHANNEL_LINK, SUPPORT, WALLETS, SUBS, BTNS, REF_PTS
    try:
        CFG          = load_config()
        CHANNEL_ID   = int(CFG["CHANNEL_ID"])
        CHANNEL_LINK = CFG["CHANNEL_LINK"]
        SUPPORT      = CFG["SUPPORT"]
        WALLETS      = CFG["WALLETS"]
        SUBS         = CFG["SUBSCRIPTIONS"]
        BTNS         = CFG["BUTTONS"]
        REF_PTS      = int(CFG.get("REFERRAL_POINTS", 5))
        await update.message.reply_text("✅ *تم إعادة تحميل الإعدادات من config.json بنجاح!*", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في تحميل الإعدادات: {e}")

# ─────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await show_admin_help(update.message)
    else:
        await update.message.reply_text(
            "👋 *مرحباً!*\n\n"
            "اضغط /start للبدء واستخدام البوت.",
            parse_mode=ParseMode.MARKDOWN
        )

# ─────────────────────────────────────────────
#  تشغيل البوت
# ─────────────────────────────────────────────
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    # ─────────────────────────────
    # ✅ أضف هذا الجزء هنا (زر Menu)
    # ─────────────────────────────
    async def set_commands(app):
        commands = [
            BotCommand("start", "بدء"),
            BotCommand("menu", "القائمة"),
            BotCommand("help", "مساعدة"),
        ]
        await app.bot.set_my_commands(commands)

    app.post_init = set_commands

    # ── أوامر المستخدمين ──
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("menu", show_main_menu))
    app.add_handler(CommandHandler("help",      cmd_help))

    # ── أوامر الأدمن ──
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("users",     cmd_users))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("send",      cmd_send))
    app.add_handler(CommandHandler("userinfo",  cmd_userinfo))
    app.add_handler(CommandHandler("reload",    cmd_reload))

    # ── الضغطات ──
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^adm_(?!ok_|no_)"))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── الرسائل ──
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 البوت الاحترافي يعمل الآن!")
    print("=" * 55)
    print("  🚀 البوت الاحترافي يعمل الآن — النسخة 2.0")
    print("=" * 55)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
