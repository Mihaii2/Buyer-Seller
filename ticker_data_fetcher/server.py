import yfinance as yf
import time
import threading
from collections import deque
from datetime import datetime
from flask import Flask, jsonify, request
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class StockDataServer:
    def __init__(self):
        self.tickers = []
        self.ticker_data = {}  # {ticker: deque of records}
        self.current_ticker_index = 0
        self.max_records = 10000
        self.max_requests_per_minute = 120
        self.request_interval = 60 / self.max_requests_per_minute  # 2 seconds between requests
        self.running = False
        self.data_thread = None
        
    def add_ticker(self, symbol):
        """Add a ticker to the monitoring list"""
        symbol = symbol.upper()
        if symbol not in self.tickers:
            self.tickers.append(symbol)
            self.ticker_data[symbol] = deque(maxlen=self.max_records)
            logger.info(f"Added ticker: {symbol}")
            return True
        return False
    
    def remove_ticker(self, symbol):
        """Remove a ticker from the monitoring list"""
        symbol = symbol.upper()
        if symbol in self.tickers:
            self.tickers.remove(symbol)
            del self.ticker_data[symbol]
            logger.info(f"Removed ticker: {symbol}")
            return True
        return False
    
    def fetch_ticker_data(self, symbol):
        """Fetch data for a single ticker"""
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.info
            
            # Extract required data
            record = {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'currentPrice': data.get('currentPrice'),
                'dayHigh': data.get('dayHigh'),
                'dayLow': data.get('dayLow'),
                'volume': data.get('volume')
            }
            
            # Add to ticker data (deque automatically handles max length)
            self.ticker_data[symbol].append(record)
            logger.info(f"Fetched data for {symbol}: ${record['currentPrice']} | volume {record['volume']} | time {record['timestamp']}")
            
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
    
    def data_collection_loop(self):
        """Main loop for collecting data in round-robin fashion"""
        while self.running:
            if not self.tickers:
                time.sleep(1)
                continue
            
            # Get next ticker in round-robin fashion
            if self.current_ticker_index >= len(self.tickers):
                self.current_ticker_index = 0
            
            current_ticker = self.tickers[self.current_ticker_index]
            self.fetch_ticker_data(current_ticker)
            
            # Move to next ticker
            self.current_ticker_index += 1
            
            # Wait for the interval (2 seconds for 30 requests/minute)
            time.sleep(self.request_interval)
    
    def start(self):
        """Start the data collection"""
        if not self.running:
            self.running = True
            self.data_thread = threading.Thread(target=self.data_collection_loop)
            self.data_thread.daemon = True
            self.data_thread.start()
            logger.info("Data collection started")
    
    def stop(self):
        """Stop the data collection"""
        self.running = False
        if self.data_thread:
            self.data_thread.join()
        logger.info("Data collection stopped")
    
    def get_ticker_data(self, symbol):
        """Get all data for a ticker"""
        symbol = symbol.upper()
        if symbol in self.ticker_data:
            return list(self.ticker_data[symbol])
        return None
    
    def get_latest_data(self, symbol):
        """Get the latest data point for a ticker"""
        symbol = symbol.upper()
        if symbol in self.ticker_data and self.ticker_data[symbol]:
            return self.ticker_data[symbol][-1]
        return None

# Initialize the server
stock_server = StockDataServer()

# Flask app for HTTP API
app = Flask(__name__)

@app.route('/tickers', methods=['GET'])
def get_tickers():
    """Get list of all monitored tickers"""
    return jsonify({
        'tickers': stock_server.tickers,
        'total_count': len(stock_server.tickers)
    })

@app.route('/tickers', methods=['POST'])
def add_ticker():
    """Add a new ticker to monitor"""
    data = request.get_json()
    if not data or 'symbol' not in data:
        return jsonify({'error': 'Symbol is required'}), 400
    
    symbol = data['symbol']
    if stock_server.add_ticker(symbol):
        return jsonify({'message': f'Ticker {symbol} added successfully'})
    else:
        return jsonify({'message': f'Ticker {symbol} already exists'})

@app.route('/tickers/<symbol>', methods=['DELETE'])
def remove_ticker(symbol):
    """Remove a ticker from monitoring"""
    if stock_server.remove_ticker(symbol):
        return jsonify({'message': f'Ticker {symbol} removed successfully'})
    else:
        return jsonify({'error': f'Ticker {symbol} not found'}), 404

@app.route('/data/<symbol>', methods=['GET'])
def get_ticker_data(symbol):
    """Get all historical data for a ticker"""
    data = stock_server.get_ticker_data(symbol)
    if data is not None:
        return jsonify({
            'symbol': symbol.upper(),
            'record_count': len(data),
            'data': data
        })
    else:
        return jsonify({'error': f'Ticker {symbol} not found'}), 404

@app.route('/data/<symbol>/latest', methods=['GET'])
def get_latest_data(symbol):
    """Get the latest data point for a ticker"""
    data = stock_server.get_latest_data(symbol)
    if data is not None:
        return jsonify(data)
    else:
        return jsonify({'error': f'No data available for ticker {symbol}'}), 404

@app.route('/status', methods=['GET'])
def get_status():
    """Get server status"""
    return jsonify({
        'running': stock_server.running,
        'tickers_count': len(stock_server.tickers),
        'current_ticker_index': stock_server.current_ticker_index,
        'max_records_per_ticker': stock_server.max_records,
        'request_interval_seconds': stock_server.request_interval
    })

@app.route('/start', methods=['POST'])
def start_collection():
    """Start data collection"""
    stock_server.start()
    return jsonify({'message': 'Data collection started'})

@app.route('/stop', methods=['POST'])
def stop_collection():
    """Stop data collection"""
    stock_server.stop()
    return jsonify({'message': 'Data collection stopped'})

if __name__ == '__main__':
    # Add some example tickers to start with
    stock_server.add_ticker('AS')
    stock_server.add_ticker('PPIH')
    stock_server.add_ticker('PLNT')
    
    # Start data collection
    stock_server.start()
    
    try:
        # Start Flask server
        app.run(host='0.0.0.0', port=5001, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stock_server.stop()