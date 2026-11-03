
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ContextTypes
)

START_BALANCE = 50.0

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Баланс", callback_data="balance")],
        [InlineKeyboardButton("📈 Тестовая сделка", callback_data="signal")],
        [InlineKeyboardButton("📋 Мои сделки", callback_data="trades")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("balance", START_BALANCE)
    context.user_data.setdefault("trades", [])
    await update.message.reply_text(
        "🤖 AI Crypto Trader\n\n"
        "Биржа: Bybit Spot\n"
        "Режим: Paper Trading\n"
        "Баланс: $50\n\n"
        "Выбери действие:",
        reply_markup=menu()
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    balance = context.user_data.setdefault("balance", START_BALANCE)
    trades = context.user_data.setdefault("trades", [])

    if data == "balance":
        message = f"💰 Свободный баланс: ${balance:.2f}"

    elif data == "settings":
        message = (
            "⚙️ Настройки\n\n"
            "Биржа: Bybit Spot\n"
            "Капитал: $50\n"
            "Горизонт: 2–14 дней\n"
            "Риск: высокий\n"
            "Режим: Paper Trading\n"
            "Подтверждение: вручную"
        )

    elif data == "signal":
        message = (
            "🧪 ДЕМО-СИГНАЛ (не рыночная рекомендация)\n\n"
            "BTC/USDT\n"
            "Сумма: $5\n\n"
            "Открыть виртуальную позицию?"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="buy"),
                InlineKeyboardButton("❌ Отклонить", callback_data="reject")
            ]
        ])
        await query.message.reply_text(message, reply_markup=keyboard)
        return

    elif data == "buy":
        if balance < 5:
            message = "Недостаточно виртуальных средств."
        else:
            context.user_data["balance"] = balance - 5
            trades.append({"pair": "BTC/USDT", "amount": 5})
            message = "✅ Демо-позиция открыта на $5."
        await query.edit_message_reply_markup(reply_markup=None)

    elif data == "reject":
        message = "❌ Сигнал отклонён."
        await query.edit_message_reply_markup(reply_markup=None)

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
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.run_polling()

if __name__ == "__main__":
    main()
