from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import threading
import time

class IBapi(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.nextOrderId = orderId
        print(f"Next valid order ID: {orderId}")

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        print(f"Order {orderId} Status: {status}, Filled: {filled}, Remaining: {remaining}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"Error {errorCode}: {errorString}")

def create_contract(symbol: str) -> Contract:
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract

def create_market_order(action: str, quantity: float) -> Order:
    order = Order()
    order.action = action
    order.orderType = "MKT"
    order.totalQuantity = quantity
    return order

def main():
    # Initialize the app
    app = IBapi()
    
    # Connect to TWS (use 7497 for paper trading, 7496 for live, adjust if needed)
    app.connect("127.0.0.1", 7497, clientId=123)
    
    # Start a separate thread to handle API messages
    api_thread = threading.Thread(target=app.run)
    api_thread.start()
    
    # Wait for connection and next valid order ID
    while app.nextOrderId is None:
        time.sleep(1)
    
    # Create contract for PPIH
    ppih_contract = create_contract("PPIH")
    
    # Create market order to buy 1 shares
    buy_order = create_market_order("BUY", 1.5)
    
    # Place the order
    print(f"Placing order to buy 1 share of PPIH")
    app.placeOrder(app.nextOrderId, ppih_contract, buy_order)
    
    # Wait to observe order status
    time.sleep(1)
    
    # Disconnect from TWS
    print("Disconnecting from TWS")
    app.disconnect()

if __name__ == "__main__":
    main()