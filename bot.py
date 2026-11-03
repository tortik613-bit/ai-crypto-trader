
import os
import json
import time
import asyncio
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          CallbackQueryHandler, ContextTypes, PicklePersistence)

VERSION = '0.5.1'
START_BALANCE = 50.0
DEMO_AMOUNT = 5.0
PRICE_TTL = 300
HISTORY_TTL = 21600
MAX_NEW_HISTORY_PER_SCAN = 2
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / 'paper_state.pkl'   # Existing balance/trades: do not change
HISTORY_FILE = ROOT / 'history_cache.json'
COINS = {
    'bitcoin': 'BTC', 'ethereum': 'ETH', 'solana': 'SOL',
    'binancecoin': 'BNB', 'ripple': 'XRP', 'chainlink': 'LINK',
    'avalanche-2': 'AVAX', 'sui': 'SUI', 'dogecoin': 'DOGE',
    'cardano': 'ADA',
}
logging.basicConfig(level=logging.WARNING,
                    format='%(asctime)s %(levelname)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def load_history_state():
    try:
        with HISTORY_FILE.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('coins'), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {'coins': {}, 'retry_after': 0, 'backoff': 300}


history_state = load_history_state()
price_cache = {'prices': None, 'saved_at': 0}
api_lock = asyncio.Lock()
scan_lock = asyncio.Lock()


def save_history_state():
    tmp = HISTORY_FILE.with_suffix('.json.tmp')
    # Only the bot's own validated JSON is written here.
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(history_state, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, HISTORY_FILE)


def menu():
    actions = [
        ('💹 Котировки', 'prices'),
        ('🧠 Анализ 10 монет', 'analysis'),
        ('📊 Баланс', 'balance'),
        ('📈 Демо-сделка', 'signal'),
        ('📋 Мои сделки', 'trades'),
        ('⚙️ Настройки', 'settings'),
    ]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=action)]
        for label, action in actions
    ])


def account(context):
    context.user_data.setdefault('balance', START_BALANCE)
    context.user_data.setdefault('trades', [])
    return context.user_data


def network_json(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'CryptoPaperBot/0.5.1',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


async def api_json(url):
    """One request at a time; cooldown is shared by prices and history."""
    async with api_lock:
        if time.time() < float(history_state.get('retry_after', 0)):
            raise RuntimeError('cooldown')
        try:
            result = await asyncio.to_thread(network_json, url)
        except urllib.error.HTTPError as exc:
            logger.warning('CoinGecko HTTP %s', exc.code)
            if exc.code == 429:
                delay = max(300, int(history_state.get('backoff', 300)))
                history_state['retry_after'] = time.time() + delay
                history_state['backoff'] = min(delay * 2, 3600)
            else:
                history_state['retry_after'] = time.time() + 60
            save_history_state()
            raise RuntimeError('api_failed') from None
        except Exception as exc:
            logger.warning('CoinGecko error: %s', type(exc).__name__)
            history_state['retry_after'] = time.time() + 60
            save_history_state()
            raise RuntimeError('api_failed') from None
        history_state['backoff'] = 300
        history_state['retry_after'] = 0
        save_history_state()
        return result


def format_price(value):
    return f'${value:,.2f}' if value >= 1 else f'${value:,.4f}'


async def prices_now():
    age = time.time() - price_cache['saved_at']
    if price_cache['prices'] is not None and age < PRICE_TTL:
        return price_cache['prices'], age, False
    ids = ','.join(COINS)
    url = ('https://api.coingecko.com/api/v3/simple/price'
           f'?ids={ids}&vs_currencies=usd')
    try:
        result = await api_json(url)
        prices = {}
        for coin_id, symbol in COINS.items():
            value = result.get(coin_id, {}).get('usd')
            if type(value) not in (int, float) or value <= 0:
                raise ValueError('missing price')
            prices[symbol] = float(value)
        price_cache.update(prices=prices, saved_at=time.time())
        return prices, 0, False
    except Exception:
        if price_cache['prices'] is not None:
            return price_cache['prices'], time.time() - price_cache['saved_at'], True
        raise RuntimeError('quotes unavailable') from None


def normalized_daily(records):
    daily = {}
    today = datetime.now(timezone.utc).date()
    for row in records:
        if not isinstance(row, list) or len(row) < 2:
            continue
        stamp, value = row[:2]
        if type(stamp) not in (int, float) or type(value) not in (int, float):
            continue
        if value <= 0:
            continue
        day = datetime.fromtimestamp(stamp / 1000, timezone.utc).date()
        if day < today:
            daily[day] = float(value)
    ordered = sorted(daily.items())
    if len(ordered) < 60:
        raise ValueError('not enough daily observations')
    recent = ordered[-60:]
    if (today - recent[-1][0]).days > 3:
        raise ValueError('old observations')
    if any((b[0] - a[0]).days != 1 for a, b in zip(recent, recent[1:])):
        raise ValueError('gaps in daily observations')
    return {'prices': [value for _, value in ordered],
            'last_day': recent[-1][0].isoformat(), 'saved_at': time.time()}


def usable_history(coin_id):
    entry = history_state['coins'].get(coin_id)
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get('saved_at', 0)) > HISTORY_TTL:
        return None
    if len(entry.get('prices', [])) < 60:
        return None
    last_day = datetime.fromisoformat(entry['last_day']).date()
    age_days = (datetime.now(timezone.utc).date() - last_day).days
    return entry if 1 <= age_days <= 3 else None


async def download_history(coin_id):
    url = ('https://api.coingecko.com/api/v3/coins/'
           f'{coin_id}/market_chart?vs_currency=usd&days=100')
    data = await api_json(url)
    entry = normalized_daily(data.get('prices', []))
    history_state['coins'][coin_id] = entry
    save_history_state()
    return entry


def ema(series, period):
    value = sum(series[:period]) / period
    k = 2 / (period + 1)
    for p in series[period:]:
        value = p * k + value * (1 - k)
    return value


def rsi(series, period=14):
    differences = [b - a for a, b in zip(series, series[1:])]
    gains = [max(d, 0) for d in differences]
    losses = [max(-d, 0) for d in differences]
    gain = sum(gains[:period]) / period
    loss = sum(losses[:period]) / period
    for i in range(period, len(differences)):
        gain = (gain * (period - 1) + gains[i]) / period
        loss = (loss * (period - 1) + losses[i]) / period
    if gain == loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def evaluate(entry):
    prices = entry['prices']
    p, e20, e50 = prices[-1], ema(prices, 20), ema(prices, 50)
    r = rsi(prices)
    if p > e20 > e50:
        trend, score = '🟢 Восходящий', 2
    elif p < e20 < e50:
        trend, score = '🔴 Нисходящий', -2
    else:
        trend, score = '🟡 Смешанный', 0
    if r >= 70:
        impulse = 'Перекупленность'; score -= 1
    elif r <= 30:
        impulse = 'Перепроданность'; score -= 1
    elif 45 <= r <= 65:
        impulse = 'Умеренный'; score += 1
    else:
        impulse = 'Неоднозначный'
    return (f'🔹 {{symbol}} | {trend}\n'
            f'Цена за {entry["last_day"]}: {format_price(p)}\n'
            f'RSI 14: {r:.1f} | EMA20: {format_price(e20)}\n'
            f'EMA50: {format_price(e50)}\n'
            f'Импульс: {impulse} | Оценка: {score}')


async def analyze(query):
    if scan_lock.locked():
        await query.message.reply_text('⏳ Предыдущее сканирование ещё идёт.')
        return
    async with scan_lock:
        await query.message.reply_text('🧠 Проверяю сохранённую историю и доступность API…')
        downloaded = 0
        for coin_id in COINS:
            if usable_history(coin_id):
                continue
            if downloaded >= MAX_NEW_HISTORY_PER_SCAN:
                break
            if time.time() < float(history_state.get('retry_after', 0)):
                break
            try:
                await download_history(coin_id)
                downloaded += 1
            except Exception as exc:
                logger.warning('History %s: %s', COINS[coin_id], type(exc).__name__)
                # Stop on API failure to avoid hammering the service.
                break
            await asyncio.sleep(4)

        results, pending = [], []
        for coin_id, symbol in COINS.items():
            entry = usable_history(coin_id)
            if not entry:
                pending.append(symbol)
                continue
            try:
                results.append(evaluate(entry).format(symbol=symbol))
            except Exception:
                pending.append(symbol)

        message = (f'🧠 Анализ v{VERSION}\n'
                   f'Готово: {len(results)}/10 монет\n'
                   f'Новых историй за нажатие: {downloaded}\n\n')
        if results:
            message += '\n\n'.join(results) + '\n\n'
        if pending:
            message += '⏳ Ещё без анализа: ' + ', '.join(pending) + '\n'
        remaining = max(0, int(float(history_state.get('retry_after', 0)) - time.time()))
        if remaining:
            message += f'⚠️ API ограничил запросы. Пауза ещё ~{(remaining + 59)//60} мин.\n'
        elif pending:
            message += 'Нажми «Анализ 10 монет» ещё раз для следующей части.\n'
        message += ('\nДанные CoinGecko — не свечи Bybit. '
                    'Индикаторы описательные, не сигнал покупки. '
                    'Реальные сделки отключены.')
        await query.message.reply_text(message, reply_markup=menu())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account(context)
    await update.message.reply_text(
        f'🤖 AI Crypto Trader v{VERSION}\n\n'
        '10 монет · RSI 14 · EMA 20/50\n'
        'Режим: Paper Trading ($50 старт)\n'
        '💾 Баланс и сделки сохраняются\n'
        '🚫 Реальные сделки отключены', reply_markup=menu())


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    data = account(context)
    balance, trades = data['balance'], data['trades']

    if action == 'analysis':
        await analyze(query)
        return
    if action == 'prices':
        try:
            prices, age, stale = await prices_now()
            message = '💹 Котировки CoinGecko\n\n'
            message += '\n'.join(f'{s}: {format_price(p)}' for s, p in prices.items())
            message += f'\n\n🕒 Возраст данных: {age:.0f} сек.'
            if stale:
                message += '\n⚠️ Старые цены. Не используй для входа.'
        except Exception:
            message = '⚠️ Котировки недоступны. Повтори позже.'
    elif action == 'balance':
        invested = sum(float(x['amount']) for x in trades)
        message = (f'📊 Демо-счёт\n\n💵 Свободно: ${balance:.2f}\n'
                   f'📋 Учебные позиции: ${invested:.2f}\n'
                   '⚠️ P&L пока не рассчитывается.')
    elif action == 'settings':
        message = (f'⚙️ v{VERSION} · Paper Trading\n'
                   '10 монет · RSI 14 · EMA 20/50\n'
                   'История CoinGecko: 100 дней\n'
                   'Кеш истории: 6 часов, сохраняется\n'
                   'До 2 новых историй за нажатие\n'
                   '429: растущая пауза от 5 до 60 минут\n'
                   'Баланс и сделки сохраняются\n'
                   'Реальная торговля отключена')
    elif action == 'signal':
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton('✅ Подтвердить', callback_data='buy'),
            InlineKeyboardButton('❌ Отклонить', callback_data='reject')
        ]])
        await query.message.reply_text(
            f'🧪 Учебная BTC/USDT на ${DEMO_AMOUNT:.2f}\n'
            'Это пример, НЕ торговый сигнал.', reply_markup=keyboard)
        return
    elif action == 'buy':
        await query.edit_message_reply_markup(reply_markup=None)
        if balance >= DEMO_AMOUNT:
            data['balance'] = round(balance - DEMO_AMOUNT, 2)
            trades.append({'pair': 'BTC/USDT', 'amount': DEMO_AMOUNT,
                           'type': 'DEMO', 'created_at': int(time.time())})
            message = f'✅ Учебная позиция создана. Остаток: ${data["balance"]:.2f}'
        else:
            message = '⚠️ Недостаточно виртуальных средств.'
    elif action == 'reject':
        await query.edit_message_reply_markup(reply_markup=None)
        message = '❌ Демо-сделка отклонена.'
    elif action == 'trades':
        if trades:
            message = '📋 Учебные позиции:\n\n' + '\n'.join(
                f'{i}. {x["pair"]} — ${float(x["amount"]):.2f}'
                for i, x in enumerate(trades, 1))
        else:
            message = '📋 Учебных позиций нет.'
    else:
        message = 'Неизвестная команда.'
    await query.message.reply_text(message, reply_markup=menu())


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN не задан')
    persistence = PicklePersistence(filepath=STATE_FILE, update_interval=5)
    app = (ApplicationBuilder().token(token)
           .persistence(persistence).build())
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.run_polling()


if __name__ == '__main__':
    main()
