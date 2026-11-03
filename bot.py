
import os
import json
import time
import asyncio
import logging
import urllib.request
import urllib.error

from pathlib import Path

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


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# =========================
# SETTINGS
# =========================

VERSION = "0.4"

START_BALANCE = 50.0
DEMO_AMOUNT = 5.0

CACHE_SECONDS = 300
RETRY_SECONDS = 300

STATE_FILE = Path(__file__).resolve().parent / "paper_state.pkl"

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


# =========================
# PRICE CACHE
# =========================

price_cache = {
    "prices": None,
    "timestamp": 0,
    "retry_after": 0,
}

price_lock = asyncio.Lock()


# =========================
# MENU
# =========================

def menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💹 Котировки",
                callback_data="prices",
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Баланс",
                callback_data="balance",
            )
        ],
        [
            InlineKeyboardButton(
                "📈 Демо-сделка",
                callback_data="signal",
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Мои сделки",
                callback_data="trades",
            )
        ],
        [
            InlineKeyboardButton(
                "⚙️ Настройки",
                callback_data="settings",
            )
        ],
    ])


# =========================
# COINGECKO
# =========================

def fetch_prices():
    ids = ",".join(COINS.keys())

    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CryptoPaperBot/0.4",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=15,
    ) as response:
        data = json.load(response)

    prices = {}

    for coin_id, symbol in COINS.items():
        price = data.get(coin_id, {}).get("usd")

        if (
            not isinstance(price, (int, float))
            or isinstance(price, bool)
            or price <= 0
        ):
            raise ValueError(
                f"Invalid price: {symbol}"
            )

        prices[symbol] = price

    return prices


async def get_prices():
    async with price_lock:
        now = time.time()

        cached = price_cache["prices"]

        age = (
            now - price_cache["timestamp"]
            if cached is not None
            else 0
        )

        if (
            cached is not None
            and age < CACHE_SECONDS
        ):
            return cached, age, False

        if now < price_cache["retry_after"]:
            if cached is not None:
                return cached, age, True

            raise RuntimeError(
                "CoinGecko cooldown"
            )

        try:
            prices = await asyncio.to_thread(
                fetch_prices
            )

            price_cache["prices"] = prices
            price_cache["timestamp"] = time.time()
            price_cache["retry_after"] = 0

            return prices, 0, False

        except urllib.error.HTTPError as exc:
            logger.warning(
                "CoinGecko HTTP status: %s",
                exc.code,
            )

            if exc.code == 429:
                cooldown = RETRY_SECONDS
            else:
                cooldown = 60

            price_cache["retry_after"] = (
                time.time() + cooldown
            )

        except Exception as exc:
            logger.warning(
                "CoinGecko error type: %s",
                type(exc).__name__,
            )

            price_cache["retry_after"] = (
                time.time() + 60
            )

        cached = price_cache["prices"]

        if cached is not None:
            age = (
                time.time()
                - price_cache["timestamp"]
            )

            return cached, age, True

        raise RuntimeError(
            "Prices unavailable"
        )


# =========================
# USER DATA
# =========================

def get_account(context):
    data = context.user_data

    if "balance" not in data:
        data["balance"] = START_BALANCE

    if "trades" not in data:
        data["trades"] = []

    return data


# =========================
# START COMMAND
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    get_account(context)

    await update.message.reply_text(
        f"🤖 AI Crypto Trader v{VERSION}\n\n"
        "Режим: Paper Trading\n"
        "Стартовый капитал: $50\n"
        "Рынок: Spot\n"
        "Монет под наблюдением: 10\n"
        "Подтверждение сделок: вручную\n\n"
        "💾 Демо-баланс и сделки сохраняются.\n\n"
        "Выбери действие:",
        reply_markup=menu(),
    )


# =========================
# BUTTON HANDLER
# =========================

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

    # ---------------------
    # PRICES
    # ---------------------

    if action == "prices":
        try:
            prices, age, stale = await get_prices()

            message = (
                "💹 Котировки CoinGecko\n\n"
            )

            for symbol, price in prices.items():
                if price >= 1:
                    formatted = f"{price:,.2f}"
                else:
                    formatted = f"{price:,.4f}"

                message += (
                    f"{symbol}: ${formatted}\n"
                )

            message += (
                f"\n🕒 Возраст данных: "
                f"{age:.0f} сек."
            )

            if stale:
                message += (
                    "\n⚠️ Используются старые цены."
                    "\nНовые данные пока недоступны."
                    "\nНе используй их для входа."
                )
            else:
                message += (
                    "\nКотировки могут поступать"
                    " с задержкой."
                )

        except Exception:
            message = (
                "⚠️ Котировки пока недоступны.\n"
                "Повтори попытку через"
                " несколько минут."
            )

    # ---------------------
    # BALANCE
    # ---------------------

    elif action == "balance":
        invested = sum(
            trade["amount"]
            for trade in trades
        )

        message = (
            "📊 Демо-счёт\n\n"
            f"💵 Свободные средства: "
            f"${balance:.2f}\n"
            f"📋 Учебные позиции: "
            f"${invested:.2f}\n\n"
            "⚠️ Прибыль и убыток пока"
            " не рассчитываются."
        )

    # ---------------------
    # SETTINGS
    # ---------------------

    elif action == "settings":
        message = (
            f"⚙️ AI Crypto Trader v{VERSION}\n\n"
            "Режим: Paper Trading\n"
            "Стартовый капитал: $50\n"
            "Горизонт: 2–14 дней\n"
            "Риск: высокий\n"
            "Рынок: Spot\n"
            "Подтверждение: вручную\n"
            "Монет: 10\n"
            "Кеш котировок: 5 минут\n"
            "Защита от HTTP 429: включена\n"
            "Сохранение демо-счёта: включено\n\n"
            "Реальная торговля отключена."
        )

    # ---------------------
    # DEMO SIGNAL
    # ---------------------

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
            "🧪 ДЕМО-СИГНАЛ\n\n"
            "BTC/USDT\n"
            f"Сумма: ${DEMO_AMOUNT:.2f}\n\n"
            "Это учебный пример, "
            "а не результат анализа.\n"
            "Реальная покупка не выполняется.",
            reply_markup=keyboard,
        )

        return

    # ---------------------
    # BUY DEMO
    # ---------------------

    elif action == "buy":
        await query.edit_message_reply_markup(
            reply_markup=None
        )

        if balance >= DEMO_AMOUNT:
            account["balance"] = (
                round(
                    balance - DEMO_AMOUNT,
                    2,
                )
            )

            trades.append({
                "pair": "BTC/USDT",
                "amount": DEMO_AMOUNT,
                "type": "DEMO",
                "created_at": int(time.time()),
            })

            message = (
                "✅ Учебная позиция добавлена.\n\n"
                f"BTC/USDT: ${DEMO_AMOUNT:.2f}\n"
                "Это не реальная сделка.\n\n"
                f"💵 Остаток: "
                f"${account['balance']:.2f}"
            )

        else:
            message = (
                "⚠️ Недостаточно виртуальных"
                " средств."
            )

    # ---------------------
    # REJECT
    # ---------------------

    elif action == "reject":
        await query.edit_message_reply_markup(
            reply_markup=None
        )

        message = (
            "❌ Демо-сделка отклонена."
        )

    # ---------------------
    # TRADES
    # ---------------------

    elif action == "trades":
        if trades:
            message = (
                "📋 Учебные позиции:\n\n"
            )

            for index, trade in enumerate(
                trades,
                start=1,
            ):
                message += (
                    f"{index}. "
                    f"{trade['pair']} — "
                    f"${trade['amount']:.2f}\n"
                )

            message += (
                "\nПозиции виртуальные."
                "\nP&L пока не рассчитывается."
            )

        else:
            message = (
                "📋 Учебных позиций пока нет."
            )

    else:
        message = (
            "Неизвестная команда."
        )

    await query.message.reply_text(
        message,
        reply_markup=menu(),
    )


# =========================
# MAIN
# =========================

def main():
    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN"
    )

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
