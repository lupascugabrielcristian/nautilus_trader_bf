import os
import subprocess
import urllib.request

from decimal import Decimal
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderFilled, OrderRejected
from nautilus_trader.indicators import SimpleMovingAverage


class DualSMAConfig(StrategyConfig):
    instrument_id: str
    trade_size: Decimal
    fast_period: int  # e.g., 10
    slow_period: int  # e.g., 50
    bar_suffix: str = "1-MINUTE-LAST-EXTERNAL"
    global_config: dict = {}


class DualSMAStrategy(Strategy):
    def __init__(self, config: DualSMAConfig) -> None:
        super().__init__(config)
        self.fast_sma = SimpleMovingAverage(self.config.fast_period)
        self.slow_sma = SimpleMovingAverage(self.config.slow_period)
        self.in_position = False
        self.order_in_flight = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        bar_type = BarType.from_str(f"{instrument.id}-{self.config.bar_suffix}")
        self.subscribe_bars(bar_type)
        self.log.info(
            f"Dual SMA strategy started: fast={self.config.fast_period} "
            f"slow={self.config.slow_period}"
        )
        self._log_message(f"Dual SMA strategy started: fast={self.config.fast_period} "
            f"slow={self.config.slow_period}")

    def on_bar(self, bar: Bar) -> None:
        self._log_message("[PAPER_TRADING - STRATEGY] on_bar")
        self._log_message(f"[PAPER_TRADING - STRATEGY] H{bar.high}")
        self._log_message(f"[PAPER_TRADING - STRATEGY]    |")
        self._log_message(f"[PAPER_TRADING - STRATEGY] O{bar.open}")
        self._log_message(f"[PAPER_TRADING - STRATEGY] C{bar.close}")
        self._log_message(f"[PAPER_TRADING - STRATEGY]    |")
        self._log_message(f"[PAPER_TRADING - STRATEGY] L{bar.low}")
        self._log_message(" ")

        if self.order_in_flight:
            self._log_message("[PAPER_TRADING - STRATEGY] order in flight, skipping bar")
            return

        self.fast_sma.handle_bar(bar)
        self.slow_sma.handle_bar(bar)

        if not self.fast_sma.initialized or not self.slow_sma.initialized:
            self._log_message("[PAPER_TRADING - STRATEGY] warming up indicators...")
            return

        fast_val = self.fast_sma.value
        slow_val = self.slow_sma.value

        self._log_message(
            f"[PAPER_TRADING - STRATEGY] fast_SMA={fast_val:.6f} slow_SMA={slow_val:.6f}"
        )

        if not self.in_position and fast_val > slow_val:
            self._log_message("[PAPER_TRADING - STRATEGY] BUY signal: fast crossed above slow")
            self.execute_order(OrderSide.BUY)
        elif self.in_position and fast_val < slow_val:
            self._log_message("[PAPER_TRADING - STRATEGY] SELL signal: fast crossed below slow")
            self.execute_order(OrderSide.SELL)

    def execute_order(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        quantity = instrument.make_qty(self.config.trade_size)

        self._sendTelegramOrder(side, quantity, instrument.id)

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
        )
        self.submit_order(order)
        self.order_in_flight = True
        self.log.info(f"Dual SMA Signal Triggered: {side.name}")

    def on_order_filled(self, event: OrderFilled) -> None:
        order = self.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self.order_in_flight = False
            self.in_position = (order.side == OrderSide.BUY)
            self.log.info(
                f"Order filled & closed: side={order.side.name} "
                f"in_position={self.in_position}"
            )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.order_in_flight = False
        self.log.warning(
            f"Order rejected: {event.client_order_id} reason={event.reason} "
            f"(in_position unchanged={self.in_position})"
        )

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self.order_in_flight = False
        self.log.warning(
            f"Order canceled: {event.client_order_id} "
            f"(in_position unchanged={self.in_position})"
        )

    def _sendTelegramOrder(self, side: OrderSide, quantity: Decimal, instrument_id: str) -> None:
        msg = f"[PAPER_TRADING - STRATEGY] [ORDER] {side.name} {quantity} {instrument_id} (Market)"
        self._sendTelegramNotification(msg)

    @staticmethod
    def _load_service_ports():
        import json
        try:
            with open('/tmp/service_ports.json') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _sendTelegramNotification(self, message: str) -> None:
        TELEGRAM_PORT = os.environ.get('TELEGRAM_PORT', '')
        if not TELEGRAM_PORT:
            ports = self._load_service_ports()
            TELEGRAM_PORT = ports.get('TELEGRAM_PORT', '')
        if not TELEGRAM_PORT:
            return
        TELEGRAM_PORT = int(TELEGRAM_PORT)
        url = f"http://localhost:{TELEGRAM_PORT}/service/telegram"
        try:
            req = urllib.request.Request(url, data=message.encode(), method='POST')
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def _log_message(self, format, *args):
        logging_port = os.environ.get('LOGGING_PORT', '')
        if not logging_port:
            ports = self._load_service_ports()
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
