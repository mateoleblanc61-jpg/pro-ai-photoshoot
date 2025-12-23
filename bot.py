import os
import logging
import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# --- 1. ФИКС ДЛЯ RENDER (Health Check Server) ---
# Этот блок создает мини-сайт, который говорит Render: "Я работаю!"
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active and running!")

def run_health_check_server():
    port = int(os.getenv("PORT", 10000)) # Render сам передает порт
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Запускаем сервер в фоновом потоке, чтобы он не мешал боту
threading.Thread(target=run_health_check_server, daemon=True).start()

# --- 2. НАСТРОЙКИ ИИ ---
logging.basicConfig(level=logging.INFO)
genai.configure(api_key=os.getenv("GEMINI_KEY"))
MODEL_NAME = "gemini-3-pro-image-preview"

# Состояния диалога
SELECT_STYLE, SEND_PHOTO = range(2)

# Стили для фотосессий
STYLES = {
    "viking": "Viking warrior in snow mountains, cinematic lighting, fur armor, photorealistic, 8k",
    "cyber": "Cyberpunk character in Tokyo neon streets, techwear, rainy night, high contrast",
    "business": "Professional business portrait, luxury office background, soft studio lighting",
    "old_money": "Aristocratic aesthetic, luxury library, tailored blazer, film grain, 35mm lens",
    "marvel": "Superhero cinematic shot, dramatic pose, epic clouds background, marvel movie style"
}

# --- 3. ЛОГИКА БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⚔️ Викинг", callback_data="viking"), 
         InlineKeyboardButton("🌃 Киберпанк", callback_data="cyber")],
        [InlineKeyboardButton("💼 Бизнес", callback_data="business"), 
         InlineKeyboardButton("💎 Old Money", callback_data="old_money")],
        [InlineKeyboardButton("🦸 Marvel", callback_data="marvel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите стиль для ИИ-фотосессии:", reply_markup=reply_markup)
    return SELECT_STYLE

async def style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['style'] = STYLES[query.data]
    await query.edit_message_text("Отлично! Теперь пришли мне своё фото (селфи).")
    return SEND_PHOTO

async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправь фото.")
        return SEND_PHOTO

    status = await update.message.reply_text("⏳ Магия началась... Генерирую фото (30-60 сек)")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        model = genai.GenerativeModel(MODEL_NAME)
        prompt = [
            f"Transform this person into: {context.user_data['style']}. Keep face identical.",
            {"mime_type": "image/jpeg", "data": image_bytes}
        ]
        
        response = model.generate_content(prompt)
        image_stream = io.BytesIO(response.parts[0].inline_data.data)
        
        await status.delete()
        await update.message.reply_photo(photo=image_stream, caption="Готово! Хочешь еще? /start")
    except Exception as e:
        logging.error(e)
        await status.edit_text("❌ Ошибка. Попробуй другое фото.")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

if __name__ == '__main__':
    token = os.getenv("TG_TOKEN")
    app = ApplicationBuilder().token(token).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECT_STYLE: [CallbackQueryHandler(style_callback)],
            SEND_PHOTO: [MessageHandler(filters.PHOTO, process_photo)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    app.add_handler(conv)
    print("Бот запущен...")
    app.run_polling()
