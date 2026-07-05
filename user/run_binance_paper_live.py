from decimal import Decimal
import os

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from strategy.strategy_generated import LiveRandomConfig
from strategy.strategy_generated import LiveRandomStrategy


_ACCOUNT_TYPE_CREDENTIAL_MAP: dict[BinanceAccountType, tuple[str, str]] = {
    BinanceAccountType.SPOT: ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
    BinanceAccountType.MARGIN: ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
    BinanceAccountType.ISOLATED_MARGIN: ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
    BinanceAccountType.USDT_FUTURES: ("BINANCE_FUTURES_TESTNET_API_KEY", "BINANCE_FUTURES_TESTNET_API_SECRET"),
    BinanceAccountType.COIN_FUTURES: ("BINANCE_FUTURES_TESTNET_API_KEY", "BINANCE_FUTURES_TESTNET_API_SECRET"),
}


def _validate_credentials(testnet: bool, account_type: BinanceAccountType) -> None:
    if testnet:
        key_var, secret_var = _ACCOUNT_TYPE_CREDENTIAL_MAP.get(account_type, ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"))
    else:
        key_var, secret_var = "BINANCE_API_KEY", "BINANCE_API_SECRET"
    key_is_set = bool(os.getenv(key_var))
    secret_is_set = bool(os.getenv(secret_var))
    env_label = "TESTNET" if testnet else "LIVE"
    print(f"Binance startup validation: env={env_label}, account_type={account_type}")
    print(f"Credential var status: {key_var}={'SET' if key_is_set else 'MISSING'}")
    print(f"Credential var status: {secret_var}={'SET' if secret_is_set else 'MISSING'}")
    missing = [name for name, is_set in ((key_var, key_is_set), (secret_var, secret_is_set)) if not is_set]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {missing_list}")
    print("Credential environment variables found.")


def _parse_env_name() -> tuple[bool, bool]:
    raw = os.getenv("BINANCE_ENV", "TESTNET").upper()
    if raw == "LIVE":
        return False, False
    if raw == "TESTNET":
        return True, False
    if raw == "DEMO":
        return False, True
    raise ValueError("BINANCE_ENV must be one of LIVE, TESTNET, DEMO")


def _parse_account_type() -> BinanceAccountType:
    name = os.getenv("BINANCE_ACCOUNT_TYPE", "USDT_FUTURES").upper()
    mapping = {
        "SPOT": BinanceAccountType.SPOT,
        "MARGIN": BinanceAccountType.MARGIN,
        "ISOLATED_MARGIN": BinanceAccountType.ISOLATED_MARGIN,
        "USDT_FUTURES": BinanceAccountType.USDT_FUTURES,
        "COIN_FUTURES": BinanceAccountType.COIN_FUTURES,
    }
    account_type = mapping.get(name)
    if account_type is None:
        raise ValueError("BINANCE_ACCOUNT_TYPE must be one of SPOT, MARGIN, ISOLATED_MARGIN, USDT_FUTURES, COIN_FUTURES")
    return account_type


def main() -> None:
    testnet, us = _parse_env_name()
    account_type = _parse_account_type()

    _validate_credentials(testnet, account_type)

    default_symbol = "BTCUSDT" if account_type == BinanceAccountType.SPOT else "BTCUSDT-PERP"
    symbol = os.getenv("BINANCE_INSTRUMENT", default_symbol)
    trader = os.getenv("BINANCE_TRADER_ID", "TESTER-001")
    instrument_id = InstrumentId.from_str(f"{symbol}.{BINANCE}")

    config_node = TradingNodeConfig(
        trader_id=TraderId(trader),
        logging=LoggingConfig(
            log_level=os.getenv("BINANCE_LOG_LEVEL", "INFO"),
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_instrument_ids=[instrument_id],
        ),
        risk_engine=LiveRiskEngineConfig(bypass=True),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=account_type,
                testnet=testnet,
                us=us,
                instrument_provider=InstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                ),
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=account_type,
                testnet=testnet,
                us=us,
                instrument_provider=InstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                ),
                max_retries=3,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    node = TradingNode(config=config_node)
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")

    strategy = LiveRandomStrategy(
        config=LiveRandomConfig(
            instrument_id=instrument_id,
            trade_size=Decimal(os.getenv("BINANCE_TRADE_SIZE", "0.01")),
            signal_probability=float(os.getenv("BINANCE_SIGNAL_PROB", "0.1")),
        ),
    )

    node.trader.add_strategy(strategy)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
