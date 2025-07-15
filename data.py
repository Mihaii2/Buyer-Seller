import yfinance as yf

symbol = 'PPIH'
ticker = yf.Ticker(symbol)

# Get real-time quote data
data = ticker.info

print(f"Symbol: {symbol}")
print(f"Current Price: {data.get('currentPrice')}")
print(f"Open: {data.get('open')}")
print(f"Day High: {data.get('dayHigh')}")
print(f"Day Low: {data.get('dayLow')}")
print(f"Volume: {data.get('volume')}")
print(f"Previous Close: {data.get('previousClose')}")
