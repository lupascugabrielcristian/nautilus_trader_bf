"""
Backtest the Dual SMA crossover strategy using real market bar data.

Usage:
    python user/backtesting/backtesting_dual_sma.py BTCUSDT
    python user/backtesting/backtesting_dual_sma.py ETHUSDT --fast-period 10 --slow-period 30
"""

import os
import sys
import time
import urllib.error
import urllib.request
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy.strategy_dual_SMA import DualSMAConfig
from strategy.strategy_dual_SMA import DualSMAStrategy

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider


DATA_DIR = Path(__file__).resolve().parent / "data"


def _download_binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download klines from Binance Futures API in paginated chunks."""
    base_url = "https://fapi.binance.com/fapi/v1/klines"
    all_rows = []
    current_start = start_ms

    max_retries = 5
    while current_start < end_ms:
        url = f"{base_url}?symbol={symbol}&interval={interval}&startTime={current_start}&endTime={end_ms}&limit=1500"
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=30)
                data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"HTTP {e.code} on attempt {attempt + 1}/{max_retries}, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise

        if not data:
            break

        all_rows.extend(data)
        last_open_time = data[-1][0]
        current_start = last_open_time + 1

        if len(data) < 1500:
            break

        time.sleep(0.2)

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore",
    ])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="first")]
    return df


def _get_or_download_klines(symbol: str, days: int = 30) -> pd.DataFrame:
    """Load cached CSV or download from Binance."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / f"{symbol.lower()}_1m_{days}d.csv"

    if csv_path.exists():
        _log_message("Loading cached data from %s", csv_path)
        df = pd.read_csv(csv_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
        return df

    _log_message("Downloading %d days of 1m klines for %s from Binance Futures...", days, symbol)
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    df = _download_binance_klines(symbol, "1m", start_ms, end_ms)
    df.to_csv(csv_path)
    _log_message("Downloaded %d bars, saved to %s", len(df), csv_path)
    return df

def _load_service_ports():    
    import json    
    try:    
        with open('/tmp/service_ports.json') as f:    
            return json.load(f)    
    except (FileNotFoundError, json.JSONDecodeError):    
        return {}



def _log_message(format, *args):
    logging_port = os.environ.get('LOGGING_PORT', '')
    if not logging_port:
        ports = _load_service_ports()
        logging_port = ports.get('LOGGING_PORT', '')
    if not logging_port:
        return
    msg = format % args
    url = f"http://localhost:{logging_port}/service/log"
    try:
        req = urllib.request.Request(url, data=msg.encode(), method='POST')
        urllib.request.urlopen(req, timeout=5)
    except Exception:
      pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backtest Dual SMA strategy")
    parser.add_argument("symbol", type=str, help="Instrument symbol, e.g. BTCUSDT or ETHUSDT")
    parser.add_argument("--fast-period", type=int, default=10, help="Fast EMA period (default: 10)")
    parser.add_argument("--slow-period", type=int, default=30, help="Slow EMA period (default: 30)")
    parser.add_argument("--trade-size", type=str, default=None, help="Trade size (default: 0.01 BTC / 0.5 ETH)")
    parser.add_argument("--days", type=int, default=30, help="Days of historical data to download (default: 30)")
    parser.add_argument("--log-level", type=str, default="ERROR", help="Log level (default: ERROR)")
    args = parser.parse_args()

    symbol = args.symbol.upper()

    if symbol == "BTCUSDT":
        instrument = TestInstrumentProvider.btcusdt_perp_binance()
    else:
        instrument = TestInstrumentProvider.ethusdt_perp_binance()

    instrument_id = instrument.id
    _log_message("instrument id is %s", instrument_id)
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")

    bars_df = _get_or_download_klines(symbol, days=args.days)

    wrangler = BarDataWrangler(bar_type, instrument)
    bars = wrangler.process(bars_df)

    default_size = "0.01" if symbol == "BTCUSDT" else "0.5"
    trade_size = args.trade_size or default_size

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level=args.log_level)),
    )

    BINANCE = Venue("BINANCE")
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(100_000, USDT)],
        base_currency=USDT,
        default_leverage=Decimal(1),
    )

    engine.add_instrument(instrument)
    engine.add_data(bars)

    strategy = DualSMAStrategy(
        config=DualSMAConfig(
            instrument_id=instrument_id,
            trade_size=Decimal(trade_size),
            fast_period=args.fast_period,
            slow_period=args.slow_period,
            bar_suffix="1-MINUTE-LAST-EXTERNAL",
            cooldown_bars=10,
            atr_period=14,
            dm_period=14,
            atr_sl_multiplier=1.5,
            atr_tp_multiplier=3.0,
            telegram_active=False,
        ),
    )
    engine.add_strategy(strategy)

    engine.run()

    account_report = engine.trader.generate_account_report(BINANCE)
    positions_report = engine.trader.generate_positions_report()
    order_fills_report = engine.trader.generate_order_fills_report()

    with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
        _log_message("\n=== ACCOUNT REPORT ===")
        _log_message("%s", account_report)
        _log_message("\n=== POSITIONS REPORT ===")
        _log_message("%s", positions_report)
        _log_message("\n=== ORDER FILLS REPORT ===")
        _log_message("%s", order_fills_report)

    project_root = Path(__file__).resolve().parents[1]
    if hasattr(account_report, "to_csv"):
        account_report.to_csv(project_root / "account_report_dual_sma.csv", index=False)
    if hasattr(positions_report, "to_csv"):
        positions_report.to_csv(project_root / "positions_report_dual_sma.csv", index=False)
    if hasattr(order_fills_report, "to_csv"):
        order_fills_report.to_csv(project_root / "order_fills_report_dual_sma.csv", index=False)

    engine.dispose()

    try:
        from nautilus_trader.analysis import create_tearsheet

        create_tearsheet(
            engine=engine,
            output_path=str(project_root / "backtest_results_dual_sma.html"),
        )
        _log_message("\nTearsheet saved to %s", project_root / 'backtest_results_dual_sma.html')
    except (ImportError, Exception) as e:
        _log_message("\nTearsheet generation skipped: %s", e)


if __name__ == "__main__":
    main()
