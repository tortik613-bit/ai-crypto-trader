
import os
import json
import time
import asyncio
import logging
import urllib.request
import urllib.error

from pathlib import Path
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
)

# -------------------------
# SETTINGS
# -------------------------

VERSION = "0.5"
START_BALANCE = 50.0
DEMO_AMOUNT = 5.0

PRICE_CACHE_SECONDS = 300
HISTORY_CACHE_SECONDS = 21600
RETRY_SECONDS = 300

STATE_FILE = (
    Path(__file__).resolve().parent
    / "paper_state.pkl"
)

COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "binancecoin": "BNB",
    "ripple": "XRP",
    "chainlink": "LINK",
    "avalanche-2": "AVAX",
    "sui": "SUI",
    "dogecoin": "DOGE",
    "cardano": "ADA",
}

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

price_cache = {
    "prices": None,
    "timestamp": 0,
}

history_cache = {}
api_retry_after = 0

price_lock = asyncio.Lock()
scan_lock = asyncio.Lock()


# -------------------------
# TELEGRAM MENU
# -------------------------

def menu():
    items = [
        ("💹 Котировки", "prices"),
        ("🧠 Анализ 10 монет", "analysis"),
        ("📊 Баланс", "balance"),
        ("📈 Демо-сделка", "signal"),
        ("📋 Мои сделки", "trades"),
        ("⚙️ Настройки", "settings"),
    ]

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=action)]
        for label, action in items
    ])


def get_account(context):
    data = context.user_data
    data.setdefault("balance", START_BALANCE)
    data.setdefault("trades", [])
    return data


# -------------------------
# API REQUESTS
# -------------------------

def api_request(url):
    global api_retry_after

    now = time.time()
    if now < api_retry_after:
        raise RuntimeError("API cooldown")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CryptoPaperBot/0.5",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            return json.load(response)

    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            api_retry_after = (
                time.time() + RETRY_SECONDS
            )
        else:
            api_retry_after = (
                time.time() + 60
            )

        logger.warning(
            "CoinGecko HTTP %s",
            exc.code,
        )
        raise

    except Exception as exc:
        api_retry_after = time.time() + 60
        logger.warning(
            "CoinGecko error: %s",
            type(exc).__name__,
        )
        raise


def fetch_prices():
    ids = ",".join(COINS.keys())

    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd"
    )

    data = api_request(url)
    prices = {}

    for coin_id, symbol in COINS.items():
        value = data.get(coin_id, {}).get("usd")

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
        ):
            raise ValueError("Invalid price")

        prices[symbol] = float(value)

    return prices


async def get_prices():
    async with price_lock:
        now = time.time()

        cached = price_cache["prices"]
        age = now - price_cache["timestamp"]

        if cached is not None and age < PRICE_CACHE_SECONDS:
            return cached, age, False

        try:
            prices = await asyncio.to_thread(
                fetch_prices
            )

            price_cache["prices"] = prices
            price_cache["timestamp"] = time.time()

            return prices, 0, False

        except Exception:
            if cached is not None:
                age = (
                    time.time()
                    - price_cache["timestamp"]
                )
                return cached, age, True

            raise RuntimeError("Prices unavailable")


# -------------------------
# HISTORICAL DAILY PRICES
# -------------------------

def fetch_history(coin_id):
    url = (
        "https://api.coingecko.com/api/v3/coins/"
        f"{coin_id}/market_chart"
        "?vs_currency=usd&days=100"
    )

    data = api_request(url)
    records = data.get("prices", [])

    if not records:
        raise ValueError("No historical prices")

    daily = {}
    today = datetime.now(
        timezone.utc
    ).date()

    for point in records:
        if (
            not isinstance(point, list)
            or len(point) < 2
        ):
            continue

        timestamp, price = point[:2]

        if (
            not isinstance(timestamp, (int, float))
            or isinstance(price, bool)
            or not isinstance(price, (int, float))
            or price <= 0
        ):
            continue

        day = datetime.fromtimestamp(
            timestamp / 1000,
            timezone.utc,
        ).date()

        # Do not use the current unfinished UTC day.
        if day >= today:
            continue

        daily[day] = float(price)

    ordered = sorted(daily.items())

    if len(ordered) < 60:
        raise ValueError("Insufficient daily prices")

    last_day = ordered[-1][0]

    if (today - last_day).days > 3:
        raise ValueError("Historical data too old")

    # Check for gaps in the recent daily series.
    recent_days = [day for day, _ in ordered[-60:]]

    for previous, current in zip(
        recent_days,
        recent_days[1:],
    ):
        if (current - previous).days != 1:
            raise ValueError("Historical price gap")

    return {
        "prices": [price for _, price in ordered],
        "last_day": last_day.isoformat(),
    }


async def get_history(coin_id):
    cached = history_cache.get(coin_id)

    if cached:
        age = time.time() - cached["saved_at"]

        if age < HISTORY_CACHE_SECONDS:
            return cached["data"]

    data = await asyncio.to_thread(
        fetch_history,
        coin_id,
    )

    history_cache[coin_id] = {
        "data": data,
        "saved_at": time.time(),
    }

    return data


# -------------------------
# INDICATORS
# -------------------------

def ema(prices, period):
    if len(prices) < period:
        raise ValueError("Not enough data for EMA")

    value = sum(prices[:period]) / period
    multiplier = 2 / (period + 1)

    for price in prices[period:]:
        value = (
            price * multiplier
            + value * (1 - multiplier)
        )

    return value


def rsi(prices, period=14):
    if len(prices) < period + 1:
        raise ValueError("Not enough data for RSI")

    changes = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def analyze_coin(history):
    prices = history["prices"]

    current = prices[-1]
    ema20 = ema(prices, 20)
    ema50 = ema(prices, 50)
    rsi14 = rsi(prices, 14)

    if current > ema20 > ema50:
        trend = "🟢 Восходящий"
        score = 2
    elif current < ema20 < ema50:
        trend = "🔴 Нисходящий"
        score = -2
    else:
        trend = "🟡 Смешанный"
        score = 0

    if rsi14 >= 70:
        momentum = "Перекупленность"
        score -= 1
    elif rsi14 <= 30:
        momentum = "Перепроданность"
        score -= 1
    elif 45 <= rsi14 <= 65:
        momentum = "Умеренный импульс"
        score += 1
    else:
        momentum = "Неоднозначный импульс"

    return {
        "price": current,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi14,
        "trend": trend,
        "momentum": momentum,
        "score": score,
        "date": history["last_day"],
    }


def format_price(price):
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:,.4f}"


# -------------------------
# MARKET ANALYSIS
# -------------------------

async def run_analysis(query):
    if scan_lock.locked():
        await query.message.reply_text(
            "⏳ Анализ уже выполняется."
        )
        return

    async with scan_lock:
        await query.message.reply_text(
            "🧠 Анализирую 10 монет.\n"
            "Получаю дневную историю цен.\n"
            "Запросы выполняются по очереди, "
            "поэтому результат появится не сразу."
        )

        results = []
        failures = []

        for index, (coin_id, symbol) in enumerate(
            COINS.items()
        ):
            if time.time() < api_retry_after:
                failures.extend(
                    symbol_left
                    for _, symbol_left in list(COINS.items())[index:]
                )
                break

            cached = history_cache.get(coin_id)

            if not (
                cached
                and time.time() - cached["saved_at"]
                < HISTORY_CACHE_SECONDS
            ):
                if index > 0:
                    await asyncio.sleep(7)

            try:
                history = await get_history(coin_id)
                result = analyze_coin(history)
                result["symbol"] = symbol

                results.append(result)

            except Exception as exc:
                logger.warning(
                    "Analysis %s: %s",
                    symbol,
                    type(exc).__name__,
                )
                failures.append(symbol)

        if not results:
            await query.message.reply_text(
                "⚠️ Анализ недоступен.\n"
                "Исторические данные не получены.\n"
                "Повтори позже.",
                reply_markup=menu(),
            )
            return

        results.sort(
            key=lambda item: item["score"],
            reverse=True,
        )

        message = (
            "🧠 АНАЛИЗ РЫНКА v0.5\n"
            "Дневные цены CoinGecko\n\n"
        )

        for item in results:
            message += (
                f"🔹 {item['symbol']}\n"
                f"Цена закрытого дня: "
                f"{format_price(item['price'])}\n"
                f"RSI 14: {item['rsi']:.1f}\n"
                f"EMA 20: {format_price(item['ema20'])}\n"
                f"EMA 50: {format_price(item['ema50'])}\n"
                f"Тренд: {item['trend']}\n"
                f"Импульс: {item['momentum']}\n"
                f"Оценка: {item['score']}\n"
                f"Дата: {item['date']} UTC\n\n"
            )

        if failures:
            message += (
                "⚠️ Нет анализа для: "
                + ", ".join(failures)
                + "\n\n"
            )

        message += (
            "⚠️ Оценки относительные и "
            "не являются сигналами на покупку.\n"
            "Данные агрегированы CoinGecko, "
            "не являются свечами Bybit.\n"
            "Реальные сделки отключены."
        )

        await query.message.reply_text(
            message,
            reply_markup=menu(),
        )


# -------------------------
# START
# -------------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    get_account(context)

    await update.message.reply_text(
        f"🤖 AI Crypto Trader v{VERSION}\n\n"
        "💰 Paper Trading: $50 стартовый капитал\n"
        "🪙 Монет: 10\n"
        "📊 RSI 14 / EMA 20 / EMA 50\n"
        "💾 Сохранение баланса включено\n"
        "🛡️ Реальные сделки отключены\n\n"
        "Выбери действие:",
        reply_markup=menu(),
    )


# -------------------------
# BUTTONS
# -------------------------

async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    action = query.data
    account = get_account(context)

    balance = account["balance"]
    trades = account["trades"]

    if action == "analysis":
        await run_analysis(query)
        return

    elif action == "prices":
        try:
            prices, age, stale = await get_prices()

            message = "💹 Котировки CoinGecko\n\n"

            for symbol, price in prices.items():
                message += (
                    f"{symbol}: {format_price(price)}\n"
                )

            message += (
                f"\n🕒 Возраст данных: {age:.0f} сек."
            )

            if stale:
                message += (
                    "\n⚠️ Данные устарели. "
                    "Не используй для входа."
                )
            else:
                message += (
                    "\nЦены могут поступать с задержкой."
                )

        except Exception:
            message = (
                "⚠️ Котировки недоступны.\n"
                "Повтори запрос позже."
            )

    elif action == "balance":
        invested = sum(
            trade["amount"]
            for trade in trades
        )

        message = (
            "📊 Демо-счёт\n\n"
            f"💵 Свободные средства: ${balance:.2f}\n"
            f"📋 Учебные позиции: ${invested:.2f}\n\n"
            "⚠️ P&L пока не рассчитывается."
        )

    elif action == "settings":
        message = (
            f"⚙️ AI Crypto Trader v{VERSION}\n\n"
            "Режим: Paper Trading\n"
            "Капитал при создании счёта: $50\n"
            "Монет: 10\n"
            "Горизонт: 2–14 дней\n"
            "Риск: высокий\n"
            "RSI: 14\n"
            "EMA: 20 / 50\n"
            "История: дневные данные\n"
            "Кеш цен: 5 минут\n"
            "Кеш истории: 6 часов\n"
            "Сохранение счёта: включено\n"
            "Реальная торговля: отключена"
        )

    elif action == "signal":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Подтвердить",
                    callback_data="buy",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data="reject",
                ),
            ]
        ])

        await query.message.reply_text(
            "🧪 ДЕМО-СДЕЛКА\n\n"
            "BTC/USDT\n"
            f"Сумма: ${DEMO_AMOUNT:.2f}\n\n"
            "Это тест механизма подтверждения.\n"
            "Не является торговым сигналом.",
            reply_markup=keyboard,
        )
        return

    elif action == "buy":
        # Remove buttons to reduce accidental repeat orders.
        await query.edit_message_reply_markup(
            reply_markup=None
        )

        if balance >= DEMO_AMOUNT:
            account["balance"] = round(
                balance - DEMO_AMOUNT,
                2,
            )

            trades.append({
                "pair": "BTC/USDT",
                "amount": DEMO_AMOUNT,
                "type": "DEMO",
                "created_at": int(time.time()),
            })

            message = (
                "✅ Учебная позиция добавлена.\n"
                f"Свободные средства: "
                f"${account['balance']:.2f}"
            )
        else:
            message = (
                "⚠️ Недостаточно виртуальных средств."
            )

    elif action == "reject":
        await query.edit_message_reply_markup(
            reply_markup=None
        )
        message = "❌ Демо-сделка отклонена."

    elif action == "trades":
        if not trades:
            message = "📋 Учебных позиций пока нет."
        else:
            message = "📋 Учебные позиции:\n\n"

            for i, trade in enumerate(
                trades,
                start=1,
            ):
                message += (
                    f"{i}. {trade['pair']} — "
                    f"${trade['amount']:.2f}\n"
                )

            message += (
                "\nПозиции виртуальные.\n"
                "P&L пока не рассчитывается."
            )

    else:
        message = "Неизвестная команда."

    await query.message.reply_text(
        message,
        reply_markup=menu(),
    )


# -------------------------
# MAIN
# -------------------------

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не задан"
        )

    persistence = PicklePersistence(
        filepath=STATE_FILE,
        update_interval=5,
    )

    app = (
        ApplicationBuilder()
        .token(token)
        .persistence(persistence)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    app.run_polling()


if __name__ == "__main__":
    main()
