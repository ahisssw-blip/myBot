from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = "8327790208:AAGRq3kDUS9bfkH2LGG7JUSX4bt_tYZinLs"

async def maintenance_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚧 البوت تحت الصيانة حالياً.\n"
        "نعتذر عن الإزعاج، يرجى المحاولة لاحقاً."
    )

app = ApplicationBuilder().token(TOKEN).build()

# الرد على كل الرسائل بدون أي استثناء
app.add_handler(MessageHandler(filters.ALL, maintenance_reply))

print("⚠️ البوت في وضع الصيانة فقط...")
app.run_polling()