import os
import json
import base64
import pytz
from datetime import datetime, time, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from groq import Groq
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import requests as req_lib

TZ = pytz.timezone("America/Argentina/Buenos_Aires")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GOOGLE_TOKEN_B64 = os.environ.get("GOOGLE_TOKEN_B64")
GOOGLE_CREDENTIALS_B64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
TASKS_FILE = "tasks.json"
CATEGORIAS = ["trabajo", "personal", "estudio", "salud", "hogar", "otro"]
CATEGORIA_EMOJI = {"trabajo": "💼", "personal": "🙋", "estudio": "📚", "salud": "❤️‍🩹", "hogar": "🏠", "otro": "📌"}

# --- Google Auth ---
def get_google_creds():
    token_data = json.loads(base64.b64decode(GOOGLE_TOKEN_B64).decode())
    creds_data = json.loads(base64.b64decode(GOOGLE_CREDENTIALS_B64).decode())
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri"),
        client_id=creds_data["installed"]["client_id"],
        client_secret=creds_data["installed"]["client_secret"],
        scopes=token_data.get("scopes")
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

# --- Gmail ---
def get_important_emails(max_results=5):
    creds = get_google_creds()
    token = creds.token
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?labelIds=INBOX&labelIds=UNREAD&maxResults={max_results}"
    r = req_lib.get(url, headers=headers)
    messages = r.json().get("messages", [])
    emails = []
    for msg in messages:
        detail = req_lib.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
            headers=headers
        ).json()
        hdrs = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        emails.append({
            "from": hdrs.get("From", "Desconocido"),
            "subject": hdrs.get("Subject", "Sin asunto"),
            "date": hdrs.get("Date", ""),
            "id": msg["id"]
        })
    return emails

# --- Google Calendar ---
def get_today_events():
    creds = get_google_creds()
    token = creds.token
    headers = {"Authorization": f"Bearer {token}"}
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0).isoformat()
    end = now.replace(hour=23, minute=59, second=59).isoformat()
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?timeMin={req_lib.utils.quote(start)}&timeMax={req_lib.utils.quote(end)}&singleEvents=true&orderBy=startTime"
    r = req_lib.get(url, headers=headers)
    return r.json().get("items", [])

def create_calendar_event(title, date_str, time_str="10:00", description=""):
    creds = get_google_creds()
    token = creds.token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    start_dt = TZ.localize(start_dt)
    end_dt = start_dt + timedelta(hours=1)
    event = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
    }
    r = req_lib.post("https://www.googleapis.com/calendar/v3/calendars/primary/events", headers=headers, json=event)
    return r.json()

# --- Tareas ---
def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def get_user_tasks(user_id):
    return load_tasks().get(str(user_id), [])

def save_user_tasks(user_id, user_tasks):
    tasks = load_tasks()
    tasks[str(user_id)] = user_tasks
    save_tasks(tasks)

def formato_tarea(t, idx=None):
    prio_emoji = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(t.get("prio", "media"), "🟡")
    cat_emoji = CATEGORIA_EMOJI.get(t.get("categoria", "otro"), "📌")
    fecha = f" — {t['date']}" if t.get("date") else ""
    num = f"{idx}. " if idx is not None else ""
    tilde = "✅ " if t.get("done") else ""
    return f"{tilde}{prio_emoji}{cat_emoji} {num}{t['title']}{fecha}"

# --- IA ---
def ask_groq(prompt):
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500
    )
    return response.choices[0].message.content

# --- Comandos ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu asistente personal con IA.\n\n"
        "📋 *Tareas:*\n"
        "/tareas — Ver tareas\n"
        "/agregar — Agregar tarea\n"
        "/editar — Editar tarea\n"
        "/analizar — Análisis IA\n"
        "/resumen — Resumen de hoy\n\n"
        "📧 *Gmail:*\n"
        "/mails — Ver mails no leídos\n\n"
        "📅 *Calendario:*\n"
        "/agenda — Ver eventos de hoy\n"
        "/evento — Crear evento\n\n"
        "Formato agregar: `/agregar título | prioridad | fecha | categoría`",
        parse_mode="Markdown"
    )

async def ver_mails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📧 Revisando tu Gmail...")
    try:
        emails = get_important_emails()
        if not emails:
            await update.message.reply_text("✅ No tenés mails no leídos.")
            return
        msg = f"📧 *Mails no leídos ({len(emails)}):*\n\n"
        for i, e in enumerate(emails, 1):
            remitente = e['from'].split('<')[0].strip()[:30]
            asunto = e['subject'][:50]
            msg += f"{i}. 👤 {remitente}\n   📌 {asunto}\n\n"

        # Resumen con IA
        lista = "\n".join([f"- De: {e['from'][:40]} | Asunto: {e['subject'][:60]}" for e in emails])
        resumen = ask_groq(f"Resumí estos mails en español en 2-3 oraciones, indicando cuáles parecen urgentes:\n{lista}")
        msg += f"🤖 *Resumen IA:*\n{resumen}"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as ex:
        await update.message.reply_text(f"❌ Error al acceder a Gmail: {str(ex)[:100]}")

async def ver_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Revisando tu calendario...")
    try:
        events = get_today_events()
        if not events:
            await update.message.reply_text("✅ No tenés eventos hoy en Google Calendar.")
            return
        msg = f"📅 *Tu agenda de hoy:*\n\n"
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            if "T" in start:
                hora = datetime.fromisoformat(start).strftime("%H:%M")
            else:
                hora = "Todo el día"
            msg += f"🕐 {hora} — {e.get('summary', 'Sin título')}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as ex:
        await update.message.reply_text(f"❌ Error al acceder al calendario: {str(ex)[:100]}")

async def crear_evento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📅 Formato:\n`/evento título | fecha | hora`\n\n"
            "Ejemplo:\n`/evento Reunión trabajo | 2026-06-11 | 14:00`\n\n"
            "La hora es opcional (por defecto 10:00).",
            parse_mode="Markdown"
        )
        return
    partes = [p.strip() for p in " ".join(args).split("|")]
    titulo = partes[0]
    fecha = partes[1] if len(partes) > 1 else datetime.now(TZ).strftime("%Y-%m-%d")
    hora = partes[2] if len(partes) > 2 else "10:00"
    try:
        event = create_calendar_event(titulo, fecha, hora)
        await update.message.reply_text(
            f"✅ Evento creado en Google Calendar:\n📅 *{titulo}*\n🕐 {fecha} a las {hora}",
            parse_mode="Markdown"
        )
    except Exception as ex:
        await update.message.reply_text(f"❌ Error al crear el evento: {str(ex)[:100]}")

async def ver_tareas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = get_user_tasks(user_id)
    if not user_tasks:
        await update.message.reply_text("✅ No tenés tareas. ¡Agregá una con /agregar!")
        return
    pendientes = [t for t in user_tasks if not t.get("done")]
    completadas = [t for t in user_tasks if t.get("done")]
    msg = f"📋 *Tus tareas* ({len(pendientes)} pendientes, {len(completadas)} completadas)\n\n"
    for cat in CATEGORIAS:
        cat_tasks = [t for t in pendientes if t.get("categoria", "otro") == cat]
        if cat_tasks:
            msg += f"{CATEGORIA_EMOJI[cat]} *{cat.capitalize()}:*\n"
            for i, t in enumerate(cat_tasks, 1):
                msg += f"{formato_tarea(t, i)}\n"
            msg += "\n"
    if completadas:
        msg += f"✅ *Completadas:* {len(completadas)} tareas\n"
    keyboard = [
        [InlineKeyboardButton("✅ Marcar como hecha", callback_data="marcar")],
        [InlineKeyboardButton("✏️ Editar tarea", callback_data="editar_menu")],
        [InlineKeyboardButton("🤖 Analizar con IA", callback_data="analizar")],
        [InlineKeyboardButton("🗑️ Limpiar completadas", callback_data="limpiar")]
    ]
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def agregar_tarea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 Formato:\n`/agregar título | prioridad | fecha | categoría`\n\nEjemplo:\n`/agregar Llamar al médico | alta | 2026-06-10 | salud`",
            parse_mode="Markdown"
        )
        return
    partes = [p.strip() for p in " ".join(args).split("|")]
    titulo = partes[0]
    prio = partes[1].lower() if len(partes) > 1 else "media"
    fecha = partes[2].strip() if len(partes) > 2 else ""
    categoria = partes[3].lower().strip() if len(partes) > 3 else "otro"
    if prio not in ["alta", "media", "baja"]: prio = "media"
    if categoria not in CATEGORIAS: categoria = "otro"
    user_id = update.effective_user.id
    user_tasks = get_user_tasks(user_id)
    user_tasks.append({"id": int(datetime.now().timestamp()), "title": titulo, "prio": prio, "date": fecha, "categoria": categoria, "done": False})
    save_user_tasks(user_id, user_tasks)
    prio_emoji = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(prio, "🟡")
    await update.message.reply_text(f"✅ Tarea agregada:\n{prio_emoji} *{titulo}*\nPrioridad: {prio} | Categoría: {categoria}{f' | Fecha: {fecha}' if fecha else ''}", parse_mode="Markdown")

async def resumen_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = get_user_tasks(user_id)
    hoy = datetime.now(TZ).strftime("%Y-%m-%d")
    tareas_hoy = [t for t in user_tasks if t.get("date") == hoy and not t.get("done")]
    vencidas = [t for t in user_tasks if t.get("date") and t.get("date") < hoy and not t.get("done")]
    msg = f"📅 *Resumen de hoy — {datetime.now(TZ).strftime('%d/%m/%Y')}*\n\n"
    if tareas_hoy:
        msg += "🗓️ *Tareas para hoy:*\n" + "\n".join([formato_tarea(t) for t in tareas_hoy]) + "\n\n"
    if vencidas:
        msg += f"⚠️ *Vencidas ({len(vencidas)}):*\n" + "\n".join([formato_tarea(t) for t in vencidas]) + "\n\n"
    if not tareas_hoy and not vencidas:
        msg += "🎉 ¡Todo al día!\n\n"
    msg += "Usá /agenda para ver tus eventos de Google Calendar."
    await update.message.reply_text(msg, parse_mode="Markdown")

async def analizar_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = get_user_tasks(user_id)
    if not user_tasks:
        await update.message.reply_text("No tenés tareas. Agregá con /agregar")
        return
    await update.message.reply_text("🤖 Analizando tu agenda...")
    hoy = datetime.now(TZ).strftime("%A %d de %B de %Y")
    lista = "\n".join([f"- \"{t['title']}\" | {t.get('prio','media')} | {t.get('categoria','otro')} | {t.get('date','sin fecha')} | {'hecha' if t.get('done') else 'pendiente'}" for t in user_tasks])
    respuesta = ask_groq(f"Asistente de productividad. Hoy {hoy}.\nTareas:\n{lista}\nAnalizá en español: resumen, tareas urgentes, recomendación. Máximo 200 palabras.")
    await update.message.reply_text(f"🤖 *Análisis:*\n\n{respuesta}", parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if query.data == "analizar":
        user_tasks = get_user_tasks(user_id)
        if not user_tasks:
            await query.message.reply_text("No tenés tareas.")
            return
        await query.message.reply_text("🤖 Analizando...")
        lista = "\n".join([f"- \"{t['title']}\" | {t.get('prio','media')} | {'hecha' if t.get('done') else 'pendiente'}" for t in user_tasks])
        respuesta = ask_groq(f"Analizá estas tareas en español, breve y útil:\n{lista}")
        await query.message.reply_text(f"🤖 *Análisis:*\n\n{respuesta}", parse_mode="Markdown")
    elif query.data == "limpiar":
        save_user_tasks(user_id, [t for t in get_user_tasks(user_id) if not t.get("done")])
        await query.message.reply_text("🗑️ Completadas eliminadas.")
    elif query.data == "marcar":
        pendientes = [t for t in get_user_tasks(user_id) if not t.get("done")]
        if not pendientes:
            await query.message.reply_text("No hay pendientes.")
            return
        keyboard = [[InlineKeyboardButton(f"✅ {t['title']}", callback_data=f"done_{t['id']}")] for t in pendientes[:10]]
        await query.message.reply_text("¿Cuál completaste?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith("done_"):
        task_id = int(query.data.replace("done_", ""))
        user_tasks = get_user_tasks(user_id)
        for t in user_tasks:
            if t["id"] == task_id:
                t["done"] = True
        save_user_tasks(user_id, user_tasks)
        await query.message.reply_text("✅ ¡Tarea completada! 🎉")
    elif query.data == "editar_menu":
        pendientes = [t for t in get_user_tasks(user_id) if not t.get("done")]
        if not pendientes:
            await query.message.reply_text("No hay tareas para editar.")
            return
        keyboard = [[InlineKeyboardButton(f"✏️ {t['title']}", callback_data=f"editar_{t['id']}")] for t in pendientes[:10]]
        await query.message.reply_text("¿Cuál querés editar?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith("editar_"):
        task_id = int(query.data.replace("editar_", ""))
        tarea = next((t for t in get_user_tasks(user_id) if t["id"] == task_id), None)
        if not tarea:
            await query.message.reply_text("Tarea no encontrada.")
            return
        context.user_data["editando"] = task_id
        await query.message.reply_text(f"✏️ Editando: *{tarea['title']}*\n\nEnviá: `nuevo título | prioridad | fecha | categoría`", parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if "editando" in context.user_data:
        task_id = context.user_data.pop("editando")
        user_tasks = get_user_tasks(user_id)
        tarea = next((t for t in user_tasks if t["id"] == task_id), None)
        if tarea:
            partes = [p.strip() for p in text.split("|")]
            if len(partes) > 0 and partes[0]: tarea["title"] = partes[0]
            if len(partes) > 1 and partes[1] in ["alta","media","baja"]: tarea["prio"] = partes[1]
            if len(partes) > 2 and partes[2]: tarea["date"] = partes[2]
            if len(partes) > 3 and partes[3] in CATEGORIAS: tarea["categoria"] = partes[3]
            save_user_tasks(user_id, user_tasks)
            await update.message.reply_text(f"✅ Tarea actualizada:\n{formato_tarea(tarea)}", parse_mode="Markdown")
        return
    user_tasks = get_user_tasks(user_id)
    pendientes = [t["title"] for t in user_tasks if not t.get("done")]
    context_tareas = f"\nTareas pendientes: {', '.join(pendientes[:5])}" if pendientes else ""
    await update.message.reply_text("🤖 Pensando...")
    respuesta = ask_groq(f"Asistente personal en Telegram. Respondé en español, breve y útil.{context_tareas}\n\nMensaje: {text}")
    await update.message.reply_text(respuesta)

async def enviar_resumen_diario(context):
    all_tasks = load_tasks()
    hoy = datetime.now(TZ).strftime("%Y-%m-%d")
    for user_id, user_tasks in all_tasks.items():
        tareas_hoy = [t for t in user_tasks if t.get("date") == hoy and not t.get("done")]
        vencidas = [t for t in user_tasks if t.get("date") and t.get("date") < hoy and not t.get("done")]
        if tareas_hoy or vencidas:
            msg = "🌅 *Buenos días! Resumen de hoy:*\n\n"
            if tareas_hoy:
                msg += "📅 *Para hoy:*\n" + "\n".join([formato_tarea(t) for t in tareas_hoy]) + "\n\n"
            if vencidas:
                msg += f"⚠️ Tenés {len(vencidas)} tarea(s) vencida(s)\n"
            msg += "\nUsá /agenda para ver tu calendario."
            try:
                await context.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
            except:
                pass

async def recordatorio_eventos(context):
    try:
        events = get_today_events()
        now = datetime.now(TZ)
        all_tasks = load_tasks()
        for user_id in all_tasks.keys():
            for e in events:
                start = e["start"].get("dateTime", "")
                if not start:
                    continue
                event_time = datetime.fromisoformat(start)
                diff = (event_time - now).total_seconds() / 60
                if 25 <= diff <= 35:
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=f"⏰ *Recordatorio:* En 30 minutos tenés:\n📅 {e.get('summary', 'Evento')} a las {event_time.strftime('%H:%M')}",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
    except:
        pass

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", start))
    app.add_handler(CommandHandler("tareas", ver_tareas))
    app.add_handler(CommandHandler("agregar", agregar_tarea))
    app.add_handler(CommandHandler("editar", lambda u, c: handle_callback(u, c)))
    app.add_handler(CommandHandler("analizar", analizar_agenda))
    app.add_handler(CommandHandler("resumen", resumen_hoy))
    app.add_handler(CommandHandler("mails", ver_mails))
    app.add_handler(CommandHandler("agenda", ver_agenda))
    app.add_handler(CommandHandler("evento", crear_evento))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    job_queue = app.job_queue
    job_queue.run_daily(enviar_resumen_diario, time=time(8, 0, tzinfo=TZ))
    job_queue.run_repeating(recordatorio_eventos, interval=300, first=10)
    print("✅ Bot v3 con Gmail y Calendar iniciado!")
    app.run_polling()

if __name__ == "__main__":
    main()
