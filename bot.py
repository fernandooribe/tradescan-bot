"""
TradeScan Bot — Señales automáticas para Pocket Option
Fuente de datos: Alpha Vantage (gratis, estable, tiempo real)
"""
import os
import asyncio
import logging
import numpy as np
import requests
import pandas as pd
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────
TOKEN        = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID      = os.getenv('TELEGRAM_CHAT_ID', '')
AV_KEY       = os.getenv('ALPHAVANTAGE_KEY', '')
INTERVAL_MIN = int(os.getenv('SIGNAL_INTERVAL', '5'))
MIN_CONF     = int(os.getenv('MIN_CONFIDENCE', '65'))
ASSETS_RAW   = os.getenv('ASSETS', 'EURUSD,GBPUSD,USDJPY,AUDUSD')
ASSETS       = [a.strip() for a in ASSETS_RAW.split(',')]

ASSET_NAMES = {
    'EURUSD': 'EUR/USD-OTC',
    'GBPUSD': 'GBP/USD-OTC',
    'USDJPY': 'USD/JPY-OTC',
    'AUDUSD': 'AUD/USD-OTC',
    'USDCAD': 'USD/CAD-OTC',
    'EURGBP': 'EUR/GBP-OTC',
    'EURJPY': 'EUR/JPY-OTC',
}

# ── ALPHA VANTAGE ─────────────────────────────────────────
def fetch_forex(pair: str) -> pd.DataFrame | None:
    """Descarga velas de 1 minuto desde Alpha Vantage"""
    from_sym = pair[:3]
    to_sym   = pair[3:]
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=FX_INTRADAY"
        f"&from_symbol={from_sym}"
        f"&to_symbol={to_sym}"
        f"&interval=1min"
        f"&outputsize=compact"
        f"&apikey={AV_KEY}"
    )
    try:
        resp = requests.get(url, timeout=20)
        data = resp.json()

        if 'Error Message' in data:
            logger.error(f"AV error: {data['Error Message']}")
            return None
        if 'Note' in data:
            logger.warning(f"AV límite de velocidad: {data['Note']}")
            return None
        if 'Information' in data:
            logger.warning(f"AV info: {data['Information']}")
            return None

        key = 'Time Series FX (1min)'
        if key not in data:
            logger.warning(f"AV sin datos para {pair}: {list(data.keys())}")
            return None

        ts = data[key]
        rows = []
        for dt_str, vals in ts.items():
            rows.append({
                'datetime': pd.to_datetime(dt_str),
                'Open':  float(vals['1. open']),
                'High':  float(vals['2. high']),
                'Low':   float(vals['3. low']),
                'Close': float(vals['4. close']),
            })

        df = pd.DataFrame(rows).sort_values('datetime').reset_index(drop=True)
        return df

    except requests.Timeout:
        logger.warning(f"Timeout descargando {pair}")
        return None
    except Exception as e:
        logger.error(f"Error fetch_forex({pair}): {e}")
        return None

# ── INDICADORES ───────────────────────────────────────────
def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_bb(series, period=20, std=2):
    mid   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return mid + std * sigma, mid, mid - std * sigma

def calc_stoch(high, low, close, k=14, d=3):
    lowest  = low.rolling(k).min()
    highest = high.rolling(k).max()
    stoch_k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    return stoch_k, stoch_k.rolling(d).mean()

# ── SEÑAL ─────────────────────────────────────────────────
def get_signal(pair: str) -> dict | None:
    try:
        df = fetch_forex(pair)
        if df is None or len(df) < 20:
            logger.warning(f"{pair}: datos insuficientes")
            return None

        close = df['Close'].astype(float)
        high  = df['High'].astype(float)
        low   = df['Low'].astype(float)
        open_ = df['Open'].astype(float)

        rsi      = float(calc_rsi(close, 14).iloc[-1])
        ema9     = float(calc_ema(close, 9).iloc[-1])
        ema21    = float(calc_ema(close, 21).iloc[-1])
        bb_up, _, bb_lo = calc_bb(close, 20)
        bb_upper = float(bb_up.iloc[-1])
        bb_lower = float(bb_lo.iloc[-1])
        stoch_k, _ = calc_stoch(high, low, close)
        stoch_kv   = float(stoch_k.iloc[-1])
        price      = float(close.iloc[-1])

        if any(np.isnan(v) for v in [rsi, ema9, ema21, stoch_kv, price]):
            return None

        last5  = df.tail(5)
        greens = sum(1 for _, r in last5.iterrows() if r['Close'] > r['Open'])
        reds   = 5 - greens
        cur_candle = 'ALCISTA' if close.iloc[-1] > open_.iloc[-1] else 'BAJISTA'

        score_call, score_put = 0, 0
        reasons = []

        # RSI
        if rsi < 25:   score_call += 28; reasons.append(f"RSI muy sobrevendido ({rsi:.0f})")
        elif rsi < 35: score_call += 18; reasons.append(f"RSI sobrevendido ({rsi:.0f})")
        elif rsi < 45: score_call += 8
        elif rsi > 75: score_put  += 28; reasons.append(f"RSI muy sobrecomprado ({rsi:.0f})")
        elif rsi > 65: score_put  += 18; reasons.append(f"RSI sobrecomprado ({rsi:.0f})")
        elif rsi > 55: score_put  += 8

        # EMA
        if ema9 > ema21: score_call += 20; reasons.append("EMA9 sobre EMA21 (alcista)")
        else:            score_put  += 20; reasons.append("EMA9 bajo EMA21 (bajista)")

        if price > ema9: score_call += 10; reasons.append("Precio sobre EMA9")
        else:            score_put  += 10; reasons.append("Precio bajo EMA9")

        # Bollinger
        if price < bb_lower:   score_call += 22; reasons.append("Precio bajo BB inferior")
        elif price > bb_upper: score_put  += 22; reasons.append("Precio sobre BB superior")
        else:
            mid = (bb_upper + bb_lower) / 2
            if price < mid: score_call += 5
            else:           score_put  += 5

        # Stochastic
        if stoch_kv < 20:   score_call += 18; reasons.append(f"Stoch sobrevendido ({stoch_kv:.0f})")
        elif stoch_kv < 35: score_call += 8
        elif stoch_kv > 80: score_put  += 18; reasons.append(f"Stoch sobrecomprado ({stoch_kv:.0f})")
        elif stoch_kv > 65: score_put  += 8

        # MHI
        if greens >= 4:   score_put  += 12; reasons.append(f"{greens} velas verdes → reversión PUT")
        elif reds >= 4:   score_call += 12; reasons.append(f"{reds} velas rojas → reversión CALL")
        elif greens == 3: score_put  += 6
        elif reds == 3:   score_call += 6

        if cur_candle == 'ALCISTA' and score_call > score_put: score_call += 5
        elif cur_candle == 'BAJISTA' and score_put > score_call: score_put += 5

        total = score_call + score_put
        if total == 0:
            return None

        if score_call > score_put:
            signal     = 'CALL'
            confidence = min(95, int(50 + (score_call - score_put) / total * 50))
        elif score_put > score_call:
            signal     = 'PUT'
            confidence = min(95, int(50 + (score_put - score_call) / total * 50))
        else:
            signal, confidence = 'WAIT', 50

        if confidence < MIN_CONF:
            signal = 'WAIT'

        if ema9 > ema21 and price > ema9:   trend = 'UP'
        elif ema9 < ema21 and price < ema9: trend = 'DOWN'
        else:                               trend = 'SIDEWAYS'

        return {
            'signal': signal, 'confidence': confidence,
            'price': price, 'rsi': rsi, 'ema9': ema9, 'ema21': ema21,
            'stoch_k': stoch_kv, 'trend': trend, 'reasons': reasons[:4],
            'greens': greens, 'reds': reds, 'cur_candle': cur_candle,
        }

    except Exception as e:
        logger.error(f"Error get_signal({pair}): {e}")
        return None

# ── FORMATO ───────────────────────────────────────────────
def format_msg(pair: str, d: dict, auto: bool = False) -> str:
    name = ASSET_NAMES.get(pair, pair)
    sig  = d['signal']
    conf = d['confidence']
    now  = datetime.now().strftime('%H:%M:%S')

    if sig == 'CALL':
        sig_line = '🟢 CALL — COMPRA ▲'
        bar = '🟩' * (conf // 10) + '⬜' * (10 - conf // 10)
    elif sig == 'PUT':
        sig_line = '🔴 PUT — VENTA ▼'
        bar = '🟥' * (conf // 10) + '⬜' * (10 - conf // 10)
    else:
        sig_line = '🟡 ESPERAR ⏸'
        bar = '🟨' * max(1, conf // 10) + '⬜' * (10 - max(1, conf // 10))

    trend_ico  = '📈' if d['trend'] == 'UP' else '📉' if d['trend'] == 'DOWN' else '➡️'
    candle_ico = '🟢' if d['cur_candle'] == 'ALCISTA' else '🔴'
    reasons_tx = '\n'.join([f'  • {r}' for r in d['reasons']]) or '  • Sin señal dominante'
    header     = '🤖 SEÑAL AUTO' if auto else '📊 SEÑAL'

    return (
        f"{header} | {now}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💱 {name}\n"
        f"{sig_line}\n\n"
        f"Confianza: {conf}%\n"
        f"{bar}\n\n"
        f"RSI: {d['rsi']:.1f} | Stoch: {d['stoch_k']:.1f}\n"
        f"{trend_ico} Tendencia: {d['trend']}\n"
        f"{candle_ico} Vela actual: {d['cur_candle']}\n"
        f"Precio: {d['price']:.5f}\n\n"
        f"Razones:\n{reasons_tx}\n\n"
        f"⏱ Recomendado: exp. 1-2 min\n"
        f"⚠️ Señal orientativa. Gestioná el riesgo."
    )

# ── ENVÍO ─────────────────────────────────────────────────
async def send_signals(bot, auto: bool = False):
    if not CHAT_ID:
        return
    if not AV_KEY:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="⚠️ Falta configurar ALPHAVANTAGE_KEY en Railway."
        )
        return

    found = 0
    for pair in ASSETS:
        try:
            data = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, get_signal, pair),
                timeout=25
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout en {pair}")
            continue
        except Exception as e:
            logger.error(f"Error {pair}: {e}")
            continue

        if not data:
            continue
        if auto and data['signal'] == 'WAIT':
            continue

        try:
            await bot.send_message(chat_id=CHAT_ID, text=format_msg(pair, data, auto))
            found += 1
            await asyncio.sleep(1.5)  # respetar límite AV
        except Exception as e:
            logger.error(f"Error enviando {pair}: {e}")

    if auto and found == 0:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🟡 {datetime.now().strftime('%H:%M')} — Sin señales claras. Esperando mejor momento."
        )

# ── HANDLERS ──────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    await update.message.reply_text(
        f"⚡ TradeScan Bot — Pocket Option\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Tu Chat ID: {cid}\n\n"
        f"Comandos:\n"
        f"/signal — Señal de todos los pares\n"
        f"/eurusd — Solo EUR/USD\n"
        f"/gbpusd — Solo GBP/USD\n"
        f"/usdjpy — Solo USD/JPY\n"
        f"/audusd — Solo AUD/USD\n"
        f"/status — Estado del bot\n\n"
        f"Señales automáticas cada {INTERVAL_MIN} min"
    )

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analizando mercado... (20-30 segundos)")
    await send_signals(ctx.bot, auto=False)

async def _pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pair: str):
    name = ASSET_NAMES.get(pair, pair)
    msg  = await update.message.reply_text(f"🔍 Analizando {name}...")
    try:
        data = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, get_signal, pair),
            timeout=25
        )
        if data:
            await update.message.reply_text(format_msg(pair, data, False))
        else:
            await update.message.reply_text(
                f"⚠️ Sin datos para {name}.\n"
                f"• Verificá que ALPHAVANTAGE_KEY esté en Railway\n"
                f"• O el mercado puede estar cerrado (fin de semana)\n"
                f"• Intentá en unos minutos"
            )
    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ Tiempo agotado. Intentá de nuevo.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:100]}")
    try:
        await msg.delete()
    except:
        pass

async def cmd_eurusd(u, c): await _pair(u, c, 'EURUSD')
async def cmd_gbpusd(u, c): await _pair(u, c, 'GBPUSD')
async def cmd_usdjpy(u, c): await _pair(u, c, 'USDJPY')
async def cmd_audusd(u, c): await _pair(u, c, 'AUDUSD')

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pairs    = ', '.join([ASSET_NAMES.get(a, a) for a in ASSETS])
    key_ok   = '✅' if AV_KEY else '❌ Falta ALPHAVANTAGE_KEY'
    await update.message.reply_text(
        f"✅ Bot activo\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Hora: {datetime.now().strftime('%H:%M:%S')}\n"
        f"Alpha Vantage: {key_ok}\n"
        f"Intervalo: {INTERVAL_MIN} min\n"
        f"Pares: {pairs}\n"
        f"Confianza mínima: {MIN_CONF}%"
    )

# ── MAIN ──────────────────────────────────────────────────
def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN no configurado")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',  cmd_start))
    app.add_handler(CommandHandler('signal', cmd_signal))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(CommandHandler('eurusd', cmd_eurusd))
    app.add_handler(CommandHandler('gbpusd', cmd_gbpusd))
    app.add_handler(CommandHandler('usdjpy', cmd_usdjpy))
    app.add_handler(CommandHandler('audusd', cmd_audusd))

    scheduler = AsyncIOScheduler(timezone='America/Argentina/Buenos_Aires')
    scheduler.add_job(
        lambda: asyncio.create_task(send_signals(app.bot, auto=True)),
        'interval', minutes=INTERVAL_MIN
    )
    scheduler.start()

    logger.info(f"✅ Bot iniciado — Alpha Vantage — señales cada {INTERVAL_MIN} min")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
