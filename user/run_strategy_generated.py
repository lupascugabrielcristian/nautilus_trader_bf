import argparse
from decimal import Decimal
import os
import sys
import yaml

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

from strategy.strategy_generated import LiveRandomConfig
from strategy.strategy_generated import LiveRandomStrategy

"""
Example usage:
  python run_strategy_generated.py BTCUSDT-PERP
  python run_strategy_generated.py ETHUSDT

Optional environment variables:
  BINANCE_ENV              LIVE | TESTNET (default) | DEMO
  BINANCE_ACCOUNT_TYPE     USDT_FUTURES (default) | SPOT | MARGIN | ISOLATED_MARGIN | COIN_FUTURES
  BINANCE_TRADER_ID        default: TESTER-001
  BINANCE_LOG_LEVEL        default: INFO
  BINANCE_TRADE_SIZE       default: 0.01
  BINANCE_SIGNAL_PROBABILITY  default: 0.108
  BINANCE_SEED             default: 42
"""



def _required_credential_env_vars(env_name: str, account_type: BinanceAccountType) -> tuple[str, str]:
    if env_name == "DEMO":
        return ("BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET")
    if env_name == "TESTNET":
        if account_type in {BinanceAccountType.SPOT, BinanceAccountType.MARGIN, BinanceAccountType.ISOLATED_MARGIN}:
            return ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET")
        return ("BINANCE_FUTURES_TESTNET_API_KEY", "BINANCE_FUTURES_TESTNET_API_SECRET")
    return ("BINANCE_API_KEY", "BINANCE_API_SECRET")


def _validate_credentials(env_name: str, account_type: BinanceAccountType) -> None:
    key_var, secret_var = _required_credential_env_vars(env_name, account_type)
    key_is_set = bool(os.getenv(key_var))
    secret_is_set = bool(os.getenv(secret_var))
    print(f"Binance startup validation: env={env_name}, account_type={account_type}")
    print(f"Credential var status: {key_var}={'SET' if key_is_set else 'MISSING'}")
    print(f"Credential var status: {secret_var}={'SET' if secret_is_set else 'MISSING'}")
    missing = [name for name, is_set in ((key_var, key_is_set), (secret_var, secret_is_set)) if not is_set]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {missing_list}")
    print("Credential environment variables found.")


def _load_global_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "trading_orchestrator", "global_config.yaml")
    config_path = os.path.normpath(config_path)
    if os.path.exists(config_path):
        with open(config_path) as f:
            data = yaml.safe_load(f)
        if data and "steps" in data:
            for step in data["steps"]:
                if step.get("name") == "7_paper_trading":
                    return step
    return {}


def _resolve_log_level(global_config: dict) -> str:
    return os.getenv("BINANCE_LOG_LEVEL") or global_config.get("BINANCE_LOG_LEVEL", "INFO")


def main() -> None:
    global_config = _load_global_config()

    parser = argparse.ArgumentParser(description="Run LiveRandomStrategy on Binance")
    parser.add_argument("symbol", type=str, help="Instrument symbol e.g. BTCUSDT-PERP or ETHUSDT")
    args = parser.parse_args()

    env_name = os.getenv("BINANCE_ENV", "TESTNET").upper()
    if env_name not in ("LIVE", "TESTNET", "DEMO"):
        raise ValueError("BINANCE_ENV must be one of LIVE, TESTNET, DEMO")
    else:
        print(f"Got env name: {env_name}")
    is_testnet = env_name != "LIVE"

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

    _validate_credentials(env_name, account_type)

    trader = os.getenv("BINANCE_TRADER_ID", "TESTER-001")
    instrument_id = InstrumentId.from_str(f"{args.symbol}.{BINANCE}")

    # Map DEMO env vars to Nautilus config (which only understands LIVE/TESTNET)
    api_key = os.getenv("BINANCE_DEMO_API_KEY") if env_name == "DEMO" else None
    api_secret = os.getenv("BINANCE_DEMO_API_SECRET") if env_name == "DEMO" else None

    config_node = TradingNodeConfig(
        trader_id=TraderId(trader),
        logging=LoggingConfig(
            log_level=_resolve_log_level(global_config),
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
                testnet=is_testnet,
                api_key=api_key,
                api_secret=api_secret,
                instrument_provider=InstrumentProviderConfig(
                    load_ids=frozenset([instrument_id]),
                ),
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=account_type,
                testnet=is_testnet,
                api_key=api_key,
                api_secret=api_secret,
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

    strategy = LiveRandomStrategy(
        config=LiveRandomConfig(
            instrument_id=instrument_id,
            trade_size=Decimal(os.getenv("BINANCE_TRADE_SIZE", "0.01")),
            signal_probability=float(os.getenv("BINANCE_SIGNAL_PROBABILITY", "0.108")),
            seed=int(os.getenv("BINANCE_SEED", "42")),
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
