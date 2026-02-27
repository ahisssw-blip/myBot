#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║         بوت الاشتراكات الاحترافي - النسخة المطورة          ║
║         تطوير: Manus AI  |  v4.1 Professional           ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import json
import sqlite3
import logging
import asyncio
import datetime
from functools import wraps
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardRemove, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─────────────────────────────────────────────
#  تحميل الإعدادات
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
CHANNELS_FILE = BASE_DIR / "channels.json"
DB_PATH = BASE_DIR / "database.db"

def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_public_channels():
    if not CHANNELS_FILE.exists(): return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("channels", [])

CFG = load_config()
TOKEN          = CFG.get("TOKEN", "")
ADMIN_IDS      = CFG.get("ADMIN_IDS", [6712633269])
CHANNEL_ID     = int(CFG.get("CHANNEL_ID", 0))
CHANNEL_LINK   = CFG.get("CHANNEL_LINK", "")
BACKUP_CH_ID   = int(CFG.get("BACKUP_CHANNEL_ID", CHANNEL_ID))
SUPPORT        = CFG.get("SUPPORT", {})
WALLETS        = CFG.get("WALLETS", {})
SUBS           = CFG.get("SUBSCRIPTIONS", {})
BTNS           = CFG.get("BUTTONS", {})

# ─────────────────────────────────────────────
#  إعداد السجل (Logging)
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  قاعدة البيانات والنسخ الاحتياطي
# ─────────────────────────────────────────────
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
            status      TEXT DEFAULT 'pending',
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS messages_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            username    TEXT,
            first_name  TEXT,
            message     TEXT,
            created_at  TEXT
        );
    """)
    conn.commit()
    conn.close()

async def backup_database(context: ContextTypes.DEFAULT_TYPE):
    try:
        if not DB_PATH.exists(): return
        with open(DB_PATH, "rb") as f:
            await context.bot.send_document(chat_id=BACKUP_CH_ID, document=f, caption=f"📦 نسخة احتياطية: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    except Exception as e: logger.error(f"❌ Backup failed: {e}")

# ─────────────────────────────────────────────
#  دوال قاعدة البيانات
# ─────────────────────────────────────────────
def db_upsert_user(user):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
    if not existing:
        conn.execute("INSERT INTO users (user_id,username,first_name,last_name,join_date,last_seen) VALUES (?,?,?,?,?,?)", (user.id, user.username or "", user.first_name or "", user.last_name or "", now, now))
    else:
        conn.execute("UPDATE users SET username=?,first_name=?,last_name=?,last_seen=? WHERE user_id=?", (user.username or "", user.first_name or "", user.last_name or "", now, user.id))
    conn.commit()
    conn.close()

def db_get_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM users WHERE sub_status='active'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM payments WHERE status='pending'").fetchone()[0]
    conn.close()
    return {"total": total, "active": active, "pending": pending}

def db_log_message(user_id, username, first_name, message):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute("INSERT INTO messages_log (user_id,username,first_name,message,created_at) VALUES (?,?,?,?,?)", (user_id, username or "", first_name or "", message, now))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
#  مساعدات عامة
# ─────────────────────────────────────────────
async def is_subscribed(bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS: return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("member", "creator", "administrator")
    except TelegramError: return False

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS: return
        return await func(update, context)
    return wrapper

async def safe_send(bot, chat_id, **kwargs):
    try: return await bot.send_message(chat_id=chat_id, **kwargs)
    except TelegramError: return None

# ─────────────────────────────────────────────
#  الأوامر الرئيسية
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_upsert_user(user)
    
    # تحديد أوامر القائمة بناءً على رتبة المستخدم
    if user.id in ADMIN_IDS:
        await context.bot.set_my_commands([BotCommand("start", "بدء"), BotCommand("admin", "لوحة التحكم"), BotCommand("help", "مساعدة")], scope={"type": "chat", "chat_id": user.id})
    else:
        await context.bot.set_my_commands([BotCommand("start", "بدء"), BotCommand("help", "مساعدة")], scope={"type": "chat", "chat_id": user.id})

    subscribed = await is_subscribed(context.bot, user.id)
    if not subscribed:
        kbd = InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك في القناة 🔥", url=CHANNEL_LINK)]])
        await update.message.reply_text("⚠️ *يجب الاشتراك في قناة البوت أولاً* 🚫\n\nبعد الاشتراك اضغط /start", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)
        return

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS.get("btn_next", "الـــتـــالـــي ➡️"), callback_data="main_menu")],
        [InlineKeyboardButton("📺 قـنـواتـنـا الـعـامـة", callback_data="public_channels")]
    ])
    await update.message.reply_text(f"✨ *أهلاً وسهلاً {user.first_name}!*\n\nتم التحقق من اشتراكك بنجاح ✅", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in ADMIN_IDS:
        help_text = "🛡 *أوامر الأدمن:*\n\n`/admin` - لوحة التحكم\n`بث [نص]` - رسالة جماعية\n`رد [آيدي] [نص]` - رد على مستخدم"
    else:
        help_text = "👋 *مرحباً بك!*\n\nاستخدم زر /start للبدء."
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS.get("btn_subscriptions", "💎 قائمة الاشتراكات"), callback_data="sub_menu")],
        [InlineKeyboardButton(BTNS.get("btn_support", "📞 التواصل المباشر مع الدعم"), callback_data="support")],
        [InlineKeyboardButton(BTNS.get("btn_end", "❌ إنهاء"), callback_data="end")]
    ])
    text = f"*مــرحــبــاً {user.first_name}* ✨\n\nيرجى اختيار ما تريد:"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

async def show_sub_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(SUBS.get("VIP", {}).get("label", "👑 اشـتـراك VIP الـمـمـيـز"), callback_data="pay_VIP")],
        [InlineKeyboardButton("⭐ اشـتـراك لـقـنـاة خـاصـة واحـدة", callback_data="single_ch_menu")],
        [InlineKeyboardButton(BTNS.get("btn_back", "🔙 رجــوع"), callback_data="main_menu")]
    ])
    await update.callback_query.edit_message_text("💯🔥 *اخــتــر الاشــتــراك الــمــطــلــوب* 🔥💯\n(التفاصيل بالداخل)", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

PRIVATE_CHANNELS = [
    "قناة كمشتك", "قناة استراد الثورة", "قناة الجمهورية+الرمل", "قناة جبلة جبيبات نقعة",
    "قناة الزقزقانية+تشرين", "قناة جبلة عمارة تضامن", "قناة الزراعة وما حولها", "قناة كمشتك جبلة",
    "قناة الصليبة وماحولها", "قناة كمشتك اللاذقية", "قناة طرطوس الشيخ بدر", "قناة القدموس",
    "قناة بانياس القصور", "قناة بانياس", "قناة الجامعة Pornhub (جديدة)"
]

async def show_pay_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_key: str, price_usd=10):
    context.user_data["sub_type"] = sub_key
    label = SUBS.get(sub_key, {}).get("label", sub_key)
    price_usd = SUBS.get(sub_key, {}).get("price_usd", price_usd)
    price_syp = SUBS.get(sub_key, {}).get("price_syp", price_usd * 3000)  # افترضنا هنا أن 1 USD = 3000 ل.س على سبيل المثال
    if "VIP" in sub_key: 
        price_usd = SUBS.get("VIP", {}).get("price_usd", 25)
        price_syp = SUBS.get("VIP", {}).get("price_syp", 3000)  # سعر بالليرة السورية
        back_cb = "sub_menu"
    else:
        price_syp = 3000  # سعر ثابت بالليرة السورية إذا لم يكن في "VIP"
        back_cb = "single_ch_menu"

    print(f"سعر الاشتراك بـ VIP هو {price_usd} USD أو {price_syp} ل.س")
    
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS.get("btn_sham", "💳 شـام كاش"), callback_data="meth_sham"),
         InlineKeyboardButton(BTNS.get("btn_syriatel", "📱 سـيـريـتـل كـاش"), callback_data="meth_syria")],
        [InlineKeyboardButton(BTNS.get("btn_usdt", "🪙 عـمـلات رقـمـيـة USDT"), callback_data="meth_usdt")],
        [InlineKeyboardButton("📝 تــفــاصــيــل الاشــتــراك", callback_data="sub_details")],
        [InlineKeyboardButton(BTNS.get("btn_back", "🔙 رجــوع"), callback_data=back_cb)]
    ])
    await update.callback_query.edit_message_text(f"💎 *الـفـئـة:* {label}\n💰 *الـتـكـلـفـة:* {price_usd}$\n\nاخـتـر وسـيـلـة الـدفـع👇👇 \n\n\n(إن لم تجد طريقة الدفع المتاحة لديك، تواصل معنا، نؤمن الاستلام من جميع انحاء العالم وبكل الطرق 👌🔥)", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  معالج الضغطات
# ─────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if data == "main_menu": await show_main_menu(update, context)
    elif data == "sub_menu": await show_sub_menu(update, context)
    elif data == "single_ch_menu":
        text = "أســمــاء الــقــنــوات الــخــاصــة:\n\n" + "\n".join([f"{i}-{n}" for i,n in enumerate(PRIVATE_CHANNELS,1)]) + "\n\nاخـتـر رقـم الـقـنـاة الـتـي تـريـد الاشـتـراك بـهـا (سعر اشتراك أي قناة 10$):"
        btns = []
        for i in range(1, 16): btns.append(InlineKeyboardButton(str(i), callback_data=f"sel_ch_{i}"))
        kbd = [btns[i:i+5] for i in range(0, 15, 5)]
        kbd.append([InlineKeyboardButton("🔙 رجــوع", callback_data="sub_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kbd))
    
    elif data.startswith("sel_ch_"):
        idx = int(data.split("_")[2])
        context.user_data["selected_channel"] = PRIVATE_CHANNELS[idx-1]
        await show_pay_menu(update, context, f"قناة {idx}", price_usd=10)
    
    elif data == "pay_VIP": await show_pay_menu(update, context, "VIP", price_usd=25)
    
    elif data == "sub_details":
        sub_key = context.user_data.get("sub_type", "VIP")
        if "VIP" in sub_key:
            details = SUBS.get("VIP", {}).get("details", "تفاصيل اشتراك VIP.")
            back_cb = "pay_VIP"
        else:
            details = "اشتراك قناة خاصة واحدة بسعر 10$. تدفع لمرة واحدة. اشتراك دائم, نشر يومي ✅✅ ."
            back_cb = f"sel_ch_{sub_key.split()[-1]}" if "قناة" in sub_key else "single_ch_menu"
            
        await query.edit_message_text(details, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجــوع", callback_data=back_cb)]]), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("meth_"):
        method = data.replace("meth_", "")
        context.user_data["pay_method"] = method
        context.user_data["waiting_code"] = True
        sub_key = context.user_data.get("sub_type", "VIP")
        back_cb = "pay_VIP" if "VIP" in sub_key else "single_ch_menu"
        
        back_kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=back_cb)]])
        if method == "sham":
            text = f"💳 *شــام كــاش*\n\nقـم بـتـحـويـل الـمـبـلـغ بالـدولار أو مـا يـعـادلـه بالـلـيـرة الـسـوريـة (سعر الصرف 12,000) إلى:\n\n`{WALLETS.get('sham_cash')}`\n\nأســم الــحــســاب: {WALLETS.get('sham_account_name')}\n\nثـم أرسـل رقـم الـعـمـلـيـة هـنـا 👇"
            if (BASE_DIR / "sham.jpg").exists():
                await query.message.reply_photo(photo=open(BASE_DIR / "sham.jpg", "rb"), caption=text, parse_mode=ParseMode.MARKDOWN)
                await query.message.reply_text("استخدم الزر للعودة 👇", reply_markup=back_kbd)
            else: await query.edit_message_text(text, reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)
        elif method == "syria":
            await query.edit_message_text(f"📱 *ســيــريــتــل كــاش*\n\n\nقـم بـتـحـويـل الـمـبـلـغ بالـدولار أو مـا يـعـادلـه باللـيـرة الـسـوريـة (سعر الصرف 12,000) إلى:\n\n`{WALLETS.get('syriatel_cash')}`\n\n\n(بطريقة التحويل اليدوي) \nثـم أرسـل رقـم الـعـمـلـيـة هـنـا 👇", reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)
        elif method == "usdt":
            await query.edit_message_text(
                f"🪙 *USDT*\n\n"
                "قــم بــتــحــويــل الــمــبــلــغ إلــى:\n\n"
                f"BEP20:\n `{WALLETS.get('usdt_bep20')}`\n\n"
                f"TRC20:\n `{WALLETS.get('usdt_trc20')}`\n\n"
                "ثـم أرسـل TxID هـنـا 👇", 
                reply_markup=back_kbd, 
                parse_mode=ParseMode.MARKDOWN
            )

    elif data == "public_channels":
        channels = load_public_channels()
        if not channels: await query.answer("لا توجد قنوات حالياً.")
        else:
            kbd = [[InlineKeyboardButton(ch["name"], url=ch["url"])] for ch in channels]
            kbd.append([InlineKeyboardButton("🔙 رجــوع", callback_data="main_menu")])
            await query.edit_message_text("📺 قــنــواتــنــا الــعــامــة 🔥🔥\n\n-هذه القائمة متغيرة باستمرار. \n\n-ملاحظة: القناة التي نكتب بجانبها (اساسية) .. يكون النشر عليها حالياً. \n\n-الــنــشــر حــالــيــاً عــلــى قــنــاة:\n\nمستمروووون ♥️♥️", reply_markup=InlineKeyboardMarkup(kbd))

    elif data == "support":
        kbd = [[InlineKeyboardButton(f"💬 {SUPPORT.get('label1')} - واتـسـاب", url=f"https://wa.me/{SUPPORT.get('whatsapp1')}")],
               [InlineKeyboardButton(f"💬 {SUPPORT.get('label2')} - واتـسـاب", url=f"https://wa.me/{SUPPORT.get('whatsapp2')}")],
               [InlineKeyboardButton("🔙 رجــوع", callback_data="main_menu")]]
        await query.edit_message_text("📞 تـواصـل مـعـنـا مـبـاشـرة:", reply_markup=InlineKeyboardMarkup(kbd))
    
    elif data == "end": await query.edit_message_text("👋 تم إغلاق الجلسة. شكراً لك!")
    elif data.startswith("adm_"): await handle_admin_callback(update, context)

# ─────────────────────────────────────────────
#  معالج الرسائل النصية
# ─────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    if user.id not in ADMIN_IDS:
        db_log_message(user.id, user.username, user.first_name, text)
        for aid in ADMIN_IDS: await safe_send(context.bot, aid, text=f"👁 *رسالة من:* {user.first_name} ({user.id})\n💬 {text}")

    if context.user_data.get("waiting_code"):
        context.user_data["waiting_code"] = False
        sub_type = context.user_data.get("sub_type")
        pay_method = context.user_data.get("pay_method")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO payments (user_id, sub_type, pay_method, pay_code, created_at) VALUES (?,?,?,?,?)", (user.id, sub_type, pay_method, text, datetime.datetime.now().isoformat()))
        pay_id = cursor.lastrowid
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ تم استلام الكود، سيتم المراجعة قريباً!")
        kbd = InlineKeyboardMarkup([[InlineKeyboardButton("✅ قبول", callback_data=f"adm_ok_{user.id}_{pay_id}"), InlineKeyboardButton("❌ رفض", callback_data=f"adm_no_{user.id}_{pay_id}")]])
        for aid in ADMIN_IDS: await safe_send(context.bot, aid, text=f"🔔 *طلب دفع جديد!*\n👤 {user.first_name}\n🆔 `{user.id}`\n💎 {sub_type}\n💳 {pay_method}\n🔑 `{text}`", reply_markup=kbd)
        return

    if user.id in ADMIN_IDS:
        if text.startswith("بث "):
            msg = text.replace("بث ", "").strip()
            users = [r['user_id'] for r in get_db().execute("SELECT user_id FROM users").fetchall()]
            for uid in users: await safe_send(context.bot, uid, text=f"📢 *رسالة جماعية:*\n\n{msg}", parse_mode=ParseMode.MARKDOWN); await asyncio.sleep(0.05)
            await update.message.reply_text(f"✅ تم الإرسال لـ {len(users)} مستخدم.")
        elif text.startswith("رد "):
            parts = text.split(maxsplit=2)
            if len(parts) == 3:
                if await safe_send(context.bot, int(parts[1]), text=f"💬 *رد من الإدارة:*\n\n{parts[2]}"): await update.message.reply_text("✅ تم الرد.")

# ─────────────────────────────────────────────
#  لوحة الأدمن
# ─────────────────────────────────────────────
@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db_get_stats()
    kbd = [[InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("💾 نسخة احتياطية", callback_data="adm_backup")],
           [InlineKeyboardButton("❌ إغلاق", callback_data="end")]]
    await update.message.reply_text(f"🛡 *لوحة التحكم*\n\nالمستخدمين: {stats['total']}\nنشطون: {stats['active']}\nمعلق: {stats['pending']}", reply_markup=InlineKeyboardMarkup(kbd), parse_mode=ParseMode.MARKDOWN)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "adm_stats":
        stats = db_get_stats()
        await query.edit_message_text(f"📊 إحصائيات:\n- الكل: {stats['total']}\n- نشط: {stats['active']}\n- معلق: {stats['pending']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]))
    elif data == "adm_main":
        stats = db_get_stats()
        kbd = [[InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("💾 نسخة احتياطية", callback_data="adm_backup")]]
        await query.edit_message_text("🛡 لوحة التحكم:", reply_markup=InlineKeyboardMarkup(kbd))
    elif data == "adm_backup":
        await backup_database(context); await query.answer("✅ تم إرسال النسخة الاحتياطية!")
    elif data.startswith("adm_ok_"):
        _, _, uid, pid = data.split("_")
        conn = get_db(); conn.execute("UPDATE users SET sub_status='active' WHERE user_id=?", (uid,)); conn.execute("UPDATE payments SET status='approved' WHERE id=?", (pid,)); conn.commit(); conn.close()
        await safe_send(context.bot, int(uid), text="✅ تم تفعيل اشتراكك بنجاح!"); await query.edit_message_text(query.message.text + "\n\n🟢 تم القبول")
    elif data.startswith("adm_no_"):
        _, _, uid, pid = data.split("_")
        conn = get_db(); conn.execute("UPDATE payments SET status='rejected' WHERE id=?", (pid,)); conn.commit(); conn.close()
        await safe_send(context.bot, int(uid), text="❌ نعتذر، تم رفض طلبك."); await query.edit_message_text(query.message.text + "\n\n🔴 تم الرفض")

# ─────────────────────────────────────────────
#  تشغيل البوت
# ─────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.job_queue.run_repeating(backup_database, interval=21600, first=10)
    logger.info("🚀 البوت v4.1 يعمل الآن!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
