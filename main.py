#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║         بوت الاشتراكات الاحترافي - النسخة المطورة          ║
║         تطوير: Manus AI  |  v5.0 Professional           ║
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
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_public_channels():
    if not CHANNELS_FILE.exists(): return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("channels", [])

CFG = load_config()
TOKEN          = CFG.get("TOKEN", "")
ADMIN_IDS      = CFG.get("ADMIN_IDS", [])
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
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        with open(DB_PATH, "rb") as f:
            await context.bot.send_document(
                chat_id=BACKUP_CH_ID, 
                document=f, 
                caption=f"📦 نسخة احتياطية دورية لقاعدة البيانات\n⏰ الوقت: {now_str}\n🛡 نظام النسخ التلقائي"
            )
        logger.info("✅ Periodic backup sent successfully.")
    except Exception as e: 
        logger.error(f"❌ Backup failed: {e}")

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
    if not CHANNEL_ID: return True # تخطي التحقق إذا لم يتم ضبط القناة
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
    
    # تحديث أوامر القائمة
    commands = [BotCommand("start", "بدء"), BotCommand("help", "مساعدة")]
    if user.id in ADMIN_IDS:
        commands.append(BotCommand("admin", "لوحة التحكم"))
    await context.bot.set_my_commands(commands, scope={"type": "chat", "chat_id": user.id})

    subscribed = await is_subscribed(context.bot, user.id)
    if not subscribed:
        kbd = InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك في القناة 🔥", url=CHANNEL_LINK)]])
        await update.message.reply_text("⚠️ *يجب الاشتراك في قناة البوت أولاً* 🚫\n\nبعد الاشتراك اضغط /start", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)
        return

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS.get("btn_next", "الـــتـــالـــي ➡️"), callback_data="main_menu")]
    ])
    await update.message.reply_text(f"✨ *أهـــلاً و ســهــلاً {user.first_name}!*\n\n*احـتـفـظ بالـبـوت لـديك أو انـسـخ رابـطـه واحـفـظـه لـتـصـل إلـيـنـا مـتـى شـئـت.. جـمـيـع الـقـنـوات الـخـاصـة و الـعـامـة مـوجـودة بـالـداخـل.🔥*\n\n-*تـم إلـغـاء ربـط الـبـوت بـقـنـاة*.\n\n-تـم إلــغــاء شــرط الإشـتـراك بـقـنـاة للـمـواصـلـة للـبـوت.\n\n-لـيـبـقـى الـبـوت بـأمـان.\n\n-تــم تـحـضـيـر 10 بـوتـات احـتـيـاطـيـة بـديـلـة ✅", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in ADMIN_IDS:
        help_text = "🛡 *أوامر الأدمن:*\n\n`/admin` - لوحة التحكم الشاملة\n`بث [نص]` - رسالة جماعية لكل المستخدمين\n`رد [آيدي] [نص]` - رد مباشر على مستخدم"
    else:
        help_text = "👋 *مرحباً بك في بوت الاشتراكات!*\n\nاستخدم الأزرار للتنقل بين القوائم.\nللدعم الفني، اضغط على زر التواصل في القائمة الرئيسية."
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS.get("btn_subscriptions", "💎 قـنـواتـنـا الـخـاصـة و العامّة"), callback_data="sub_menu")],
        [InlineKeyboardButton(BTNS.get("btn_support", "📞 الـتـواصـل الـمـبـاشر مـع الـدعـم"), callback_data="support")],
        [InlineKeyboardButton(BTNS.get("btn_end", "❌ إنــهــاء"), callback_data="end")]
    ])
    text = f"*مــرحــبــاً {user.first_name}* ✨\n\n\n*يـــرجـــى اخـــتـــيــار:*\n\n1️⃣ للإشتراك في *👑الــقــنــوات الــخــاصــة👑* أو الدخول المجاني للقنوات العامة.\n\n2️⃣ للتواصل المباشر معنا عبر الواتساب.\n\n❤️❤️❤️❤️"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

async def show_sub_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(SUBS.get("VIP", {}).get("label", "👑 اشـتـراك VIP الـمـمـيـز 👑"), callback_data="pay_VIP")],
        [InlineKeyboardButton("📺 قـنـواتـنـا الـعـامـة 📺 ", callback_data="public_channels")],
        [InlineKeyboardButton(BTNS.get("btn_back", "🔙 رجـــــوع 🔙 "), callback_data="main_menu")]
    ])
    await update.callback_query.edit_message_text("💯🔥 *اخــتــر الاشــتــراك الــمــطــلــوب* 🔥💯\n\n\n*-👑اشـتـراك VIP الـمـمـيـز👑*:\nيمنحك الوصول لكافة القنوات الخاصة والمحتوى الخاص بالكامل (التفاصيل بالداخل).\n\n-القنوات العامة:\n متاحة للجميع مجاناً , والقائمة متغيّرة باستمرار نتيجة حظر.\n\n ❤️❤️❤️❤️", reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

async def show_pay_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_key: str):
    label = SUBS.get(sub_key, {}).get("label", sub_key)
    price_usd = SUBS.get(sub_key, {}).get("price_usd", 25)
    price_syp = SUBS.get(sub_key, {}).get("price_syp", price_usd * 14000)
    
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(BTNS.get("btn_sham", "💳 شـام كاش"), callback_data="meth_sham"),
         InlineKeyboardButton(BTNS.get("btn_syriatel", "📱 سـيـريـتـل كـاش"), callback_data="meth_syria")],
        [InlineKeyboardButton(BTNS.get("btn_usdt", "🪙 عـمـلات رقـمـيـة USDT"), callback_data="meth_usdt")],
        [InlineKeyboardButton("📝 تــفــاصــيــل الاشــتــراك 📝 ", callback_data="sub_details")],
        [InlineKeyboardButton(BTNS.get("btn_back", "🔙 رجــوع"), callback_data="sub_menu")]
    ])
    
    text = (
        f"💎 *الـفـئـة:* {label}\n"
        f"\n💰 الـتـكـلـفـة: {price_usd}$"
        "*\nاخـتـر وسـيـلـة الـدفـع👇👇*\n\n"
        "(إن لم تجد طريقة الدفع المتاحة لديك، تواصل معنا، نؤمن الاستلام من جميع انحاء العالم وبكل الطرق 👌🔥)"
    )
    await update.callback_query.edit_message_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

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
    elif data == "pay_VIP":
        context.user_data["sub_type"] = "VIP"
        await show_pay_menu(update, context, "VIP")
    
    elif data == "sub_details":
        details = SUBS.get("VIP", {}).get("details", "اشتراك VIP يمنحك الوصول لكافة القنوات الخاصة بشكل دائم.")
        await query.edit_message_text(details, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجــوع", callback_data="pay_VIP")]]), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("meth_"):
        method = data.replace("meth_", "")
        context.user_data["pay_method"] = method
        context.user_data["waiting_code"] = True
        
        back_kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="pay_VIP")]])
        if method == "sham":
            text = f"💳 *شــام كــاش*\n\nقـم بـتـحـويـل 25$ أو 3500 ل.س جـديـدة إلى:\n\n`{WALLETS.get('sham_cash')}`\n\nأســم الــحــســاب: {WALLETS.get('sham_account_name')}\n\n\n*ثـم أرسـل رقـم الـعـمـلـيـة هـنـا 👇*"
            if (BASE_DIR / "sham.jpg").exists():
                await query.message.reply_photo(photo=open(BASE_DIR / "sham.jpg", "rb"), caption=text, parse_mode=ParseMode.MARKDOWN)
                await query.message.reply_text("استخدم الزر للعودة 👇", reply_markup=back_kbd)
            else: await query.edit_message_text(text, reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)
        elif method == "syria":
            await query.edit_message_text(f"📱 *ســيــريــتــل كــاش*\n\nقـم بـتـحـويـل 3500 ل.س جـديـدة إلى:\n\n`{WALLETS.get('syriatel_cash')}`\n\n*ثـم أرسـل رقـم الـعـمـلـيـة هـنـا 👇*", reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)
        elif method == "usdt":
            await query.edit_message_text(f"🪙 *USDT*\n\nقــم بــتــحــويــل 25$ إلــى أحـد الـمـحـافـظ الـتـالـيـة:\n\nBEP20:\n `{WALLETS.get('usdt_bep20')}`\n\nTRC20:\n `{WALLETS.get('usdt_trc20')}`\n\nيمكنك التواصل مع الدعم للتحويل المباشر (خارج السلسلة) إلى بينانس أو كوين اكس أو تراست والت أو سي والت .\nبالإضافة إلى شبكات: Erc20 ETH - TON .\n\n*ثـم أرسـل TxID هـنـا 👇*", reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN)

    elif data == "public_channels":
        channels = load_public_channels()
        if not channels: 
            await query.edit_message_text("لا توجد قنوات حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="sub_menu")]]))
        else:
            # عرض القنوات كمربعات (3 في كل صف)
            kbd = []
            row = []
            for ch in channels:
                row.append(InlineKeyboardButton(ch["name"], url=ch["url"]))
                if len(row) == 3:
                    kbd.append(row)
                    row = []
            if row: kbd.append(row)
            
            kbd.append([InlineKeyboardButton("🔙 رجــوع", callback_data="sub_menu")])
            await query.edit_message_text("📺 قــنــواتــنــا الــعــامــة 🔥🔥\n\n-نقوم بتغيير هذه القائمة باستمرار. \n\n-سيكون لكل قناة محتوى خاص بها. 🔥\n\n-يمكنك الاشتراك بهم جميعاً لتبقى معنا.\n\n\nاضغط على اي قناة في الاسفل للانتقال إليها👇👇👇:", reply_markup=InlineKeyboardMarkup(kbd))

    elif data == "support":
        kbd = [[InlineKeyboardButton(f"💬 {SUPPORT.get('label1')} - واتـسـاب", url=f"https://wa.me/{SUPPORT.get('whatsapp1')}")],
               [InlineKeyboardButton(f"💬 {SUPPORT.get('label2')} - واتـسـاب", url=f"https://wa.me/{SUPPORT.get('whatsapp2')}")],
               [InlineKeyboardButton("🔙 رجــوع", callback_data="main_menu")]]
        await query.edit_message_text("📞 تـواصـل مـعـنـا مـبـاشـرة عبر الواتساب:", reply_markup=InlineKeyboardMarkup(kbd))
    
    elif data == "end": await query.edit_message_text("👋 تم إغلاق الجلسة. شكراً لاستخدامك البوت!")
    elif data.startswith("adm_"): await handle_admin_callback(update, context)

# ─────────────────────────────────────────────
#  معالج الرسائل النصية
# ─────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    # تسجيل الرسائل من غير الأدمن
    if user.id not in ADMIN_IDS:
        db_log_message(user.id, user.username, user.first_name, text)
        for aid in ADMIN_IDS: 
            await safe_send(context.bot, aid, text=f"👁 *رسالة من:* {user.first_name} ({user.id})\n💬 {text}")

    # معالجة انتظار كود الدفع
    if context.user_data.get("waiting_code"):
        context.user_data["waiting_code"] = False
        sub_type = context.user_data.get("sub_type", "VIP")
        pay_method = context.user_data.get("pay_method", "unknown")
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO payments (user_id, sub_type, pay_method, pay_code, created_at) VALUES (?,?,?,?,?)", 
                       (user.id, sub_type, pay_method, text, datetime.datetime.now().isoformat()))
        pay_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        await update.message.reply_text("✅ تم استلام الكود بنجاح! سيتم مراجعته من قبل الإدارة وتفعيل اشتراكك قريباً.")
        
        kbd = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ قبول", callback_data=f"adm_ok_{user.id}_{pay_id}"), 
             InlineKeyboardButton("❌ رفض", callback_data=f"adm_no_{user.id}_{pay_id}")]
        ])
        for aid in ADMIN_IDS: 
            await safe_send(context.bot, aid, 
                            text=f"🔔 *طلب دفع جديد!*\n👤 {user.first_name}\n🆔 `{user.id}`\n💎 {sub_type}\n💳 {pay_method}\n🔑 `{text}`", 
                            reply_markup=kbd)
        return

    # أوامر الأدمن النصية
    if user.id in ADMIN_IDS:
        if text.startswith("بث "):
            msg = text.replace("بث ", "").strip()
            conn = get_db()
            users = [r['user_id'] for r in conn.execute("SELECT user_id FROM users").fetchall()]
            conn.close()
            
            sent, blocked = 0, 0
            status_msg = await update.message.reply_text(f"⏳ جاري الإرسال لـ {len(users)} مستخدم...")
            
            for uid in users:
                try:
                    await context.bot.send_message(chat_id=uid, text=f"💬 *رســالــة مــن الإدارة 📩:*\n\n{msg}", parse_mode=ParseMode.MARKDOWN)
                    sent += 1
                    await asyncio.sleep(0.05)
                except TelegramError as e:
                    if "bot was blocked" in str(e): blocked += 1
            
            await status_msg.edit_text(f"✅ تم الإرسال لـ {sent} مستخدم\n🚫 {blocked} مستخدمين حظروا البوت")

        elif text.startswith("رد "):
            parts = text.split(maxsplit=2)
            if len(parts) == 3:
                target_id = int(parts[1])
                response = parts[2]
                if await safe_send(context.bot, target_id, text=f"💬 *رد من الإدارة:* \n\n{response}", parse_mode=ParseMode.MARKDOWN):
                    await update.message.reply_text(f"✅ تم إرسال الرد إلى {target_id}")
                else:
                    await update.message.reply_text("❌ فشل إرسال الرد، قد يكون المستخدم حظر البوت.")

# ─────────────────────────────────────────────
#  لوحة الأدمن الشاملة
# ─────────────────────────────────────────────
@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db_get_stats()
    kbd = [
        [InlineKeyboardButton("📊 الإحصائيات التفصيلية", callback_data="adm_stats"), 
         InlineKeyboardButton("💾 نسخة احتياطية فورية", callback_data="adm_backup")],
        [InlineKeyboardButton("📋 طلبات الدفع المعلقة", callback_data="adm_pending")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_menu"),
         InlineKeyboardButton("🔓 فك حظر", callback_data="adm_unban_menu")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="end")]
    ]
    text = (
        f"🛡 *لوحة تحكم الإدارة الشاملة*\n\n"
        f"👥 إجمالي المستخدمين: `{stats['total']}`\n"
        f"👑 المشتركون النشطون: `{stats['active']}`\n"
        f"⏳ طلبات بانتظار المراجعة: `{stats['pending']}`\n\n"
        "اختر أحد الخيارات للتحكم:"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kbd), parse_mode=ParseMode.MARKDOWN)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "adm_main":
        stats = db_get_stats()
        kbd = [
            [InlineKeyboardButton("📊 الإحصائيات التفصيلية", callback_data="adm_stats"), 
             InlineKeyboardButton("💾 نسخة احتياطية فورية", callback_data="adm_backup")],
            [InlineKeyboardButton("📋 طلبات الدفع المعلقة", callback_data="adm_pending")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="end")]
        ]
        await query.edit_message_text(f"🛡 لوحة التحكم:\n\nالمستخدمين: {stats['total']}\nنشطون: {stats['active']}", reply_markup=InlineKeyboardMarkup(kbd))

    elif data == "adm_stats":
        stats = db_get_stats()
        text = f"📊 *إحصائيات البوت:*\n\n- إجمالي المستخدمين: {stats['total']}\n- المشتركون VIP: {stats['active']}\n- طلبات معلقة: {stats['pending']}"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]), parse_mode=ParseMode.MARKDOWN)

    elif data == "adm_backup":
        await backup_database(context)
        await query.answer("✅ تم إرسال النسخة الاحتياطية لقناة الأرشيف!")

    elif data == "adm_pending":
        conn = get_db()
        pending = conn.execute("SELECT p.*, u.first_name FROM payments p JOIN users u ON p.user_id = u.user_id WHERE p.status='pending' LIMIT 10").fetchall()
        conn.close()
        if not pending:
            await query.answer("لا توجد طلبات معلقة حالياً.")
            return
        
        text = "📋 *آخر الطلبات المعلقة:*\n\n"
        for p in pending:
            text += f"👤 {p['first_name']} | 💎 {p['sub_type']} | 🔑 `{p['pay_code']}`\n"
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("adm_ok_"):
        _, _, uid, pid = data.split("_")
        conn = get_db()
        conn.execute("UPDATE users SET sub_status='active', sub_type='VIP' WHERE user_id=?", (uid,))
        conn.execute("UPDATE payments SET status='approved' WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        await safe_send(context.bot, int(uid), text="✅ *تهانينا!* تم تفعيل اشتراك VIP الخاص بك بنجاح. يمكنك الآن الاستمتاع بكافة الميزات.", parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_text(query.message.text + "\n\n🟢 تم القبول والتفعيل ✅")

    elif data.startswith("adm_no_"):
        _, _, uid, pid = data.split("_")
        conn = get_db()
        conn.execute("UPDATE payments SET status='rejected' WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        await safe_send(context.bot, int(uid), text="❌ نعتذر، تم رفض طلب الدفع الخاص بك. يرجى التأكد من البيانات أو التواصل مع الدعم.")
        await query.edit_message_text(query.message.text + "\n\n🔴 تم الرفض ❌")

# ─────────────────────────────────────────────
#  تشغيل البوت
# ─────────────────────────────────────────────
def main():
    init_db()
    if not TOKEN:
        print("❌ خطأ: لم يتم العثور على TOKEN في ملف الإعدادات!")
        return
        
    app = Application.builder().token(TOKEN).build()
    
    # المعالجات
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # جدولة النسخ الاحتياطي (كل 6 ساعات = 21600 ثانية)
    if app.job_queue:
        app.job_queue.run_repeating(backup_database, interval=21600, first=10)
    
    logger.info("🚀 البوت v5.0 يعمل الآن بكافة التعديلات المطلوبة!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
