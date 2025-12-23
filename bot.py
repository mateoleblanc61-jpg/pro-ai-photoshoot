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

# --- 1. ВЕБ-СЕРВЕР ДЛЯ RENDER.COM (Port Binding Fix) ---
# Это необходимо, чтобы Render видел, что приложение работает
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def run_health_check():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Запускаем сервер в фоновом потоке
threading.Thread(target=run_health_check, daemon=True).start()

# --- 2. КОНФИГУРАЦИЯ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

# Настройка Gemini
genai.configure(api_key=os.getenv("GEMINI_KEY"))
MODEL_NAME = "gemini-3-pro-image-preview" # Самая мощная модель для фото

# Состояния диалога
SELECT_STYLE, SEND_PHOTO = range(2)

# Библиотека стилей (Промпты)
STYLES = {
    "viking": "Viking warrior in snow mountains, cinematic lighting, fur armor, photorealistic, 8k",
    "cyber": "Cyberpunk character in Tokyo neon streets, techwear, rainy night, high contrast, cinematic",
    "business": "Professional business portrait, luxury office background, soft studio lighting, sharp suit",
    "old_money": "Aristocratic aesthetic, luxury library, tailored blazer, film grain, 35mm lens, high class",
    "marvel": "Superhero cinematic shot, dramatic pose, epic clouds background, marvel movie style"
}

# --- 3. ФУНКЦИИ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и вывод кнопок выбора стиля."""
    keyboard = [
        [InlineKeyboardButton("⚔️ Викинг", callback_data="viking"), 
         InlineKeyboardButton("🌃 Киберпанк", callback_data="cyber")],
        [InlineKeyboardButton("💼 Бизнес-портрет", callback_data="business"), 
         InlineKeyboardButton("💎 Old Money", callback_data="old_money")],
        [InlineKeyboardButton("🦸 Marvel Style", callback_data="marvel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📸 Привет! Я твой ИИ-фотограф.\n\n"
        "Выбери стиль будущей фотосессии:",
        reply_markup=reply_markup
    )
    return SELECT_STYLE

async def style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение выбранного стиля и запрос фото."""
    query = update.callback_query
    await query.answer()
    
    style_key = query.data
    context.user_data['chosen_style'] = STYLES[style_key]
    
    await query.edit_message_text(
        f"✅ Выбран стиль: {style_key.replace('_', ' ').upper()}\n\n"
        "Теперь пришли мне своё селфи (фотографию). \n"
        "Важно: лицо должно быть хорошо освещено и направлено в камеру."
    )
    return SEND_PHOTO

async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главная магия: Генерация фото через Gemini."""
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, пришли именно фотографию.")
        return SEND_PHOTO

    # Берем фото в лучшем качестве
    photo_file = await update.message.photo[-1].get_file()
    image_bytes = await photo_file.download_as_bytearray()
    
    status_msg = await update.message.reply_text("⏳ Магия началась... Проявляю плёнку (это займет 30-60 сек)")

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        style_prompt = context.user_data.get('chosen_style', "Professional portrait")
        
        # Инструкция для модели
        full_prompt = [
            f"Transform the person in this image into the following setting: {style_prompt}. "
            "IMPORTANT: Keep the facial identity, features, and expression identical to the original person. "
            "Output must be a high-quality cinematic photograph.",
            {"mime_type": "image/jpeg", "data": image_bytes}
        ]

        # Запрос к Gemini
        response = model.generate_content(full_prompt)
        
        # Проверка ответа и отправка
        if response.parts:
            generated_data = response.parts[0].inline_data.data
            image_stream = io.BytesIO(generated_data)
            
            await status_msg.delete()
            await update.message.reply_photo(
                photo=image_stream, 
                caption="Твоё фото готово! 🔥\nХочешь еще? Жми /start"
            )
        else:
            await status_msg.edit_text("😕 ИИ не смог создать фото. Попробуй другой стиль или фото.")
        
    except Exception as e:
        logging.error(f"Генерация провалена: {e}")
        await status_msg.edit_text("❌ Произошла ошибка. Попробуй позже или пришли другое фото.")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена диалога."""
    await update.message.reply_text("Фотосессия отменена. Жду тебя снова!")
    return ConversationHandler.END

# --- 4. ЗАПУСК ---
if __name__ == '__main__':
    token = os.getenv("TG_TOKEN")
    if not token:
        print("ОШИБКА: Не найден TG_TOKEN в переменных окружения!")
        exit(1)

    app = ApplicationBuilder().token(token).build()

    # Настройка машины состояний
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECT_STYLE: [CallbackQueryHandler(style_callback)],
            SEND_PHOTO: [MessageHandler(filters.PHOTO, process_photo)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)

    print("Бот успешно запущен и готов к работе!")
    app.run_polling()
