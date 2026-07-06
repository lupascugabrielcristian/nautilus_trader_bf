import numpy as np
from decimal import Decimal
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce

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

class LiveRandomStrategy(Strategy):
    def __init__(self, config: LiveRandomConfig) -> None:
        super().__init__(config)
        # Initialize the random number generator for the live stream
        self.rng = np.random.default_rng(self.config.seed)
        self.in_position = False

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
        
        # Roll the dice: generate a uniform random number between 0.0 and 1.0
        if self.rng.random() < self.config.signal_probability:
            print("[PAPER_TRADING - STRATEGY] passed the random number")
            
            # Alternating entry/exit logic
            if not self.in_position:
                self.execute_order(OrderSide.BUY)
                self.in_position = True
            else:
                self.execute_order(OrderSide.SELL)
                self.in_position = False

    def execute_order(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
        self.log.info(f"Live Random Signal Triggered: {side.name}")
