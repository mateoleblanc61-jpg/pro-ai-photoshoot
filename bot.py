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
        # 1. Скачиваем фото
        photo_file = await update.message.photo[-1].get_file()
        image_data_raw = await photo_file.download_as_bytearray()
        
        # 2. Преобразуем bytearray в bytes (Исправление ошибки!)
        image_bytes = bytes(image_data_raw)
        
        # 3. Настройка модели
        model = genai.GenerativeModel('gemini-1.5-pro') 
        style_prompt = context.user_data.get('style', "High quality portrait")
        
        prompt = [
            f"Transform the person in this photo into: {style_prompt}. "
            "Keep the facial features and identity identical. Output the result as an image.",
            {"mime_type": "image/jpeg", "data": image_bytes}
        ]
        
        # Настройки безопасности (чтобы не блокировал лица)
        safety = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        # 4. Запрос к ИИ
        response = model.generate_content(prompt, safety_settings=safety)

        # 5. Проверка результата
        if response.parts:
            # Ищем часть с данными изображения
            for part in response.parts:
                if part.inline_data:
                    generated_img = io.BytesIO(part.inline_data.data)
                    await status.delete()
                    await update.message.reply_photo(photo=generated_img, caption="Готово! Хочешь еще? /start")
                    return ConversationHandler.END
            
            await status.edit_text("❌ ИИ прислал ответ, но в нем нет картинки. Попробуй другой стиль.")
        else:
            await status.edit_text("❌ Ошибка: Модель заблокировала запрос или не смогла сгенерировать фото.")

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await status.edit_text(f"❌ Техническая ошибка: {str(e)[:100]}")
    
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
