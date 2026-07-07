import os
import subprocess

import numpy as np
from decimal import Decimal
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderFilled, OrderRejected

#   Examples of valid values:
#      - 1-MINUTE-LAST-EXTERNAL (default)
#      - 5-MINUTE-LAST-EXTERNAL
#      - 15-MINUTE-LAST-EXTERNAL
#      - 1-HOUR-LAST-EXTERNAL
#      - 1-SECOND-LAST-EXTERNAL


class LiveRandomConfig(StrategyConfig):
    instrument_id: str
    trade_size: Decimal
    # Instead of a fixed bar count, pass the execution probability per bar
    signal_probability: float  # e.g., 0.108 for ~10.8% chance per bar
    bar_suffix: str = "1-MINUTE-LAST-EXTERNAL"  # e.g. 5-MINUTE-LAST-EXTERNAL, 1-SECOND-LAST-EXTERNAL
    seed: int = 42
    global_config: dict = {}

class LiveRandomStrategy(Strategy):
    def __init__(self, config: LiveRandomConfig) -> None:
        super().__init__(config)
        # Initialize the random number generator for the live stream
        self.rng = np.random.default_rng(self.config.seed)
        self.in_position = False
        self.order_in_flight = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        bar_type = BarType.from_str(f"{instrument.id}-{self.config.bar_suffix}")
        self.subscribe_bars(bar_type)
        self.log.info(f"Live random strategy started with per-bar probability: {self.config.signal_probability}")

    def on_bar(self, bar: Bar) -> None:
        """Processes each live bar as it arrives in real time."""

        print("[PAPER_TRADING - STRATEGY] on_bar")
        print(f"[PAPER_TRADING - STRATEGY] H{bar.high}")
        print(f"[PAPER_TRADING - STRATEGY]    |")
        print(f"[PAPER_TRADING - STRATEGY] O{bar.open}")
        print(f"[PAPER_TRADING - STRATEGY] C{bar.close}")
        print(f"[PAPER_TRADING - STRATEGY]    |")
        print(f"[PAPER_TRADING - STRATEGY] L{bar.low}")
        print(" ")

        if self.order_in_flight:
            print("[PAPER_TRADING - STRATEGY] order in flight, skipping bar")
            return

        # Roll the dice: generate a uniform random number between 0.0 and 1.0
        rn = self._get_random_number()
        print(f"{rn}")
        if rn < self.config.signal_probability:
            print("[PAPER_TRADING - STRATEGY] passed the random number")

            # Alternating entry/exit logic (state is updated on fill/reject events)
            if not self.in_position:
                self.execute_order(OrderSide.BUY)
            else:
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
        self.log.info(f"Live Random Signal Triggered: {side.name}")

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

    def _get_random_number(self) -> float:
        base_dir = self.config.global_config.get("base_working_dir", "/app/")
        script_path = os.path.join(base_dir, "trading_tools", "random_number.py")
        result = subprocess.check_output(["python", script_path])
        return float(result.strip())

    def _sendTelegramOrder(self, side: OrderSide, quantity: Decimal, instrument_id: str) -> None:
        msg = f"[PAPER_TRADING - STRATEGY] [ORDER] {side.name} {quantity} {instrument_id} (Market)"
        self._sendTelegramNotification(msg)

    def _sendTelegramNotification(self, message: str) -> None:
        base_dir = self.config.global_config.get("base_working_dir", "/app/")
        script_path = os.path.join(base_dir, "trading_tools", "telegram", "send_notification.py")
        subprocess.run(["python", script_path, message])
