"""
Backtest the Dual SMA crossover strategy using synthetic 1-minute bar data.

Usage:
    python user/backtesting/backtesting_dual_sma.py BTCUSDT
    python user/backtesting/backtesting_dual_sma.py ETHUSDT --fast-period 10 --slow-period 50
    python user/backtesting/backtesting_dual_sma.py SOLUSDT --fast-period 5 --slow-period 30 --bars 20000
"""

import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
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
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider


def _generate_bars(bar_type: BarType, instrument, n: int, seed: int = 42):
    rng = np.random.default_rng(seed)
    base_price = 50_000.0 if "BTC" in str(bar_type) else 3_000.0
    price = base_price + np.cumsum(rng.normal(0, base_price * 0.0004, n))
    spread = np.abs(rng.normal(0, base_price * 0.0003, n))
    bars_df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread,
            "low": price - spread,
            "close": price + rng.normal(0, base_price * 0.00005, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    bars_df["high"] = bars_df[["open", "high", "close"]].max(axis=1)
    bars_df["low"] = bars_df[["open", "low", "close"]].min(axis=1)
    return BarDataWrangler(bar_type, instrument).process(bars_df)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backtest Dual SMA strategy")
    parser.add_argument("symbol", type=str, help="Instrument symbol, e.g. BTCUSDT or ETHUSDT")
    parser.add_argument("--fast-period", type=int, default=10, help="Fast SMA period (default: 10)")
    parser.add_argument("--slow-period", type=int, default=50, help="Slow SMA period (default: 50)")
    parser.add_argument("--bars", type=int, default=30_000, help="Number of synthetic 1-min bars (default: 30000)")
    parser.add_argument("--trade-size", type=str, default="0.01", help="Trade size (default: 0.01)")
    parser.add_argument("--log-level", type=str, default="ERROR", help="Log level (default: ERROR)")
    args = parser.parse_args()

    symbol = args.symbol.upper()

    instrument = TestInstrumentProvider.btcusdt_perp_binance() if symbol == "BTCUSDT" else TestInstrumentProvider.ethusdt_perp_binance()
    instrument_id = instrument.id

    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")

    bars = _generate_bars(bar_type, instrument, args.bars)

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
            trade_size=Decimal(args.trade_size),
            fast_period=args.fast_period,
            slow_period=args.slow_period,
            bar_suffix="1-MINUTE-LAST-EXTERNAL",
            telegram_active=False
        ),
    )
    engine.add_strategy(strategy)

    engine.run()

    account_report = engine.trader.generate_account_report(BINANCE)
    positions_report = engine.trader.generate_positions_report()
    order_fills_report = engine.trader.generate_order_fills_report()

    with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
        print("\n=== ACCOUNT REPORT ===")
        print(account_report)
        print("\n=== POSITIONS REPORT ===")
        print(positions_report)
        print("\n=== ORDER FILLS REPORT ===")
        print(order_fills_report)

    out_dir = Path(__file__).resolve().parents[1]
    if hasattr(account_report, "to_csv"):
        account_report.to_csv(out_dir / "account_report_dual_sma.csv", index=False)
    if hasattr(positions_report, "to_csv"):
        positions_report.to_csv(out_dir / "positions_report_dual_sma.csv", index=False)
    if hasattr(order_fills_report, "to_csv"):
        order_fills_report.to_csv(out_dir / "order_fills_report_dual_sma.csv", index=False)

    engine.dispose()

    try:
        from nautilus_trader.analysis import create_tearsheet

        create_tearsheet(
            engine=engine,
            output_path=str(out_dir / "backtest_results_dual_sma.html"),
        )
        print(f"\nTearsheet saved to {out_dir / 'backtest_results_dual_sma.html'}")
    except (ImportError, Exception) as e:
        print(f"\nTearsheet generation skipped: {e}")


if __name__ == "__main__":
    main()
