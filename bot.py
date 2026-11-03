
import os
import json
import urllib.request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

START_BALANCE = 50.0
COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}


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
        headers={"User-Agent": "CryptoPaperBot/0.2"}
    )

    with urllib.request.urlopen(request, timeout=12) as response:
        data = json.load(response)

    prices = {}
    for coin_id, symbol in COINS.items():
        price = data.get(coin_id, {}).get("usd")
        if not isinstance(price, (int, float)) or price <= 0:
            raise ValueError(f"Нет корректной цены для {symbol}")
        prices[symbol] = price

    return prices


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("balance", START_BALANCE)
    context.user_data.setdefault("trades", [])

    await update.message.reply_text(
        "🤖 AI Crypto Trader v0.2\n\n"
        "Режим: Paper Trading\n"
        "Стартовый капитал: $50\n"
        "Биржа для будущей торговли: Bybit Spot\n\n"
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
            prices = await __import__("asyncio").to_thread(fetch_prices)
            message = "💹 Котировки CoinGecko\n\n"
            for symbol, price in prices.items():
                message += f"{symbol}: ${price:,.2f}\n"
            message += "\nДанные могут поступать с задержкой."
        except Exception:
            message = (
                "⚠️ Не удалось получить котировки.\n"
                "Попробуй позже. Возможно, API временно недоступен."
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
            "Источник котировок: CoinGecko"
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
            "Это учебная операция, а не рыночный сигнал.",
            reply_markup=keyboard,
        )
        return

    elif data == "buy":
        await query.edit_message_reply_markup(reply_markup=None)
        if balance >= 5:
            context.user_data["balance"] = balance - 5
            trades.append({"pair": "BTC/USDT", "amount": 5})
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
                message += f"{trade['pair']} — ${trade['amount']}\n"
        else:
            message = "Открытых виртуальных позиций нет."

    else:
        message = "Неизвестная команда."

    await query.message.reply_text(message, reply_markup=menu())


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.run_polling()


if __name__ == "__main__":
    main()
