# Bybit Demo Live Trading

Script: `demo_live_bybit.py`

Runs continuous paper trading on Bybit's DEMO environment using the built-in
`EMACross` strategy. Configurable entirely through environment variables.

---

## Quick start

```bash
export BYBIT_ENV=DEMO
export BYBIT_DEMO_API_KEY=your_demo_key
export BYBIT_DEMO_API_SECRET=your_demo_secret
PYTHONPATH=. python user/demo_live_bybit.py
```

---

## Environment variables

### Credentials

| Variable | For | Required |
|---|---|---|
| `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET` | `DEMO` environment | ✅ |
| `BYBIT_TESTNET_API_KEY` / `BYBIT_TESTNET_API_SECRET` | `TESTNET` environment | ✅ |
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | `MAINNET` environment | ✅ |

### Runtime settings

| Variable | Default | Description |
|---|---|---|
| `BYBIT_ENV` | `DEMO` | One of `DEMO`, `TESTNET`, `MAINNET` |
| `BYBIT_PRODUCT_TYPE` | `LINEAR` | One of `LINEAR`, `SPOT`, `INVERSE`, `OPTION` |
| `BYBIT_SYMBOL` | `ETHUSDT` | Base symbol (e.g., `BTCUSDT`, `SOLUSDT`) |
| `BYBIT_TRADE_SIZE` | `0.010` | Quantity per market order |
| `BYBIT_TRADER_ID` | `TESTER-001` | Trader identifier for the node |
| `BYBIT_LOG_LEVEL` | `INFO` | `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Strategy parameters

| Variable | Default | Description |
|---|---|---|
| `BYBIT_FAST_EMA` | `10` | Fast EMA period |
| `BYBIT_SLOW_EMA` | `20` | Slow EMA period |

---

## Example setups

### 1) ETHUSDT linear perpetuals on DEMO (paper trading)

```bash
export BYBIT_ENV=DEMO
export BYBIT_DEMO_API_KEY=your_demo_api_key
export BYBIT_DEMO_API_SECRET=your_demo_api_secret
export BYBIT_SYMBOL=ETHUSDT
export BYBIT_PRODUCT_TYPE=LINEAR
export BYBIT_TRADE_SIZE=0.010
PYTHONPATH=. python user/demo_live_bybit.py
```

### 2) BTCUSDT linear perpetuals on TESTNET

```bash
export BYBIT_ENV=TESTNET
export BYBIT_TESTNET_API_KEY=your_testnet_api_key
export BYBIT_TESTNET_API_SECRET=your_testnet_api_secret
export BYBIT_SYMBOL=BTCUSDT
export BYBIT_PRODUCT_TYPE=LINEAR
export BYBIT_TRADE_SIZE=0.001
PYTHONPATH=. python user/demo_live_bybit.py
```

### 3) SPOT trading on mainnet

```bash
export BYBIT_ENV=MAINNET
export BYBIT_API_KEY=your_api_key
export BYBIT_API_SECRET=your_api_secret
export BYBIT_SYMBOL=BTCUSDT
export BYBIT_PRODUCT_TYPE=SPOT
export BYBIT_TRADE_SIZE=0.0001
PYTHONPATH=. python user/demo_live_bybit.py
```

---

## Notes

- The script uses `EMACross` — a simple test strategy with no alpha advantage.
  It is **not intended for live trading with real money**.
- API keys are read directly from environment variables by the Rust adapter
  layer — they are never logged or stored by the script.
- The script validates that required credential variables are set before
  connecting, and prints their status at startup.
- Stop the script with Ctrl+C — it handles graceful shutdown (closes positions,
  cancels orders, disconnects).
