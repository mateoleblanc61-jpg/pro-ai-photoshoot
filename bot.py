import os
import logging
import io
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# --- 1. ВЕБ-СЕРВЕР ДЛЯ RENDER.COM (Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AI Professional Photographer is LIVE")

def run_health_check():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check, daemon=True).start()

# --- 2. КОНФИГУРАЦИЯ ИИ GEMINI ---
logging.basicConfig(level=logging.INFO)
genai.configure(api_key=os.getenv("GEMINI_KEY"))

# Системная установка для ИИ
SYSTEM_INSTRUCTION = (
    "You are a professional AI Photo Editor and Compositor. "
    "Task 1 (Creation): You take Image 1 (User Face) and Image 2 (Target Style) and merge them. "
    "Task 2 (Editing): You take an existing image and apply text-based edits. "
    "CRITICAL: Always maintain the EXACT facial identity and features of the user from the reference. "
    "Output must be a high-quality cinematic photograph in JPEG format."
)

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# Состояния диалога
USER_PHOTO, STYLE_PHOTO, EDITING = range(3)

# --- 3. ИНТЕРФЕЙС (КНОПКИ) ---

def get_main_menu():
    """Кнопка внизу экрана"""
    return ReplyKeyboardMarkup([['🚀 Начать фотосессию']], resize_keyboard=True)

def get_cancel_inline():
    """Инлайн-кнопка под сообщением во время процесса"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_action")]])

def get_editing_options():
    """Инлайн-кнопка под готовым результатом"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Начать всё заново", callback_data="restart_action")]])

# --- 4. ЛОГИКА БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало работы"""
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Добро пожаловать в ИИ-фотостудию!\n\n"
        "Я могу перенести твоё лицо на любое фото или создать образ с нуля.",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

async def init_photoshoot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск процесса после нажатия кнопки"""
    await update.message.reply_text(
        "📸 **Шаг 1:** Пришли мне СВОЁ фото (лицо крупным планом).",
        reply_markup=get_cancel_inline()
    )
    return USER_PHOTO

async def get_user_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение лица пользователя"""
    photo_file = await update.message.photo[-1].get_file()
    context.user_data['user_face'] = await photo_file.download_as_bytearray()
    
    await update.message.reply_text(
        "✅ Лицо сохранено!\n\n"
        "**Шаг 2:** Теперь пришли фото-референс (образ, который хочешь примерить).",
        reply_markup=get_cancel_inline()
    )
    return STYLE_PHOTO

async def generate_initial_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первая генерация по двум фото"""
    photo_file = await update.message.photo[-1].get_file()
    style_ref_raw = await photo_file.download_as_bytearray()
    
    user_face_raw = context.user_data.get('user_face')
    
    # Индикация прогресса
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
    status = await update.message.reply_text("🔍 [1/3] Анализирую черты лица...")

    try:
        await asyncio.sleep(1)
        await status.edit_text("🎨 [2/3] Накладываю стиль и свет...")
        
        model = genai.GenerativeModel(model_name='gemini-1.5-pro', system_instruction=SYSTEM_INSTRUCTION)
        prompt = [
            "Merge the face from the first image into the second image's style and scene. Preserve identity.",
            {"mime_type": "image/jpeg", "data": bytes(user_face_raw)},
            {"mime_type": "image/jpeg", "data": bytes(style_ref_raw)}
        ]
        
        # Выполнение в потоке, чтобы не вешать бота
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response = await asyncio.to_thread(model.generate_content, prompt, safety_settings=SAFETY_SETTINGS)

        await status.edit_text("📸 [3/3] Финальная ретушь...")

        if response.parts and response.parts[0].inline_data:
            generated_bytes = response.parts[0].inline_data.data
            context.user_data['current_image'] = generated_bytes # Сохраняем для правок
            
            image_stream = io.BytesIO(generated_bytes)
            await status.delete()
            await update.message.reply_photo(
                photo=image_stream, 
                caption="✨ Готово! Твой образ создан.\n\n"
                        "💬 Напиши правку текстом (например: 'сделай костюм красным') или нажми кнопку ниже.",
                reply_markup=get_editing_options()
            )
            return EDITING
        else:
            await status.edit_text("❌ ИИ не смог обработать фото из-за фильтров. Попробуй другие фото.", reply_markup=get_main_menu())
            return ConversationHandler.END

    except Exception as e:
        logging.error(f"Gen Error: {e}")
        await status.edit_text(f"❌ Техническая ошибка: {str(e)[:50]}...", reply_markup=get_main_menu())
        return ConversationHandler.END

async def process_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Цикл текстовых правок"""
    user_edit_prompt = update.message.text
    current_image = context.user_data.get('current_image')
    original_face = context.user_data.get('user_face')

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    status = await update.message.reply_text(f"🔧 Вношу правку: '{user_edit_prompt}'...")

    try:
        model = genai.GenerativeModel(model_name='gemini-1.5-pro', system_instruction=SYSTEM_INSTRUCTION)
        prompt = [
            f"Modify this image: {user_edit_prompt}. Keep the person's face identical to the reference.",
            {"mime_type": "image/jpeg", "data": bytes(current_image)},
            {"mime_type": "image/jpeg", "data": bytes(original_face)}
        ]
        
        response = await asyncio.to_thread(model.generate_content, prompt, safety_settings=SAFETY_SETTINGS)

        if response.parts and response.parts[0].inline_data:
            generated_bytes = response.parts[0].inline_data.data
            context.user_data['current_image'] = generated_bytes
            
            image_stream = io.BytesIO(generated_bytes)
            await status.delete()
            await update.message.reply_photo(
                photo=image_stream, 
                caption="✅ Изменено! Что-то еще?",
                reply_markup=get_editing_options()
            )
            return EDITING
        else:
            await status.edit_text("❌ Не удалось применить правку. Опиши по-другому.", reply_markup=get_editing_options())
            return EDITING

    except Exception as e:
        logging.error(f"Edit Error: {e}")
        await status.edit_text("❌ Ошибка при редактировании.", reply_markup=get_editing_options())
        return EDITING

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс и возврат в меню"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text("Процесс остановлен. Начнем заново?", reply_markup=get_main_menu())
    return ConversationHandler.END

# --- 5. ЗАПУСК ПРИЛОЖЕНИЯ ---

if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("TG_TOKEN")).build()
    
    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            MessageHandler(filters.Text("🚀 Начать фотосессию"), init_photoshoot)
        ],
        states={
            USER_PHOTO: [
                MessageHandler(filters.PHOTO, get_user_photo),
                CallbackQueryHandler(cancel_callback, pattern="cancel_action")
            ],
            STYLE_PHOTO: [
                MessageHandler(filters.PHOTO, generate_initial_transfer),
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
    print("Бот успешно развернут!")
    app.run_polling()
