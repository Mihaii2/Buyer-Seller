import yfinance as yf
import time
import threading
from collections import deque
from datetime import datetime, timezone, date, timedelta
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
        self.ticker_initial_prices = {}  # Store initial prices for each ticker
        self.current_ticker_index = 0
        self.max_records = 10000
        self.max_requests_per_minute = 120
        self.request_interval = 60 / self.max_requests_per_minute  # 0.5 seconds between requests
        self.running = False
        self.data_thread = None
        self.market_check_interval = 30  # Check market status every 30 seconds when closed
        self.last_cleanup_date = None
        self.market_just_opened = False
        self.last_market_status = None
        
        # Define timezones
        self.et_tz = pytz.timezone('America/New_York')  # NYSE/NASDAQ timezone
        self.local_tz = pytz.timezone('Europe/Bucharest')  # Romanian timezone
        
    def get_current_et_time(self):
        """Get current time in Eastern Time"""
        return datetime.now(self.et_tz)
    
    def get_current_local_time(self):
        """Get current time in local timezone (Romania)"""
        return datetime.now(self.local_tz)
        
    def is_market_open(self):
        """Check if the US stock market is currently open"""
        try:
            # Get current time in Eastern Time
            et_now = self.get_current_et_time()
            
            # Market is closed on weekends (Saturday = 5, Sunday = 6)
            if et_now.weekday() >= 5:
                return False, self.get_time_until_next_open(et_now)
            
            # Regular market hours: 9:30 AM - 4:00 PM ET
            market_open_time = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close_time = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
            
            # Check if current time is within market hours
            is_open = market_open_time <= et_now <= market_close_time
            
            if is_open:
                return True, None
            else:
                return False, self.get_time_until_next_open(et_now)
                
        except Exception as e:
            logger.error(f"Error checking market status: {str(e)}")
            # Default to checking if it's a weekday and reasonable hours
            et_now = self.get_current_et_time()
            if et_now.weekday() < 5 and 9 <= et_now.hour < 16:
                return True, None
            return False, timedelta(hours=1)  # Default wait time
    
    def get_time_until_next_open(self, current_et_time):
        """Calculate time until next market open in ET"""
        try:
            # Start with today's market open time
            next_open = current_et_time.replace(hour=9, minute=30, second=0, microsecond=0)
            
            # If it's already past today's market open time, move to next day
            if current_et_time >= next_open:
                next_open += timedelta(days=1)
            
            # Skip weekends - if next open falls on Saturday or Sunday, move to Monday
            while next_open.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
                next_open += timedelta(days=1)
            
            time_diff = next_open - current_et_time
            return time_diff
            
        except Exception as e:
            logger.error(f"Error calculating time until market open: {str(e)}")
            return timedelta(hours=1)  # Default fallback
    
    def format_time_until_open(self, time_diff):
        """Format time difference into readable string with local time info"""
        if time_diff is None:
            return "Market is open"
        
        total_seconds = int(time_diff.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        
        # Calculate when market opens in local time
        et_now = self.get_current_et_time()
        local_now = self.get_current_local_time()
        
        # Get next market open time in ET
        next_open_et = et_now + time_diff
        # Convert to local time
        next_open_local = next_open_et.astimezone(self.local_tz)
        
        local_time_str = next_open_local.strftime('%H:%M %Z')
        date_str = next_open_local.strftime('%A, %B %d')
        
        if hours > 24:
            days = hours // 24
            remaining_hours = hours % 24
            return f"Market opens in {days} days, {remaining_hours} hours ({local_time_str} on {date_str})"
        elif hours > 0:
            return f"Market opens in {hours}h {minutes}m ({local_time_str} on {date_str})"
        else:
            return f"Market opens in {minutes} minutes ({local_time_str})"
    
    def cleanup_old_records(self):
        """Remove all records that are not from today (in ET timezone)"""
        # Use ET timezone for consistency with market hours
        today_et = self.get_current_et_time().date()
        
        # Only run cleanup once per day
        if self.last_cleanup_date == today_et:
            return
        
        logger.info("Performing daily cleanup of old records...")
        
        cleaned_tickers = 0
        total_removed = 0
        
        for symbol in self.tickers:
            if symbol in self.ticker_data:
                original_count = len(self.ticker_data[symbol])
                
                # Filter to keep only today's records (in ET)
                today_records = deque(maxlen=self.max_records)
                for record in self.ticker_data[symbol]:
                    try:
                        # Parse timestamp and convert to ET for comparison
                        record_dt = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
                        if record_dt.tzinfo is None:
                            record_dt = record_dt.replace(tzinfo=timezone.utc)
                        record_et = record_dt.astimezone(self.et_tz)
                        record_date = record_et.date()
                        
                        if record_date == today_et:
                            today_records.append(record)
                    except (ValueError, KeyError, AttributeError) as e:
                        logger.warning(f"Skipping malformed record: {e}")
                        continue
                
                self.ticker_data[symbol] = today_records
                removed_count = original_count - len(today_records)
                
                if removed_count > 0:
                    total_removed += removed_count
                    cleaned_tickers += 1
        
        if cleaned_tickers > 0:
            logger.info(f"Cleaned up {total_removed} old records from {cleaned_tickers} tickers")
        
        self.last_cleanup_date = today_et
    
    def add_initial_market_open_record(self, symbol, current_price):
        """Add an initial record with volume 0 when market opens"""
        try:
            # Create initial record with volume 0
            initial_record = {
                'symbol': symbol,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'currentPrice': current_price,
                'dayHigh': current_price,
                'dayLow': current_price,
                'volume': 0  # Set volume to 0 for market open record
            }
            
            # Clear existing data and add the initial record
            self.ticker_data[symbol].clear()
            self.ticker_data[symbol].append(initial_record)
            self.ticker_initial_prices[symbol] = current_price
            
            logger.info(f"Added market open record for {symbol}: ${current_price} with volume 0")
            
        except Exception as e:
            logger.error(f"Error adding initial market open record for {symbol}: {str(e)}")
    
    def add_ticker(self, symbol):
        """Add a ticker to the monitoring list"""
        symbol = symbol.upper().strip()
        if symbol and symbol not in self.tickers:
            self.tickers.append(symbol)
            self.ticker_data[symbol] = deque(maxlen=self.max_records)
            self.ticker_initial_prices[symbol] = None
            logger.info(f"Added ticker: {symbol}")
            return True
        return False
    
    def remove_ticker(self, symbol):
        """Remove a ticker from the monitoring list"""
        symbol = symbol.upper().strip()
        if symbol in self.tickers:
            self.tickers.remove(symbol)
            del self.ticker_data[symbol]
            if symbol in self.ticker_initial_prices:
                del self.ticker_initial_prices[symbol]
            logger.info(f"Removed ticker: {symbol}")
            return True
        return False
    
    def fetch_ticker_data(self, symbol):
        """Fetch data for a single ticker"""
        try:
            ticker = yf.Ticker(symbol)
            
            # Use multiple methods to get current price
            current_price = None
            volume = None
            day_high = None
            day_low = None
            
            # Try getting from info first
            try:
                info = ticker.info
                current_price = info.get('currentPrice') or info.get('regularMarketPrice')
                volume = info.get('volume') or info.get('regularMarketVolume')
                day_high = info.get('dayHigh') or info.get('regularMarketDayHigh')
                day_low = info.get('dayLow') or info.get('regularMarketDayLow')
            except Exception as e:
                logger.warning(f"Could not get info for {symbol}: {e}")
            
            # If info didn't work, try history
            if current_price is None:
                try:
                    hist = ticker.history(period="1d", interval="1m")
                    if not hist.empty:
                        latest = hist.iloc[-1]
                        current_price = latest['Close']
                        volume = latest['Volume']
                        day_high = hist['High'].max()
                        day_low = hist['Low'].min()
                except Exception as e:
                    logger.warning(f"Could not get history for {symbol}: {e}")
            
            if current_price is None:
                logger.warning(f"No price data available for {symbol}")
                return
            
            record = {
                'symbol': symbol,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'currentPrice': float(current_price),
                'dayHigh': float(day_high) if day_high is not None else float(current_price),
                'dayLow': float(day_low) if day_low is not None else float(current_price),
                'volume': int(volume) if volume is not None else 0
            }
            
            # If market just opened and we don't have an initial price, add initial record
            if self.market_just_opened and self.ticker_initial_prices.get(symbol) is None:
                self.add_initial_market_open_record(symbol, current_price)
                return  # Don't add the regular record yet
            
            # Check for duplicates before adding (skip if same price and volume)
            if not self.ticker_data[symbol] or (
                abs(self.ticker_data[symbol][-1]['currentPrice'] - record['currentPrice']) > 0.001 or 
                self.ticker_data[symbol][-1]['volume'] != record['volume']
            ):
                self.ticker_data[symbol].append(record)
                logger.info(f"Fetched data for {symbol}: ${record['currentPrice']:.4f} | volume {record['volume']} | time {record['timestamp'][:19]}")
            else:
                logger.debug(f"Skipped duplicate data for {symbol}")
                
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
    
    def data_collection_loop(self):
        """Main loop for collecting data in round-robin fashion"""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self.running:
            try:
                # Perform daily cleanup
                self.cleanup_old_records()
                
                # Check if market is open
                market_open, time_until_open = self.is_market_open()
                
                # Log market status changes
                if self.last_market_status != market_open:
                    if market_open:
                        logger.info("Market status: OPEN - Starting data collection")
                    else:
                        status_msg = self.format_time_until_open(time_until_open)
                        logger.info(f"Market status: CLOSED - {status_msg}")
                
                # Check if market just opened
                if market_open and self.last_market_status is False:
                    self.market_just_opened = True
                    logger.info("Market just opened! Waiting 15 seconds before starting data collection...")
                    time.sleep(15)
                    logger.info("15-second delay complete. Will add initial records with volume 0.")
                    # Reset initial prices for all tickers
                    for symbol in self.tickers:
                        self.ticker_initial_prices[symbol] = None
                else:
                    self.market_just_opened = False
                
                self.last_market_status = market_open
                
                # If market is closed, wait and continue
                if not market_open:
                    time.sleep(self.market_check_interval)
                    continue
                
                # If no tickers, wait briefly
                if not self.tickers:
                    time.sleep(5)
                    continue
                
                # Get next ticker in round-robin fashion
                if self.current_ticker_index >= len(self.tickers):
                    self.current_ticker_index = 0
                
                current_ticker = self.tickers[self.current_ticker_index]
                self.fetch_ticker_data(current_ticker)
                
                # Move to next ticker
                self.current_ticker_index += 1
                
                # Reset error counter on success
                consecutive_errors = 0
                
                # Wait for the interval
                time.sleep(self.request_interval)
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in data collection loop (#{consecutive_errors}): {str(e)}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}). Waiting 60 seconds...")
                    time.sleep(60)
                    consecutive_errors = 0
                else:
                    time.sleep(5)  # Brief wait before retrying
    
    def start(self):
        """Start the data collection"""
        if not self.running:
            self.running = True
            self.data_thread = threading.Thread(target=self.data_collection_loop)
            self.data_thread.daemon = True
            self.data_thread.start()
            logger.info("Data collection thread started")
    
    def stop(self):
        """Stop the data collection"""
        if self.running:
            self.running = False
            if self.data_thread and self.data_thread.is_alive():
                self.data_thread.join(timeout=5)
            logger.info("Data collection stopped")
    
    def get_ticker_data(self, symbol):
        """Get all data for a ticker"""
        symbol = symbol.upper().strip()
        if symbol in self.ticker_data:
            return list(self.ticker_data[symbol])
        return None
    
    def get_latest_data(self, symbol):
        """Get the latest data point for a ticker"""
        symbol = symbol.upper().strip()
        if symbol in self.ticker_data and self.ticker_data[symbol]:
            return self.ticker_data[symbol][-1]
        return None
    
    def get_market_status(self):
        """Get current market status with local time information"""
        market_open, time_until_open = self.is_market_open()
        
        # Add current time information
        et_time = self.get_current_et_time()
        local_time = self.get_current_local_time()
        
        return {
            'is_open': market_open,
            'message': "Market is open" if market_open else self.format_time_until_open(time_until_open),
            'current_et_time': et_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'current_local_time': local_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'next_open_local': None if market_open else (et_time + time_until_open).astimezone(self.local_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
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
    try:
        data = request.get_json()
        if not data or 'symbol' not in data:
            return jsonify({'error': 'Symbol is required'}), 400
        
        symbol = data['symbol']
        if stock_server.add_ticker(symbol):
            return jsonify({'message': f'Ticker {symbol.upper()} added successfully'})
        else:
            return jsonify({'message': f'Ticker {symbol.upper()} already exists'})
    except Exception as e:
        logger.error(f"Error adding ticker: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/tickers/<symbol>', methods=['DELETE'])
def remove_ticker(symbol):
    """Remove a ticker from monitoring"""
    try:
        if stock_server.remove_ticker(symbol):
            return jsonify({'message': f'Ticker {symbol.upper()} removed successfully'})
        else:
            return jsonify({'error': f'Ticker {symbol.upper()} not found'}), 404
    except Exception as e:
        logger.error(f"Error removing ticker: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/data/<symbol>', methods=['GET'])
def get_ticker_data(symbol):
    """Get all historical data for a ticker"""
    try:
        data = stock_server.get_ticker_data(symbol)
        if data is not None:
            return jsonify({
                'symbol': symbol.upper(),
                'record_count': len(data),
                'data': data
            })
        else:
            return jsonify({'error': f'Ticker {symbol.upper()} not found'}), 404
    except Exception as e:
        logger.error(f"Error getting ticker data: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/data/<symbol>/latest', methods=['GET'])
def get_latest_data(symbol):
    """Get the latest data point for a ticker"""
    try:
        data = stock_server.get_latest_data(symbol)
        if data is not None:
            return jsonify(data)
        else:
            return jsonify({'error': f'No data available for ticker {symbol.upper()}'}), 404
    except Exception as e:
        logger.error(f"Error getting latest data: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/market-status', methods=['GET'])
def get_market_status():
    """Get current market status"""
    try:
        return jsonify(stock_server.get_market_status())
    except Exception as e:
        logger.error(f"Error getting market status: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Get server status"""
    try:
        market_status = stock_server.get_market_status()
        return jsonify({
            'running': stock_server.running,
            'market_open': market_status['is_open'],
            'market_message': market_status['message'],
            'current_et_time': market_status['current_et_time'],
            'current_local_time': market_status['current_local_time'],
            'tickers_count': len(stock_server.tickers),
            'current_ticker_index': stock_server.current_ticker_index,
            'max_records_per_ticker': stock_server.max_records,
            'request_interval_seconds': stock_server.request_interval,
            'last_cleanup_date': str(stock_server.last_cleanup_date) if stock_server.last_cleanup_date else None
        })
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/start', methods=['POST'])
def start_collection():
    """Start data collection"""
    try:
        stock_server.start()
        return jsonify({'message': 'Data collection started'})
    except Exception as e:
        logger.error(f"Error starting collection: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/stop', methods=['POST'])
def stop_collection():
    """Stop data collection"""
    try:
        stock_server.stop()
        return jsonify({'message': 'Data collection stopped'})
    except Exception as e:
        logger.error(f"Error stopping collection: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manually trigger cleanup of old records"""
    try:
        stock_server.last_cleanup_date = None  # Force cleanup
        stock_server.cleanup_old_records()
        return jsonify({'message': 'Manual cleanup completed'})
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Start data collection
    stock_server.start()
    
    try:
        # Start Flask server
        app.run(host='0.0.0.0', port=5001, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stock_server.stop()