
import os
import json
import time
import asyncio
import logging
import urllib.request
import urllib.error

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

START_BALANCE = 50.0
CACHE_SECONDS = 300
RETRY_SECONDS = 300

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

price_cache = {
    "prices": None,
    "timestamp": 0,
    "retry_after": 0,
}
price_lock = asyncio.Lock()


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💹 Котировки", callback_data="prices")],
        [InlineKeyboardButton("📊 Баланс", callback_data="balance")],
        [InlineKeyboardButton("📈 Демо-сделка", callback_data="signal")],
        [InlineKeyboardButton("📋 Мои сделки", callback_data="trades")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
    ])


def fetch_prices():
    ids = ",".join(COINS.keys())
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CryptoPaperBot/0.3",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)

    prices = {}
    for coin_id, symbol in COINS.items():
        price = data.get(coin_id, {}).get("usd")
        if not isinstance(price, (int, float)) or price <= 0:
            raise ValueError(f"Некорректная цена {symbol}")
        prices[symbol] = price

    return prices


async def get_prices():
    async with price_lock:
        now = time.time()
        cached = price_cache["prices"]
        age = now - price_cache["timestamp"]

        if cached is not None and age < CACHE_SECONDS:
            return cached, age, False

        if now < price_cache["retry_after"]:
            if cached is not None:
                return cached, age, True
            raise RuntimeError("API cooldown")

        try:
            prices = await asyncio.to_thread(fetch_prices)
            price_cache["prices"] = prices
            price_cache["timestamp"] = time.time()
            price_cache["retry_after"] = 0
            return prices, 0, False

        except urllib.error.HTTPError as exc:
            logger.warning("CoinGecko HTTP status: %s", exc.code)
            price_cache["retry_after"] = time.time() + RETRY_SECONDS

        except Exception as exc:
            logger.warning(
                "CoinGecko error: %s", type(exc).__name__
            )
            price_cache["retry_after"] = time.time() + 60

        cached = price_cache["prices"]
        if cached is not None:
            age = time.time() - price_cache["timestamp"]
            return cached, age, True

        raise RuntimeError("Котировки недоступны")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("balance", START_BALANCE)
    context.user_data.setdefault("trades", [])

    await update.message.reply_text(
        "🤖 AI Crypto Trader v0.3\n\n"
        "Режим: Paper Trading\n"
        "Капитал: $50\n"
        "Биржа для будущих сделок: Bybit Spot\n"
        "Монет под наблюдением: 10\n\n"
        "Выбери действие:",
        reply_markup=menu(),
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    balance = context.user_data.setdefault("balance", START_BALANCE)
    trades = context.user_data.setdefault("trades", [])

    if data == "prices":
        try:
            prices, age, stale = await get_prices()

            message = "💹 Котировки CoinGecko\n\n"
            for symbol, price in prices.items():
                message += f"{symbol}: ${price:,.4f}\n"

            message += f"\n🕒 Возраст данных: {age:.0f} сек."

            if stale:
                message += "\n⚠️ API недоступен. Цены устарели."
            else:
                message += "\nКотировки могут поступать с задержкой."

        except Exception:
            message = (
                "⚠️ Котировки пока недоступны.\n"
                "Повтори попытку через несколько минут."
            )

    elif data == "balance":
        message = f"💰 Виртуальный баланс: ${balance:.2f}"

    elif data == "settings":
        message = (
            "⚙️ Настройки\n\n"
            "Режим: Paper Trading\n"
            "Капитал: $50\n"
            "Горизонт: 2–14 дней\n"
            "Риск: высокий\n"
            "Подтверждение: вручную\n"
            "Монет: 10\n"
            "Кеш: 5 минут"
        )

    elif data == "signal":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Подтвердить", callback_data="buy"
                ),
                InlineKeyboardButton(
                    "❌ Отклонить", callback_data="reject"
                ),
            ]
        ])

        await query.message.reply_text(
            "🧪 ДЕМО-СИГНАЛ\n\n"
            "BTC/USDT\n"
            "Сумма: $5\n\n"
            "Это учебный пример, а не результат анализа.",
            reply_markup=keyboard,
        )
        return

    elif data == "buy":
        await query.edit_message_reply_markup(reply_markup=None)

        if balance >= 5:
            context.user_data["balance"] = balance - 5
            trades.append({
                "pair": "BTC/USDT",
                "amount": 5,
            })
            message = "✅ Учебная позиция на $5 добавлена."
        else:
            message = "⚠️ Недостаточно виртуальных средств."

    elif data == "reject":
        await query.edit_message_reply_markup(reply_markup=None)
        message = "❌ Демо-сделка отклонена."

    elif data == "trades":
        if trades:
            message = "📋 Учебные позиции:\n\n"
            for trade in trades:
                message += f"{trade['pair']} — ${trade['amount']}\n"
        else:
            message = "Открытых виртуальных позиций нет."

    else:
        message = "Неизвестная команда."

    await query.message.reply_text(
        message,
        reply_markup=menu(),
    )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.run_polling()


if __name__ == "__main__":
    main()
