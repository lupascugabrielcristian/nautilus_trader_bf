#!/usr/bin/env python3
"""
Path B: Production-grade live paper trading system.

Architecture:
  - Alpaca Markets StockDataStream (WebSocket) delivers real-time minute bars.
  - NautilusTrader TradingNode receives these bars and runs the strategy + portfolio.
  - The SandboxExecutionClient simulates order fills locally.
  - The Alpaca TradingClient submits matching market orders to the paper API.
  - On shutdown both the WebSocket and the TradingNode are torn down cleanly.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import signal
import sys
from decimal import Decimal


def _load_dotenv(path: str) -> None:
    """Load KEY=VAL pairs from a .env file into process environment."""
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and val:
            os.environ.setdefault(key, val)


_load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")

import pandas as pd

# ── Alpaca SDK ────────────────────────────────────────────────────────────────
from alpaca.data.enums import DataFeed
from alpaca.data.live import StockDataStream
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import MarketOrderRequest

# ── Nautilus Trader ───────────────────────────────────────────────────────────
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.execution import SandboxExecutionClient
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

# ==============================================================================
# Environment
# ==============================================================================

ALPACA_API_KEY: str = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY: str = os.environ.get("ALPACA_SECRET_KEY", "")
SYMBOL: str = os.environ.get("ALPACA_SYMBOL", "AAPL")

# ==============================================================================
# 1. Trading Strategy
# ==============================================================================


class AlpacaPaperStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_quantity: Decimal = Decimal("50")


class AlpacaPaperStrategy(Strategy):
    def __init__(self, config: AlpacaPaperStrategyConfig) -> None:
        super().__init__(config)
        self.last_close: Price | None = None
        self.alpaca: TradingClient | None = None

    def on_start(self) -> None:
        self.log.info(f"AlpacaPaperStrategy started")

    def on_bar(self, bar: Bar) -> None:
        sys.stdout.write(
            f"[BAR] {bar.bar_type} | "
            f"O={bar.open} H={bar.high} L={bar.low} C={bar.close} "
            f"V={bar.volume} @ {pd.Timestamp(bar.ts_event, unit='ns')}\n"
        )
        sys.stdout.flush()

        if self.last_close is None:
            self.last_close = bar.close
            return

        qty = Quantity(float(self.config.trade_quantity), precision=0)

        # ── Buy signal: price dropped ────────────────────────────────────
        if bar.close < self.last_close:
            if self.portfolio.is_flat(self.config.instrument_id):
                sys.stdout.write(
                    f"[EXECUTION ALERT] Placing Paper Order for "
                    f"{qty} shares of {self.config.instrument_id.symbol} – BUY\n"
                )
                sys.stdout.flush()
                self._submit_alpaca_order(
                    symbol=str(self.config.instrument_id.symbol),
                    qty=int(self.config.trade_quantity),
                    side=AlpacaOrderSide.BUY,
                )
                self._submit_local_order(OrderSide.BUY, qty)

        # ── Sell signal: price recovered ─────────────────────────────────
        elif bar.close > self.last_close:
            if self.portfolio.is_net_long(self.config.instrument_id):
                sys.stdout.write(
                    f"[EXECUTION ALERT] Liquidating Paper Position for "
                    f"{qty} shares of {self.config.instrument_id.symbol} – SELL\n"
                )
                sys.stdout.flush()
                self._submit_alpaca_order(
                    symbol=str(self.config.instrument_id.symbol),
                    qty=int(self.config.trade_quantity),
                    side=AlpacaOrderSide.SELL,
                )
                self.close_all_positions(self.config.instrument_id)

        self.last_close = bar.close

    def _submit_alpaca_order(
        self,
        symbol: str,
        qty: int,
        side: AlpacaOrderSide,
    ) -> None:
        if self.alpaca is None:
            return
        try:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=AlpacaTimeInForce.DAY,
            )
            resp = self.alpaca.submit_order(order_data=req)
            sys.stdout.write(
                f"[ALPACA] Order submitted – id={resp.id} symbol={resp.symbol} "
                f"side={resp.side} qty={resp.qty} status={resp.status}\n"
            )
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(f"[ALPACA ERROR] {e}\n")
            sys.stdout.flush()

    def _submit_local_order(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=qty,
        )
        self.submit_order(order)

    def on_stop(self) -> None:
        self.log.info("AlpacaPaperStrategy stopped.")


# ==============================================================================
# 2. Alpaca WebSocket Data Streamer
# ==============================================================================


class AlpacaDataStreamer:
    """Background worker that translates Alpaca live bars into Nautilus Bar
    objects and delivers them to the strategy."""

    def __init__(
        self,
        strategy: AlpacaPaperStrategy,
        bar_type: BarType,
        symbols: list[str],
        api_key: str,
        secret_key: str,
        feed: DataFeed = DataFeed.SIP,
    ) -> None:
        self.strategy = strategy
        self.bar_type = bar_type
        self.symbols = symbols
        self._ws: StockDataStream | None = None
        self._api_key = api_key
        self._secret_key = secret_key
        self._feed = feed
        self._stopped = False

    async def run(self) -> None:
        self._ws = StockDataStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
            feed=self._feed,
        )
        self._ws.subscribe_bars(self._on_bar, *self.symbols)

        sys.stdout.write(f"[STREAM] Connecting Alpaca WebSocket ({self._feed})…\n")
        sys.stdout.flush()

        sys.stdout.write("[STREAM] Waiting for bars...\n")
        sys.stdout.flush()

        try:
            await self._ws._run_forever()
        except asyncio.CancelledError:
            pass
        finally:
            if not self._stopped:
                await self._stop()

    async def _stop(self) -> None:
        self._stopped = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        sys.stdout.write("[STREAM] Alpaca WebSocket closed.\n")
        sys.stdout.flush()

    async def _on_bar(self, data) -> None:
        if self._stopped:
            return

        ts_ns = dt_to_unix_nanos(pd.Timestamp(data.timestamp))
        bar = Bar(
            bar_type=self.bar_type,
            open=Price(data.open, precision=2),
            high=Price(data.high, precision=2),
            low=Price(data.low, precision=2),
            close=Price(data.close, precision=2),
            volume=Quantity(data.volume, precision=0),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        self.strategy.on_bar(bar)


# ==============================================================================
# 3. Node Construction and Lifecycle
# ==============================================================================


async def run_live_node() -> None:
    VENUE = Venue("XNAS")
    INSTRUMENT_ID = InstrumentId.from_str(f"{SYMBOL}.XNAS")
    BAR_TYPE = BarType.from_str(f"{SYMBOL}.XNAS-1-MINUTE-LAST-INTERNAL")

    # ── Alpaca API clients ───────────────────────────────────────────────
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        sys.stderr.write(
            "FATAL: ALPACA_API_KEY and ALPACA_SECRET_KEY environment "
            "variables must be set.\n"
        )
        sys.exit(1)

    alpaca_trading = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    sys.stdout.write(f"[CONFIG] Alpaca TradingClient created (paper=True)\n")
    sys.stdout.flush()

    # ── Instrument definition ────────────────────────────────────────────
    now_ns = dt_to_unix_nanos(pd.Timestamp.now("UTC"))
    apple_stock = Equity(
        instrument_id=INSTRUMENT_ID,
        raw_symbol=Symbol(SYMBOL),
        currency=USD,
        price_precision=2,
        price_increment=Price(0.01, precision=2),
        lot_size=Quantity(1, precision=0),
        ts_event=now_ns,
        ts_init=now_ns,
    )

    # ── Nautilus TradingNode ─────────────────────────────────────────────
    node_config = TradingNodeConfig(
        trader_id="demo-trader-01",
        logging=LoggingConfig(log_level="INFO", log_colors=True),
    )
    node = TradingNode(config=node_config)
    node.cache.add_instrument(apple_stock)
    sys.stdout.write(f"[INSTRUMENT] Added {INSTRUMENT_ID} to cache\n")
    sys.stdout.flush()

    # ── Sandbox execution client (local fill simulation) ─────────────────
    sandbox_config = SandboxExecutionClientConfig(
        venue=VENUE.value,
        starting_balances=[f"100000 {USD.code}"],
        oms_type="NETTING",
        account_type="MARGIN",
        bar_execution=True,
        trade_execution=False,
        reject_stop_orders=False,
        support_gtd_orders=False,
        support_contingent_orders=False,
    )
    sandbox_client = SandboxExecutionClient(
        loop=node.get_event_loop(),
        portfolio=node.portfolio,
        msgbus=node.kernel.msgbus,
        cache=node.kernel.cache,
        clock=node.kernel.clock,
        config=sandbox_config,
    )
    sandbox_client.exchange.add_instrument(apple_stock)
    sys.stdout.write(f"[SANDBOX] Exchange configured with {INSTRUMENT_ID}\n")
    sys.stdout.flush()
    node.kernel.exec_engine.register_client(sandbox_client)
    node.kernel.exec_engine.register_venue_routing(sandbox_client, VENUE)
    sys.stdout.write(f"[SANDBOX] Execution client registered for {VENUE}\n")
    sys.stdout.flush()

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy_config = AlpacaPaperStrategyConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        trade_quantity=Decimal("50"),
    )
    strategy = AlpacaPaperStrategy(config=strategy_config)
    strategy.alpaca = alpaca_trading
    node.trader.add_strategy(strategy)
    sys.stdout.write(f"[STRATEGY] AlpacaPaperStrategy registered for {INSTRUMENT_ID}\n")
    sys.stdout.flush()

    # ── Build and start the node ─────────────────────────────────────────
    sys.stdout.write("[NODE] Building TradingNode…\n")
    sys.stdout.flush()
    node.build()
    sys.stdout.write("[NODE] Starting TradingNode…\n")
    sys.stdout.flush()
    node_task = asyncio.create_task(node.run_async())
    sys.stdout.write("[NODE] TradingNode is running (engine queue tasks active)\n")
    sys.stdout.flush()

    # ── Heartbeat ────────────────────────────────────────────────────────
    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(30)
            sys.stdout.write("[HEARTBEAT] Script is running...\n")
            sys.stdout.flush()

    heartbeat_task = asyncio.create_task(_heartbeat())

    # ── Alpaca data stream ───────────────────────────────────────────────
    streamer = AlpacaDataStreamer(
        strategy=strategy,
        bar_type=BAR_TYPE,
        symbols=[SYMBOL],
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
        feed=DataFeed.IEX,
    )
    stream_task = asyncio.create_task(streamer.run())

    # ── Await shutdown signal ────────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        sys.stdout.write("\n[SIGNAL] Shutdown requested…\n")
        sys.stdout.flush()
        shutdown_event.set()

    loop = node.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    except (NotImplementedError, RuntimeError):
        pass

    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        pass

    # ── Graceful shutdown ────────────────────────────────────────────────
    sys.stdout.write("[SHUTDOWN] Cancelling heartbeat…\n")
    sys.stdout.flush()
    heartbeat_task.cancel()

    sys.stdout.write("[SHUTDOWN] Closing Alpaca WebSocket…\n")
    sys.stdout.flush()
    if not stream_task.done():
        stream_task.cancel()
        try:
            await stream_task
        except (asyncio.CancelledError, Exception):
            pass

    sys.stdout.write("[SHUTDOWN] Stopping TradingNode…\n")
    sys.stdout.flush()
    node.stop()
    await node_task

    sys.stdout.write("[SHUTDOWN] Disposing TradingNode…\n")
    sys.stdout.flush()
    node.dispose()

    sys.stdout.write("[SHUTDOWN] Complete.\n")
    sys.stdout.flush()


# ==============================================================================
# Entry Point
# ==============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NautilusTrader + Alpaca paper trading")
    parser.add_argument("symbol", nargs="?", default=None, help="Ticker symbol (e.g. AAPL, MSFT)")
    args = parser.parse_args()
    if args.symbol:
        SYMBOL = args.symbol.upper()

    asyncio.run(run_live_node())
