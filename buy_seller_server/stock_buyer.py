import json
import time
import threading
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from flask import Flask, request, jsonify
import math
from queue import Queue
import uuid
import flask_cors
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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

class IBWebAPI:
    def __init__(self, base_url="https://localhost:5050/v1/api"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.verify = False
        self.session.timeout = 30

    def get_contract_details(self, conid):
        """Get contract details for a given conid"""
        try:
            url = f"{self.base_url}/iserver/secdef/info?conid={conid}"
            response = self.session.get(url, timeout=30)
            if response.status_code == 200:
                return response.json()
            print(f"❌ Failed to get contract details for conid {conid}: {response.status_code}, {response.text}")
            return None
        except Exception as e:
            print(f"❌ Contract details error: {str(e)}")
            return None
        
    def is_connected(self):
        try:
            response = self.session.get(f"{self.base_url}/iserver/auth/status", timeout=10)
            return response.status_code == 200 and response.json().get('authenticated', False)
        except:
            return False
        
    def get_accounts(self):
        """Get account information"""
        try:
            response = self.session.get(f"{self.base_url}/iserver/accounts", timeout=10)
            print(f"   📊 Accounts response: {response.status_code}")
            if response.status_code == 200:
                print(f"   📊 Accounts: {response.json()}")
            else:
                print(f"   📊 Accounts error: {response.text}")
            return response
        except Exception as e:
            print(f"   ❌ Get accounts error: {str(e)}")
            return None
    
    def place_order(self, conid, order_data):
        """Place order via Web API"""
        # First get the account ID
        accounts_response = self.session.get(f"{self.base_url}/iserver/accounts", timeout=10)
        if accounts_response.status_code != 200:
            print(f"   ❌ Failed to get accounts: {accounts_response.status_code}")
            return accounts_response
        
        accounts_data = accounts_response.json()
        account_id = accounts_data.get('selectedAccount')
        if not account_id:
            print(f"   ❌ No selected account found")
            return accounts_response
        
        url = f"{self.base_url}/iserver/account/{account_id}/orders"
        payload = {
            "orders": [{
                "acctId": account_id,
                "conid": int(conid),  # Ensure it's an integer
                "orderType": order_data["orderType"],
                "side": order_data["side"],
                "quantity": order_data["quantity"],
                "tif": "DAY"  # Time in force is usually required
            }]
        }
        
        # Only add price fields if they exist
        if order_data.get("price") is not None:
            payload["orders"][0]["price"] = order_data["price"]
        if order_data.get("auxPrice") is not None:
            payload["orders"][0]["auxPrice"] = order_data["auxPrice"]
        
        print(f"   📤 Sending order to: {url}")
        print(f"   📋 Payload: {json.dumps(payload, indent=2)}")
        
        try:
            response = self.session.post(url, json=payload, timeout=30)
            print(f"   📥 Response status: {response.status_code}")
            if response.status_code != 200:
                print(f"   📥 Response headers: {dict(response.headers)}")
                print(f"   📥 Response text: {response.text}")
            return response
        except Exception as e:
            print(f"   ❌ Request exception: {str(e)}")
            raise
    
    def get_contract_id(self, symbol):
        """Get contract ID for a symbol"""
        url = f"{self.base_url}/iserver/secdef/search"
        payload = {"symbol": symbol}
        response = self.session.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                return data[0].get("conid")
        return None
    
    def get_order_status(self, order_id=None):
        """Get order status"""
        url = f"{self.base_url}/iserver/account/orders"
        if order_id:
            url += f"/{order_id}"
        return self.session.get(url, timeout=30)
    
    def cancel_order(self, order_id):
        """Cancel an order with proper account context"""
        try:
            # Optionally include allocationId if present in order details
            order_status = self.get_order_status(order_id)
            if order_status.status_code == 200:
                try:
                    order_data = order_status.json()
                    if isinstance(order_data, list) and order_data:
                        order_data = order_data[0]
                    allocation_id = order_data.get("allocationId")
                    if allocation_id:
                        payload['allocationId'] = allocation_id
                        print(f"📋 Using allocationId from order: {allocation_id}")
                except Exception:
                    pass
            
            # Get account information to retrieve allocationId or account context
            accounts_response = self.session.get(f"{self.base_url}/iserver/accounts", timeout=10)
            if accounts_response.status_code != 200:
                print(f"❌ Failed to get accounts for cancellation: {accounts_response.status_code}, {accounts_response.text}")
                return accounts_response

            accounts_data = accounts_response.json()
            account_id = accounts_data.get('selectedAccount')
            if not account_id:
                print(f"❌ No selected account found for cancellation")
                return accounts_response

            # Construct the cancellation URL
            url = f"{self.base_url}/iserver/account/{account_id}/order/{order_id}"

            
            # Optionally include allocationId if required
            payload = {}
            if 'allocationProfile' in accounts_data:
                allocation_id = accounts_data.get('allocationProfile', {}).get('id')
                if allocation_id:
                    payload['allocationId'] = allocation_id
                    print(f"📋 Using allocationId: {allocation_id}")

            print(f"📤 Sending cancel request to: {url}")
            print(f"📋 Cancel payload: {json.dumps(payload, indent=2)}")
            
            response = self.session.delete(url, json=payload, timeout=30)
            print(f"📥 Cancel response status: {response.status_code}, Body: {response.text}")
            
            if response.status_code in [200, 201]:
                return response
            else:
                print(f"❌ Cancel failed: {response.status_code}, {response.text}")
                return response

        except Exception as e:
            print(f"❌ Cancel order error: {str(e)}")
            return {'status_code': 500, 'text': str(e)}

class StockTradingServer:
    def __init__(self):
        # In-memory storage
        self.trades: List[Trade] = []
        self.available_risk: float = 0.0
        self.error_log: List[Dict] = []
        
        self.server_start_time = time.time()
        self.last_trade_time = None
        
        # Threading for sequential processing
        self.request_queue = Queue()
        self.processing_thread = None
        self.is_processing = False
        self.server_running = True
        
        # IBKR Web API connection
        self.ib_api = None
        
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
        """Connect to IB Web API"""
        try:
            self.ib_api = IBWebAPI()
            
            # Test connection
            if not self.ib_api.is_connected():
                error_msg = "Not authenticated with IBKR Web API. Please authenticate first."
                self._log_error("CONNECTION_FAILED", ticker, error_msg)
                print(f"❌ {error_msg}")
                return False
            
            # Test basic functionality
            test_response = self.ib_api.session.get(f"{self.ib_api.base_url}/iserver/accounts")
            if test_response.status_code != 200:
                error_msg = f"IBKR Web API test failed: {test_response.status_code}"
                self._log_error("CONNECTION_TEST_FAILED", ticker, error_msg)
                print(f"❌ {error_msg}")
                return False
            
            # Test account access and contract lookup
            print("✅ Testing account access...")
            self.ib_api.get_accounts()

            print(f"✅ Testing contract lookup for {ticker}...")
            test_conid = self.ib_api.get_contract_id(ticker)
            if test_conid:
                print(f"✅ Found contract ID for {ticker}: {test_conid}")
            else:
                print(f"⚠️ Could not find contract ID for {ticker}")
                    
            print("✅ Connected to IBKR Web API")
            return True
            
        except Exception as e:
            error_msg = f"Failed to connect to IBKR Web API: {str(e)}"
            print(f"❌ {error_msg}")
            self._log_error("CONNECTION_FAILED", ticker, error_msg)
            return False
    
    def _disconnect_from_ib(self):
        """Disconnect from IBKR Web API"""
        self.ib_api = None
        print("✅ Disconnected from IBKR Web API")
        
        self.ib_wrapper = None
        self.ib_client = None
        
    def _execute_order(self, symbol: str, side: str, quantity: float, order_type: str = "MKT", price: float = None, stop_price: float = None) -> dict:
        try:
            conid = self.ib_api.get_contract_id(symbol)
            if not conid:
                return {'success': False, 'error': f'Could not find contract for {symbol}'}

            # Validate quantity
            if quantity < 0.1:
                print(f"   ⚠️ Warning: Order quantity {quantity} is very small and may not be supported for {symbol}")
                return {'success': False, 'error': f'Quantity {quantity} too small for {symbol}'}

            order_data = {
                "orderType": order_type,
                "side": side.upper(),
                "quantity": quantity
            }
            if price:
                order_data["price"] = price
            if stop_price:
                order_data["auxPrice"] = stop_price
                if order_type == "STP":  # For stop orders, set price equal to auxPrice
                    order_data["price"] = stop_price

            response = self.ib_api.place_order(conid, order_data)
            print(f"   📤 Order request sent. Status: {response.status_code}")
            print(f"   📋 Order data: {order_data}")
            print(f"   📋 Contract ID: {conid}")
            print(f"   📥 Response headers: {dict(response.headers)}")
            print(f"   📥 Response body: {response.text}")

            if response.status_code != 200:
                error_details = f'Status: {response.status_code}, Response: {response.text}'
                print(f"   ❌ Order failed - {error_details}")
                return {'success': False, 'error': error_details}

            result = response.json()
            print(f"   ✅ Order response: {result}")

            # Handle multiple confirmations
            max_confirmations = 3
            confirmation_count = 0
            current_result = result

            while isinstance(current_result, list) and len(current_result) > 0 and 'id' in current_result[0] and confirmation_count < max_confirmations:
                confirmation_id = current_result[0]['id']
                print(f"   📩 Confirmation required. Sending reply to ID: {confirmation_id}")
                reply_response = self.ib_api.session.post(
                    f"{self.ib_api.base_url}/iserver/reply/{confirmation_id}",
                    json={"confirmed": True},
                    timeout=30
                )
                print(f"   📥 Reply response status: {reply_response.status_code}, Headers: {dict(reply_response.headers)}, Body: {reply_response.text}")
                if reply_response.status_code != 200:
                    return {
                        'success': False,
                        'error': f'Confirmation failed: Status {reply_response.status_code}, Response: {reply_response.text}'
                    }
                current_result = reply_response.json()
                print(f"   ✅ Confirmation response: {current_result}")
                confirmation_count += 1

            # Extract order_id from final response
            order_id = None
            if isinstance(current_result, list) and current_result:
                order_id = current_result[0].get('order_id') or current_result[0].get('id')
            elif isinstance(current_result, dict):
                order_id = current_result.get('order_id') or current_result.get('id')

            if order_id:
                print(f"   📤 BUY ORDER SUBMITTED (Order ID: {order_id})")
                # Immediate status check
                time.sleep(1)  # Short delay to allow order registration
                status_response = self.ib_api.get_order_status(order_id)
                print(f"   📋 Immediate order status check: {status_response.text}")
                return {
                    'success': True,
                    'order_id': order_id,
                    'message': 'Order confirmed and placed'
                }
            else:
                error_msg = f'Confirmation succeeded but no order_id returned: {current_result}'
                print(f"   ❌ BUY ORDER SUBMISSION FAILED: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = f"Order execution failed: {str(e)}"
            print(f"   ❌ {error_msg}")
            return {'success': False, 'error': error_msg}

    def _wait_for_order_fill_webapi(self, order_id: str, expected_shares: float, timeout: int = 7) -> dict:
        """Wait for an order to fill, handling partial fills and cancellation with proper account context."""
        print(f"   ⏳ Waiting for order {order_id} to fill {expected_shares} shares...")
        
        start_time = time.time()
        last_filled = 0.0
        no_progress_time = 0
        max_retries = 3
        
        # Initial delay to allow order registration
        time.sleep(2)
        
        while time.time() - start_time < timeout:
            for attempt in range(max_retries):
                try:
                    # Try specific order status first
                    response = self.ib_api.get_order_status(order_id)
                    print(f"   📋 Specific order status response (attempt {attempt + 1}): {response.text}")
                    
                    order_info = None
                    if response.status_code == 200 and response.json():
                        order_info = response.json()
                        if isinstance(order_info, list):
                            order_info = order_info[0] if order_info else None
                    
                    # Fall back to full order list if specific status fails
                    if not order_info:
                        response = self.ib_api.get_order_status()
                        print(f"   📋 Full order status response (attempt {attempt + 1}): {response.text}")
                        if response.status_code == 200:
                            orders = response.json().get('orders', [])
                            for order in orders:
                                if str(order.get('orderId')) == str(order_id) or str(order.get('id')) == str(order_id):
                                    order_info = order
                                    break
                    
                    # Check trades endpoint as a fallback
                    if not order_info:
                        print(f"   ⚠️ Order {order_id} not found in order list (attempt {attempt + 1})")
                        print(f"   ⚠️ Checking trades for order {order_id}...")
                        trades_response = self.ib_api.session.get(f"{self.ib_api.base_url}/iserver/account/trades", timeout=30)
                        if trades_response.status_code == 200:
                            trades = trades_response.json()
                            for trade in trades:
                                if str(trade.get('order_id')) == str(order_id):
                                    print(f"   ✅ Found trade: {trade}")
                                    return {
                                        'success': True,
                                        'filled_shares': float(trade.get('executed_qty', 0)),
                                        'remaining_shares': 0,
                                        'avg_price': float(trade.get('avg_price', 0)),
                                        'status': 'FILLED',
                                        'cancelled': False
                                    }
                        print(f"   ⚠️ Order {order_id} not found in trades")
                        if attempt < max_retries - 1:
                            time.sleep(1)
                            continue
                        break
                    
                    # Process order information
                    status = order_info.get('status', order_info.get('orderStatus', 'Unknown')).upper()
                    filled_qty = float(order_info.get('filledQuantity', order_info.get('filled', 0)))
                    remaining_qty = float(order_info.get('remainingQuantity', order_info.get('remaining', expected_shares - filled_qty)))
                    avg_price = float(order_info.get('avgPrice', order_info.get('avgFillPrice', 0)))
                    
                    if filled_qty > last_filled:
                        last_filled = filled_qty
                        no_progress_time = 0
                        print(f"   📈 Progress: {filled_qty}/{expected_shares} shares filled at avg ${avg_price}")
                    
                    if status in ['FILLED', 'COMPLETE']:
                        print(f"   ✅ Order {order_id} FULLY FILLED: {filled_qty} shares at ${avg_price}")
                        return {
                            'success': True,
                            'filled_shares': filled_qty,
                            'remaining_shares': 0,
                            'avg_price': avg_price,
                            'status': status,
                            'cancelled': False
                        }
                    elif status in ['CANCELLED', 'CANCELED']:
                        print(f"   ❌ Order {order_id} CANCELLED: {filled_qty} shares filled, {remaining_qty} remaining")
                        return {
                            'success': filled_qty > 0,
                            'filled_shares': filled_qty,
                            'remaining_shares': remaining_qty,
                            'avg_price': avg_price,
                            'status': status,
                            'cancelled': True
                        }
                    elif status in ['PRESUBMITTED']:
                        print(f"   ⏳ Order {order_id} in PreSubmitted state, waiting for status change...")
                        time.sleep(3)
                        continue
                    elif filled_qty > 0 and filled_qty < expected_shares:
                        if status in ['PARTIALLYFILLED', 'SUBMITTED', 'PENDING']:
                            no_progress_time += 1
                            print(f"   ⏳ Partial fill: {filled_qty}/{expected_shares} shares. Waiting for more...")
                            time.sleep(1)
                            continue
                    
                    time.sleep(1)
                    no_progress_time += 1
                    break
                except Exception as e:
                    print(f"   ⚠️ Error checking order status: {str(e)}")
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    time.sleep(1)
                    no_progress_time += 1
                    break
        
        # Timeout reached - final check
        print(f"   ⏰ Order {order_id} timeout. Checking final status...")
        final_filled = 0.0
        final_remaining = expected_shares
        final_avg = 0.0
        final_status = "Timeout"
        cancelled = False
        try:
            response = self.ib_api.get_order_status()  # Full order list
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                order_info = next((o for o in orders if str(o.get('orderId')) == str(order_id)), None)
                if order_info:
                    final_filled = float(order_info.get('filledQuantity', 0))
                    final_remaining = float(order_info.get('remainingQuantity', expected_shares - final_filled))
                    final_avg = float(order_info.get('avgPrice', 0))
                    final_status = order_info.get('status')
                    print(f"   📋 Final status: {final_status}, Filled: {final_filled}, Remaining: {final_remaining}")
                    if final_remaining > 0:
                        print(f"   📤 Cancelling remaining {final_remaining} shares...")
                        cancel_response = self.ib_api.cancel_order(order_id)
                        if isinstance(cancel_response, dict):
                            # Handle error case from cancel_order
                            if cancel_response.get('status_code') in [200, 201]:
                                cancelled = True
                                final_remaining = 0.0
                                print("   ✅ Cancelled remaining")
                            else:
                                print(f"   ❌ Cancel failed: {cancel_response.get('text', 'Unknown error')}")
                        else:
                            # Handle standard response object
                            if cancel_response.status_code in [200, 201]:
                                cancelled = True
                                final_remaining = 0.0
                                print("   ✅ Cancelled remaining")
                            else:
                                print(f"   ❌ Cancel failed: {cancel_response.status_code}, {cancel_response.text}")
            if final_filled == 0:  # Fallback to trades if no filled found
                print(f"   ⚠️ Checking trades for final status of order {order_id}...")
                trades_response = self.ib_api.session.get(f"{self.ib_api.base_url}/iserver/account/trades", timeout=30)
                if trades_response.status_code == 200:
                    trades = trades_response.json()
                    exec_qty_total = 0.0
                    price_total = 0.0
                    for trade in trades:
                        if str(trade.get('order_id')) == str(order_id):
                            exec_qty = float(trade.get('executed_qty', 0))
                            exec_qty_total += exec_qty
                            price_total += float(trade.get('avg_price', 0)) * exec_qty
                    if exec_qty_total > 0:
                        final_filled = exec_qty_total
                        final_avg = price_total / exec_qty_total
                        final_remaining = 0.0
                        final_status = "Filled"
                        print(f"   📋 Found filled from trades: {final_filled} shares")
        except Exception as e:
            print(f"   ❌ Error in final check: {str(e)}")
            self._log_error("FINAL_STATUS_CHECK_FAILED", "UNKNOWN", str(e), order_id=order_id)
        
        return {
            'success': final_filled > 0,
            'filled_shares': final_filled,
            'remaining_shares': final_remaining,
            'avg_price': final_avg,
            'status': final_status,
            'cancelled': cancelled
        }
    
    def _execute_buy_order(self, trade: Trade) -> dict:
        """Execute buy order via Web API with partial order handling"""
        print(f"\n🔵 EXECUTING BUY ORDER:")
        print(f"   Ticker: {trade.ticker}")
        print(f"   Shares: {trade.shares}")
        print(f"   Risk Amount: ${trade.risk_amount}")
        
        try:
            result = self._execute_order(trade.ticker, "BUY", trade.shares)
            
            if not result['success']:
                error_msg = f"Buy order submission failed: {result['error']}"
                print(f"   ❌ BUY ORDER SUBMISSION FAILED: {result['error']}")
                self._log_error("BUY_ORDER_FAILED", trade.ticker, error_msg)
                return {
                    'success': False,
                    'filled_shares': 0,
                    'avg_price': 0,
                    'full_fill': False
                }
            
            order_id = result['order_id']
            print(f"   📤 BUY ORDER SUBMITTED (Order ID: {order_id})")
            
            # Wait for order to fill with partial handling
            fill_result = self._wait_for_order_fill_webapi(order_id, trade.shares, timeout=7)
            
            if fill_result['filled_shares'] > 0:
                filled_shares = fill_result['filled_shares']
                avg_price = fill_result['avg_price']
                full_fill = filled_shares == trade.shares
                print(f"   ✅ BUY ORDER {'FULLY ' if full_fill else 'PARTIALLY '}COMPLETED: {filled_shares} shares at ${avg_price}")
                return {
                    'success': True,
                    'filled_shares': filled_shares,
                    'avg_price': avg_price,
                    'full_fill': full_fill
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
        """Execute sell stop orders based on actual shares bought with proper scaling"""
        print(f"\n🔴 SETTING SELL STOP ORDERS for {actual_shares_bought} shares:")
        
        if actual_shares_bought == 0:
            print("   ❌ No shares bought - skipping sell stop orders")
            return
        
        total_planned_shares = trade.shares
        scale_factor = float(actual_shares_bought) / total_planned_shares
        
        # Get contract ID for tick size validation
        conid = self.ib_api.get_contract_id(trade.ticker)
        if not conid:
            print(f"   ❌ Could not find contract for {trade.ticker}")
            self._log_error("CONTRACT_NOT_FOUND", trade.ticker, "Could not find contract ID")
            return
        
        # Get contract details for tick size
        contract_details = self.ib_api.get_contract_details(conid)
        price_increment = 0.01  # Default tick size
        if contract_details:
            price_increment = float(contract_details.get('priceIncrement', 0.01))
            print(f"   📏 Price increment (tick size): ${price_increment}")
        
        try:
            for i, stop in enumerate(trade.sell_stops, 1):
                try:
                    # Scale the shares but keep fractional precision for Web API
                    scaled_shares = stop.shares * scale_factor
                    
                    if scaled_shares < 0.001:  # Minimum fractional share
                        print(f"   ⚠️ Stop {i}: Skipping (scaled to {scaled_shares:.3f} shares - too small)")
                        continue
                    
                    # Adjust stop price to nearest tick
                    adjusted_stop_price = round(stop.price / price_increment) * price_increment
                    if abs(adjusted_stop_price - stop.price) > 0.001:
                        print(f"   🔧 Adjusted stop price for {trade.ticker} from ${stop.price} to ${adjusted_stop_price}")
                    
                    result = self._execute_order(
                        trade.ticker, 
                        "SELL", 
                        scaled_shares,
                        "STP", 
                        stop_price=adjusted_stop_price
                    )
                    
                    if result['success']:
                        print(f"   Stop {i}: {scaled_shares:.3f} shares at ${adjusted_stop_price} - Order ID: {result.get('order_id', 'N/A')}")
                    else:
                        print(f"   ❌ Stop {i} FAILED: {result['error']}")
                        self._log_error("SELL_STOP_ORDER_FAILED", trade.ticker, result['error'])
                    
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