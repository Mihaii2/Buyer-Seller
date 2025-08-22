import requests
import json
import time
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class IBWebAPI:
    def __init__(self, base_url="https://localhost:5050/v1/api"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.verify = False
        self.session.timeout = 30
        
    def is_connected(self):
        try:
            response = self.session.get(f"{self.base_url}/iserver/auth/status", timeout=10)
            return response.status_code == 200 and response.json().get('authenticated', False)
        except Exception as e:
            print(f"❌ Connection check failed: {str(e)}")
            return False
        
    def get_accounts(self):
        try:
            response = self.session.get(f"{self.base_url}/iserver/accounts", timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Failed to get accounts: {response.status_code}, {response.text}")
                return None
        except Exception as e:
            print(f"❌ Get accounts error: {str(e)}")
            return None
    
    def get_contract_id(self, symbol):
        try:
            url = f"{self.base_url}/iserver/secdef/search"
            payload = {"symbol": symbol}
            response = self.session.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0].get("conid")
            print(f"❌ Failed to get contract ID for {symbol}: {response.status_code}, {response.text}")
            return None
        except Exception as e:
            print(f"❌ Contract ID error: {str(e)}")
            return None
    
    def get_contract_details(self, conid):
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
    
    def place_order(self, conid, order_data):
        try:
            accounts_response = self.get_accounts()
            if not accounts_response:
                print("❌ Failed to get accounts")
                return None
            account_id = accounts_response.get('selectedAccount')
            if not account_id:
                print("❌ No selected account found")
                return None
                
            url = f"{self.base_url}/iserver/account/{account_id}/orders"
            payload = {
                "orders": [{
                    "acctId": account_id,
                    "conid": int(conid),
                    "orderType": order_data["orderType"],
                    "side": order_data["side"],
                    "quantity": order_data["quantity"],
                    "tif": "DAY",
                    "auxPrice": order_data["auxPrice"]
                }]
            }
            # Add price field if provided
            if "price" in order_data:
                payload["orders"][0]["price"] = order_data["price"]
                
            print(f"📤 Sending sell stop order to: {url}")
            print(f"📋 Payload: {json.dumps(payload, indent=2)}")
            
            response = self.session.post(url, json=payload, timeout=30)
            print(f"📥 Response status: {response.status_code}")
            if response.status_code != 200:
                print(f"❌ Order failed: {response.text}")
                return {"success": False, "error": response.text}
                
            result = response.json()
            print(f"✅ Order response: {json.dumps(result, indent=2)}")
            
            # Handle confirmations
            max_confirmations = 3
            confirmation_count = 0
            current_result = result
            
            while isinstance(current_result, list) and len(current_result) > 0 and 'id' in current_result[0] and confirmation_count < max_confirmations:
                confirmation_id = current_result[0]['id']
                print(f"📩 Confirmation required. Sending reply to ID: {confirmation_id}")
                reply_response = self.session.post(
                    f"{self.base_url}/iserver/reply/{confirmation_id}",
                    json={"confirmed": True},
                    timeout=30
                )
                print(f"📥 Reply response status: {reply_response.status_code}, Body: {reply_response.text}")
                if reply_response.status_code != 200:
                    print(f"❌ Confirmation failed: {reply_response.text}")
                    return {"success": False, "error": f"Confirmation failed: {reply_response.text}"}
                current_result = reply_response.json()
                confirmation_count += 1
                
            order_id = None
            if isinstance(current_result, list) and current_result:
                order_id = current_result[0].get('order_id') or current_result[0].get('id')
            elif isinstance(current_result, dict):
                order_id = current_result.get('order_id') or current_result.get('id')
                
            if order_id:
                print(f"✅ Sell stop order placed successfully. Order ID: {order_id}")
                return {"success": True, "order_id": order_id}
            else:
                print(f"❌ No order ID returned: {current_result}")
                return {"success": False, "error": f"No order ID returned: {current_result}"}
                
        except Exception as e:
            print(f"❌ Order placement error: {str(e)}")
            return {"success": False, "error": str(e)}

def test_sell_stop_order():
    print("🚀 Testing sell stop order with fractional shares...")
    
    # Configuration
    ticker = "AAPL"
    shares = 5.25  # Fractional shares
    stop_price = 148.0
    ib_api = IBWebAPI()
    
    # Check connection
    print("\n🔗 Checking IBKR API connection...")
    if not ib_api.is_connected():
        print("❌ Not connected to IBKR API. Please ensure IB Gateway is running and authenticated.")
        return
    
    # Get contract ID
    print(f"\n🔍 Looking up contract ID for {ticker}...")
    conid = ib_api.get_contract_id(ticker)
    if not conid:
        print(f"❌ Failed to get contract ID for {ticker}")
        return
    
    print(f"✅ Found contract ID: {conid}")
    
    # Get contract details to validate tick size
    print(f"\n📋 Retrieving contract details for {ticker}...")
    contract_details = ib_api.get_contract_details(conid)
    if contract_details:
        print(f"✅ Contract details: {json.dumps(contract_details, indent=2)}")
        price_increment = float(contract_details.get('priceIncrement', 0.01))  # Default to 0.01 if not found
        print(f"📏 Price increment (tick size): ${price_increment}")
        
        # Round stop price to nearest valid tick
        if price_increment > 0:
            stop_price = round(stop_price / price_increment) * price_increment
            print(f"🔧 Adjusted stop price to nearest tick: ${stop_price}")
    
    # Place sell stop order
    print(f"\n📤 Placing sell stop order: {shares} shares of {ticker} at ${stop_price}")
    order_data = {
        "orderType": "STP",
        "side": "SELL",
        "quantity": shares,
        "auxPrice": stop_price,
        "price": stop_price  # Include price field as a fallback
    }
    
    result = ib_api.place_order(conid, order_data)
    
    if result and result.get("success"):
        print(f"\n🎉 Sell stop order placed successfully! Order ID: {result['order_id']}")
    else:
        print(f"\n❌ Sell stop order failed: {result.get('error', 'Unknown error')}")

if __name__ == "__main__":
    try:
        test_sell_stop_order()
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")