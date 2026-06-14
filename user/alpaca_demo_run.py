import asyncio
import random
from decimal import Decimal

from nautilus_trader.config import LiveTradingNodeConfig, LoggingConfig, StrategyConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.live.node import LiveTradingNode
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, BarType, OmsType, OrderSide, PriceType
from nautilus_trader.model.identifiers import AccountId, InstrumentId, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import CurrencyMoney, Price, Quantity
from nautilus_trader.trading.impl.execution import OrderEmulator
from nautilus_trader.trading.strategy import Strategy


# ==============================================================================
# 1. THE TRADING STRATEGY
# ==============================================================================
class LiveDemoStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_quantity: Decimal = Decimal("10")

class LiveDemoStrategy(Strategy):
    """
    A basic live demo strategy that listens to incoming candles and executes
    simulated orders using the local OrderEmulator.
    """
    def __init__(self, config: LiveDemoStrategyConfig) -> None:
        super().__init__(config)
        self.config = config
        self.last_close = None

    def on_start(self) -> None:
        self.log.info("Live Demo Strategy Started.")
        # Instruct the engine that we expect to process bars matching this spec
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Fires automatically whenever a new bar is injected into the engine.
        """
        self.log.info(f"[STRATEGY] Received Live Candle -> Close: {bar.close}")
        
        # Simple Logic: If the price dropped compared to the last candle, simulated BUY
        if self.last_close and bar.close < self.last_close:
            if self.portfolio.is_flat(self.config.instrument_id):
                self.log.info(f"Price dropped from {self.last_close} to {bar.close}. Submitting BUY Order...")
                
                # Submit a market buy order to the emulator
                self.submit_order(
                    self.order_factory.market(
                        instrument_id=self.config.instrument_id,
                        order_side=OrderSide.BUY,
                        quantity=Quantity(self.config.trade_quantity),
                    )
                )
        
        # If we hold a position and the price recovered, simulated SELL to flatten
        elif self.last_close and bar.close > self.last_close:
            if self.portfolio.is_net_long(self.config.instrument_id):
                self.log.info(f"Price recovered from {self.last_close} to {bar.close}. Liquidating Position...")
                self.close_all_positions(self.config.instrument_id)

        self.last_close = bar.close

    def on_stop(self) -> None:
        self.log.info("Live Demo Strategy Stopped.")


# ==============================================================================
# 2. MOCK MARKET DATA CLIENT STUB (Simulating a Live Alpaca Feed)
# ==============================================================================
async def simulate_external_live_feed(node: LiveTradingNode, instrument: Equity, bar_type: BarType):
    """
    Simulates a live WebSocket background stream pulling candle ticks 
    from an external provider and pushing them into Nautilus's Live Node.
    """
    await asyncio.sleep(2) # Allow node boot routines to clear
    
    base_price = Decimal("180.00")
    self_generated_id = 1
    
    print("\n>>> Starting external live data stream simulation...")
    
    # Generate 5 sample live candle cycles
    for _ in range(5):
        if not node.is_running:
            break
            
        # Add random equity price fluctuations
        price_change = Decimal(str(round(random.uniform(-1.5, 1.5), 2)))
        close_price = base_price + price_change
        
        # Construct a native Nautilus Bar object from the incoming data points
        now_ns = dt_to_unix_nanos()
        bar = Bar(
            bar_type=bar_type,
            instrument_id=instrument.id,
            ts_event=now_ns,                            # When the exchange formed it
            ts_init=now_ns,                             # When your script received it
            open_prop=Price(base_price, precision=2),
            high_prop=Price(max(base_price, close_price) + Decimal("0.5"), precision=2),
            low_prop=Price(min(base_price, close_price) - Decimal("0.5"), precision=2),
            close_prop=Price(close_price, precision=2),
            volume_prop=Quantity(random.randint(1000, 5000)),
        )
        
        # Inject the parsed object directly onto the central Live Node event bus
        node.data_engine.handle_bar(bar)
        
        base_price = close_price
        self_generated_id += 1
        await asyncio.sleep(3) # Wait 3 seconds between live candle arrivals


# ==============================================================================
# 3. CONSTRUCTING THE PLATFORM NODE ARCHITECTURE
# ==============================================================================
async def run_live_node():
    # Define Core Structural Identifiers
    VENUE = Venue("XNAS") # Nasdaq boundary
    INSTRUMENT_ID = InstrumentId.from_str("AAPL.XNAS")
    ACCOUNT_ID = AccountId("SIM-PORTFOLIO-001")
    BAR_TYPE = BarType.from_str("AAPL.XNAS-1-MINUTE-LAST-INTERNAL")

    # Define the Ticker Object Parameters
    apple_stock = Equity(
        instrument_id=INSTRUMENT_ID,
        raw_symbol="AAPL",
        venue=VENUE,
        currency=USD,
        price_precision=2,
        size_precision=0,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("1"),
    )

    # A. Configure the Primary Node Runtime Engine
    node_config = LiveTradingNodeConfig(
        trader_id="demo-trader-01",
        logging=LoggingConfig(log_level="INFO", log_colors=True)
    )
    node = LiveTradingNode(config=node_config)

    # Register the asset context to the internal node memory cache
    node.cache.add_instrument(apple_stock)

    # B. Initialize the Virtual Execution Venue (Order Emulator)
    order_emulator = OrderEmulator(
        venue=VENUE,
        account_id=ACCOUNT_ID,
        account_type=AccountType.MARGIN,
        oms_type=OmsType.NETTING,
        initial_cash=[CurrencyMoney(100000, USD)], # Starting sandbox balance
    )
    
    # Wire the Emulator Backend into the Node's routing engine
    node.execution_engine.register_provider(VENUE, order_emulator)

    # C. Instantiate the Logic Layer Configuration
    strategy_config = LiveDemoStrategyConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        trade_quantity=Decimal("50")
    )
    strategy = LiveDemoStrategy(config=strategy_config)
    node.add_strategy(strategy)

    # D. Spin up the Node Core System loops
    node.start()

    # E. Launch the concurrent external input stream handler
    try:
        await simulate_external_live_feed(node, apple_stock, BAR_TYPE)
    finally:
        # Gracefully spin down and free memory slots upon exit
        print("\n>>> Shutting down Live Node...")
        node.stop()
        node.dispose()

if __name__ == "__main__":
    # Execute inside an asynchronous execution frame
    asyncio.run(run_live_node())