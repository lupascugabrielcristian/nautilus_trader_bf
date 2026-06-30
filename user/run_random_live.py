from decimal import Decimal
import os

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.adapters.binance import BinanceInstrumentProviderConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

from strategy.strategy_generated import LiveRandomConfig, LiveRandomStrategy


def _required_credential_env_vars(environment: BinanceEnvironment, account_type: BinanceAccountType) -> tuple[str, str]:
    if environment == BinanceEnvironment.DEMO:
        return ("BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET")
    if environment == BinanceEnvironment.TESTNET:
        if account_type in {BinanceAccountType.SPOT, BinanceAccountType.MARGIN, BinanceAccountType.ISOLATED_MARGIN}:
            return ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET")
        return ("BINANCE_FUTURES_TESTNET_API_KEY", "BINANCE_FUTURES_TESTNET_API_SECRET")
    return ("BINANCE_API_KEY", "BINANCE_API_SECRET")


def _validate_credentials(environment: BinanceEnvironment, account_type: BinanceAccountType) -> None:
    key_var, secret_var = _required_credential_env_vars(environment, account_type)
    key_is_set = bool(os.getenv(key_var))
    secret_is_set = bool(os.getenv(secret_var))
    print(f"Binance startup validation: env={environment}, account_type={account_type}")
    print(f"Credential var status: {key_var}={'SET' if key_is_set else 'MISSING'}")
    print(f"Credential var status: {secret_var}={'SET' if secret_is_set else 'MISSING'}")
    missing = [name for name, is_set in ((key_var, key_is_set), (secret_var, secret_is_set)) if not is_set]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {missing_list}")
    print("Credential environment variables found.")


def _confirm_live() -> None:
    print("=" * 60)
    print("  LIVE TRADING MODE - This script will trade with REAL money!")
    print("=" * 60)
    response = input("Type 'YES' to confirm you want to run live: ")
    if response.strip().upper() != "YES":
        print("Aborted.")
        raise SystemExit(0)


def main() -> None:
    env_name = os.getenv("BINANCE_ENV", "LIVE").upper()
    environment = {
        "LIVE": BinanceEnvironment.LIVE,
        "TESTNET": BinanceEnvironment.TESTNET,
        "DEMO": BinanceEnvironment.DEMO,
    }.get(env_name)
    if environment is None:
        raise ValueError("BINANCE_ENV must be one of LIVE, TESTNET, DEMO")

    if environment == BinanceEnvironment.LIVE:
        _confirm_live()

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

    _validate_credentials(environment, account_type)

    default_symbol = "BTCUSDT" if account_type == BinanceAccountType.SPOT else "BTCUSDT-PERP"
    symbol = os.getenv("BINANCE_INSTRUMENT", default_symbol)
    trader = os.getenv("BINANCE_TRADER_ID", "TESTER-001")
    instrument_id = InstrumentId.from_str(f"{symbol}.{BINANCE}")

    config_node = TradingNodeConfig(
        trader_id=TraderId(trader),
        logging=LoggingConfig(
            log_level=os.getenv("BINANCE_LOG_LEVEL", "INFO"),
            use_pyo3=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_instrument_ids=[instrument_id],
        ),
        risk_engine=LiveRiskEngineConfig(bypass=True),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=account_type,
                environment=environment,
                instrument_provider=BinanceInstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                    query_commission_rates=True,
                ),
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=account_type,
                environment=environment,
                instrument_provider=BinanceInstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                    query_commission_rates=True,
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

    trade_size = Decimal(os.getenv("BINANCE_TRADE_SIZE", "0.001"))
    signal_probability = float(os.getenv("BINANCE_SIGNAL_PROBABILITY", "0.108"))
    seed = int(os.getenv("BINANCE_RANDOM_SEED", "42"))

    strategy = LiveRandomStrategy(
        config=LiveRandomConfig(
            instrument_id=str(instrument_id),
            trade_size=trade_size,
            signal_probability=signal_probability,
            seed=seed,
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
