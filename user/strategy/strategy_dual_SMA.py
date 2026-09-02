from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderFilled, OrderRejected
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import DirectionalMovement
from nautilus_trader.indicators import ExponentialMovingAverage

from tr_utils import log_message
from tr_utils import tr_notification


class DualSMAConfig(StrategyConfig):
    instrument_id: str
    trade_size: Decimal
    fast_period: int = 10
    slow_period: int = 50
    bar_suffix: str = "1-MINUTE-LAST-EXTERNAL"
    cooldown_bars: int = 5
    atr_period: int = 14
    dm_period: int = 14
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 3.0
    telegram_active: bool = False
    global_config: dict = {}


class DualSMAStrategy(Strategy):
    def __init__(self, config: DualSMAConfig) -> None:
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(self.config.fast_period)
        self.slow_ema = ExponentialMovingAverage(self.config.slow_period)
        self.atr = AverageTrueRange(self.config.atr_period)
        self.dm = DirectionalMovement(self.config.dm_period)
        self.in_position = False
        self.order_in_flight = False
        self.bars_since_last_trade = 0

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        bar_type = BarType.from_str(f"{instrument.id}-{self.config.bar_suffix}")
        self.subscribe_bars(bar_type)
        log_message(
            f"Dual SMA strategy started: fast={self.config.fast_period} "
            f"slow={self.config.slow_period} atr={self.config.atr_period} "
            f"dm={self.config.dm_period}"
        )

    def on_bar(self, bar: Bar) -> None:
        log_message('on bar')

        if self.order_in_flight:
            log_message('order in flight - cancelling')
            return

        self.bars_since_last_trade += 1
        self.fast_ema.handle_bar(bar)
        self.slow_ema.handle_bar(bar)
        self.atr.handle_bar(bar)
        self.dm.handle_bar(bar)

        if not all([
            self.fast_ema.initialized,
            self.slow_ema.initialized,
            self.atr.initialized,
            self.dm.initialized,
        ]):
            return

        fast_val = self.fast_ema.value
        slow_val = self.slow_ema.value
        atr_val = self.atr.value
        dm_pos = self.dm.pos
        dm_neg = self.dm.neg

        is_uptrend = dm_pos > dm_neg
        is_downtrend = dm_neg > dm_pos

        current_side = self.portfolio.net_position(self.config.instrument_id)

        if current_side == 0:
            if fast_val > slow_val and is_uptrend:
                if self.bars_since_last_trade >= self.config.cooldown_bars:
                    log_message(
                        f"BUY signal: fast={fast_val:.2f} > slow={slow_val:.2f} "
                        f"dm_pos={dm_pos:.2f} > dm_neg={dm_neg:.2f}"
                    )
                    self._enter_long(bar, atr_val)
            elif fast_val < slow_val and is_downtrend:
                if self.bars_since_last_trade >= self.config.cooldown_bars:
                    log_message(
                        f"SELL signal: fast={fast_val:.2f} < slow={slow_val:.2f} "
                        f"dm_neg={dm_neg:.2f} > dm_pos={dm_pos:.2f}"
                    )
                    self._enter_short(bar, atr_val)
        elif current_side > 0:
            if fast_val < slow_val and is_downtrend:
                if self.bars_since_last_trade >= self.config.cooldown_bars:
                    log_message(f"CLOSE LONG + SELL SHORT: fast < slow, downtrend")
                    self.close_all_positions(self.config.instrument_id)
                    self.cancel_all_orders(self.config.instrument_id)
                    self._enter_short(bar, atr_val)
        elif current_side < 0:
            if fast_val > slow_val and is_uptrend:
                if self.bars_since_last_trade >= self.config.cooldown_bars:
                    log_message(f"CLOSE SHORT + BUY LONG: fast > slow, uptrend")
                    self.close_all_positions(self.config.instrument_id)
                    self.cancel_all_orders(self.config.instrument_id)
                    self._enter_long(bar, atr_val)

    def _enter_long(self, bar: Bar, atr_val: float) -> None:
        log_message('trying to enter long')
        instrument = self.cache.instrument(self.config.instrument_id)
        quantity = instrument.make_qty(self.config.trade_size)
        close = bar.close.as_double()
        sl_distance = self.config.atr_sl_multiplier * atr_val
        tp_distance = self.config.atr_tp_multiplier * atr_val

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            sl_trigger_price=instrument.make_price(close - sl_distance),
            tp_price=instrument.make_price(close + tp_distance),
        )
        self.submit_order_list(order_list)
        self.order_in_flight = True
        log_message(
            f"LONG entry={close:.2f} SL={close - sl_distance:.2f} "
            f"TP={close + tp_distance:.2f}"
        )
        self._sendTelegramNotification(
            f"LONG entry={close:.2f} SL={close - sl_distance:.2f} "
            f"TP={close + tp_distance:.2f}"
        )

    def _enter_short(self, bar: Bar, atr_val: float) -> None:
        log_message('trying to enter short')
        instrument = self.cache.instrument(self.config.instrument_id)
        quantity = instrument.make_qty(self.config.trade_size)
        close = bar.close.as_double()
        sl_distance = self.config.atr_sl_multiplier * atr_val
        tp_distance = self.config.atr_tp_multiplier * atr_val

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            sl_trigger_price=instrument.make_price(close + sl_distance),
            tp_price=instrument.make_price(close - tp_distance),
        )
        self.submit_order_list(order_list)
        self.order_in_flight = True
        log_message(
            f"SHORT entry={close:.2f} SL={close + sl_distance:.2f} "
            f"TP={close - tp_distance:.2f}"
        )
        self._sendTelegramNotification(
            f"SHORT entry={close:.2f} SL={close + sl_distance:.2f} "
            f"TP={close - tp_distance:.2f}"
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        order = self.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self.order_in_flight = False
            self.bars_since_last_trade = 0
            log_message(f"Order filled: side={order.side.name}")

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.order_in_flight = False
        log_message(f"Order rejected: {event.client_order_id} reason={event.reason}")

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self.order_in_flight = False
        log_message(f"Order canceled: {event.client_order_id}")

    def _sendTelegramNotification(self, message: str) -> None:
        if not self.config.telegram_active:
            return
        tr_notification(message)
