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

# 1. Establish a timezone-aware timestamp for exactly 1 month ago
#    Using America/New_York avoids offset issues with market open/close times
now = pd.Timestamp.now(tz="America/New_York")
one_month_ago = now - pd.Timedelta(days=30)

# 2. Define your target intraday timeframe
#    For 1-minute bars:  TimeFrame(1, TimeFrameUnit.Minute)
#    For 15-minute bars: TimeFrame(15, TimeFrameUnit.Minute)
target_timeframe = TimeFrame(15, TimeFrameUnit.Minute)

request_params = StockBarsRequest(
    symbol_or_symbols="AAPL",
    timeframe=target_timeframe,
    start=one_month_ago,
    end=now,
    feed="iex"
)

# Fetch bars - on the free tier, this data is sourced from IEX
bars = client.get_stock_bars(request_params).df.loc["AAPL"]

# 3. Generate the interactive intraday Candlestick chart
fig = go.Figure(data=[go.Candlestick(
    x=bars.index,
    open=bars['open'],
    high=bars['high'],
    low=bars['low'],
    close=bars['close']
)])

# Update layout for cleaner intraday viewing (removes empty weekend gaps)
fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
fig.update_layout(title="AAPL 15-Min Bars (Past 30 Days)", xaxis_title="Date/Time", yaxis_title="Price")

fig.show()