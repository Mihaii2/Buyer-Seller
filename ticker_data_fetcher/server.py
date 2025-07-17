import yfinance as yf
import time
import threading
from collections import deque
from datetime import datetime, timezone
from flask import Flask, jsonify, request
import json
import logging
import flask_cors
import pytz

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
        self.market_check_interval = 60  # Check market status every minute
        
    def is_market_open(self):
        """Check if the US stock market is currently open"""
        try:
            # Get current time in Eastern Time (NYSE/NASDAQ timezone)
            et = pytz.timezone('America/New_York')
            now = datetime.now(et)
            
            # Market is closed on weekends
            if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
                return False, self.get_time_until_next_open(now)
            
            # Regular market hours: 9:30 AM - 4:00 PM ET
            market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
            
            if market_open <= now <= market_close:
                return True, None
            else:
                return False, self.get_time_until_next_open(now)
                
        except Exception as e:
            logger.error(f"Error checking market status: {str(e)}")
            return True, None  # Default to open if we can't check
    
    def get_time_until_next_open(self, current_time):
        """Calculate time until next market open"""
        et = pytz.timezone('America/New_York')
        
        # If it's before 9:30 AM today, next open is today at 9:30 AM
        if current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30):
            next_open = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
        else:
            # Market is closed for the day, next open is tomorrow at 9:30 AM
            next_day = current_time.date().replace(day=current_time.day + 1)
            next_open = et.localize(datetime.combine(next_day, datetime.min.time().replace(hour=9, minute=30)))
            
            # If tomorrow is Saturday, next open is Monday
            while next_open.weekday() >= 5:
                next_day = next_day.replace(day=next_day.day + 1)
                next_open = et.localize(datetime.combine(next_day, datetime.min.time().replace(hour=9, minute=30)))
        
        time_diff = next_open - current_time
        return time_diff
    
    def format_time_until_open(self, time_diff):
        """Format time difference into readable string"""
        if time_diff is None:
            return "Market is open"
        
        total_seconds = int(time_diff.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        
        if hours > 0:
            return f"{hours} hours and {minutes} minutes until market opens"
        else:
            return f"{minutes} minutes until market opens"
    
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
            
            # Check for duplicates before adding
            if not self.ticker_data[symbol] or (
                self.ticker_data[symbol][-1]['currentPrice'] != record['currentPrice'] or 
                self.ticker_data[symbol][-1]['volume'] != record['volume']
            ):
                self.ticker_data[symbol].append(record)
                logger.info(f"Fetched data for {symbol}: ${record['currentPrice']} | volume {record['volume']} | time {record['timestamp']}")
            else:
                logger.info(f"Skipped duplicate data for {symbol}: ${record['currentPrice']} | volume {record['volume']}")  
                
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
    
    def data_collection_loop(self):
        """Main loop for collecting data in round-robin fashion"""
        while self.running:
            # Check if market is open
            market_open, time_until_open = self.is_market_open()
            
            if not market_open:
                time_msg = self.format_time_until_open(time_until_open)
                logger.info(f"Market is closed. {time_msg}")
                # Wait for market check interval before checking again
                time.sleep(self.market_check_interval)
                continue
            
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
    
    def get_market_status(self):
        """Get current market status"""
        market_open, time_until_open = self.is_market_open()
        return {
            'is_open': market_open,
            'message': "Market is open" if market_open else self.format_time_until_open(time_until_open)
        }

# Initialize the server
stock_server = StockDataServer()

# Flask app for HTTP API
app = Flask(__name__)
flask_cors.CORS(app)  # Enable CORS for all routes

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

@app.route('/market-status', methods=['GET'])
def get_market_status():
    """Get current market status"""
    return jsonify(stock_server.get_market_status())

@app.route('/status', methods=['GET'])
def get_status():
    """Get server status"""
    market_status = stock_server.get_market_status()
    return jsonify({
        'running': stock_server.running,
        'market_open': market_status['is_open'],
        'market_message': market_status['message'],
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
    
    # Start data collection
    stock_server.start()
    
    try:
        # Start Flask server
        app.run(host='0.0.0.0', port=5001, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stock_server.stop()