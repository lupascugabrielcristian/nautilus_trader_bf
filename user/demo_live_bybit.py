from decimal import Decimal
import os

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.adapters.bybit import BybitExecClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitLiveExecClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.examples.strategies.ema_cross import EMACross
from nautilus_trader.examples.strategies.ema_cross import EMACrossConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


ENV_MAP = {
    "DEMO": BybitEnvironment.DEMO,
    "TESTNET": BybitEnvironment.TESTNET,
    "MAINNET": BybitEnvironment.MAINNET,
}

CREDENTIAL_ENV_VARS = {
    BybitEnvironment.DEMO: ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"),
    BybitEnvironment.TESTNET: ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"),
    BybitEnvironment.MAINNET: ("BYBIT_API_KEY", "BYBIT_API_SECRET"),
}


def _validate_credentials(environment: BybitEnvironment) -> None:
    key_var, secret_var = CREDENTIAL_ENV_VARS[environment]
    key_is_set = bool(os.getenv(key_var))
    secret_is_set = bool(os.getenv(secret_var))
    print(f"Bybit startup validation: env={environment}")
    print(f"  {key_var}={'SET' if key_is_set else 'MISSING'}")
    print(f"  {secret_var}={'SET' if secret_is_set else 'MISSING'}")
    missing = [name for name, is_set in ((key_var, key_is_set), (secret_var, secret_is_set)) if not is_set]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    print("Credential environment variables found.")


def main() -> None:
    env_name = os.getenv("BYBIT_ENV", "DEMO").upper()
    environment = ENV_MAP.get(env_name)
    if environment is None:
        raise ValueError(f"BYBIT_ENV must be one of {list(ENV_MAP.keys())}")

    _validate_credentials(environment)

    product_type_name = os.getenv("BYBIT_PRODUCT_TYPE", "LINEAR").upper()
    product_type = getattr(BybitProductType, product_type_name, None)
    if product_type is None:
        raise ValueError(f"BYBIT_PRODUCT_TYPE must be one of LINEAR, SPOT, INVERSE, OPTION")

    base_symbol = os.getenv("BYBIT_SYMBOL", "ETHUSDT")
    symbol = f"{base_symbol}-{product_type.value.upper()}"
    trader_id = os.getenv("BYBIT_TRADER_ID", "TESTER-001")
    log_level = os.getenv("BYBIT_LOG_LEVEL", "INFO")
    trade_size = Decimal(os.getenv("BYBIT_TRADE_SIZE", "0.010"))

    instrument_id = InstrumentId.from_str(f"{symbol}.{BYBIT}")

    config_node = TradingNodeConfig(
        trader_id=TraderId(trader_id),
        logging=LoggingConfig(log_level=log_level, use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        data_clients={
            BYBIT: BybitDataClientConfig(
                environment=environment,
                instrument_provider=InstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                ),
                product_types=(product_type,),
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                environment=environment,
                instrument_provider=InstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                ),
                product_types=(product_type,),
            ),
        },
        timeout_connection=20.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    node = TradingNode(config=config_node)
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
    fast_ema = int(os.getenv("BYBIT_FAST_EMA", "10"))
    slow_ema = int(os.getenv("BYBIT_SLOW_EMA", "20"))

    strategy = EMACross(
        config=EMACrossConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=fast_ema,
            slow_ema_period=slow_ema,
            order_id_tag="001",
        ),
    )

    node.trader.add_strategy(strategy)
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
