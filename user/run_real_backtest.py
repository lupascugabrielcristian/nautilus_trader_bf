from decimal import Decimal

import pandas as pd
from pathlib import Path

from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.loaders import CSVTickDataLoader
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


class EMACrossConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 10
    slow_ema_period: int = 20


class EMACross(Strategy):
    def __init__(self, config: EMACrossConfig):
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar):
        if not self.indicators_initialized():
            return

        if self.fast_ema.value >= self.slow_ema.value:
            if self.portfolio.is_flat(self.config.instrument_id):
                self.buy()
            elif self.portfolio.is_net_short(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
                self.buy()
        elif self.fast_ema.value < self.slow_ema.value:
            if self.portfolio.is_flat(self.config.instrument_id):
                self.sell()
            elif self.portfolio.is_net_long(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
                self.sell()

    def buy(self):
        instrument = self.cache.instrument(self.config.instrument_id)
        order = self.order_factory.market(
            self.config.instrument_id,
            OrderSide.BUY,
            instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)

    def sell(self):
        instrument = self.cache.instrument(self.config.instrument_id)
        order = self.order_factory.market(
            self.config.instrument_id,
            OrderSide.SELL,
            instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)

    def on_stop(self):
        self.close_all_positions(self.config.instrument_id)


SIM = Venue("SIM")
AUDUSD = TestInstrumentProvider.default_fx_ccy("AUD/USD", SIM)
bar_type = BarType.from_str("AUD/USD.SIM-1-MINUTE-MID-INTERNAL")

data_path = Path(__file__).resolve().parents[1] / "tests" / "test_data" / "truefx" / "audusd-ticks.csv"
quotes_df = CSVTickDataLoader.load(file_path=data_path)
quotes_df = quotes_df.sort_index()
wrangler = QuoteTickDataWrangler(AUDUSD)
ticks = wrangler.process(quotes_df)

engine = BacktestEngine(
    config=BacktestEngineConfig(
        logging=LoggingConfig(log_level="ERROR"),
    ),
)

engine.add_venue(
    venue=SIM,
    oms_type=OmsType.NETTING,
    account_type=AccountType.MARGIN,
    starting_balances=[Money(1_000_000, USD)],
    base_currency=USD,
    default_leverage=Decimal(1),
)

engine.add_instrument(AUDUSD)
engine.add_data(ticks)

strategy = EMACross(
    EMACrossConfig(
        instrument_id=AUDUSD.id,
        bar_type=bar_type,
        trade_size=Decimal(100000),
    ),
)
engine.add_strategy(strategy)

engine.run()

account_report = engine.trader.generate_account_report(SIM)
positions_report = engine.trader.generate_positions_report()
order_fills_report = engine.trader.generate_order_fills_report()

with pd.option_context(
    "display.max_rows",
    100,
    "display.max_columns",
    None,
    "display.width",
    300,
):
    print("\n=== ACCOUNT REPORT ===")
    print(account_report)
    print("\n=== POSITIONS REPORT ===")
    print(positions_report)
    print("\n=== ORDER FILLS REPORT ===")
    print(order_fills_report)

if hasattr(account_report, "to_csv"):
    account_report.to_csv("user/account_report_real.csv", index=False)
if hasattr(positions_report, "to_csv"):
    positions_report.to_csv("user/positions_report_real.csv", index=False)
if hasattr(order_fills_report, "to_csv"):
    order_fills_report.to_csv("user/order_fills_report_real.csv", index=False)

create_tearsheet(
    engine=engine,
    output_path="user/backtest_results_real.html",
)

engine.dispose()
