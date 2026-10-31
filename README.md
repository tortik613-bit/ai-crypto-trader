# AI Crypto Trader — Bybit Spot Swing Research

A Python starter for researching a semi-automatic, long-only Bybit Spot swing system. The project currently uses public market data only and **does not place exchange orders**.

## Current behavior

- Downloads completed Bybit V5 Spot candles (4H and 1D), with backward pagination for historical tests.
- Calculates EMA, RSI, ATR and MACD histogram.
- Looks for a daily uptrend plus a 4H momentum setup.
- Sizes a hypothetical position using 1% equity risk, a 30% allocation cap and a 10% cash reserve.
- Runs a single-symbol historical backtest using next-candle-open entries, taker-fee and slippage assumptions, stop/target exits, and conservative stop-first handling when both levels are touched in one candle.
- Records paper signals. Paper signals are not simulated fills and do not represent PnL.

## Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
```

## Commands

```bash
crypto-trader scan --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT LINKUSDT
crypto-trader paper --symbol BTCUSDT
crypto-trader backtest --symbol BTCUSDT --h4-bars 3000 --daily-bars 500
pytest
```

Backtest reports are saved under `data/backtests/`. The backtest currently handles one symbol and one open position at a time. It does not yet model exchange-specific quantity/price increments, partial fills, spread changes, funding (Spot has no funding), outages, or dynamic fee tiers. Fee and slippage settings are assumptions in `config/default.json`; replace them with the actual account fee tier before interpreting results.

## Safety and limitations

- No API keys are required for public market-data commands.
- No authenticated order endpoints are implemented.
- `paper` writes a signal record only; it does not simulate order fills, cash, open positions or realized PnL.
- Backtest results are historical simulations, not forecasts. They can be distorted by market regime, data quality, execution assumptions and parameter overfitting.
- The backtest currently liquidates any open position at the end of the sample.
- Do not connect real trading permissions until the backtest, paper ledger, order validation, monitoring and manual approval workflow have been separately tested.

## Next milestones

1. Out-of-sample and walk-forward validation across a liquid USDT Spot universe.
2. Portfolio-level simulation with simultaneous positions and shared cash.
3. A review queue that presents each proposed trade for explicit human approval.
4. Bybit Demo Trading integration, then limited live execution only after separate approval.
