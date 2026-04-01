import ccxt
import pandas as pd
from config import MEXC_API_KEY, MEXC_SECRET_KEY, SYMBOL, TIMEFRAME

def get_exchange():
    exchange = ccxt.mexc({
        "apiKey": MEXC_API_KEY,
        "secret": MEXC_SECRET_KEY,
        "options": {"defaultType": "swap"},
    })
    pass
    return exchange

def get_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=300):
    exchange = get_exchange()
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    print(f"candles carregados: {len(df)} | preco: {df['close'].iloc[-1]}")
    return df

def get_current_price(symbol=SYMBOL):
    exchange = get_exchange()
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])

def get_orderbook(symbol=SYMBOL, limit=20):
    exchange = get_exchange()
    ob = exchange.fetch_order_book(symbol, limit=limit)
    total_bid = sum(x[1] for x in ob["bids"])
    total_ask = sum(x[1] for x in ob["asks"])
    imbalance = (total_bid - total_ask) / (total_bid + total_ask)
    return {
        "bids": ob["bids"][:5],
        "asks": ob["asks"][:5],
        "imbalance": round(imbalance, 4),
        "imbalance_pct": round(imbalance * 100, 2),
    }
