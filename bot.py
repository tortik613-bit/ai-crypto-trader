
import os
import json
import time
import asyncio
import logging
import urllib.request

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
CACHE_SECONDS = 60

COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}

price_cache = {
    "prices": None,
    "timestamp": 0,
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
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,solana&vs_currencies=usd"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CryptoPaperBot/0.2.1",
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

        try:
            prices = await asyncio.to_thread(fetch_prices)
            price_cache["prices"] = prices
            price_cache["timestamp"] = time.time()
            return prices, 0, False

        except Exception:
            logger.exception("CoinGecko request failed")

            if cached is not None:
                age = time.time() - price_cache["timestamp"]
                return cached, age, True

            raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("balance", START_BALANCE)
    context.user_data.setdefault("trades", [])

    await update.message.reply_text(
        "🤖 AI Crypto Trader v0.2.1\n\n"
        "Режим: Paper Trading\n"
        "Стартовый капитал: $50\n"
        "Планируемая биржа: Bybit Spot\n\n"
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
                message += f"{symbol}: ${price:,.2f}\n"

            if stale:
                message += (
                    "\n⚠️ API временно недоступен.\n"
                    f"Последние сохранённые цены: {age:.0f} сек. назад."
                )
            else:
                message += (
                    f"\n🕒 Возраст данных в кеше: {age:.0f} сек.\n"
                    "Котировки могут поступать с задержкой."
                )

        except Exception:
            message = (
                "⚠️ Не удалось получить котировки.\n"
                "Сохранённых цен пока нет.\n"
                "Попробуй через минуту."
            )

    elif data == "balance":
        message = f"💰 Свободный виртуальный баланс: ${balance:.2f}"

    elif data == "settings":
        message = (
            "⚙️ Настройки\n\n"
            "Режим: Paper Trading\n"
            "Капитал: $50\n"
            "Горизонт: 2–14 дней\n"
            "Риск: высокий\n"
            "Подтверждение: вручную\n"
            "Источник: CoinGecko\n"
            "Кеш котировок: 60 секунд"
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
            "Это учебная операция, не торговая рекомендация.",
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
            message = "📋 Виртуальные позиции:\n\n"

            for trade in trades:
                message += (
                    f"{trade['pair']} — ${trade['amount']}\n"
                )
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

    logger.info("Crypto bot starting")
    app.run_polling()


if __name__ == "__main__":
    main()
