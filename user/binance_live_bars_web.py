import argparse
import json
import os
import re
from collections import deque
from datetime import UTC
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from queue import Queue
import threading
from typing import Any

import requests
from requests import HTTPError

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceInstrumentProviderConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.strategy import Strategy


class BarsState:
    def __init__(self, max_bars: int = 50) -> None:
        self._bars: deque[dict[str, Any]] = deque(maxlen=max_bars)
        self._listeners: set[Queue[str | None]] = set()
        self._lock = threading.Lock()

    def set_initial(self, bars: list[dict[str, Any]]) -> None:
        with self._lock:
            self._bars.clear()
            self._bars.extend(bars[-self._bars.maxlen :])

    def add_bar(self, bar: dict[str, Any]) -> None:
        payload = json.dumps(bar)
        with self._lock:
            self._bars.append(bar)
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(payload)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._bars)

    def register_listener(self) -> Queue[str | None]:
        queue: Queue[str | None] = Queue()
        with self._lock:
            self._listeners.add(queue)
        return queue

    def unregister_listener(self, queue: Queue[str | None]) -> None:
        with self._lock:
            self._listeners.discard(queue)

    def shutdown_listeners(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(None)


class VisualBarsStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class VisualBarsStrategy(Strategy):
    def __init__(self, config: VisualBarsStrategyConfig, state: BarsState) -> None:
        super().__init__(config)
        self._state = state

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._state.add_bar(
            {
                "t": datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=UTC).isoformat(),
                "o": bar.open.as_double(),
                "h": bar.high.as_double(),
                "l": bar.low.as_double(),
                "c": bar.close.as_double(),
            },
        )


def parse_runtime_env() -> tuple[BinanceEnvironment, BinanceAccountType, str, str]:
    env_name = os.getenv("BINANCE_ENV", "TESTNET").upper()
    environment = {
        "LIVE": BinanceEnvironment.LIVE,
        "TESTNET": BinanceEnvironment.TESTNET,
        "DEMO": BinanceEnvironment.DEMO,
    }.get(env_name)
    if environment is None:
        raise ValueError("BINANCE_ENV must be one of LIVE, TESTNET, DEMO")

    account_type_name = os.getenv("BINANCE_ACCOUNT_TYPE", "USDT_FUTURES").upper()
    account_type = {
        "SPOT": BinanceAccountType.SPOT,
        "MARGIN": BinanceAccountType.MARGIN,
        "ISOLATED_MARGIN": BinanceAccountType.ISOLATED_MARGIN,
        "USDT_FUTURES": BinanceAccountType.USDT_FUTURES,
        "COIN_FUTURES": BinanceAccountType.COIN_FUTURES,
    }.get(account_type_name)
    if account_type is None:
        raise ValueError(
            "BINANCE_ACCOUNT_TYPE must be one of SPOT, MARGIN, ISOLATED_MARGIN, USDT_FUTURES, COIN_FUTURES",
        )

    default_symbol = "BTCUSDT" if account_type == BinanceAccountType.SPOT else "BTCUSDT-PERP"
    symbol = os.getenv("BINANCE_INSTRUMENT", default_symbol)
    trader = os.getenv("BINANCE_TRADER_ID", "TESTER-001")
    return environment, account_type, symbol, trader


def interval_to_bar_type_suffix(interval: str) -> str:
    match = re.fullmatch(r"(\d+)([mhdwM])", interval)
    if match is None:
        raise ValueError("--interval must look like 1m, 5m, 15m, 1h, 1d, 1w, or 1M")
    value, unit = match.groups()
    unit_map = {
        "m": "MINUTE",
        "h": "HOUR",
        "d": "DAY",
        "w": "WEEK",
        "M": "MONTH",
    }
    return f"{value}-{unit_map[unit]}"


def rest_symbol_for_account(symbol: str) -> str:
    return symbol.replace("-PERP", "")


def rest_klines_url(account_type: BinanceAccountType) -> str:
    if account_type in {BinanceAccountType.USDT_FUTURES, BinanceAccountType.COIN_FUTURES}:
        return "https://fapi.binance.com/fapi/v1/klines"
    return "https://api.binance.com/api/v3/klines"


def parse_kline(entry: list[Any]) -> dict[str, Any]:
    return {
        "t": datetime.fromtimestamp(int(entry[6]) / 1000, tz=UTC).isoformat(),
        "o": float(entry[1]),
        "h": float(entry[2]),
        "l": float(entry[3]),
        "c": float(entry[4]),
    }


def fetch_last_50_bars(symbol: str, interval: str, account_type: BinanceAccountType) -> list[dict[str, Any]]:
    response = requests.get(
        rest_klines_url(account_type),
        params={"symbol": rest_symbol_for_account(symbol).upper(), "interval": interval, "limit": "50"},
        timeout=20,
    )
    try:
        response.raise_for_status()
    except HTTPError as exc:
        detail = ""
        try:
            payload = response.json()
            detail = f" ({payload.get('msg', 'Unknown Binance error')})"
        except ValueError:
            detail = ""
        raise ValueError(
            f"Failed to fetch klines for symbol={symbol.upper()} interval={interval}{detail}.",
        ) from exc
    data = response.json()
    return [parse_kline(item) for item in data]


def build_node(
    environment: BinanceEnvironment,
    account_type: BinanceAccountType,
    symbol: str,
    trader: str,
    interval: str,
    state: BarsState,
) -> TradingNode:
    instrument_id = InstrumentId.from_str(f"{symbol}.{BINANCE}")
    bar_suffix = interval_to_bar_type_suffix(interval)
    bar_type = BarType.from_str(f"{instrument_id}-{bar_suffix}-LAST-EXTERNAL")

    node_config = TradingNodeConfig(
        trader_id=TraderId(trader),
        logging=LoggingConfig(
            log_level=os.getenv("BINANCE_LOG_LEVEL", "INFO"),
            use_pyo3=True,
        ),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=account_type,
                environment=environment,
                instrument_provider=BinanceInstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                ),
            ),
        },
        timeout_connection=20.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(
        VisualBarsStrategy(
            config=VisualBarsStrategyConfig(
                instrument_id=instrument_id,
                bar_type=bar_type,
            ),
            state=state,
        ),
    )
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.build()
    return node


def run_node(node: TradingNode) -> None:
    try:
        node.run()
    finally:
        node.dispose()


def html_page() -> str:
    return """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Binance Last 50 Bars</title>
    <script src=\"https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js\"></script>
    <style>
      body { font-family: sans-serif; margin: 0; padding: 16px; }
      #chart { width: 100%; height: 480px; }
    </style>
  </head>
  <body>
    <h3>Binance Last 50 Bars</h3>
    <div id=\"chart\"></div>
    <script>
      const chart = LightweightCharts.createChart(document.getElementById('chart'), {
        layout: { background: { color: '#ffffff' }, textColor: '#000000' },
        width: window.innerWidth - 32,
        height: 480,
      });
      const series = chart.addCandlestickSeries();
      const toPoint = (bar) => ({
        time: Math.floor(new Date(bar.t).getTime() / 1000),
        open: bar.o,
        high: bar.h,
        low: bar.l,
        close: bar.c,
      });
      async function loadInitial() {
        const response = await fetch('/bars');
        const bars = await response.json();
        series.setData(bars.map(toPoint));
      }
      loadInitial();
      const events = new EventSource('/events');
      events.onmessage = (event) => {
        const bar = JSON.parse(event.data);
        series.update(toPoint(bar));
      };
      window.addEventListener('resize', () => {
        chart.applyOptions({ width: window.innerWidth - 32 });
      });
    </script>
  </body>
</html>
"""


class AppHandler(BaseHTTPRequestHandler):
    state: BarsState

    def do_GET(self) -> None:
        if self.path == "/":
            body = html_page().encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/bars":
            body = json.dumps(self.state.snapshot()).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            queue = self.state.register_listener()
            try:
                for bar in self.state.snapshot():
                    self.wfile.write(f"data: {json.dumps(bar)}\n\n".encode())
                self.wfile.flush()
                while True:
                    item = queue.get()
                    if item is None:
                        break
                    self.wfile.write(f"data: {item}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.state.unregister_listener(queue)
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def log_message(self, _: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    environment, account_type, symbol, trader = parse_runtime_env()
    state = BarsState(max_bars=50)
    try:
        state.set_initial(fetch_last_50_bars(symbol, args.interval, account_type))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    node = build_node(
        environment=environment,
        account_type=account_type,
        symbol=symbol,
        trader=trader,
        interval=args.interval,
        state=state,
    )

    AppHandler.state = state
    node_thread = threading.Thread(target=run_node, args=(node,), daemon=True)
    node_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown_listeners()
        node.dispose()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
