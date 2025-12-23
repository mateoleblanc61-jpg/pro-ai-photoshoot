import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Крошечный веб-сервер для обмана Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_health_check():
    server = HTTPServer(('0.0.0.0', int(os.getenv("PORT", 10000))), HealthCheckHandler)
    server.serve_forever()

# Запускаем сервер в отдельном потоке
threading.Thread(target=run_health_check, daemon=True).start()
import os
import logging
import io
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

# --- НАСТРОЙКИ ---
genai.configure(api_key=os.getenv("GEMINI_KEY"))
MODEL_NAME = "gemini-3-pro-image-preview" # Самая мощная модель для Pro

# Состояния диалога
SELECT_STYLE, SEND_PHOTO = range(2)

# Библиотека стилей
STYLES = {
    "viking": "Viking warrior in snow mountains, cinematic lighting, fur armor, 8k photo",
    "cyber": "Cyberpunk character in Tokyo neon streets, rainy night, techwear, vibrant colors",
    "business": "Professional business portrait, luxury office background, soft studio lighting, sharp suit",
    "old_money": "Aristocratic aesthetic, luxury library, wearing a tailored blazer, film grain, 35mm lens",
    "marvel": "Superhero cinematic shot, dramatic pose, epic clouds background, comic style lighting"
}

logging.basicConfig(level=logging.INFO)

# --- ЛОГИКА БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и выбор стиля через кнопки."""
    keyboard = [
        [InlineKeyboardButton("⚔️ Викинг", callback_data="viking"), InlineKeyboardButton("🌃 Киберпанк", callback_data="cyber")],
        [InlineKeyboardButton("💼 Бизнес-портрет", callback_data="business"), InlineKeyboardButton("💎 Old Money", callback_data="old_money")],
        [InlineKeyboardButton("🦸 Marvel Style", callback_data="marvel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📸 Привет! Я твой персональный ИИ-фотограф.\n\n"
        "Выбери стиль будущей фотосессии:",
        reply_markup=reply_markup
    )
    return SELECT_STYLE

async def style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку стиля."""
    query = update.callback_query
    await query.answer()
    
    style_key = query.data
    context.user_data['chosen_style'] = STYLES[style_key]
    
    await query.edit_message_text(
        f"Выбран стиль: {style_key.replace('_', ' ').title()}\n\n"
        "Теперь пришли мне своё селфи (фотографию), где хорошо видно лицо."
    )
    return SEND_PHOTO

async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение фото и генерация результата."""
    user_photo = update.message.photo[-1]
    photo_file = await user_photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()
    
    status_msg = await update.message.reply_text("⏳ Начинаю фотосессию... Проявляю плёнку (30-60 сек)")

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        style_prompt = context.user_data.get('chosen_style', "High quality portrait")
        
        # Инструкция для Gemini
        full_prompt = [
            f"Apply the following style to the person in the reference image: {style_prompt}. "
            "Keep the facial features, identity, and gender of the person exactly the same. "
            "The output must be a single high-quality, photorealistic image.",
            {"mime_type": "image/jpeg", "data": image_bytes}
        ]

        # Генерация (Gemini Pro Image возвращает картинку)
        response = model.generate_content(full_prompt)
        
        # Отправка результата
        generated_data = response.parts[0].inline_data.data
        image_stream = io.BytesIO(generated_data)
        
        await status_msg.delete()
        await update.message.reply_photo(
            photo=image_stream, 
            caption="Твоё фото готово! Хочешь ещё одну? Жми /start"
        )
        
    except Exception as e:
        logging.error(f"Error: {e}")
        await status_msg.edit_text("❌ Ошибка при генерации. Попробуй другое фото или другой стиль.")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Фотосессия отменена.")
    return ConversationHandler.END

if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("TG_TOKEN")).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECT_STYLE: [CallbackQueryHandler(style_callback)],
            SEND_PHOTO: [MessageHandler(filters.PHOTO, process_photo)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    print("Бот-фотограф запущен...")
    app.run_polling()
