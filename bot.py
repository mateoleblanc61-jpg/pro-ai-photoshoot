import os
import logging
import io
import asyncio
import threading
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from PIL import Image # Потребуется pip install Pillow
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, 
    ReplyKeyboardRemove,
    WebAppInfo
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# --- 1. ВЕБ-СЕРВЕР ДЛЯ HEALTH CHECK (RENDER.COM) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"AI Professional Photographer is LIVE")
    
    def log_message(self, format, *args):
        return # Тихий режим для логов сервера

def run_health_check():
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Запуск Health Check сервера на порту {port}...")
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка Health Check сервера: {e}")

# --- 2. КОНФИГУРАЦИЯ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройка Gemini
genai.configure(api_key=os.getenv("GEMINI_KEY"))
MODEL_NAME = 'gemini-1.5-flash'

SYSTEM_INSTRUCTION = (
    "You are a professional AI Photo Editor. "
    "Merge the face from Image 1 into Image 2's style. "
    "Maintain facial identity exactly. High-quality cinematic output."
)

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

USER_PHOTO, STYLE_PHOTO, EDITING = range(3)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def process_image_size(image_bytes, max_size=(1024, 1024)):
    """Оптимизирует размер изображения для стабильной работы API и экономии памяти"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Конвертируем в RGB если нужно (для поддержки PNG/WebP)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        # Пропорциональное уменьшение
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка при сжатии фото: {e}")
        return image_bytes

# --- 3. КЛАВИАТУРЫ ---

def get_main_menu():
    web_app_url = os.getenv("WEBAPP_URL", "https://your-mini-app-url.vercel.app")
    keyboard = [
        [InlineKeyboardButton("🎨 Открыть Фотостудию (Mini App)", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton("🚀 Начать в чате", callback_data="start_chat_flow")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_reply_keyboard():
    return ReplyKeyboardMarkup([['🚀 Начать фотосессию']], resize_keyboard=True)

def get_cancel_inline():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_action")]])

def get_editing_options():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Начать всё заново", callback_data="restart_action")]])

# --- 4. ЛОГИКА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"User {update.effective_user.id} started the bot")
    context.user_data.clear()
    welcome_text = (
        "👋 Добро пожаловать в ИИ-фотостудию!\n\n"
        "Я могу перенести твое лицо на любой образ. Используй Mini App или общайся здесь."
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")
    return ConversationHandler.END

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Понг! Бот активен.")

async def start_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Окей! Нажми кнопку ниже, чтобы начать загрузку фото.",
        reply_markup=get_reply_keyboard()
    )

async def init_photoshoot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 **Шаг 1:** Пришли мне СВОЁ фото (лицо крупным планом).",
        reply_markup=get_cancel_inline()
    )
    return USER_PHOTO

async def get_user_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
    elif update.message.document and update.message.document.mime_type.startswith('image/'):
        photo_file = await update.message.document.get_file()
    else:
        await update.message.reply_text("Пожалуйста, пришли именно изображение.")
        return USER_PHOTO
        
    raw_data = await photo_file.download_as_bytearray()
    # Сжимаем перед сохранением в память
    context.user_data['user_face'] = process_image_size(raw_data)
    
    await update.message.reply_text(
        "✅ Лицо сохранено!\n\n**Шаг 2:** Теперь пришли фото-референс (образ).",
        reply_markup=get_cancel_inline()
    )
    return STYLE_PHOTO

async def generate_initial_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
    elif update.message.document and update.message.document.mime_type.startswith('image/'):
        photo_file = await update.message.document.get_file()
    else:
        await update.message.reply_text("Нужно прислать фото стиля.")
        return STYLE_PHOTO

    raw_style_data = await photo_file.download_as_bytearray()
    style_ref_raw = process_image_size(raw_style_data)
    user_face_raw = context.user_data.get('user_face')
    
    status = await update.message.reply_text("🔍 Анализирую черты лица...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")

    try:
        await status.edit_text("🎨 Генерирую образ...")
        model = genai.GenerativeModel(model_name=MODEL_NAME, system_instruction=SYSTEM_INSTRUCTION)
        
        prompt = [
            "Merge face from image 1 to style of image 2. Preserve identity exactly.",
            {"mime_type": "image/jpeg", "data": bytes(user_face_raw)},
            {"mime_type": "image/jpeg", "data": bytes(style_ref_raw)}
        ]
        
        response = await asyncio.to_thread(model.generate_content, prompt, safety_settings=SAFETY_SETTINGS)

        if response.parts and any(part.inline_data for part in response.parts):
            img_part = next(part for part in response.parts if part.inline_data)
            generated_bytes = img_part.inline_data.data
            context.user_data['current_image'] = generated_bytes
            
            await status.delete()
            await update.message.reply_photo(
                photo=io.BytesIO(generated_bytes), 
                caption="✨ Готово! Напиши правку или нажми кнопку ниже.",
                reply_markup=get_editing_options()
            )
            return EDITING
        else:
            await status.delete()
            await update.message.reply_text("❌ ИИ не смог создать фото. Попробуйте другой референс.", reply_markup=get_reply_keyboard())
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Gen Error: {e}")
        if "status" in locals(): await status.delete()
        await update.message.reply_text("❌ Ошибка генерации. Попробуйте еще раз с другими фото.", reply_markup=get_reply_keyboard())
        return ConversationHandler.END

async def process_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_edit_prompt = update.message.text
    current_image = context.user_data.get('current_image')
    original_face = context.user_data.get('user_face')

    status = await update.message.reply_text(f"🔧 Вношу правку: '{user_edit_prompt}'...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        model = genai.GenerativeModel(model_name=MODEL_NAME, system_instruction=SYSTEM_INSTRUCTION)
        prompt = [
            f"Modify this image: {user_edit_prompt}. Keep face identical.",
            {"mime_type": "image/jpeg", "data": bytes(current_image)},
            {"mime_type": "image/jpeg", "data": bytes(original_face)}
        ]
        
        response = await asyncio.to_thread(model.generate_content, prompt, safety_settings=SAFETY_SETTINGS)

        if response.parts and any(part.inline_data for part in response.parts):
            img_part = next(part for part in response.parts if part.inline_data)
            generated_bytes = img_part.inline_data.data
            context.user_data['current_image'] = generated_bytes
            
            await status.delete()
            await update.message.reply_photo(
                photo=io.BytesIO(generated_bytes), 
                caption="✅ Изменено!",
                reply_markup=get_editing_options()
            )
            return EDITING
        else:
            await status.edit_text("❌ Не удалось применить правку.", reply_markup=get_editing_options())
            return EDITING
    except Exception as e:
        logger.error(f"Edit Error: {e}")
        await status.edit_text("❌ Ошибка при редактировании.", reply_markup=get_editing_options())
        return EDITING

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.delete()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Процесс остановлен.",
        reply_markup=get_reply_keyboard()
    )
    return ConversationHandler.END

# --- 5. ОСНОВНОЙ ЗАПУСК ---

if __name__ == '__main__':
    threading.Thread(target=run_health_check, daemon=True).start()

    token = os.getenv("TG_TOKEN", "").strip().replace('"', '').replace("'", "")
    
    if not token:
        logger.error("КРИТИЧЕСКАЯ ОШИБКА: TG_TOKEN не найден!")
        sys.exit(1)

    try:
        logger.info("Проверка доступности моделей Gemini...")
        genai.list_models()
        logger.info("API Gemini успешно авторизован.")

        app = ApplicationBuilder().token(token).build()
        
        app.add_handler(CommandHandler('ping', ping))
        app.add_handler(CallbackQueryHandler(start_chat_callback, pattern="start_chat_flow"))
        
        conv = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                MessageHandler(filters.Text("🚀 Начать фотосессию"), init_photoshoot)
            ],
            states={
                USER_PHOTO: [
                    MessageHandler(filters.PHOTO | filters.Document.IMAGE, get_user_photo),
                    CallbackQueryHandler(cancel_callback, pattern="cancel_action")
                ],
                STYLE_PHOTO: [
                    MessageHandler(filters.PHOTO | filters.Document.IMAGE, generate_initial_transfer),
                    CallbackQueryHandler(cancel_callback, pattern="cancel_action")
                ],
                EDITING: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_text),
                    CallbackQueryHandler(cancel_callback, pattern="restart_action")
                ],
            },
            fallbacks=[CommandHandler('start', start), CallbackQueryHandler(cancel_callback)],
        )
        
        app.add_handler(conv)
        
        logger.info("Бот готов к работе.")
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}")
        sys.exit(1)
