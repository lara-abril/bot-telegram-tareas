import os
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TASKS_FILE = "tasks.json"

def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def get_user_tasks(user_id):
    return load_tasks().get(user_id, [])

def save_user_tasks(user_id, user_tasks):
    tasks = load_tasks()
    tasks[user_id] = user_tasks
    save_tasks(tasks)

def ask_groq(prompt):
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500
    )
    return response.choices[0].message.content

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu asistente de tareas con IA.\n\n"
        "📋 *Comandos disponibles:*\n"
        "/tareas — Ver tus tareas pendientes\n"
        "/agregar — Agregar una nueva tarea\n"
        "/analizar — Que la IA analice tu agenda\n"
        "/ayuda — Ver esta ayuda\n\n"
        "También podés escribirme directamente y te respondo 🤖",
        parse_mode="Markdown"
    )

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def ver_tareas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_tasks = get_user_tasks(user_id)

    if not user_tasks:
        await update.message.reply_text("✅ No tenés tareas pendientes. ¡Todo al día!")
        return

    pendientes = [t for t in user_tasks if not t.get("done")]
    completadas = [t for t in user_tasks if t.get("done")]

    msg = f"📋 *Tus tareas* ({len(pendientes)} pendientes, {len(completadas)} completadas)\n\n"
    if pendientes:
        msg += "⏳ *Pendientes:*\n"
        for i, t in enumerate(pendientes):
            emoji = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(t.get("prio", "media"), "🟡")
            fecha = f" — {t['date']}" if t.get("date") else ""
            msg += f"{emoji} {i+1}. {t['title']}{fecha}\n"
    if completadas:
        msg += f"\n✅ *Completadas:* {len(completadas)} tareas\n"

    keyboard = [
        [InlineKeyboardButton("✅ Marcar como hecha", callback_data="marcar")],
        [InlineKeyboardButton("🤖 Analizar con IA", callback_data="analizar")],
        [InlineKeyboardButton("🗑️ Limpiar completadas", callback_data="limpiar")]
    ]
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def agregar_tarea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 Usá el comando así:\n`/agregar Comprar pan | alta | 2026-06-10`\n\n"
            "Formato: `/agregar título | prioridad | fecha`\n"
            "La prioridad (alta/media/baja) y fecha son opcionales.",
            parse_mode="Markdown"
        )
        return

    partes = [p.strip() for p in " ".join(args).split("|")]
    titulo = partes[0]
    prio = partes[1].lower() if len(partes) > 1 else "media"
    fecha = partes[2] if len(partes) > 2 else ""
    if prio not in ["alta", "media", "baja"]:
        prio = "media"

    user_id = str(update.effective_user.id)
    user_tasks = get_user_tasks(user_id)
    user_tasks.append({"id": int(datetime.now().timestamp()), "title": titulo, "prio": prio, "date": fecha, "done": False})
    save_user_tasks(user_id, user_tasks)

    emoji = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(prio, "🟡")
    await update.message.reply_text(
        f"✅ Tarea agregada:\n{emoji} *{titulo}*\nPrioridad: {prio}{f' — Fecha: {fecha}' if fecha else ''}",
        parse_mode="Markdown"
    )

async def analizar_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_tasks = get_user_tasks(user_id)
    if not user_tasks:
        await update.message.reply_text("No tenés tareas para analizar. Agregá algunas con /agregar")
        return

    await update.message.reply_text("🤖 Analizando tu agenda...")
    hoy = datetime.now().strftime("%A %d de %B de %Y")
    lista = "\n".join([
        f"- \"{t['title']}\" | prioridad: {t.get('prio','media')} | fecha: {t.get('date','sin fecha')} | estado: {'completada' if t.get('done') else 'pendiente'}"
        for t in user_tasks
    ])
    respuesta = ask_groq(
        f"Sos un asistente de productividad personal. Hoy es {hoy}.\n\n"
        f"Tareas del usuario:\n{lista}\n\n"
        f"Analizá la agenda y respondé en español con:\n"
        f"1. Resumen del estado general (1-2 oraciones)\n"
        f"2. Las 2-3 tareas más urgentes a atender hoy\n"
        f"3. Una recomendación práctica\n"
        f"Sé amigable, directo y breve (máximo 200 palabras)."
    )
    await update.message.reply_text(f"🤖 *Análisis de tu agenda:*\n\n{respuesta}", parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    if query.data == "analizar":
        user_tasks = get_user_tasks(user_id)
        if not user_tasks:
            await query.message.reply_text("No tenés tareas para analizar.")
            return
        await query.message.reply_text("🤖 Analizando tu agenda...")
        hoy = datetime.now().strftime("%A %d de %B de %Y")
        lista = "\n".join([f"- \"{t['title']}\" | {t.get('prio','media')} | {'hecha' if t.get('done') else 'pendiente'}" for t in user_tasks])
        respuesta = ask_groq(f"Asistente de productividad. Hoy {hoy}. Tareas:\n{lista}\nAnalizá en español, breve y útil.")
        await query.message.reply_text(f"🤖 *Análisis:*\n\n{respuesta}", parse_mode="Markdown")

    elif query.data == "limpiar":
        user_tasks = [t for t in get_user_tasks(user_id) if not t.get("done")]
        save_user_tasks(user_id, user_tasks)
        await query.message.reply_text("🗑️ Tareas completadas eliminadas.")

    elif query.data == "marcar":
        pendientes = [t for t in get_user_tasks(user_id) if not t.get("done")]
        if not pendientes:
            await query.message.reply_text("No hay tareas pendientes para marcar.")
            return
        keyboard = [[InlineKeyboardButton(f"✅ {t['title']}", callback_data=f"done_{t['id']}")] for t in pendientes[:10]]
        await query.message.reply_text("¿Cuál tarea querés marcar como hecha?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("done_"):
        task_id = int(query.data.replace("done_", ""))
        user_tasks = get_user_tasks(user_id)
        for t in user_tasks:
            if t["id"] == task_id:
                t["done"] = True
                break
        save_user_tasks(user_id, user_tasks)
        await query.message.reply_text("✅ ¡Tarea marcada como completada!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_tasks = get_user_tasks(user_id)
    pendientes = [t["title"] for t in user_tasks if not t.get("done")]
    context_tareas = f"\nTareas pendientes del usuario: {', '.join(pendientes[:5])}" if pendientes else ""
    await update.message.reply_text("🤖 Pensando...")
    respuesta = ask_groq(
        f"Sos un asistente personal amigable en Telegram. Respondé en español, breve y útil.{context_tareas}\n\nMensaje: {update.message.text}"
    )
    await update.message.reply_text(respuesta)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("tareas", ver_tareas))
    app.add_handler(CommandHandler("agregar", agregar_tarea))
    app.add_handler(CommandHandler("analizar", analizar_agenda))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Bot iniciado con Groq!")
    app.run_polling()

if __name__ == "__main__":
    main()
