import json
import time
import threading
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from flask import Flask, request, jsonify
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import math
from queue import Queue
import uuid
import flask_cors


@dataclass
class SellStopOrder:
    price: float
    shares: float

@dataclass
class Trade:
    ticker: str
    shares: float
    risk_amount: float
    lower_price_range: float
    higher_price_range: float
    sell_stops: List[SellStopOrder]
    trade_id: str = None
    
    def __post_init__(self):
        if self.trade_id is None:
            self.trade_id = str(uuid.uuid4())

class IBWrapper(EWrapper):
    def __init__(self):
        EWrapper.__init__(self)
        self.next_order_id = None
        self.order_id_event = threading.Event()
        self.order_fills = {}
        self.order_events = {}
        
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self.order_id_event.set()
        
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        print(f"Order {orderId}: Status={status}, Filled={filled}, Remaining={remaining}, AvgPrice={avgFillPrice}")
        
        self.order_fills[orderId] = {
            'status': status,
            'filled': float(filled),
            'remaining': float(remaining),
            'avgFillPrice': float(avgFillPrice)
        }
        
        if status in ['Filled', 'Cancelled']:
            if orderId in self.order_events:
                self.order_events[orderId].set()
        
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"Error {reqId}: {errorCode} - {errorString}")
        
        if reqId in self.order_events:
            self.order_events[reqId].set()

class IBClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)
        self.wrapper = wrapper

class StockTradingServer:
    def __init__(self):
        # In-memory storage
        self.trades: List[Trade] = []
        self.available_risk: float = 0.0
        self.error_log: List[Dict] = []
        
        self.server_start_time = time.time()  # Track when server started
        self.last_trade_time = None  # Track last trade execution
        
        # Threading for sequential processing
        self.request_queue = Queue()
        self.processing_thread = None
        self.is_processing = False
        self.server_running = True
        
        # IBKR connection
        self.ib_wrapper = None
        self.ib_client = None
        
        
        
        # Start processing thread
        self.start_processing_thread()
        
    def _remove_trade_internal(self, data: dict) -> dict:
        """Internal method to remove a trade"""
        try:
            trade_id = data.get('trade_id')
            ticker = data.get('ticker')
            lower_price = data.get('lower_price')
            higher_price = data.get('higher_price')
            
            # Find trade by ID first, then by criteria as fallback
            trade_to_remove = None
            
            if trade_id:
                for trade in self.trades:
                    if trade.trade_id == trade_id:
                        trade_to_remove = trade
                        break
            
            # If not found by ID, try to find by criteria
            if trade_to_remove is None and ticker and lower_price is not None and higher_price is not None:
                trade_to_remove = self._find_trade_by_criteria(ticker, lower_price, higher_price)
            
            if trade_to_remove is None:
                return {'success': False, 'error': 'Trade not found'}
            
            # Remove the trade
            self.trades.remove(trade_to_remove)
            
            return {
                'success': True,
                'message': f'Trade removed: {trade_to_remove.ticker} (${trade_to_remove.lower_price_range} - ${trade_to_remove.higher_price_range})',
                'removed_trade': {
                    'trade_id': trade_to_remove.trade_id,
                    'ticker': trade_to_remove.ticker,
                    'shares': trade_to_remove.shares,
                    'risk_amount': trade_to_remove.risk_amount,
                    'lower_price_range': trade_to_remove.lower_price_range,
                    'higher_price_range': trade_to_remove.higher_price_range
                },
                'available_risk': self.available_risk
            }
            
        except Exception as e:
            error_msg = f"Error removing trade: {str(e)}"
            self._log_error("REMOVE_TRADE_ERROR", data.get('ticker', 'unknown'), error_msg, data)
            return {'success': False, 'error': error_msg}


    def remove_trade(self, trade_id: str = None, ticker: str = None, lower_price: float = None, higher_price: float = None) -> dict:
        """Remove a trade by ID or criteria"""
        return self._queue_request('remove_trade', {
            'trade_id': trade_id,
            'ticker': ticker,
            'lower_price': lower_price,
            'higher_price': higher_price
        })

    
    def start_processing_thread(self):
        """Start the sequential processing thread"""
        self.processing_thread = threading.Thread(target=self._process_requests)
        self.processing_thread.daemon = True
        self.processing_thread.start()
    
    def _process_requests(self):
        """Process requests sequentially"""
        while self.server_running:
            try:
                # Get next request (blocks until available)
                request_data = self.request_queue.get(timeout=1)
                if request_data is None:
                    continue
                
                self.is_processing = True
                
                # Process the request
                request_type = request_data['type']
                response_queue = request_data['response_queue']
                
                try:
                    if request_type == 'execute_trade':
                        result = self._execute_trade_internal(request_data['data'])
                    elif request_type == 'add_trade':
                        result = self._add_trade_internal(request_data['data'])
                    elif request_type == 'get_status':
                        result = self._get_status_internal()
                    elif request_type == 'remove_trade':
                        result = self._remove_trade_internal(request_data['data'])
                    else:
                        result = {'success': False, 'error': 'Unknown request type'}
                    
                    response_queue.put(result)
                    
                except Exception as e:
                    error_result = {'success': False, 'error': str(e)}
                    response_queue.put(error_result)
                    self._log_error("REQUEST_PROCESSING_ERROR", "", str(e))
                
                finally:
                    self.is_processing = False
                    self.request_queue.task_done()
                    
            except:
                # Timeout or other error - continue processing
                continue
    
    def _queue_request(self, request_type: str, data: dict = None) -> dict:
        """Queue a request for sequential processing"""
        response_queue = Queue()
        request_data = {
            'type': request_type,
            'data': data,
            'response_queue': response_queue
        }
        
        self.request_queue.put(request_data)
        
        # Wait for response (with timeout)
        try:
            return response_queue.get(timeout=300)  # 5 minute timeout
        except:
            return {'success': False, 'error': 'Request timeout'}
    
    def _log_error(self, error_type: str, ticker: str, error_message: str, trade_data: dict = None):
        """Log errors to in-memory storage"""
        error_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error_type": error_type,
            "ticker": ticker,
            "error_message": error_message,
            "trade_data": trade_data
        }
        
        self.error_log.append(error_entry)
        
        # Keep only last 100 errors
        if len(self.error_log) > 100:
            self.error_log = self.error_log[-100:]
        
        print(f"🚨 Error logged: {error_type} - {error_message}")
    
    def _connect_to_ib(self, ticker: str = "UNKNOWN") -> bool:
        """Connect to IB TWS"""
        try:
            self.ib_wrapper = IBWrapper()
            self.ib_client = IBClient(self.ib_wrapper)
            
            self.ib_client.connect("127.0.0.1", 7497, clientId=1)
            
            # Start API thread
            api_thread = threading.Thread(target=self.ib_client.run)
            api_thread.daemon = True
            api_thread.start()
            
            # Wait for connection
            if not self.ib_wrapper.order_id_event.wait(timeout=5):
                error_msg = "Failed to receive next valid order ID from IB within 10 seconds"
                self._log_error("CONNECTION_TIMEOUT", ticker, error_msg)
                return False
                
            print("✅ Connected to IB TWS")
            return True
            
        except Exception as e:
            error_msg = f"Failed to connect to IB: {str(e)}"
            print(f"❌ {error_msg}")
            self._log_error("CONNECTION_FAILED", ticker, error_msg)
            return False
    
    def _disconnect_from_ib(self):
        """Disconnect from IB TWS"""
        if self.ib_client and self.ib_client.isConnected():
            self.ib_client.disconnect()
            print("✅ Disconnected from IB TWS")
        
        self.ib_wrapper = None
        self.ib_client = None
    
    def _wait_for_order_fill(self, order_id: int, expected_shares: float, timeout: int = 60) -> dict:
        """Wait for order to be filled with cancellation support"""
        self.ib_wrapper.order_events[order_id] = threading.Event()
        
        print(f"   ⏳ Waiting for order {order_id} to fill {expected_shares} shares...")
        
        start_time = time.time()
        last_filled = 0
        no_progress_time = 0
        
        while time.time() - start_time < timeout:
            if self.ib_wrapper.order_events[order_id].wait(timeout=5):
                order_status = self.ib_wrapper.order_fills.get(order_id, {})
                status = order_status.get('status', 'Unknown')
                filled_qty = float(order_status.get('filled', 0))
                remaining_qty = float(order_status.get('remaining', expected_shares))
                avg_price = float(order_status.get('avgFillPrice', 0))
                
                if filled_qty > last_filled:
                    last_filled = filled_qty
                    no_progress_time = 0
                    print(f"   📈 Progress: {filled_qty}/{expected_shares} shares filled at avg ${avg_price}")
                
                if status == 'Filled':
                    print(f"   ✅ Order {order_id} FULLY FILLED: {filled_qty} shares at ${avg_price}")
                    return {
                        'success': True,
                        'filled_shares': filled_qty,
                        'remaining_shares': 0,
                        'avg_price': avg_price,
                        'status': status,
                        'cancelled': False
                    }
                
                elif status == 'Cancelled':
                    print(f"   ❌ Order {order_id} CANCELLED: {filled_qty} shares filled, {remaining_qty} remaining")
                    return {
                        'success': filled_qty > 0,
                        'filled_shares': filled_qty,
                        'remaining_shares': remaining_qty,
                        'avg_price': avg_price,
                        'status': status,
                        'cancelled': True
                    }
                
                elif filled_qty > 0 and filled_qty < expected_shares:
                    if status in ['PartiallyFilled', 'Submitted']:
                        no_progress_time += 5
                        
                        if no_progress_time >= 30:
                            print(f"   ⚠️ No progress for 30s on partial fill. Considering cancellation...")
                            break
                        
                        print(f"   ⏳ Partial fill: {filled_qty}/{expected_shares} shares. Waiting for more...")
                        continue
                
                self.ib_wrapper.order_events[order_id].clear()
            else:
                no_progress_time += 5
        
        # Cancel order on timeout
        print(f"   ⏰ Order {order_id} timeout. Attempting to cancel...")
        
        try:
            self.ib_client.reqGlobalCancel()
            print(f"   📤 Cancellation request sent for order {order_id}")
            
            if self.ib_wrapper.order_events[order_id].wait(timeout=10):
                order_status = self.ib_wrapper.order_fills.get(order_id, {})
                filled_qty = float(order_status.get('filled', 0))
                remaining_qty = float(order_status.get('remaining', expected_shares - filled_qty))
                avg_price = float(order_status.get('avgFillPrice', 0))
                
                return {
                    'success': filled_qty > 0,
                    'filled_shares': filled_qty,
                    'remaining_shares': remaining_qty,
                    'avg_price': avg_price,
                    'status': 'Cancelled',
                    'cancelled': True
                }
        except Exception as e:
            print(f"   ❌ Failed to cancel order {order_id}: {str(e)}")
        
        # Fallback
        order_status = self.ib_wrapper.order_fills.get(order_id, {})
        filled_qty = float(order_status.get('filled', 0))
        remaining_qty = float(order_status.get('remaining', expected_shares - filled_qty))
        avg_price = float(order_status.get('avgFillPrice', 0))
        
        return {
            'success': filled_qty > 0,
            'filled_shares': filled_qty,
            'remaining_shares': remaining_qty,
            'avg_price': avg_price,
            'status': 'Timeout',
            'cancelled': False
        }
    
    def _create_stock_contract(self, ticker: str) -> Contract:
        """Create stock contract"""
        contract = Contract()
        contract.symbol = ticker
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract
    
    def _create_market_order(self, action: str, shares: float) -> Order:
        """Create market order"""
        order = Order()
        order.action = action
        order.totalQuantity = shares
        order.orderType = "MKT"
        return order
    
    def _create_stop_order(self, action: str, shares: float, stop_price: float) -> Order:
        """Create stop order"""
        order = Order()
        order.action = action
        order.totalQuantity = shares
        order.orderType = "STP"
        order.auxPrice = stop_price
        return order
    
    def _get_next_order_id(self) -> int:
        """Get next available order ID"""
        order_id = self.ib_wrapper.next_order_id
        self.ib_wrapper.next_order_id += 1
        return order_id
    
    def _execute_buy_order(self, trade: Trade) -> dict:
        """Execute buy order via IB API"""
        print(f"\n🔵 EXECUTING BUY ORDER:")
        print(f"   Ticker: {trade.ticker}")
        print(f"   Shares: {trade.shares}")
        print(f"   Risk Amount: ${trade.risk_amount}")
        
        try:
            contract = self._create_stock_contract(trade.ticker)
            order = self._create_market_order("BUY", trade.shares)
            order_id = self._get_next_order_id()
            
            self.ib_client.placeOrder(order_id, contract, order)
            print(f"   📤 BUY ORDER SUBMITTED (Order ID: {order_id})")
            
            fill_result = self._wait_for_order_fill(order_id, trade.shares, timeout=10)
            
            if fill_result['success']:
                filled_shares = float(fill_result['filled_shares'])
                avg_price = float(fill_result['avg_price'])
                
                if filled_shares == trade.shares:
                    print(f"   ✅ BUY ORDER FULLY COMPLETED: {filled_shares} shares at ${avg_price}")
                else:
                    print(f"   ⚠️ BUY ORDER PARTIALLY COMPLETED: {filled_shares}/{trade.shares} shares at ${avg_price}")
                
                return {
                    'success': True,
                    'filled_shares': filled_shares,
                    'avg_price': avg_price,
                    'full_fill': filled_shares == trade.shares
                }
            else:
                error_msg = f"Buy order {order_id} failed to fill any shares"
                print(f"   ❌ BUY ORDER FAILED: {error_msg}")
                self._log_error("BUY_ORDER_NO_FILL", trade.ticker, error_msg)
                return {
                    'success': False,
                    'filled_shares': 0,
                    'avg_price': 0,
                    'full_fill': False
                }
                
        except Exception as e:
            error_msg = f"Buy order failed: {str(e)}"
            print(f"   ❌ BUY ORDER FAILED: {str(e)}")
            self._log_error("BUY_ORDER_FAILED", trade.ticker, error_msg)
            return {
                'success': False,
                'filled_shares': 0,
                'avg_price': 0,
                'full_fill': False
            }
    
    def _execute_sell_stop_orders(self, trade: Trade, actual_shares_bought: float):
        """Execute sell stop orders based on actual shares bought"""
        print(f"\n🔴 SETTING SELL STOP ORDERS for {actual_shares_bought} shares:")
        
        if actual_shares_bought == 0:
            print("   ❌ No shares bought - skipping sell stop orders")
            return
        
        total_planned_shares = trade.shares
        scale_factor = float(actual_shares_bought) / total_planned_shares
        
        try:
            contract = self._create_stock_contract(trade.ticker)
            
            for i, stop in enumerate(trade.sell_stops, 1):
                try:
                    scaled_shares = math.floor(stop.shares * scale_factor)
                    
                    if scaled_shares == 0:
                        print(f"   ⚠️ Stop {i}: Skipping (scaled to 0 shares)")
                        continue
                    
                    order = self._create_stop_order("SELL", scaled_shares, stop.price)
                    order_id = self._get_next_order_id()
                    
                    self.ib_client.placeOrder(order_id, contract, order)
                    print(f"   Stop {i}: {scaled_shares} shares at ${stop.price} - Order ID: {order_id}")
                    
                    time.sleep(0.5)
                    
                except Exception as e:
                    error_msg = f"Sell stop order {i} failed: {str(e)}"
                    print(f"   ❌ SELL STOP ORDER {i} FAILED: {str(e)}")
                    self._log_error("SELL_STOP_ORDER_FAILED", trade.ticker, error_msg)
                    continue
            
            print(f"   ✅ SELL STOP ORDERS PLACED")
            
        except Exception as e:
            error_msg = f"Sell stop orders failed: {str(e)}"
            print(f"   ❌ SELL STOP ORDERS FAILED: {str(e)}")
            self._log_error("SELL_STOP_ORDERS_FAILED", trade.ticker, error_msg)
            raise
    
    def _find_trade_by_criteria(self, ticker: str, lower_price: float, higher_price: float) -> Optional[Trade]:
        """Find trade by criteria"""
        for trade in self.trades:
            if (trade.ticker.upper() == ticker.upper() and 
                abs(trade.lower_price_range - lower_price) < 0.001 and
                abs(trade.higher_price_range - higher_price) < 0.001):
                return trade
        return None
    
    def _validate_trade(self, trade: Trade) -> bool:
        """Validate trade data"""
        total_stop_shares = sum(stop.shares for stop in trade.sell_stops)
        if abs(total_stop_shares - trade.shares) > 0.001:
            print(f"ERROR: Sell stop shares ({total_stop_shares}) don't match total shares ({trade.shares})")
            return False
        
        if trade.risk_amount > self.available_risk:
            print(f"ERROR: Insufficient risk capital. Required: ${trade.risk_amount}, Available: ${self.available_risk}")
            return False
        
        return True
    
    def _execute_trade_internal(self, data: dict) -> dict:
        """Internal method to execute trade"""
        ticker = data['ticker']
        lower_price = data['lower_price']
        higher_price = data['higher_price']
        
        print(f"\n📊 Looking for trade: {ticker} (${lower_price} - ${higher_price})...")
        
        # Find the trade
        trade = self._find_trade_by_criteria(ticker, lower_price, higher_price)
        
        if trade is None:
            error_msg = f"No trade found for {ticker} with price range ${lower_price} - ${higher_price}"
            self._log_error("TRADE_NOT_FOUND", ticker, error_msg)
            print(f"❌ {error_msg}")
            return {'success': False, 'error': error_msg}
        
        print(f"✅ Found trade for {trade.ticker}")
        
        # Validate trade
        if not self._validate_trade(trade):
            error_msg = "Trade validation failed"
            print(f"❌ {error_msg}. Removing invalid trade.")
            self._log_error("TRADE_VALIDATION_FAILED", ticker, error_msg)
            self.trades.remove(trade)
            return {'success': False, 'error': error_msg}
        
        # Connect to IB
        if not self._connect_to_ib(ticker):
            error_msg = "Failed to connect to IB"
            self._log_error("CONNECTION_FAILED", ticker, error_msg)
            return {'success': False, 'error': error_msg}
        
        try:
            # Update last trade time when a trade is executed
            self.last_trade_time = time.time()
            
            # Remove trade from list and update risk
            self.trades.remove(trade)
            self.available_risk -= trade.risk_amount
            print(f"✅ Trade removed from queue. Available risk: ${self.available_risk}")
            
            # Execute buy order
            buy_result = self._execute_buy_order(trade)
            
            if not buy_result['success']:
                error_msg = "Buy order failed completely"
                print(f"❌ {error_msg}")
                self._log_error("BUY_ORDER_COMPLETE_FAILURE", ticker, error_msg)
                return {'success': False, 'error': error_msg}
            
            # Place sell stops
            self._execute_sell_stop_orders(trade, buy_result['filled_shares'])
            
            result = {
                'success': True,
                'ticker': trade.ticker,
                'filled_shares': buy_result['filled_shares'],
                'avg_price': buy_result['avg_price'],
                'full_fill': buy_result['full_fill']
            }
            
            print(f"\n🎉 Trade for {trade.ticker} completed!")
            return result
            
        except Exception as e:
            error_msg = f"Error processing trade: {str(e)}"
            print(f"❌ {error_msg}")
            self._log_error("TRADE_PROCESSING_ERROR", ticker, error_msg)
            return {'success': False, 'error': error_msg}
            
        finally:
            self._disconnect_from_ib()
    
    def _add_trade_internal(self, trade_data: dict) -> dict:
        """Internal method to add trade"""
        try:
            
            sell_stops = []
            for stop in trade_data.get('sell_stops', []):
                sell_stops.append(SellStopOrder(
                    price=float(stop['price']),
                    shares=float(stop['shares'])
                ))
            
            trade = Trade(
                ticker=trade_data['ticker'],
                shares=float(trade_data['shares']),
                risk_amount=float(trade_data['risk_amount']),
                lower_price_range=float(trade_data['lower_price_range']),
                higher_price_range=float(trade_data['higher_price_range']),
                sell_stops=sell_stops
            )
            
            if not self._validate_trade(trade):
                return {'success': False, 'error': 'Trade validation failed'}
            
            self.trades.append(trade)
            
            return {
                'success': True,
                'trade_id': trade.trade_id,
                'message': f'Trade added for {trade.ticker}'
            }
            
        except Exception as e:
            self._log_error("ADD_TRADE", trade_data.get('ticker', 'unknown'), str(e), trade_data)
            return {'success': False, 'error': str(e)}
    
    def _get_status_internal(self) -> dict:
        """Internal method to get server status"""
        trades_data = []
        for trade in self.trades:
            trade_dict = asdict(trade)
            trades_data.append(trade_dict)
        
        # Calculate server uptime
        uptime_seconds = time.time() - self.server_start_time
        uptime_hours = int(uptime_seconds // 3600)
        uptime_minutes = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{uptime_hours}h {uptime_minutes}m"
        
        # Format last trade time
        last_trade_formatted = None
        if self.last_trade_time:
            last_trade_formatted = datetime.fromtimestamp(self.last_trade_time).strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            'success': True,
            'available_risk': self.available_risk,
            'active_trades': len(self.trades),  # Changed from 'trades_count'
            'server_uptime': uptime_str,        # Added server uptime
            'last_trade_time': last_trade_formatted,  # Added last trade time
            'trades': trades_data,
            'is_processing': self.is_processing,
            'error_count': len(self.error_log)
        }

    
    # Public API methods (these queue requests)
    def execute_trade(self, ticker: str, lower_price: float, higher_price: float) -> dict:
        """Execute a specific trade"""
        return self._queue_request('execute_trade', {
            'ticker': ticker,
            'lower_price': lower_price,
            'higher_price': higher_price
        })
    
    def add_trade(self, trade_data: dict) -> dict:
        """Add a new trade"""
        return self._queue_request('add_trade', trade_data)
    
    def get_status(self) -> dict:
        """Get server status"""
        return self._queue_request('get_status')
    
    def get_errors(self) -> List[Dict]:
        """Get error log (direct access, no queuing needed)"""
        return self.error_log.copy()
    
    def update_risk_amount(self, new_amount: float) -> dict:
        """Update available risk amount (direct access)"""
        self.available_risk = new_amount
        return {'success': True, 'available_risk': self.available_risk}
    
    def shutdown(self):
        """Shutdown the server"""
        self.server_running = False
        self.request_queue.put(None)  # Signal shutdown

# Flask app
app = Flask(__name__)
trading_server = StockTradingServer()
flask_cors.CORS(app)  # Enable CORS for all routes

@app.route('/execute_trade', methods=['POST'])
def execute_trade():
    """Execute a specific trade"""
    data = request.json
    
    if not data or 'ticker' not in data or 'lower_price' not in data or 'higher_price' not in data:
        return jsonify({'success': False, 'error': 'Missing required fields: ticker, lower_price, higher_price'}), 400
    
    try:
        ticker = data['ticker']
        lower_price = float(data['lower_price'])
        higher_price = float(data['higher_price'])
        
        if lower_price >= higher_price:
            return jsonify({'success': False, 'error': 'Lower price must be less than higher price'}), 400
        
        result = trading_server.execute_trade(ticker, lower_price, higher_price)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid price values'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/add_trade', methods=['POST'])
def add_trade():
    """Add a new trade"""
    data = request.json
    
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
    
    required_fields = ['ticker', 'shares', 'risk_amount', 'lower_price_range', 'higher_price_range', 'sell_stops']
    for field in required_fields:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    
    result = trading_server.add_trade(data)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400
    
@app.route('/remove_trade', methods=['POST'])
def remove_trade():
    """Remove a trade by ID or criteria"""
    data = request.json
    
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
    
    trade_id = data.get('trade_id')
    ticker = data.get('ticker')
    lower_price = data.get('lower_price')
    higher_price = data.get('higher_price')
    
    # Must provide either trade_id or all criteria
    if not trade_id and not (ticker and lower_price is not None and higher_price is not None):
        return jsonify({
            'success': False, 
            'error': 'Must provide either trade_id or all criteria (ticker, lower_price, higher_price)'
        }), 400
    
    try:
        if lower_price is not None:
            lower_price = float(lower_price)
        if higher_price is not None:
            higher_price = float(higher_price)
            
        result = trading_server.remove_trade(trade_id, ticker, lower_price, higher_price)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 404
            
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid price values'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/status', methods=['GET'])
def get_status():
    """Get server status"""
    result = trading_server.get_status()
    return jsonify(result), 200

@app.route('/errors', methods=['GET'])
def get_errors():
    """Get error log"""
    errors = trading_server.get_errors()
    return jsonify({'success': True, 'errors': errors}), 200

@app.route('/update_risk', methods=['POST'])
def update_risk():
    """Update available risk amount"""
    data = request.json
    
    if not data or 'amount' not in data:
        return jsonify({'success': False, 'error': 'Missing required field: amount'}), 400
    
    try:
        amount = float(data['amount'])
        result = trading_server.update_risk_amount(amount)
        return jsonify(result), 200
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid amount value'}), 400

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
    }), 200

if __name__ == '__main__':
    print("🚀 Starting Stock Trading Server...")
    print("📡 Server will process requests sequentially")
    print("🔗 IBKR connections are managed automatically")
    print("\nAPI Endpoints:")
    print("  POST /execute_trade - Execute a specific trade")
    print("  POST /add_trade - Add a new trade")
    print("  POST /remove_trade - Remove a trade")  # ADD THIS LINE
    print("  GET /status - Get server status")
    print("  GET /errors - Get error log")
    print("  POST /update_risk - Update available risk amount")
    print("  GET /health - Health check")

    
    try:
        app.run(host='0.0.0.0', port=5002, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down server...")
        trading_server.shutdown()
        print("✅ Server shutdown complete")