from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
import plotly.graph_objects as go
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise ValueError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env file")

client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# --- Config ---
# Timeframe options: TimeFrameUnit.Minute, TimeFrameUnit.Hour, TimeFrameUnit.Day, TimeFrameUnit.Week, TimeFrameUnit.Month
SYMBOL = "NVDA"
TIMEFRAME = TimeFrame(1, TimeFrameUnit.Minute)

now = pd.Timestamp.now(tz="America/New_York")
one_month_ago = now - pd.Timedelta(days=30)

request_params = StockBarsRequest(
    symbol_or_symbols=SYMBOL,
    timeframe=TIMEFRAME,
    start=one_month_ago,
    end=now,
    feed="iex"
)

bars_df = client.get_stock_bars(request_params).df
if bars_df.empty:
    raise ValueError(f"No bars returned for symbol '{SYMBOL}'")
bars = bars_df.loc[SYMBOL] if "symbol" in bars_df.index.names else bars_df

# Generate the interactive Candlestick chart
fig = go.Figure(data=[go.Candlestick(
    x=bars.index,
    open=bars['open'],
    high=bars['high'],
    low=bars['low'],
    close=bars['close']
)])

# Update layout for cleaner intraday viewing (removes empty weekend gaps)
fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
fig.update_layout(title=f"{SYMBOL} {TIMEFRAME} Bars (Past 30 Days)", xaxis_title="Date/Time", yaxis_title="Price")

fig.show()