# TradeScan Bot — Guía de instalación completa

## ¿Qué hace este bot?
Manda señales automáticas a tu Telegram cada X minutos analizando pares forex/OTC con:
- RSI 14
- EMA 9 y EMA 21
- Bollinger Bands 20
- Stochastic 14
- Patrón de las últimas 5 velas (MHI)

---

## PASO 1 — Crear el bot en Telegram

1. Abrí Telegram y buscá **@BotFather**
2. Escribí `/newbot`
3. Ponele un nombre, por ejemplo: `TradeScan Señales`
4. Ponele un username, por ejemplo: `tradescan_señales_bot`
5. BotFather te da un **token** → copialo (parece: `123456789:AAxxxxxxx`)
6. Buscá tu bot recién creado y escribí `/start`
7. El bot te va a responder con tu **Chat ID** → copialo también

---

## PASO 2 — Subir el código a GitHub

1. Creá una cuenta en **github.com** (si no tenés)
2. Hacé click en **New repository** → nombre: `tradescan-bot`
3. Subí los archivos de esta carpeta:
   - `bot.py`
   - `requirements.txt`
   - `Procfile`
4. Hacé commit y push

**Alternativa más fácil:**
- Instalá GitHub Desktop (desktop.github.com)
- Arrastrá la carpeta `tradescan_bot` dentro
- Click en "Commit to main" → "Push origin"

---

## PASO 3 — Deployar en Railway (gratis)

1. Entrá a **railway.app**
2. Click en **Start a New Project**
3. Elegí **Deploy from GitHub repo**
4. Conectá tu cuenta de GitHub
5. Seleccioná el repositorio `tradescan-bot`
6. Railway lo detecta automáticamente

**Configurar las variables de entorno:**
1. En Railway, click en tu proyecto
2. Click en **Variables**
3. Agregá estas variables una por una:

| Variable | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | tu token de BotFather |
| `TELEGRAM_CHAT_ID` | tu chat ID del /start |
| `SIGNAL_INTERVAL` | `5` (cada 5 minutos) |
| `MIN_CONFIDENCE` | `65` |
| `ASSETS` | `EURUSD=X,GBPUSD=X,USDJPY=X,AUDUSD=X` |

4. Click en **Deploy** → ¡listo!

---

## PASO 4 — Verificar que funciona

1. En el bot de Telegram escribí `/start`
2. Debería responder con el menú
3. Escribí `/signal` para ver una señal ahora
4. En Railway → Logs podés ver que el bot está activo

---

## Comandos del bot

| Comando | Acción |
|---|---|
| `/start` | Menú principal y tu Chat ID |
| `/signal` | Señal inmediata de todos los pares |
| `/eurusd` | Señal solo EUR/USD |
| `/gbpusd` | Señal solo GBP/USD |
| `/usdjpy` | Señal solo USD/JPY |
| `/audusd` | Señal solo AUD/USD |
| `/status` | Estado y configuración actual |
| `/help` | Ayuda completa |

---

## Notas importantes

- Los datos vienen de Yahoo Finance (gratis, sin API key)
- Las señales usan velas de 1 minuto → recomendado operar con expiración de 1-2 minutos
- Los mercados OTC de Pocket Option son similares a los pares forex reales pero no idénticos
- **Siempre probá en cuenta DEMO primero antes de operar con dinero real**
- Railway tiene crédito gratuito suficiente para correr el bot 24/7

---

## Ajustar la sensibilidad

En Railway → Variables podés cambiar:
- `MIN_CONFIDENCE=70` → más selectivo, menos señales pero más precisas
- `SIGNAL_INTERVAL=3` → señales cada 3 minutos
- `SIGNAL_INTERVAL=10` → señales cada 10 minutos
