"""
TradeScan Bot — Señales automáticas para Pocket Option
Indicadores: RSI, EMA 9/21, Bollinger Bands, Stochastic, MHI
(Sin pandas-ta — cálculo manual compatible con Python 3.11+)
"""

import os
import asyncio
import logging
import numpy as np
from datetime import datetime

import yfinance as yf
import pandas as pd
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────
TOKEN        = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID      = os.getenv('TELEGRAM_CHAT_ID', '')
INTERVAL_MIN = int(os.getenv('SIGNAL_INTERVAL', '5'))
MIN_CONF     = int(os.getenv('MIN_CONFIDENCE', '65'))
ASSETS_RAW   = os.getenv('ASSETS', 'EURUSD=X,GBPUSD=X,USDJPY=X,AUDUSD=X')
ASSETS       = [a.strip() for a in ASSETS_RAW.split(',')]

ASSET_NAMES = {
    'EURUSD=X': 'EUR/USD-OTC',
    'GBPUSD=X': 'GBP/USD-OTC',
    'USDJPY=X': 'USD/JPY-OTC',
    'AUDUSD=X': 'AUD/USD-OTC',
    'USDCAD=X': 'USD/CAD-OTC',
    'EURGBP=X': 'EUR/GBP-OTC',
    'EURJPY=X': 'EUR/JPY-OTC',
}

# ── INDICADORES MANUALES ──────────────────────────────────
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
    stoch_d = stoch_k.rolling(d).mean()
    return stoch_k, stoch_d

# ── SEÑAL ─────────────────────────────────────────────────
def get_signal(ticker: str) -> dict | None:
    try:
        df = yf.download(ticker, period='2d', interval='1m',
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df['Close'].astype(float)
        high  = df['High'].astype(float)
        low   = df['Low'].astype(float)
        open_ = df['Open'].astype(float)

        rsi     = float(calc_rsi(close, 14).iloc[-1])
        ema9    = float(calc_ema(close, 9).iloc[-1])
        ema21   = float(calc_ema(close, 21).iloc[-1])
        bb_up, bb_mid, bb_lo = calc_bb(close, 20)
        bb_upper = float(bb_up.iloc[-1])
        bb_lower = float(bb_lo.iloc[-1])
        stoch_k, _ = calc_stoch(high, low, close)
        stoch_kv = float(stoch_k.iloc[-1])
        price    = float(close.iloc[-1])

        last5   = df.tail(5)
        greens  = sum(1 for _, r in last5.iterrows()
                      if float(r['Close']) > float(r['Open']))
        reds    = 5 - greens
        cur_candle = 'ALCISTA' if float(close.iloc[-1]) > float(open_.iloc[-1]) else 'BAJISTA'

        score_call = 0
        score_put  = 0
        reasons    = []

        # RSI
        if rsi < 25:
            score_call += 28; reasons.append(f"RSI muy sobrevendido ({rsi:.0f})")
        elif rsi < 35:
            score_call += 18; reasons.append(f"RSI sobrevendido ({rsi:.0f})")
        elif rsi < 45:
            score_call += 8
        elif rsi > 75:
            score_put  += 28; reasons.append(f"RSI muy sobrecomprado ({rsi:.0f})")
        elif rsi > 65:
            score_put  += 18; reasons.append(f"RSI sobrecomprado ({rsi:.0f})")
        elif rsi > 55:
            score_put  += 8

        # EMA
        if ema9 > ema21:
            score_call += 20; reasons.append("EMA9 sobre EMA21 (alcista)")
        else:
            score_put  += 20; reasons.append("EMA9 bajo EMA21 (bajista)")

        if price > ema9:
            score_call += 10; reasons.append("Precio sobre EMA9")
        else:
            score_put  += 10; reasons.append("Precio bajo EMA9")

        # Bollinger
        if price < bb_lower:
            score_call += 22; reasons.append("Precio bajo BB inferior")
        elif price > bb_upper:
            score_put  += 22; reasons.append("Precio sobre BB superior")
        else:
            mid = (bb_upper + bb_lower) / 2
            if price < mid: score_call += 5
            else:           score_put  += 5

        # Stochastic
        if stoch_kv < 20:
            score_call += 18; reasons.append(f"Stoch sobrevendido ({stoch_kv:.0f})")
        elif stoch_kv < 35:
            score_call += 8
        elif stoch_kv > 80:
            score_put  += 18; reasons.append(f"Stoch sobrecomprado ({stoch_kv:.0f})")
        elif stoch_kv > 65:
            score_put  += 8

        # MHI
        if greens >= 4:
            score_put  += 12; reasons.append(f"{greens} velas verdes → reversión PUT")
        elif reds >= 4:
            score_call += 12; reasons.append(f"{reds} velas rojas → reversión CALL")
        elif greens == 3:
            score_put  += 6
        elif reds == 3:
            score_call += 6

        if cur_candle == 'ALCISTA' and score_call > score_put:
            score_call += 5
        elif cur_candle == 'BAJISTA' and score_put > score_call:
            score_put  += 5

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
            signal     = 'WAIT'
            confidence = 50

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
            'bb_upper': bb_upper, 'bb_lower': bb_lower,
        }

    except Exception as e:
        logger.error(f"Error analizando {ticker}: {e}")
        return None

# ── FORMATO MENSAJE ───────────────────────────────────────
def format_msg(ticker: str, d: dict, auto: bool = False) -> str:
    name = ASSET_NAMES.get(ticker, ticker)
    sig  = d['signal']
    conf = d['confidence']
    now  = datetime.now().strftime('%H:%M:%S')

    if sig == 'CALL':
        sig_line = '🟢 *CALL — COMPRÁ ▲*'
        bar = '🟩' * (conf // 10) + '⬜' * (10 - conf // 10)
    elif sig == 'PUT':
        sig_line = '🔴 *PUT — VENDÉ ▼*'
        bar = '🟥' * (conf // 10) + '⬜' * (10 - conf // 10)
    else:
        sig_line = '🟡 *ESPERAR ⏸*'
        bar = '🟨' * max(1, conf // 10) + '⬜' * (10 - max(1, conf // 10))

    trend_ico  = '📈' if d['trend'] == 'UP' else '📉' if d['trend'] == 'DOWN' else '➡️'
    candle_ico = '🟢' if d['cur_candle'] == 'ALCISTA' else '🔴'
    reasons_text = '\n'.join([f'  ✦ {r}' for r in d['reasons']]) or '  ✦ Sin señales fuertes'
    header = '🤖 *SEÑAL AUTOMÁTICA*' if auto else '📊 *SEÑAL SOLICITADA*'

    return (
        f"{header} — `{now}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💱 *{name}*\n"
        f"{sig_line}\n\n"
        f"📊 Confianza: *{conf}%*\n"
        f"`{bar}`\n\n"
        f"📐 *Indicadores:*\n"
        f"  RSI: `{d['rsi']:.1f}` | Stoch: `{d['stoch_k']:.1f}`\n"
        f"  {trend_ico} Tendencia: `{d['trend']}`\n"
        f"  {candle_ico} Vela actual: `{d['cur_candle']}`\n"
        f"  Precio: `{d['price']:.5f}`\n\n"
        f"✅ *Razones:*\n{reasons_text}\n\n"
        f"⏱ _Recomendado: velas 1M · exp. 1\\-2M_\n"
        f"⚠️ _Señal orientativa\\. Gestioná el riesgo\\._"
    )

# ── ENVÍO AUTO ────────────────────────────────────────────
async def send_signals(bot: Bot, auto: bool = False):
    if not CHAT_ID:
        return
    found = 0
    header_sent = False
    for ticker in ASSETS:
        data = get_signal(ticker)
        if not data:
            continue
        if auto and data['signal'] == 'WAIT':
            continue
        if auto and not header_sent:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"🔔 *ESCANEO AUTOMÁTICO* — {datetime.now().strftime('%H:%M')}",
                parse_mode='Markdown'
            )
            header_sent = True
        await bot.send_message(
            chat_id=CHAT_ID,
            text=format_msg(ticker, data, auto),
            parse_mode='MarkdownV2'
        )
        found += 1
        await asyncio.sleep(0.6)

    if auto and found == 0:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🟡 *{datetime.now().strftime('%H:%M')}* — Sin señales claras\\. Esperando\\.",
            parse_mode='MarkdownV2'
        )

# ── HANDLERS ──────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    await update.message.reply_text(
        f"⚡ *TradeScan Bot* — Pocket Option\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Tu Chat ID: `{cid}`\n\n"
        f"📋 *Comandos:*\n"
        f"/signal — Señal de todos los pares\n"
        f"/eurusd — Solo EUR/USD\n"
        f"/gbpusd — Solo GBP/USD\n"
        f"/usdjpy — Solo USD/JPY\n"
        f"/audusd — Solo AUD/USD\n"
        f"/status — Estado del bot\n\n"
        f"🤖 Señales automáticas cada *{INTERVAL_MIN} min*",
        parse_mode='Markdown'
    )

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analizando mercado...")
    await send_signals(ctx.bot, auto=False)

async def _pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE, ticker: str):
    name = ASSET_NAMES.get(ticker, ticker)
    await update.message.reply_text(f"🔍 Analizando {name}...")
    data = get_signal(ticker)
    if data:
        await update.message.reply_text(
            format_msg(ticker, data, False), parse_mode='MarkdownV2')
    else:
        await update.message.reply_text(
            f"⚠️ Sin datos para {name}. Puede ser horario de mercado cerrado.")

async def cmd_eurusd(u, c): await _pair(u, c, 'EURUSD=X')
async def cmd_gbpusd(u, c): await _pair(u, c, 'GBPUSD=X')
async def cmd_usdjpy(u, c): await _pair(u, c, 'USDJPY=X')
async def cmd_audusd(u, c): await _pair(u, c, 'AUDUSD=X')

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pairs = ', '.join([ASSET_NAMES.get(a, a) for a in ASSETS])
    await update.message.reply_text(
        f"✅ *Bot activo*\n"
        f"🕐 Hora: `{datetime.now().strftime('%H:%M:%S')}`\n"
        f"⏱ Intervalo: `{INTERVAL_MIN} minutos`\n"
        f"📊 Pares: `{pairs}`\n"
        f"🎯 Confianza mínima: `{MIN_CONF}%`",
        parse_mode='Markdown'
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

    logger.info(f"✅ Bot iniciado — señales cada {INTERVAL_MIN} minutos")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
