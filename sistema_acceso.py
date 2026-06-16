import asyncio
import json
import os
from telegram import Update
from datetime import datetime
from telegram.ext import Application, CommandHandler, ContextTypes
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522

# CONFIGURACIÓN
TOKEN_TELEGRAM = "8419122906:AAGvTHQ-1ixQsGaMa2Ntb92HVDPVLo0CSrw"
DB_FILE = "usuarios.json"

# ID de Telegram donde el bot enviará las alertas de intrusos de forma automática
# REEMPLAZAD este número por vuestro ID real (sin comillas)
CHAT_ID_AVISOS = 5007522722  

# Pines GPIO de la Raspberry Pi (Numeración BOARD - Pines Físicos)
LED_VERDE = 11  
LED_ROJO = 13   
BUZZER = 15     

# Variables globales de control del sistema
modo_registro = False
sistema_bloqueado = False  # Controla el blindaje del laboratorio
nombre_nuevo_usuario = ""
uid_detectado = None
evento_registro = asyncio.Event()

# Inicialización de periféricos y hardware
reader = SimpleMFRC522()
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
GPIO.setup(LED_VERDE, GPIO.OUT)
GPIO.setup(LED_ROJO, GPIO.OUT)
GPIO.setup(BUZZER, GPIO.OUT)

# Asegurar estado inicial apagado
GPIO.output(LED_VERDE, GPIO.LOW)
GPIO.output(LED_ROJO, GPIO.LOW)
GPIO.output(BUZZER, GPIO.LOW)


# GESTIÓN DE LA BASE DE DATOS LOCAL (JSON)
def inicializar_db():
    """Crea el archivo JSON local con un usuario de prueba si no existe."""
    if not os.path.exists(DB_FILE):
        estructura = {
            "tarjetas_autorizadas": {
                "12345678": "Usuario de Prueba"
            },
            "logs": []
        }
        with open(DB_FILE, "w") as f:
            json.dump(estructura, f, indent=4)
    print("[INFO] Base de datos JSON lista y cargada.")

def guardar_nuevo_usuario(uid, nombre):
    """Inserta de forma permanente una nueva tarjeta vinculada a un nombre."""
    with open(DB_FILE, "r") as f:
        data = json.load(f)
    data["tarjetas_autorizadas"][str(uid)] = nombre
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def verificar_base_datos(uid):
    """Comprueba si el UID de la tarjeta escaneada existe en el JSON."""
    with open(DB_FILE, "r") as f:
        data = json.load(f)
    if str(uid) in data["tarjetas_autorizadas"]:
        return data["tarjetas_autorizadas"][str(uid)]
    return None

async def guardar_log_acceso(nombre_usuario):
    """Añade un registro con la fecha y hora actual a la sección 'logs' del JSON."""
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    nuevo_registro = f"{nombre_usuario} entró el {ahora}"
    try:
        with open(DB_FILE, "r") as f:
            base_datos = json.load(f)
        if "logs" not in base_datos:
            base_datos["logs"] = []
        base_datos["logs"].append(nuevo_registro)
        with open(DB_FILE, "w") as f:
            json.dump(base_datos, f, indent=4)
        print(f"[LOG] Registro guardado: {nuevo_registro}")
    except Exception as e:
        print(f"[ERROR LOG] No se pudo guardar: {e}")


# RESPUESTAS DE HARDWARE (FEEDBACK)
async def feedback_acceso_concedido():
    """Enciende el LED verde y pita de forma continua durante la apertura."""
    GPIO.output(LED_VERDE, GPIO.HIGH)
    GPIO.output(BUZZER, GPIO.HIGH)
    await asyncio.sleep(0.5)
    GPIO.output(BUZZER, GPIO.LOW)
    await asyncio.sleep(2.0)  
    GPIO.output(LED_VERDE, GPIO.LOW)

async def feedback_acceso_denegado():
    """Enciende el LED rojo y emite 3 pitidos cortos de alerta."""
    GPIO.output(LED_ROJO, GPIO.HIGH)
    for _ in range(3):
        GPIO.output(BUZZER, GPIO.HIGH)
        await asyncio.sleep(0.1)
        GPIO.output(BUZZER, GPIO.LOW)
        await asyncio.sleep(0.1)
    GPIO.output(LED_ROJO, GPIO.LOW)


# MANEJADORES DE COMANDOS DEL BOT DE TELEGRAM
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida y listado de comandos actualizado."""
    await update.message.reply_text(
        "👋 ¡Sistema de Control de Acceso Inteligente Activo!\n\n"
        "Comandos de gestión disponibles:\n"
        "/alta [Nombre] - Registrar nueva tarjeta\n"
        "/baja [Nombre] - Eliminar usuario por nombre\n"
        "/usuarios - Ver lista de personas autorizadas\n"
        "/historial - Ver últimos 10 accesos guardados\n"
        "/bloqueo - Blindar sistema (desactiva RFID)\n"
        "/desbloqueo - Desactivar blindaje de seguridad\n"
        "/apertura - Apertura remota manual\n"
        "/estado - Comprobar si el sistema está online"
    )

async def alta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Espera el contacto físico con el sensor para vincular tarjeta."""
    global modo_registro, nombre_nuevo_usuario, uid_detectado
    if modo_registro:
        await update.message.reply_text("⚠️ Ya hay un proceso de alta en curso. Espera a que termine.")
        return
    if not context.args:
        await update.message.reply_text("❌ Uso incorrecto. Formato: /alta [Nombre]")
        return

    nombre_nuevo_usuario = " ".join(context.args)
    modo_registro = True
    uid_detectado = None
    evento_registro.clear()

    await update.message.reply_text(
        f"⏳ Modo registro activado para: *{nombre_nuevo_usuario}*.\n"
        "Pasa la nueva tarjeta o llavero por el lector físico ahora (Tienes 30 segundos)...",
        parse_mode="Markdown"
    )

    try:
        await asyncio.wait_for(evento_registro.wait(), timeout=30.0)
        guardar_nuevo_usuario(uid_detectado, nombre_nuevo_usuario)
        await update.message.reply_text(
            f"✅ ¡Éxito! Tarjeta vinculada a *{nombre_nuevo_usuario}* (UID: {uid_detectado})", 
            parse_mode="Markdown"
        )
    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ Tiempo de espera agotado. El proceso de registro ha sido cancelado.")
    finally:
        modo_registro = False

async def baja(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina un usuario buscando por su nombre en el JSON."""
    if not context.args:
        await update.message.reply_text("❌ Uso incorrecto. Formato: /baja [Nombre]")
        return

    nombre_eliminar = " ".join(context.args)

    try:
        with open(DB_FILE, "r") as f:
            base_datos = json.load(f)
    except FileNotFoundError:
        await update.message.reply_text("⚠️ No hay base de datos creada.")
        return

    datos = base_datos.get("tarjetas_autorizadas", {})
    uid_a_borrar = None

    for uid, nombre in datos.items():
        if nombre.strip().lower() == nombre_eliminar.strip().lower():
            uid_a_borrar = uid
            nombre_exacto = nombre  
            break

    if uid_a_borrar:
        del base_datos["tarjetas_autorizadas"][uid_a_borrar]
        with open(DB_FILE, "w") as f:
            json.dump(base_datos, f, indent=4)
        await update.message.reply_text(f"🗑️ El usuario *{nombre_exacto}* ha sido eliminado del sistema.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"🔍 No se encontró a ningún usuario llamado *{nombre_eliminar}*.")

async def usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de usuarios autorizados."""
    try:
        with open(DB_FILE, "r") as f:
            base_datos = json.load(f)
    except FileNotFoundError:
        await update.message.reply_text("⚠️ No hay base de datos creada todavía.")
        return

    datos = base_datos.get("tarjetas_autorizadas", {})

    if not datos:
        await update.message.reply_text("👥 No hay ningún usuario registrado en el sistema.")
        return

    respuesta = "📋 *Usuarios Registrados:*\n\n"
    for uid, nombre in datos.items():
        respuesta += f"• *{nombre}* — (UID: `{uid}`)\n"
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los últimos 10 accesos almacenados en el JSON."""
    try:
        with open(DB_FILE, "r") as f:
            base_datos = json.load(f)
    except FileNotFoundError:
        await update.message.reply_text("⚠️ No hay base de datos.")
        return

    logs = base_datos.get("logs", [])
    if not logs:
        await update.message.reply_text("📭 El historial está vacío. Nadie ha fichado todavía.")
        return

    ultimos_logs = logs[-10:]
    respuesta = "📊 *Últimos Accesos Registrados:*\n\n"
    for log in reversed(ultimos_logs):  
        respuesta += f"🔑 {log}\n"
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def bloqueo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa el bloqueo de seguridad deshabilitando la lectura física."""
    global sistema_bloqueado
    sistema_bloqueado = True
    await update.message.reply_text("🔒 *SISTEMA BLINDADO*. El lector RFID ha sido desactivado. Acceso físico denegado.", parse_mode="Markdown")

async def desbloqueo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restaura el sistema al funcionamiento normal."""
    global sistema_bloqueado
    sistema_bloqueado = False
    await update.message.reply_text("🔓 *SISTEMA DESBLOQUEADO*. El lector RFID vuelve a estar plenamente operativo.", parse_mode="Markdown")

async def apertura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite abrir la puerta de forma remota desde el chat de Telegram."""
    await update.message.reply_text("🔓 Comando recibido desde Telegram: Abriendo puerta de forma remota...")
    asyncio.create_task(feedback_acceso_concedido())

async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comprobación rápida de salud del script."""
    estado_lector = "🔒 BLINDADO" if sistema_bloqueado else "🟢 NORMAL"
    await update.message.reply_text(f"🟢 El sistema está online.\n🛡️ Modo del Lector RFID: *{estado_lector}*", parse_mode="Markdown")


# BUCLE ASÍNCRONO DEL LECTOR RFID (SEGUNDO PLANO)
async def bucle_rfid(app: Application):
    """Escanea continuamente el lector RC522 aplicando filtros de seguridad y logs."""
    global modo_registro, uid_detectado, sistema_bloqueado
    print("[RFID] Bucle del lector activo y buscando tarjetas...")
    
    while True:
        loop = asyncio.get_running_loop()
        try:
            id_tarjeta, text = await loop.run_in_executor(None, reader.read_no_block)
            
            if id_tarjeta is not None:
                uid = str(id_tarjeta)
                print(f"[RFID] Tarjeta detectada en el sensor. UID: {uid}")
                
                # 1. Filtro estricto si el Modo Bloqueo está activo
                if sistema_bloqueado:
                    print(f"[RECHAZADO] Intento de acceso denegado (UID: {uid}) por Bloqueo Activo.")
                    asyncio.create_task(feedback_acceso_denegado())
                    # Notificación automática de seguridad a Telegram
                    mensaje_bloqueo = f"🔒 *Intento de acceso denegado:* El sistema está en *Modo Bloqueo* e ignoró una tarjeta (UID: `{uid}`)."
                    asyncio.create_task(app.bot.send_message(chat_id=CHAT_ID_AVISOS, text=mensaje_bloqueo, parse_mode="Markdown"))
                    await asyncio.sleep(2)
                    continue

                # 2. Modo de registro para dar de alta
                if modo_registro:
                    uid_detectado = uid
                    loop.call_soon_threadsafe(evento_registro.set)
                
                # 3. Funcionamiento normal de verificación
                else:
                    usuario = verificar_base_datos(uid)
                    if usuario:
                        print(f"[ACCESO CONCEDIDO] Bienvenido/a {usuario}")
                        await guardar_log_acceso(usuario)
                        asyncio.create_task(feedback_acceso_concedido())
                    else:
                        print(f"[ACCESO DENEGADO] ID de tarjeta no reconocido: {uid}")
                        asyncio.create_task(feedback_acceso_denegado())
                        # 🚨 Notificación automática de intruso
                        mensaje_intruso = f"🚨 *¡ALERTA DE INTRUSO!* 🚨\nSe ha intentado escanear una tarjeta NO autorizada.\n🆔 *UID:* `{uid}`"
                        asyncio.create_task(app.bot.send_message(chat_id=CHAT_ID_AVISOS, text=mensaje_intruso, parse_mode="Markdown"))
                
                await asyncio.sleep(2) 
        except Exception as e:
            print(f"[ERROR CRÍTICO HARDWARE] {e}")
            
        await asyncio.sleep(0.1)


# PROGRAMA PRINCIPAL
def main():
    inicializar_db()
    
    # Construcción y arranque de la API de Telegram
    app = Application.builder().token(TOKEN_TELEGRAM).build()

    # Registro de los comandos asignados a sus funciones
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alta", alta))
    app.add_handler(CommandHandler("baja", baja))
    app.add_handler(CommandHandler("usuarios", usuarios))
    app.add_handler(CommandHandler("historial", historial))
    app.add_handler(CommandHandler("bloqueo", bloqueo))
    app.add_handler(CommandHandler("desbloqueo", desbloqueo))
    app.add_handler(CommandHandler("apertura", apertura))
    app.add_handler(CommandHandler("estado", estado))

    # Integración del bucle de lectura RFID en el gestor de eventos asíncronos
    loop = asyncio.get_event_loop()
    loop.create_task(bucle_rfid(app))

    print("[SISTEMA] Arrancando bot de Telegram por Polling... Pulsa Ctrl+C para apagar.")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    finally:
        GPIO.cleanup()
        print("\n[SISTEMA] GPIO liberados. Programa finalizado correctamente.")