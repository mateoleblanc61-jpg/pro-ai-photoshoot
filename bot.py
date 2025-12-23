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

# --- 1. ВЕБ-СЕРВЕР ДЛЯ HEALTH CHECK ---
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

# --- 2. КОНФИГУРАЦИЯ ---
logging.basicConfig(level=logging.INFO)
genai.configure(api_key=os.getenv("GEMINI_KEY"))

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

# --- 3. КЛАВИАТУРЫ ---

def get_main_menu():
    return ReplyKeyboardMarkup([['🚀 Начать фотосессию']], resize_keyboard=True)

def get_cancel_inline():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_action")]])

def get_editing_options():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Начать всё заново", callback_data="restart_action")]])

# --- 4. ЛОГИКА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Добро пожаловать в ИИ-фотостудию!\nПришли свое лицо, выбери стиль, а я сделаю магию.",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

async def init_photoshoot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 **Шаг 1:** Пришли мне СВОЁ фото (лицо крупным планом).",
        reply_markup=get_cancel_inline()
    )
    return USER_PHOTO

async def get_user_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, пришли именно фото.")
        return USER_PHOTO
        
    photo_file = await update.message.photo[-1].get_file()
    context.user_data['user_face'] = await photo_file.download_as_bytearray()
    
    await update.message.reply_text(
        "✅ Лицо сохранено!\n\n**Шаг 2:** Теперь пришли фото-референс (образ).",
        reply_markup=get_cancel_inline()
    )
    return STYLE_PHOTO

async def generate_initial_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Нужно фото стиля.")
        return STYLE_PHOTO

    photo_file = await update.message.photo[-1].get_file()
    style_ref_raw = await photo_file.download_as_bytearray()
    user_face_raw = context.user_data.get('user_face')
    
    status = await update.message.reply_text("🔍 [1/3] Анализирую черты лица...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")

    try:
        await status.edit_text("🎨 [2/3] Накладываю стиль и свет...")
        
        model = genai.GenerativeModel(model_name='gemini-1.5-pro', system_instruction=SYSTEM_INSTRUCTION)
        prompt = [
            "Merge face from image 1 to style of image 2. High resolution.",
            {"mime_type": "image/jpeg", "data": bytes(user_face_raw)},
            {"mime_type": "image/jpeg", "data": bytes(style_ref_raw)}
        ]
        
        response = await asyncio.to_thread(model.generate_content, prompt, safety_settings=SAFETY_SETTINGS)
        await status.edit_text("📸 [3/3] Финальная ретушь...")

        if response.parts and response.parts[0].inline_data:
            generated_bytes = response.parts[0].inline_data.data
            context.user_data['current_image'] = generated_bytes
            
            await status.delete()
            await update.message.reply_photo(
                photo=io.BytesIO(generated_bytes), 
                caption="✨ Готово! Напиши правку текстом или нажми кнопку ниже.",
                reply_markup=get_editing_options()
            )
            return EDITING
        else:
            # ИСПРАВЛЕНИЕ: Используем reply_text вместо edit_text для ReplyKeyboardMarkup
            await status.delete()
            await update.message.reply_text("❌ Ошибка фильтров ИИ. Попробуй другие фото.", reply_markup=get_main_menu())
            return ConversationHandler.END

    except Exception as e:
        logging.error(f"Gen Error: {e}")
        await status.delete()
        await update.message.reply_text(f"❌ Техническая ошибка.", reply_markup=get_main_menu())
        return ConversationHandler.END

async def process_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_edit_prompt = update.message.text
    current_image = context.user_data.get('current_image')
    original_face = context.user_data.get('user_face')

    status = await update.message.reply_text(f"🔧 Вношу правку: '{user_edit_prompt}'...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        model = genai.GenerativeModel(model_name='gemini-1.5-pro', system_instruction=SYSTEM_INSTRUCTION)
        prompt = [
            f"Modify this image: {user_edit_prompt}. Keep face identical.",
            {"mime_type": "image/jpeg", "data": bytes(current_image)},
            {"mime_type": "image/jpeg", "data": bytes(original_face)}
        ]
        
        response = await asyncio.to_thread(model.generate_content, prompt, safety_settings=SAFETY_SETTINGS)

        if response.parts and response.parts[0].inline_data:
            generated_bytes = response.parts[0].inline_data.data
            context.user_data['current_image'] = generated_bytes
            
            await status.delete()
            await update.message.reply_photo(
                photo=io.BytesIO(generated_bytes), 
                caption="✅ Изменено! Что-то еще?",
                reply_markup=get_editing_options()
            )
            return EDITING
        else:
            await status.edit_text("❌ Не удалось применить правку.", reply_markup=get_editing_options())
            return EDITING
    except Exception as e:
        await status.edit_text("❌ Ошибка редактирования.", reply_markup=get_editing_options())
        return EDITING

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    # Удаляем сообщение с инлайн кнопкой и отправляем новое с обычным меню
    await query.message.delete()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Процесс остановлен. Начнем заново?",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

# --- 5. ЗАПУСК ---

if __name__ == '__main__':
    token = os.getenv("TG_TOKEN")
    if not token:
        print("Ошибка: TG_TOKEN не найден в переменных окружения!")
        exit(1)

    app = ApplicationBuilder().token(token).build()
    
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
    print("Бот запущен!")
    app.run_polling()
