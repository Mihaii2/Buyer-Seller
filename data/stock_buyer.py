import json
import os
import time
import msvcrt
import sys
from typing import List, Dict, Any
from dataclasses import dataclass
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import threading
import atexit
import math

"""
Sample JSON structure for trades.json:
[
    {
        "ticker": "PPIH",
        "shares": 1,
        "risk_amount": 50.0,
        "lower_price_range": 145.0,
        "higher_price_range": 155.0,
        "sell_stops": [
            {"price": 150.0, "shares": 5},
            {"price": 145.0, "shares": 3},
            {"price": 140.0, "shares": 2}
        ]
    }
]
"""

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
    
class GlobalLock:
    """Global lock to ensure only one instance of the script runs at a time"""
    
    def __init__(self, lock_file: str = 'stock_buyer_global.lock'):
        self.lock_file = lock_file
        self.lock_handle = None
        self.acquired = False
    
    def acquire(self, timeout: int = 10) -> bool:
        """Acquire the global lock with timeout"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Try to create and open the lock file exclusively
                self.lock_handle = open(self.lock_file, 'w')
                
                # Try to lock it (Windows-specific)
                try:
                    msvcrt.locking(self.lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    self.acquired = True
                    
                    # Write process info to lock file
                    self.lock_handle.write(f"PID: {os.getpid()}\n")
                    self.lock_handle.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    self.lock_handle.flush()
                    
                    # Register cleanup function
                    atexit.register(self.release)
                    
                    return True
                    
                except OSError:
                    # Lock failed, file is locked by another process
                    self.lock_handle.close()
                    self.lock_handle = None
                    
            except (OSError, IOError):
                # File creation failed or other error
                if self.lock_handle:
                    self.lock_handle.close()
                    self.lock_handle = None
            
            # Wait a bit before retrying
            time.sleep(0.1)
        
        return False
    
    def release(self):
        """Release the global lock"""
        if self.acquired and self.lock_handle:
            try:
                msvcrt.locking(self.lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                self.lock_handle.close()
                
                # Remove lock file
                if os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
                    
            except:
                pass  # Ignore errors during cleanup
            
            self.acquired = False
            self.lock_handle = None
    
    def __enter__(self):
        if not self.acquire():
            raise Exception("Could not acquire global lock - another instance is already running")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class FileLocker:
    """Windows-specific file locking using msvcrt"""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file_handle = None
    
    def __enter__(self):
        # Open file in read+write mode, create if doesn't exist
        self.file_handle = open(self.file_path, 'r+', encoding='utf-8')
        
        # Try to lock the file (Windows-specific)
        max_retries = 10
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Lock the entire file
                msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_LOCK, 1)
                break
            except OSError:
                retry_count += 1
                time.sleep(0.1)
                if retry_count >= max_retries:
                    raise Exception(f"Could not lock file {self.file_path} after {max_retries} attempts")
        
        return self.file_handle
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file_handle:
            # Unlock the file
            try:
                msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except:
                pass
            self.file_handle.close()
            
class IBWrapper(EWrapper):
    def __init__(self):
        EWrapper.__init__(self)
        self.next_order_id = None
        self.order_id_event = threading.Event()
        self.order_fills = {}  # Track order fills
        self.order_events = {}  # Track order completion events
        
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self.order_id_event.set()
        
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        print(f"Order {orderId}: Status={status}, Filled={filled}, Remaining={remaining}, AvgPrice={avgFillPrice}")
        
        # Store order status
        self.order_fills[orderId] = {
            'status': status,
            'filled': filled,
            'remaining': remaining,
            'avgFillPrice': avgFillPrice
        }
        
        # Signal completion for filled or cancelled orders
        if status in ['Filled', 'Cancelled']:
            if orderId in self.order_events:
                self.order_events[orderId].set()
        
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"Error {reqId}: {errorCode} - {errorString}")
        
        # Signal error for order if it exists
        if reqId in self.order_events:
            self.order_events[reqId].set()

class IBClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)
        self.wrapper = wrapper

class StockBuyer:
    def __init__(self, trades_file: str = 'trades.json', risk_file: str = 'risk_amount.json'):
        self.trades_file = trades_file
        self.risk_file = risk_file
        
        # Initialize IB API
        self.ib_wrapper = IBWrapper()
        self.ib_client = IBClient(self.ib_wrapper)
        
        # Create files if they don't exist
        self._initialize_files()
    
    def _initialize_files(self):
        """Initialize files with default structure if they don't exist"""
        if not os.path.exists(self.trades_file):
            with open(self.trades_file, 'w') as f:
                json.dump([], f)
        
        if not os.path.exists(self.risk_file):
            with open(self.risk_file, 'w') as f:
                json.dump({"available_risk": 10000.0}, f)
                
    def _wait_for_order_fill(self, order_id: int, expected_shares: float, timeout: int = 60) -> dict:
        """
        Wait for order to be filled (fully or partially) with cancellation support
        
        Returns:
            dict: {
                'success': bool,
                'filled_shares': float,
                'remaining_shares': float,
                'avg_price': float,
                'status': str,
                'cancelled': bool
            }
        """
        # Create event for this order
        self.ib_wrapper.order_events[order_id] = threading.Event()
        
        print(f"   ⏳ Waiting for order {order_id} to fill {expected_shares} shares (timeout: {timeout}s)...")
        
        start_time = time.time()
        last_filled = 0
        no_progress_time = 0
        
        while time.time() - start_time < timeout:
            # Check if order event was triggered
            if self.ib_wrapper.order_events[order_id].wait(timeout=5):  # Check every 5 seconds
                order_status = self.ib_wrapper.order_fills.get(order_id, {})
                status = order_status.get('status', 'Unknown')
                filled_qty = order_status.get('filled', 0)
                remaining_qty = order_status.get('remaining', expected_shares)
                avg_price = order_status.get('avgFillPrice', 0)
                
                # Check if we made progress
                if filled_qty > last_filled:
                    last_filled = filled_qty
                    no_progress_time = 0  # Reset no progress timer
                    print(f"   📈 Progress: {filled_qty}/{expected_shares} shares filled at avg ${avg_price}")
                
                # Order is completely filled
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
                
                # Order was cancelled
                elif status == 'Cancelled':
                    print(f"   ❌ Order {order_id} CANCELLED: {filled_qty} shares filled, {remaining_qty} remaining")
                    return {
                        'success': filled_qty > 0,  # Partial success if some shares were filled
                        'filled_shares': filled_qty,
                        'remaining_shares': remaining_qty,
                        'avg_price': avg_price,
                        'status': status,
                        'cancelled': True
                    }
                
                # Partial fill - decide if we should wait longer
                elif filled_qty > 0 and filled_qty < expected_shares:
                    # For market orders, we typically want to wait longer for partial fills
                    # as they often get filled in chunks
                    if status in ['PartiallyFilled', 'Submitted']:
                        no_progress_time += 5
                        
                        # If no progress for 30 seconds on a partial fill, consider cancelling
                        if no_progress_time >= 30:
                            print(f"   ⚠️ No progress for 30s on partial fill. Considering cancellation...")
                            break
                        
                        print(f"   ⏳ Partial fill: {filled_qty}/{expected_shares} shares. Waiting for more...")
                        continue
                
                # Reset the event for next iteration
                self.ib_wrapper.order_events[order_id].clear()
            
            else:
                # No event triggered, increment no progress time
                no_progress_time += 5
        
        # Timeout reached or no progress - cancel the order
        print(f"   ⏰ Order {order_id} timeout or stalled. Attempting to cancel...")
        
        # Cancel the order
        try:
            self.ib_client.cancelOrder(order_id, "")
            print(f"   📤 Cancellation request sent for order {order_id}")
            
            # Wait a bit for cancellation confirmation
            if self.ib_wrapper.order_events[order_id].wait(timeout=10):
                order_status = self.ib_wrapper.order_fills.get(order_id, {})
                filled_qty = order_status.get('filled', 0)
                remaining_qty = order_status.get('remaining', expected_shares - filled_qty)
                avg_price = order_status.get('avgFillPrice', 0)
                
                print(f"   ✅ Order {order_id} cancelled. Final: {filled_qty} filled, {remaining_qty} remaining")
                
                return {
                    'success': filled_qty > 0,  # Success if any shares were filled
                    'filled_shares': filled_qty,
                    'remaining_shares': remaining_qty,
                    'avg_price': avg_price,
                    'status': 'Cancelled',
                    'cancelled': True
                }
            else:
                print(f"   ❌ Order {order_id} cancellation timeout")
                
        except Exception as e:
            print(f"   ❌ Failed to cancel order {order_id}: {str(e)}")
        
        # Fallback - return whatever we have
        order_status = self.ib_wrapper.order_fills.get(order_id, {})
        filled_qty = order_status.get('filled', 0)
        remaining_qty = order_status.get('remaining', expected_shares - filled_qty)
        avg_price = order_status.get('avgFillPrice', 0)
        
        return {
            'success': filled_qty > 0,
            'filled_shares': filled_qty,
            'remaining_shares': remaining_qty,
            'avg_price': avg_price,
            'status': 'Timeout',
            'cancelled': False
        }
    
    def _connect_to_ib(self, ticker: str = "UNKNOWN"):
        """Connect to IB TWS with error logging"""
        try:
            self.ib_client.connect("127.0.0.1", 7496, clientId=1) # 7497 for TWS, 4002 for Gateway
            
            # Start the API thread
            api_thread = threading.Thread(target=self.ib_client.run)
            api_thread.daemon = True
            api_thread.start()
            
            # Wait for next valid order ID
            if not self.ib_wrapper.order_id_event.wait(timeout=10):
                error_msg = "Failed to receive next valid order ID from IB within 10 seconds"
                self._log_error("CONNECTION_TIMEOUT", ticker, error_msg)
                raise Exception(error_msg)
                
            print("✅ Connected to IB TWS")
            return True
            
        except Exception as e:
            error_msg = f"Failed to connect to IB: {str(e)}"
            print(f"❌ Failed to connect to IB: {str(e)}")
            self._log_error("CONNECTION_FAILED", ticker, error_msg)
            return False

    def _disconnect_from_ib(self):
        """Disconnect from IB TWS"""
        if self.ib_client.isConnected():
            self.ib_client.disconnect()
            print("✅ Disconnected from IB TWS")

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
    
    def _log_error(self, error_type: str, ticker: str, error_message: str, trade_data: dict = None):
        """Log errors to a separate error file"""
        error_file = 'trade_errors.json'
        
        error_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error_type": error_type,
            "ticker": ticker,
            "error_message": error_message,
            "trade_data": trade_data
        }
        
        try:
            # Read existing errors
            if os.path.exists(error_file):
                with open(error_file, 'r') as f:
                    errors = json.load(f)
            else:
                errors = []
            
            # Add new error
            errors.append(error_entry)
            
            # Write back to file
            with open(error_file, 'w') as f:
                json.dump(errors, f, indent=2)
            
            print(f"🚨 Error logged to {error_file}")
            
        except Exception as e:
            print(f"❌ Failed to log error: {str(e)}")
    
    def _read_trades(self) -> List[Dict[str, Any]]:
        """Read trades from file"""
        try:
            with open(self.trades_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def _write_trades(self, trades: List[Dict[str, Any]]):
        """Write trades to file"""
        with open(self.trades_file, 'w') as f:
            json.dump(trades, f, indent=2)
    
    def _read_risk_amount(self) -> float:
        """Read available risk amount from file"""
        try:
            with open(self.risk_file, 'r') as f:
                data = json.load(f)
                return data.get('available_risk', 0.0)
        except (json.JSONDecodeError, FileNotFoundError):
            return 0.0
    
    def _write_risk_amount(self, amount: float):
        """Write available risk amount to file"""
        with open(self.risk_file, 'w') as f:
            json.dump({"available_risk": amount}, f, indent=2)
    
    def _parse_trade(self, trade_data: Dict[str, Any]) -> Trade:
        """Parse trade data from JSON format"""
        sell_stops = []
        for stop in trade_data.get('sell_stops', []):
            sell_stops.append(SellStopOrder(
                price=float(stop['price']),
                shares=float(stop['shares'])
            ))
        
        return Trade(
            ticker=trade_data['ticker'],
            shares=float(trade_data['shares']),
            risk_amount=float(trade_data['risk_amount']),
            lower_price_range=float(trade_data['lower_price_range']),
            higher_price_range=float(trade_data['higher_price_range']),
            sell_stops=sell_stops
        )
    
    def _validate_trade(self, trade: Trade) -> bool:
        """Validate trade data"""
        # Check if sell stop shares sum matches total shares
        total_stop_shares = sum(stop.shares for stop in trade.sell_stops)
        if abs(total_stop_shares - trade.shares) > 0.001:  # Allow for floating point precision
            print(f"ERROR: Sell stop shares ({total_stop_shares}) don't match total shares ({trade.shares})")
            return False
        
        # Check if we have enough risk capital
        with FileLocker(self.risk_file) as f:
            f.seek(0)
            risk_data = json.load(f)
            available_risk = risk_data.get('available_risk', 0.0)
            
            if trade.risk_amount > available_risk:
                print(f"ERROR: Insufficient risk capital. Required: ${trade.risk_amount}, Available: ${available_risk}")
                return False
        
        return True
    
    def _execute_buy_order(self, trade: Trade) -> dict:
        
        """Execute buy order via IB API with partial fill handling"""
        print(f"\n🔵 EXECUTING BUY ORDER:")
        print(f"   Ticker: {trade.ticker}")
        print(f"   Shares: {trade.shares}")
        print(f"   Risk Amount: ${trade.risk_amount}")
        print(f"   Price Range: ${trade.lower_price_range} - ${trade.higher_price_range}")
        
        try:
            contract = self._create_stock_contract(trade.ticker)
            order = self._create_market_order("BUY", trade.shares)
            order_id = self._get_next_order_id()
            
            self.ib_client.placeOrder(order_id, contract, order)
            print(f"   📤 BUY ORDER SUBMITTED (Order ID: {order_id})")
            
            # Wait for order fill with longer timeout for market orders
            # Market orders typically fill faster but we allow time for partial fills
            fill_result = self._wait_for_order_fill(order_id, trade.shares, timeout=120)
            
            if fill_result['success']:
                filled_shares = fill_result['filled_shares']
                avg_price = fill_result['avg_price']
                
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
                self._log_error("BUY_ORDER_NO_FILL", trade.ticker, error_msg, {
                    "order_id": order_id,
                    "expected_shares": trade.shares,
                    "fill_result": fill_result
                })
                return {
                    'success': False,
                    'filled_shares': 0,
                    'avg_price': 0,
                    'full_fill': False
                }
                
        except Exception as e:
            error_msg = f"Buy order failed: {str(e)}"
            print(f"   ❌ BUY ORDER FAILED: {str(e)}")
            self._log_error("BUY_ORDER_FAILED", trade.ticker, error_msg, {
                "shares": trade.shares,
                "risk_amount": trade.risk_amount,
                "price_range": f"{trade.lower_price_range}-{trade.higher_price_range}"
            })
            return {
                'success': False,
                'filled_shares': 0,
                'avg_price': 0,
                'full_fill': False
            }

    
    def _execute_sell_stop_orders(self, trade: Trade, actual_shares_bought: float):
        
        """Execute sell stop orders based on actual shares bought (handles partial fills)"""
        print(f"\n🔴 SETTING SELL STOP ORDERS for {actual_shares_bought} shares:")
        
        if actual_shares_bought == 0:
            print("   ❌ No shares bought - skipping sell stop orders")
            return
        
        # Calculate proportional sell stops based on actual shares bought
        total_planned_shares = trade.shares
        scale_factor = actual_shares_bought / total_planned_shares
        
        try:
            contract = self._create_stock_contract(trade.ticker)
            
            for i, stop in enumerate(trade.sell_stops, 1):
                try:
                    # Scale the stop order size based on actual shares bought
                    scaled_shares = math.floor(stop.shares * scale_factor)  # Round to whole shares
                    
                    # Skip if scaled shares is 0
                    if scaled_shares == 0:
                        print(f"   ⚠️ Stop {i}: Skipping (scaled to 0 shares)")
                        continue
                    
                    order = self._create_stop_order("SELL", scaled_shares, stop.price)
                    order_id = self._get_next_order_id()
                    
                    self.ib_client.placeOrder(order_id, contract, order)
                    print(f"   Stop {i}: {scaled_shares} shares at ${stop.price} (scaled from {stop.shares}) - Order ID: {order_id}")
                    
                    # Small delay between orders
                    time.sleep(0.5)
                    
                except Exception as e:
                    error_msg = f"Sell stop order {i} failed: {str(e)}"
                    print(f"   ❌ SELL STOP ORDER {i} FAILED: {str(e)}")
                    self._log_error("SELL_STOP_ORDER_FAILED", trade.ticker, error_msg, {
                        "stop_number": i,
                        "original_shares": stop.shares,
                        "scaled_shares": scaled_shares,
                        "price": stop.price
                    })
                    # Continue with other stop orders even if one fails
                    continue
            
            print(f"   ✅ SELL STOP ORDERS PLACED (scaled for {actual_shares_bought} shares)")
            
        except Exception as e:
            error_msg = f"Sell stop orders failed: {str(e)}"
            print(f"   ❌ SELL STOP ORDERS FAILED: {str(e)}")
            self._log_error("SELL_STOP_ORDERS_FAILED", trade.ticker, error_msg, {
                "actual_shares_bought": actual_shares_bought,
                "total_stops": len(trade.sell_stops)
            })
            raise

    
    def _remove_processed_trade(self, trade_index: int):
        """Remove processed trade from file with file locking"""
        with FileLocker(self.trades_file) as f:
            f.seek(0)
            trades = json.load(f)
            
            if 0 <= trade_index < len(trades):
                removed_trade = trades.pop(trade_index)
                
                # Write back the updated trades
                f.seek(0)
                f.truncate()
                json.dump(trades, f, indent=2)
                
                return removed_trade
            else:
                raise IndexError(f"Trade index {trade_index} out of range")
    
    def _update_risk_amount(self, risk_to_subtract: float):
        """Update risk amount with file locking"""
        with FileLocker(self.risk_file) as f:
            f.seek(0)
            risk_data = json.load(f)
            
            current_risk = risk_data.get('available_risk', 0.0)
            new_risk = current_risk - risk_to_subtract
            
            risk_data['available_risk'] = new_risk
            
            # Write back the updated risk amount
            f.seek(0)
            f.truncate()
            json.dump(risk_data, f, indent=2)
            
            print(f"💰 Risk amount updated: ${current_risk} → ${new_risk}")
    
    def find_trade_by_criteria(self, ticker: str, lower_price: float, higher_price: float) -> tuple:
        """Find trade by ticker and price range criteria"""
        with FileLocker(self.trades_file) as f:
            f.seek(0)
            trades = json.load(f)
            
            for index, trade_data in enumerate(trades):
                if (trade_data['ticker'].upper() == ticker.upper() and 
                    abs(trade_data['lower_price_range'] - lower_price) < 0.001 and
                    abs(trade_data['higher_price_range'] - higher_price) < 0.001):
                    return index, trade_data
            
            return None, None
            
    def process_specific_trade(self, ticker: str, lower_price: float, higher_price: float) -> bool:
        """Process a specific trade with improved partial fill handling"""
        print(f"\n📊 Looking for trade: {ticker} (${lower_price} - ${higher_price})...")
        
        trade_index = None
        trade_data = None
        
        try:
            # Find the trade
            trade_index, trade_data = self.find_trade_by_criteria(ticker, lower_price, higher_price)
            
            if trade_data is None:
                error_msg = f"No trade found for {ticker} with price range ${lower_price} - ${higher_price}"
                print(f"❌ {error_msg}")
                self._log_error("TRADE_NOT_FOUND", ticker, error_msg)
                return False
            
            trade = self._parse_trade(trade_data)
            print(f"✅ Found trade for {trade.ticker}")
            
            # Validate trade
            if not self._validate_trade(trade):
                error_msg = "Trade validation failed"
                print(f"❌ {error_msg}. Removing invalid trade.")
                self._log_error("TRADE_VALIDATION_FAILED", ticker, error_msg, trade_data)
                self._remove_processed_trade(trade_index)
                return False
            
            # ###############################3Connect to IB
            if not self._connect_to_ib(ticker):
                error_msg = "Failed to connect to IB"
                self._log_error("CONNECTION_FAILED", ticker, error_msg, trade_data)
                return False
            
            # Remove trade from file first (atomic operation)
            self._remove_processed_trade(trade_index)
            print("✅ Trade removed from queue")
            
            # Update risk amount
            self._update_risk_amount(trade.risk_amount)
            
            # Execute the buy order with partial fill handling
            buy_result = self._execute_buy_order(trade)
            
            if not buy_result['success']:
                error_msg = "Buy order failed completely"
                print(f"❌ {error_msg} - No sell stop orders will be placed")
                self._log_error("BUY_ORDER_COMPLETE_FAILURE", ticker, error_msg, trade_data)
                return False
            
            # Check if we got a partial fill
            if not buy_result['full_fill']:
                filled_shares = buy_result['filled_shares']
                print(f"⚠️ Partial fill: {filled_shares}/{trade.shares} shares bought")
                print(f"   Will scale sell stop orders proportionally")
            
            # Place sell stops based on actual shares bought
            self._execute_sell_stop_orders(trade, buy_result['filled_shares'])
            
            print(f"\n🎉 Trade for {trade.ticker} completed!")
            if buy_result['full_fill']:
                print(f"   ✅ Full fill: {buy_result['filled_shares']} shares at ${buy_result['avg_price']}")
            else:
                print(f"   ⚠️ Partial fill: {buy_result['filled_shares']} shares at ${buy_result['avg_price']}")
            
            return True
            
        except Exception as e:
            error_msg = f"Error processing trade: {str(e)}"
            print(f"❌ {error_msg}")
            self._log_error("TRADE_PROCESSING_ERROR", ticker, error_msg, trade_data)
            return False
            
        ########### 
        finally:
            ############################ Always disconnect
            self._disconnect_from_ib()
        
def main():
    print("=== Stock Buyer Script ===")
    
    # Try to acquire global lock
    try:
        with GlobalLock() as lock:
            print("✅ Global lock acquired - script is running exclusively")
            buyer = StockBuyer()
            
            # Command line argument handling
            if len(sys.argv) == 4:
                try:
                    ticker = sys.argv[1]
                    lower_price = float(sys.argv[2])
                    higher_price = float(sys.argv[3])
                    
                    if lower_price >= higher_price:
                        print("ERROR: Lower price must be less than higher price")
                        return
                    
                    success = buyer.process_specific_trade(ticker, lower_price, higher_price)
                    if not success:
                        sys.exit(1)
                        
                except ValueError:
                    print("ERROR: Invalid price values. Please provide numeric values.")
                    print("Usage: python stock_buyer.py TICKER LOWER_PRICE HIGHER_PRICE")
                    sys.exit(1)
                return
            
            # Show usage if no valid arguments provided
            print("Usage:")
            print("  python stock_buyer.py TICKER LOWER_PRICE HIGHER_PRICE  - Process specific trade")
            print("\nExample:")
            print("  python stock_buyer.py AAPL 145.0 155.0")
            
    except Exception as e:
        if "another instance is already running" in str(e):
            print("❌ Another instance of the script is already running. Please wait for it to complete.")
            
            # Optionally, show info about running instance
            lock_file = 'stock_buyer_global.lock'
            if os.path.exists(lock_file):
                try:
                    with open(lock_file, 'r') as f:
                        print("Running instance info:")
                        print(f.read().strip())
                except:
                    pass
            
            sys.exit(1)
        else:
            print(f"❌ Error: {str(e)}")
            sys.exit(1)

if __name__ == "__main__":
    main()