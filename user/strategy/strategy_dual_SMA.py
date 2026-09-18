from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model import InstrumentId
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderDenied, OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.model.orders import StopMarketOrder
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
        self.order_in_flight = False
        self.bars_since_last_trade = 0
        self._entry_order_id: ClientOrderId | None = None
        self._exit_plan: dict | None = None
        self._sl_order_id: ClientOrderId | None = None
        self._tp_order_id: ClientOrderId | None = None

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        bar_type = BarType.from_str(f"{instrument.id}-{self.config.bar_suffix}")
        self.subscribe_bars(bar_type)
        self._log_cache_state()
        self._log_open_positions()
        self._adopt_open_exit_orders()
        log_message(
            f"Dual SMA strategy started: fast={self.config.fast_period} "
            f"slow={self.config.slow_period} atr={self.config.atr_period} "
            f"dm={self.config.dm_period}"
        )

    def _log_cache_state(self) -> None:
        instruments = self.cache.instruments()
        for inst in instruments:
            log_message(f"[CACHE] instrument available: {inst.id}")
        orders = self.cache.orders()
        open_orders = self.cache.orders_open()
        log_message(
            f"[CACHE] instruments={len(instruments)} "
            f"orders={len(orders)} open_orders={len(open_orders)} "
            f"accounts={len(self.cache.accounts())}"
        )

    def _log_open_positions(self) -> None:
        positions = self.cache.positions_open()
        log_message(f"[POSITIONS] open positions count: {len(positions)}")
        for position in positions:
            log_message(
                f"[POSITION] id={position.id} instrument={position.instrument_id} "
                f"side={'LONG' if position.is_long else 'SHORT'} "
                f"qty={position.quantity} avg_open={position.avg_px_open} "
                f"realized_pnl={position.realized_pnl}"
            )
        try:
            exposures = self.portfolio.net_exposures(
                venue=self.config.instrument_id.venue,
            )
        except TypeError:
            log_message("[POSITION] net exposures unavailable during startup")
            return
        if not exposures:
            log_message("[POSITION] no net exposure to report")
            return
        for instrument_id, quantity in exposures.items():
            log_message(f"[POSITION] net exposure {instrument_id}: {quantity}")

    def on_stop(self) -> None:
        instrument = self.config.instrument_id
        open_orders = self.cache.orders_open(instrument_id=instrument)
        open_positions = self.cache.positions_open(instrument_id=instrument)
        log_message(
            f"Stopping strategy - cancelling open orders "
            f"count={len(open_orders)} and closing open positions count={len(open_positions)}"
        )
        self.cancel_all_orders(instrument)
        self.close_all_positions(instrument)
        self.order_in_flight = False

    def on_bar(self, bar: Bar) -> None:
        log_message(f"bar data received. OPEN: {float(bar.open):.2f}")

        if self.order_in_flight:
            log_message(
                f"entry order in flight - skipping bar "
                f"(entry_order_id={self._entry_order_id})"
            )
            return

        self.bars_since_last_trade += 1
        self.fast_ema.handle_bar(bar)
        self.slow_ema.handle_bar(bar)
        self.atr.handle_bar(bar)
        self.dm.handle_bar(bar)

        if not self.fast_ema.initialized:
            log_message(
                f"warming up - not enough history bars for fast EMA "
                f"({self.fast_ema.count}/{self.config.fast_period})"
            )
        if not self.slow_ema.initialized:
            log_message(
                f"warming up - not enough history bars for slow EMA "
                f"({self.slow_ema.count}/{self.config.slow_period})"
            )
        if not all([
            self.fast_ema.initialized,
            self.slow_ema.initialized,
            self.atr.initialized,
            self.dm.initialized,
        ]):
            log_message('initial conditions not satified - stop processing bar')
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
        log_message('Done precessing candle')

    def _enter_long(self, bar: Bar, atr_val: float) -> None:
        log_message('trying to enter long')
        instrument = self.cache.instrument(self.config.instrument_id)
        quantity = instrument.make_qty(self.config.trade_size)
        close = bar.close.as_double()
        sl_distance = self.config.atr_sl_multiplier * atr_val
        tp_distance = self.config.atr_tp_multiplier * atr_val

        entry = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=quantity,
        )
        self._entry_order_id = entry.client_order_id
        self._exit_plan = {
            "exit_side": OrderSide.SELL,
            "sl_price": instrument.make_price(close - sl_distance),
            "tp_price": instrument.make_price(close + tp_distance),
        }
        self.submit_order(entry)
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

        entry = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=quantity,
        )
        self._entry_order_id = entry.client_order_id
        self._exit_plan = {
            "exit_side": OrderSide.BUY,
            "sl_price": instrument.make_price(close + sl_distance),
            "tp_price": instrument.make_price(close - tp_distance),
        }
        self.submit_order(entry)
        self.order_in_flight = True
        log_message(
            f"SHORT entry={close:.2f} SL={close + sl_distance:.2f} "
            f"TP={close - tp_distance:.2f}"
        )
        self._sendTelegramNotification(
            f"SHORT entry={close:.2f} SL={close + sl_distance:.2f} "
            f"TP={close - tp_distance:.2f}"
        )

    def _submit_exits(self, entry_order) -> None:
        plan = self._exit_plan
        if plan is None:
            return
        quantity = entry_order.filled_qty
        sl_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=plan["exit_side"],
            quantity=quantity,
            trigger_price=plan["sl_price"],
            reduce_only=True,
        )
        tp_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=plan["exit_side"],
            quantity=quantity,
            price=plan["tp_price"],
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
        )
        self._sl_order_id = sl_order.client_order_id
        self._tp_order_id = tp_order.client_order_id
        self.submit_order(sl_order)
        self.submit_order(tp_order)
        self._exit_plan = None
        log_message(
            f"exit orders submitted: side={plan['exit_side'].name} qty={quantity} "
            f"SL={plan['sl_price']} TP={plan['tp_price']}"
        )

    def _cancel_sibling_exit(self, filled_order_id: ClientOrderId) -> None:
        sibling_id = None
        if filled_order_id == self._sl_order_id:
            self._sl_order_id = None
            sibling_id = self._tp_order_id
        elif filled_order_id == self._tp_order_id:
            self._tp_order_id = None
            sibling_id = self._sl_order_id
        if sibling_id is None:
            return
        sibling = self.cache.order(sibling_id)
        if sibling is not None and sibling.is_open:
            self.cancel_order(sibling)

    def _reset_entry_state(self) -> None:
        self.order_in_flight = False
        self._entry_order_id = None
        self._exit_plan = None

    def _adopt_open_exit_orders(self) -> None:
        working_orders = self.cache.orders_open(
            instrument_id=self.config.instrument_id,
            strategy_id=self.id,
        )
        for order in working_orders:
            if isinstance(order, StopMarketOrder):
                self._sl_order_id = order.client_order_id
            elif isinstance(order, LimitOrder):
                self._tp_order_id = order.client_order_id
        if self._sl_order_id is not None or self._tp_order_id is not None:
            log_message(
                f"adopted open exit orders from previous session: "
                f"SL={self._sl_order_id} TP={self._tp_order_id}"
            )

    def on_order_filled(self, event: OrderFilled) -> None:
        order = self.cache.order(event.client_order_id)
        if order is None:
            return
        if event.client_order_id == self._entry_order_id:
            if order.is_closed:
                self.order_in_flight = False
                self.bars_since_last_trade = 0
                log_message(f"Entry order filled: side={order.side.name}")
                self._submit_exits(order)
            return
        if not order.is_closed:
            return
        if event.client_order_id == self._sl_order_id:
            self.bars_since_last_trade = 0
            log_message("SL exit filled - cancelling TP sibling")
            self._cancel_sibling_exit(event.client_order_id)
        elif event.client_order_id == self._tp_order_id:
            self.bars_since_last_trade = 0
            log_message("TP exit filled - cancelling SL sibling")
            self._cancel_sibling_exit(event.client_order_id)

    def on_order_denied(self, event: OrderDenied) -> None:
        log_message(f"Order denied: {event.client_order_id} reason={event.reason}")
        self._handle_order_failed(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        log_message(f"Order rejected: {event.client_order_id} reason={event.reason}")
        self._handle_order_failed(event.client_order_id)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        if event.client_order_id == self._entry_order_id:
            log_message(f"Entry order canceled: {event.client_order_id}")
            self._reset_entry_state()
        elif event.client_order_id == self._sl_order_id:
            self._sl_order_id = None
            log_message(f"SL exit order canceled: {event.client_order_id}")
        elif event.client_order_id == self._tp_order_id:
            self._tp_order_id = None
            log_message(f"TP exit order canceled: {event.client_order_id}")

    def _handle_order_failed(self, order_id: ClientOrderId) -> None:
        if order_id == self._entry_order_id:
            self._reset_entry_state()
        elif order_id == self._sl_order_id:
            self._sl_order_id = None
            log_message("WARNING: SL exit failed - TP remains working, position may be unprotected")
            self._sendTelegramNotification(
                "WARNING: SL exit failed - TP remains working, position may be unprotected"
            )
        elif order_id == self._tp_order_id:
            self._tp_order_id = None
            log_message("WARNING: TP exit failed - SL remains working")

    def _sendTelegramNotification(self, message: str) -> None:
        if not self.config.telegram_active:
            return
        tr_notification(message)
