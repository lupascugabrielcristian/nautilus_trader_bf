import argparse
from decimal import Decimal
import json
import os
import sys
import requests

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

from strategy.strategy_dual_SMA import DualSMAConfig
from strategy.strategy_dual_SMA import DualSMAStrategy

from tr_utils import global_config
from tr_utils import log_message

"""
Example usage:
  python 07_run_strategy_dual_SMA.py BTCUSDT-PERP
  python 07_run_strategy_dual_SMA.py ETHUSDT --fast-period 10 --slow-period 50

Optional environment variables:
  BINANCE_ENV              LIVE | TESTNET (default) | DEMO
  BINANCE_ACCOUNT_TYPE     USDT_FUTURES (default) | SPOT | MARGIN | ISOLATED_MARGIN | COIN_FUTURES
  BINANCE_TRADER_ID        default: TESTER-001
  BINANCE_LOG_LEVEL        default: INFO
  BINANCE_TRADE_SIZE       default: 0.2
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


def _resolve_log_level(global_config: dict) -> str:
    return os.getenv("BINANCE_LOG_LEVEL") or global_config.get("BINANCE_LOG_LEVEL", "INFO")


def _resolve_trade_size(global_config: dict) -> Decimal:
    return Decimal(os.getenv("BINANCE_TRADE_SIZE") or global_config.get("LOT_SIZE", "0.2"))


def _load_step_config() -> dict:
    raw = global_config()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    for step in data.get("steps", []):
        if step.get("name") == "7_paper_trading":
            return step
    return {}


def _load_global_config() -> dict:
    raw = global_config()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


_INTERVAL_MAP = {
    "s": "SECOND",
    "m": "MINUTE",
    "h": "HOUR",
    "d": "DAY",
    "w": "WEEK",
}


def _resolve_bar_suffix(interval: str) -> str:
    suffix = "LAST-EXTERNAL"
    if not interval:
        return f"1-MINUTE-{suffix}"
    interval = interval.strip().lower()
    if len(interval) < 2:
        return f"1-MINUTE-{suffix}"
    num_part = interval[:-1]
    unit_part = interval[-1]
    if not num_part.isdigit() or unit_part not in _INTERVAL_MAP:
        return f"1-MINUTE-{suffix}"
    step = int(num_part)
    unit = _INTERVAL_MAP[unit_part]
    return f"{step}-{unit}-{suffix}"


def main() -> None:
    global_config = _load_step_config()

    parser = argparse.ArgumentParser(description="Run DualSMAStrategy on Binance")
    parser.add_argument("symbol", type=str, help="Instrument symbol e.g. BTCUSDT-PERP or ETHUSDT")
    parser.add_argument("--fast-period", type=int, default=10, help="Fast SMA period")
    parser.add_argument("--slow-period", type=int, default=50, help="Slow SMA period")
    parser.add_argument("--bar-suffix", type=str, default=None, help="Bar suffix e.g. 15-MINUTE-LAST-EXTERNAL. Overrides interval from global config.")
    args = parser.parse_args()

    env_name = os.getenv("BINANCE_ENV", "TESTNET").upper()
    if env_name not in ("LIVE", "TESTNET", "DEMO"):
        raise ValueError("BINANCE_ENV must be one of LIVE, TESTNET, DEMO")
    environment = {
        "LIVE": BinanceEnvironment.LIVE,
        "TESTNET": BinanceEnvironment.TESTNET,
        "DEMO": BinanceEnvironment.DEMO,
    }[env_name]

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

    key_var, secret_var = _required_credential_env_vars(env_name, account_type)
    api_key = os.getenv(key_var)
    api_secret = os.getenv(secret_var)

    from nautilus_trader.adapters.binance.common.urls import get_http_base_url, get_ws_api_base_url
    resolved_http = get_http_base_url(account_type, environment, False)
    resolved_ws_api = get_ws_api_base_url(account_type, environment, False)

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
                environment=environment,
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
                environment=environment,
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
    log_message("Trading node is ready")

#   Examples of valid values:
#      - 1-MINUTE-LAST-EXTERNAL
#      - 5-MINUTE-LAST-EXTERNAL
#      - 15-MINUTE-LAST-EXTERNAL
#      - 1-HOUR-LAST-EXTERNAL
#      - 1-SECOND-LAST-EXTERNAL

    trade_size = _resolve_trade_size(global_config)

    bar_suffix = args.bar_suffix or _resolve_bar_suffix(
        _load_global_config().get("global", {}).get("interval", "1m"),
    )

    strategy = DualSMAStrategy(
        config=DualSMAConfig(
            instrument_id=instrument_id,
            trade_size=trade_size,
            fast_period=args.fast_period,
            slow_period=args.slow_period,
            bar_suffix=bar_suffix,
            global_config=global_config,
            telegram_active=True
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
