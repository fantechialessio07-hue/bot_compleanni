from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import threading
from threading import Thread
from zoneinfo import ZoneInfo

from flask import Flask
import gspread
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Finto server web per mantenere felice Render ---
app_web = Flask(__name__)


@app_web.route("/")
def home():
  return "Il bot di Telegram è attivo e online!"


def run_web():
  port = int(os.environ.get("PORT", 10000))
  app_web.run(host="0.0.0.0", port=port)


Thread(target=run_web, daemon=True).start()


# --- CONFIGURAZIONE DEL SISTEMA DI LOG ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Spegne i messaggi di controllo continuo di httpx nel terminale
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- CONFIGURAZIONI DAI "SECRETS" / VARIABILI D'AMBIENTE ---
TOKEN = os.environ["TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
NOME_GOOGLE_SHEET = os.environ["NOME_GOOGLE_SHEET"]

# --- CONFIGURAZIONE GOOGLE SHEETS SICURA (SENZA FILE FISICO) ---
creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
gc = gspread.service_account_from_dict(creds_dict)
spreadsheet = gc.open(NOME_GOOGLE_SHEET)
worksheet = spreadsheet.sheet1


# --- TRUCCO ANTI-SPEGNIMENTO (WEB SERVER SECONDARIO) ---
class HealthCheckHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"Bot is online and running!")


def start_web_server():
  server = HTTPServer(("0.0.0.0", 7860), HealthCheckHandler)
  server.serve_forever()


# --- 1. COMANDO /inserisci ---
async def inserisci(update: Update, context: ContextTypes.DEFAULT_TYPE):
  try:
    args = context.args
    utente_che_scrive = update.effective_user.first_name

    festeggiato_id = None
    festeggiato_nome = None
    festeggiato_username = None
    data = None

    has_mention = False
    if update.message.entities:
      for entity in update.message.entities:
        if entity.type == "text_mention":
          festeggiato_id = entity.user.id
          festeggiato_nome = entity.user.first_name
          has_mention = True
          break
        elif entity.type == "mention":
          offset = entity.offset
          length = entity.length
          festeggiato_username = update.message.text[
              offset : offset + length
          ]
          has_mention = True
          break

    if not args and not has_mention:
      await update.message.reply_text(
          "⚠️ **Comando vuoto!** Per registrare un compleanno usa il tag dalla"
          " tendina di Telegram:\n\n👉 *Esempio:* `/inserisci @nomeutente"
          " 11/10`",
          parse_mode="Markdown",
      )
      return

    if not has_mention:
      await update.message.reply_text(
          "⚠️ **Utente non valido!** Ricordati di selezionare l'utente"
          " direttamente dalla tendina di Telegram quando digiti la"
          " `@`.\n\n👉 *Esempio corretto:* `/inserisci @mario 31/12`",
          parse_mode="Markdown",
      )
      return

    if festeggiato_username and not festeggiato_id:
      try:
        chat = await context.bot.get_chat(festeggiato_username)
        festeggiato_id = chat.id
        festeggiato_nome = chat.first_name or festeggiato_username
      except Exception:
        festeggiato_nome = festeggiato_username

    if args and any("/" in arg for arg in args) and "/" not in args[-1]:
      await update.message.reply_text(
          "⚠️ **Ordine errato!** Hai messo la data all'inizio. La data (`GG/MM`)"
          " va posizionata sempre alla **fine** del comando.\n\n👉 *Esempio"
          " corretto:* `/inserisci @mario 31/12`",
          parse_mode="Markdown",
      )
      return

    if not args or "/" not in args[-1]:
      await update.message.reply_text(
          "⚠️ **Data mancante!** Hai inserito il tag dalla tendina ma ti sei"
          " dimenticato di mettere la data (`GG/MM`) alla fine.\n\n👉 *Esempio"
          " corretto:* `/inserisci @mario 31/12`",
          parse_mode="Markdown",
      )
      return

    data = args[-1]

    try:
      datetime.strptime(f"{data}/2024", "%d/%m/%Y")
    except ValueError:
      await update.message.reply_text(
          "⚠️ **Data inesistente!** Inserisci una data reale del calendario nel"
          " formato `GG/MM` (es. `31/12`).",
          parse_mode="Markdown",
      )
      return

    if festeggiato_id:
      nome_da_salvare = (
          festeggiato_nome
          if festeggiato_nome
          else (festeggiato_username if festeggiato_username else "Utente")
      )
      festeggiato_display = (
          f'<a href="tg://user?id={festeggiato_id}">{nome_da_salvare}</a>'
      )
      id_per_foglio = festeggiato_id
      nome_per_foglio = nome_da_salvare
    elif festeggiato_username:
      festeggiato_display = festeggiato_username
      nome_per_foglio = festeggiato_username
      id_per_foglio = ""
    else:
      id_per_foglio = ""
      nome_per_foglio = ""

    worksheet.append_row([
        str(id_per_foglio),
        str(nome_per_foglio),
        str(data),
        str(update.effective_chat.id),
    ])

    await update.message.reply_text(
        f"✅ Perfetto {utente_che_scrive}! Il compleanno di"
        f" {festeggiato_display} è stato registrato correttamente per il"
        f" giorno {data}.",
        parse_mode="HTML",
    )

  except Exception as e:
    await update.message.reply_text(
        "⚠️ **Errore nel formato!** Controlla di aver selezionato l'utente"
        " dalla tendina e inserito una data valida alla fine.\n\n👉 *Esempio:*"
        " `/inserisci @mario 31/12`",
        parse_mode="Markdown",
    )

# --- 2. COMANDO /lista (Ordinato, con Data Prima e Nomi Cliccabili) ---
async def mostralista(update: Update, context: ContextTypes.DEFAULT_TYPE):
  try:
    righe = worksheet.get_all_values()

    if len(righe) <= 1:
      await update.message.reply_text(
          "📭 **Nessun compleanno registrato!** La lista è ancora vuota.",
          parse_mode="Markdown",
      )
      return

    dati_compleanni = []
    for riga in righe[1:]:
      user_id = riga[0].strip() if len(riga) > 0 else ""
      nome = riga[1].strip() if len(riga) > 1 else "Sconosciuto"
      data_str = riga[2].strip() if len(riga) > 2 else "--/--"

      try:
        data_dt = datetime.strptime(data_str, "%d/%m")
      except ValueError:
        try:
          data_dt = datetime.strptime(data_str, "%d/%m/%Y")
        except ValueError:
          data_dt = datetime(9999, 12, 31)

      dati_compleanni.append({
          "user_id": user_id,
          "nome": nome,
          "data_str": data_str,
          "data_dt": data_dt,
      })

    # Ordiniamo cronologicamente
    dati_ordinati = sorted(dati_compleanni, key=lambda x: x["data_dt"])

    # Costruiamo il messaggio con data a sinistra e nome cliccabile a destra
    testo_lista = "📅 Lista Compleanni Registrati:\n\n"

    for item in dati_ordinati:
      user_id = item["user_id"]
      nome = item["nome"]
      data = item["data_str"]

      # Link cliccabile in HTML
      if user_id and user_id.isdigit():
        nome_display = f'<a href="tg://user?id={user_id}">{nome}</a>'
      elif nome.startswith("@"):
        username_pulito = nome.lstrip("@")
        nome_display = f'<a href="https://t.me/{username_pulito}">{nome}</a>'
      else:
        nome_display = nome

      # Mettiamo prima la data (lunghezza fissa) e poi il nome cliccabile
      testo_lista += f"• {data} ➔ {nome_display}\n"

    await update.message.reply_text(
        testo_lista, parse_mode="HTML", disable_web_page_preview=True
    )

  except Exception as e:
    logger.error(f"Errore mostralista: {e}")
    await update.message.reply_text(
        "⚠️ Errore nel recuperare la lista dal Google Sheet."
    )



# --- 3. COMANDO /elimina (Con controllo anti-data) ---
async def elimina(update: Update, context: ContextTypes.DEFAULT_TYPE):
  try:
    args = context.args
    if not args:
      await update.message.reply_text(
          "⚠️ **Nome mancante!** Specifica chi vuoi eliminare.\n\n👉 *Esempio:*"
          " `/elimina @mario`",
          parse_mode="Markdown",
      )
      return

    if any("/" in arg for arg in args):
      await update.message.reply_text(
          "⚠️ **Data non necessaria!** Per eliminare un compleanno basta"
          " indicare solo il nome o il tag dell'utente, senza inserire la"
          " data.\n\n👉 *Esempio corretto:* `/elimina @mario`",
          parse_mode="Markdown",
      )
      return

    nome_da_cercare = " ".join(args).strip().lower()
    nome_pulito = nome_da_cercare.lstrip("@")

    righe = worksheet.get_all_values()
    indices_to_delete = []

    for i, riga in enumerate(righe[1:], start=2):
      if len(riga) > 1:
        val_foglio = riga[1].strip().lower()
        val_pulito_foglio = val_foglio.lstrip("@")

        if val_foglio == nome_da_cercare or val_pulito_foglio == nome_pulito:
          indices_to_delete.append(i)

    if not indices_to_delete:
      await update.message.reply_text(
          "❌ Non ho trovato nessun compleanno registrato per"
          f" **{' '.join(args)}**.",
          parse_mode="Markdown",
      )
      return

    indices_to_delete.sort(reverse=True)
    for indice in indices_to_delete:
      worksheet.delete_rows(indice)

    totale_eliminati = len(indices_to_delete)
    await update.message.reply_text(
        f"✅ Ho eliminato con successo **{totale_eliminati}** record associati a"
        f" **{' '.join(args)}** dal database!",
        parse_mode="Markdown",
    )

  except Exception as e:
    await update.message.reply_text(
        "⚠️ Errore durante l'eliminazione dal foglio Google."
    )


# --- 4. FUNZIONE DI INVIO AUGURI (AUTOMATICA A MEZZANOTTE) ---
async def controlla_e_invia_auguri(context: ContextTypes.DEFAULT_TYPE):
  try:
    oggi = datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m")
    righe = worksheet.get_all_values()

    if len(righe) <= 1:
      return

    for riga in righe[1:]:
      user_id = riga[0].strip() if len(riga) > 0 else ""
      nome = riga[1].strip() if len(riga) > 1 else "Festeggiato"
      compleanno = riga[2].strip() if len(riga) > 2 else ""
      chat_id = riga[3].strip() if len(riga) > 3 else ""

      if compleanno == oggi and chat_id:
        if user_id and user_id.isdigit():
          nome_display = f'<a href="tg://user?id={user_id}">{nome}</a>'
        elif nome.startswith("@"):
          username_pulito = nome.lstrip("@")
          nome_display = (
              f'<a href="https://t.me/{username_pulito}">{nome}</a>'
          )
        else:
          nome_display = nome

        messaggio = (
            "🥳 Oggi è un giorno speciale!🍾\n\n"
            f"🎉 Tanti auguri di buon compleanno a {nome_display}! 🎂 "
        )

        await context.bot.send_message(
            chat_id=chat_id, text=messaggio, parse_mode="HTML"
        )
  except Exception as e:
    logger.error(f"Errore durante il controllo degli auguri automatici: {e}")


# --- 5. COMANDO DI TEST (Riservato solo all'amministratore) ---
async def test_auguri(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.effective_user.id != ADMIN_ID:
    await update.message.reply_text(
        "⛔ Non hai i permessi per usare questo comando!"
    )
    return

  await update.message.reply_text(
      "🧪 Eseguo il test degli auguri per la data di oggi..."
  )
  await controlla_e_invia_auguri(context)
  await update.message.reply_text("✅ Test completato!")


# --- AVVIO DEL BOT ---
if __name__ == "__main__":
  # 1. Avvia il server web secondario
  threading.Thread(target=start_web_server, daemon=True).start()

  app = ApplicationBuilder().token(TOKEN).build()

  # Registrazione dei comandi
  app.add_handler(CommandHandler("inserisci", inserisci))
  app.add_handler(CommandHandler("lista", mostralista))
  app.add_handler(CommandHandler("elimina", elimina))
  app.add_handler(CommandHandler("testauguri", test_auguri))

  # Schedulazione giornaliera blindata con l'ora italiana (Europe/Rome)
  app.job_queue.run_daily(
      controlla_e_invia_auguri,
      time=time(hour=0, minute=0, second=5, tzinfo=ZoneInfo("Europe/Rome")),
  )

  print("🤖 Bot avviato con successo e pronto all'ascolto!")
  app.run_polling()
