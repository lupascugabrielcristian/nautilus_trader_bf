# User scripts

## Alpaca paper trading

Script: `alpaca_demo_run.py`

Runs a live paper trading system using Alpaca Markets for streaming data
and order execution, with NautilusTrader as the strategy engine and
`SandboxExecutionClient` for local fill simulation.

### Quick start

```bash
# Edit user/.env with your Alpaca API keys
python user/alpaca_demo_run.py [SYMBOL]
```

The symbol is optional; defaults to `AAPL` (or the value from `.env` / `ALPACA_SYMBOL`).

### Configuration

Create `user/.env`:

```ini
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_SYMBOL=AAPL
```

The script loads `.env` automatically from the `user/` directory.

### Requirements

- `alpaca-py>=0.13,<1.0` (listed in `user/requirements.txt`)

---

## Binance scripts

### Scripts

- `run_binance_data_monitor.py`: data-only monitoring (no order execution)
- `run_binance_paper_live.py`: paper/live workflow with execution enabled

Both scripts were switched from AX to Binance adapter usage.

### Required environment variables

#### Common runtime settings

- `BINANCE_ENV`
  - `LIVE` for production Binance
  - `TESTNET` for Binance testnet
  - `DEMO` for Binance demo
- `BINANCE_ACCOUNT_TYPE`
  - `SPOT`
  - `MARGIN`
  - `ISOLATED_MARGIN`
  - `USDT_FUTURES`
  - `COIN_FUTURES`
- `BINANCE_INSTRUMENT`
  - Spot example: `BTCUSDT`
  - Futures example: `BTCUSDT-PERP`
- `BINANCE_TRADER_ID` (example: `TESTER-001`)
- `BINANCE_LOG_LEVEL` (default: `INFO`)
  - Allowed values:
    - `TRACE`
    - `DEBUG`
    - `INFO`
    - `WARNING`
    - `ERROR`
  - Use uppercase values.

#### Strategy parameters used by `run_binance_paper_live.py`

- `BINANCE_TRADE_SIZE` (example: `0.01`)
- `BINANCE_BB_PERIOD` (example: `20`)
- `BINANCE_BB_STD` (example: `2.0`)
- `BINANCE_RSI_PERIOD` (example: `14`)
- `BINANCE_RSI_BUY` (example: `0.30`)
- `BINANCE_RSI_SELL` (example: `0.70`)

### API credential variables

Credentials are only required when private endpoints are used (execution/account data).

#### LIVE

- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`

#### TESTNET

For spot/margin:

- `BINANCE_TESTNET_API_KEY`
- `BINANCE_TESTNET_API_SECRET`

For futures:

- `BINANCE_FUTURES_TESTNET_API_KEY`
- `BINANCE_FUTURES_TESTNET_API_SECRET`

#### DEMO

- `BINANCE_DEMO_API_KEY`
- `BINANCE_DEMO_API_SECRET`

### Example setups

#### 1) Data-only monitoring on public market data (no credentials required)

```bash
export BINANCE_ENV=DEMO
export BINANCE_ACCOUNT_TYPE=USDT_FUTURES
export BINANCE_INSTRUMENT=BTCUSDT-PERP
export BINANCE_TRADER_ID=TESTER-001
export BINANCE_LOG_LEVEL=INFO
PYTHONPATH=. python user/run_binance_data_monitor.py
```

#### 2) Futures testnet with execution enabled

```bash
export BINANCE_ENV=TESTNET
export BINANCE_ACCOUNT_TYPE=USDT_FUTURES
export BINANCE_INSTRUMENT=ETHUSDT-PERP
export BINANCE_TRADER_ID=TESTER-001
export BINANCE_LOG_LEVEL=INFO
export BINANCE_FUTURES_TESTNET_API_KEY=your_key
export BINANCE_FUTURES_TESTNET_API_SECRET=your_secret
export BINANCE_TRADE_SIZE=0.02
PYTHONPATH=. python user/run_binance_paper_live.py
```

#### 3) Spot testnet with execution enabled

```bash
export BINANCE_ENV=TESTNET
export BINANCE_ACCOUNT_TYPE=SPOT
export BINANCE_INSTRUMENT=BTCUSDT
export BINANCE_TRADER_ID=TESTER-001
export BINANCE_LOG_LEVEL=INFO
export BINANCE_TESTNET_API_KEY=AACt88gJfD9BN3FxEl7mb59dZPgNWV0H2zG3ln16JndQuZSyAZvfFRIR1kAuzLmi
export BINANCE_TESTNET_API_SECRET=MC4CAQAwBQYDK2VwBCIEICVUs+P86MURyo6+kvakuuxhmcsMf0Q7zi0+Ft9dDmMg
export BINANCE_TRADE_SIZE=0.01
PYTHONPATH=. python user/run_binance_paper_live.py
```

#### 4) Futures demo with execution enabled

```bash
export BINANCE_ENV=DEMO
export BINANCE_ACCOUNT_TYPE=SPOT
export BINANCE_INSTRUMENT=BCHEUR
export BINANCE_TRADER_ID=TESTER-001
export BINANCE_LOG_LEVEL=INFO
export BINANCE_DEMO_API_KEY=Ntk9Xu9On1VdB8jtgiVqFdZ8Z0r0iQjoQHpSexiNTIrftV6860t2iwCrWHVAURRM
export BINANCE_DEMO_API_SECRET=QlzqpNvF5JtnBegg7Y5jsXAkqxuNOTMg3RAzjKMUEhdIyfwY8W7QhRdPqDU0sPhW
export BINANCE_TRADE_SIZE=0.01
PYTHONPATH=. python user/run_binance_paper_live.py
```

```bash
export BINANCE_ENV=DEMO
export BINANCE_ACCOUNT_TYPE=SPOT
export BINANCE_INSTRUMENT=ACTUSDC
export BINANCE_TRADER_ID=TESTER-001
export BINANCE_LOG_LEVEL=INFO
export BINANCE_DEMO_API_KEY=D4l48f9wKt5FTzaVWENwwI1pvmmeu02xk49Dhg0KK9Jsk42DAsRSlZkjvIvTQCYI
export BINANCE_DEMO_API_SECRET=MC4CAQAwBQYDK2VwBCIEIHfyJhO6VzfrbtXNPG9DpOIeSpwQgtF1vzBi0XvolUbA
export BINANCE_TRADE_SIZE=0.01
PYTHONPATH=. python user/run_binance_paper_live.py
```