import json
import os
import sys
import time
import msvcrt
import atexit
from typing import List, Dict, Any


class GlobalLock:
    """Global lock to ensure only one instance of the script runs at a time"""
    
    def __init__(self, lock_file: str = 'trades_modifier_global.lock'):
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


class TradesModifier:
    def __init__(self, trades_file: str = 'trades.json'):
        self.trades_file = trades_file
        self._initialize_file()
    
    def _initialize_file(self):
        """Initialize trades file with default structure if it doesn't exist"""
        if not os.path.exists(self.trades_file):
            with open(self.trades_file, 'w') as f:
                json.dump([], f, indent=2)
    
    def _validate_trade(self, trade: Dict[str, Any]) -> bool:
        """Validate trade data structure"""
        required_fields = ['ticker', 'shares', 'risk_amount', 'lower_price_range', 'higher_price_range', 'sell_stops']
        
        # Check required fields
        for field in required_fields:
            if field not in trade:
                print(f"❌ Missing required field: {field}")
                return False
        
        # Validate data types and values
        try:
            ticker = str(trade['ticker']).strip().upper()
            shares = float(trade['shares'])
            risk_amount = float(trade['risk_amount'])
            lower_price = float(trade['lower_price_range'])
            higher_price = float(trade['higher_price_range'])
            sell_stops = trade['sell_stops']
            
            if not ticker:
                print("❌ Ticker cannot be empty")
                return False
            
            if shares <= 0:
                print("❌ Shares must be positive")
                return False
            
            if risk_amount <= 0:
                print("❌ Risk amount must be positive")
                return False
            
            if lower_price >= higher_price:
                print("❌ Lower price must be less than higher price")
                return False
            
            if not isinstance(sell_stops, list) or len(sell_stops) == 0:
                print("❌ Sell stops must be a non-empty list")
                return False
            
            # Validate sell stops
            total_stop_shares = 0
            for i, stop in enumerate(sell_stops):
                if not isinstance(stop, dict) or 'price' not in stop or 'shares' not in stop:
                    print(f"❌ Sell stop {i+1} must have 'price' and 'shares' fields")
                    return False
                
                try:
                    stop_price = float(stop['price'])
                    stop_shares = float(stop['shares'])
                    
                    if stop_price <= 0:
                        print(f"❌ Sell stop {i+1} price must be positive")
                        return False
                    
                    if stop_shares <= 0:
                        print(f"❌ Sell stop {i+1} shares must be positive")
                        return False
                    
                    total_stop_shares += stop_shares
                    
                except (ValueError, TypeError):
                    print(f"❌ Sell stop {i+1} price and shares must be numeric")
                    return False
            
            # Check if sell stop shares sum matches total shares
            if abs(total_stop_shares - shares) > 0.001:  # Allow for floating point precision
                print(f"❌ Sell stop shares ({total_stop_shares}) don't match total shares ({shares})")
                return False
            
            return True
            
        except (ValueError, TypeError) as e:
            print(f"❌ Invalid data types in trade: {str(e)}")
            return False
    
    def _normalize_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize trade data (convert types, clean up)"""
        normalized = {
            'ticker': str(trade['ticker']).strip().upper(),
            'shares': float(trade['shares']),
            'risk_amount': float(trade['risk_amount']),
            'lower_price_range': float(trade['lower_price_range']),
            'higher_price_range': float(trade['higher_price_range']),
            'sell_stops': []
        }
        
        for stop in trade['sell_stops']:
            normalized['sell_stops'].append({
                'price': float(stop['price']),
                'shares': float(stop['shares'])
            })
        
        return normalized
    
    def list_trades(self) -> List[Dict[str, Any]]:
        """List all trades"""
        try:
            with FileLocker(self.trades_file) as f:
                f.seek(0)
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def add_trade(self, trade: Dict[str, Any]) -> bool:
        """Add a new trade"""
        try:
            # Validate trade
            if not self._validate_trade(trade):
                return False
            
            # Normalize trade data
            normalized_trade = self._normalize_trade(trade)
            
            with FileLocker(self.trades_file) as f:
                f.seek(0)
                trades = json.load(f)
                
                # Check for duplicate trades
                for existing_trade in trades:
                    if (existing_trade['ticker'] == normalized_trade['ticker'] and
                        abs(existing_trade['lower_price_range'] - normalized_trade['lower_price_range']) < 0.001 and
                        abs(existing_trade['higher_price_range'] - normalized_trade['higher_price_range']) < 0.001):
                        print(f"❌ Trade for {normalized_trade['ticker']} with price range ${normalized_trade['lower_price_range']}-${normalized_trade['higher_price_range']} already exists")
                        return False
                
                # Add new trade
                trades.append(normalized_trade)
                
                # Write back
                f.seek(0)
                f.truncate()
                json.dump(trades, f, indent=2)
                
                print(f"✅ Trade added: {normalized_trade['ticker']} - {normalized_trade['shares']} shares (${normalized_trade['lower_price_range']}-${normalized_trade['higher_price_range']})")
                return True
                
        except Exception as e:
            print(f"❌ Failed to add trade: {str(e)}")
            return False
    
    def remove_trade(self, ticker: str, lower_price: float, higher_price: float) -> bool:
        """Remove a trade by ticker and price range"""
        try:
            with FileLocker(self.trades_file) as f:
                f.seek(0)
                trades = json.load(f)
                
                # Find trade to remove
                for i, trade in enumerate(trades):
                    if (trade['ticker'].upper() == ticker.upper() and
                        abs(trade['lower_price_range'] - lower_price) < 0.001 and
                        abs(trade['higher_price_range'] - higher_price) < 0.001):
                        
                        removed_trade = trades.pop(i)
                        
                        # Write back
                        f.seek(0)
                        f.truncate()
                        json.dump(trades, f, indent=2)
                        
                        print(f"✅ Trade removed: {removed_trade['ticker']} - {removed_trade['shares']} shares (${removed_trade['lower_price_range']}-${removed_trade['higher_price_range']})")
                        return True
                
                print(f"❌ Trade not found: {ticker} with price range ${lower_price}-${higher_price}")
                return False
                
        except Exception as e:
            print(f"❌ Failed to remove trade: {str(e)}")
            return False
    
    def clear_all_trades(self) -> bool:
        """Clear all trades"""
        try:
            with FileLocker(self.trades_file) as f:
                f.seek(0)
                trades = json.load(f)
                
                if not trades:
                    print("📋 No trades to clear")
                    return True
                
                count = len(trades)
                
                # Clear all trades
                f.seek(0)
                f.truncate()
                json.dump([], f, indent=2)
                
                print(f"✅ Cleared {count} trades")
                return True
                
        except Exception as e:
            print(f"❌ Failed to clear trades: {str(e)}")
            return False


def parse_sell_stops(stops_string: str) -> List[Dict[str, Any]]:
    """Parse sell stops from command line string format: price1:shares1,price2:shares2,..."""
    stops = []
    
    if not stops_string:
        return stops
    
    try:
        for stop_str in stops_string.split(','):
            stop_str = stop_str.strip()
            if ':' not in stop_str:
                raise ValueError(f"Invalid sell stop format: {stop_str}")
            
            price_str, shares_str = stop_str.split(':', 1)
            price = float(price_str.strip())
            shares = float(shares_str.strip())
            
            stops.append({'price': price, 'shares': shares})
        
        return stops
        
    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid sell stops format: {str(e)}")


def main():
    print("=== Trades Modifier Script ===")
    
    # Try to acquire global lock
    try:
        with GlobalLock() as lock:
            print("✅ Global lock acquired - script is running exclusively")
            modifier = TradesModifier()
            
            # Command line argument handling
            if len(sys.argv) < 2:
                print("Usage:")
                print("  python trades_modifier.py list                                           - List all trades")
                print("  python trades_modifier.py add <ticker> <shares> <risk> <low> <high> <stops>  - Add trade")
                print("  python trades_modifier.py remove <ticker> <low> <high>                   - Remove trade")
                print("  python trades_modifier.py clear                                          - Clear all trades")
                print("\nSell stops format: price1:shares1,price2:shares2,...")
                print("\nExamples:")
                print("  python trades_modifier.py list")
                print("  python trades_modifier.py add AAPL 10 500.0 145.0 155.0 '150.0:5,145.0:3,140.0:2'")
                print("  python trades_modifier.py remove AAPL 145.0 155.0")
                print("  python trades_modifier.py clear")
                return
            
            command = sys.argv[1].lower()
            
            if command == "list":
                trades = modifier.list_trades()
                if not trades:
                    print("📋 No trades found")
                else:
                    print(f"📋 Found {len(trades)} trades:")
                    for i, trade in enumerate(trades, 1):
                        print(f"\n{i}. {trade['ticker']} - {trade['shares']} shares")
                        print(f"   Risk: ${trade['risk_amount']}")
                        print(f"   Price Range: ${trade['lower_price_range']} - ${trade['higher_price_range']}")
                        print(f"   Sell Stops:")
                        for j, stop in enumerate(trade['sell_stops'], 1):
                            print(f"     {j}. ${stop['price']} - {stop['shares']} shares")
                            
            elif command == "add":
                if len(sys.argv) != 8:
                    print("❌ Usage: python trades_modifier.py add <ticker> <shares> <risk> <low> <high> <stops>")
                    print("   Example: python trades_modifier.py add AAPL 10 500.0 145.0 155.0 '150.0:5,145.0:3,140.0:2'")
                    sys.exit(1)
                
                try:
                    ticker = sys.argv[2].strip().upper()
                    shares = float(sys.argv[3])
                    risk_amount = float(sys.argv[4])
                    lower_price = float(sys.argv[5])
                    higher_price = float(sys.argv[6])
                    stops_str = sys.argv[7]
                    
                    # Parse sell stops
                    sell_stops = parse_sell_stops(stops_str)
                    
                    # Create trade object
                    trade = {
                        'ticker': ticker,
                        'shares': shares,
                        'risk_amount': risk_amount,
                        'lower_price_range': lower_price,
                        'higher_price_range': higher_price,
                        'sell_stops': sell_stops
                    }
                    
                    if not modifier.add_trade(trade):
                        sys.exit(1)
                        
                except ValueError as e:
                    print(f"❌ Invalid input: {str(e)}")
                    sys.exit(1)
                    
            elif command == "remove":
                if len(sys.argv) != 5:
                    print("❌ Usage: python trades_modifier.py remove <ticker> <low> <high>")
                    print("   Example: python trades_modifier.py remove AAPL 145.0 155.0")
                    sys.exit(1)
                
                try:
                    ticker = sys.argv[2].strip().upper()
                    lower_price = float(sys.argv[3])
                    higher_price = float(sys.argv[4])
                    
                    if not modifier.remove_trade(ticker, lower_price, higher_price):
                        sys.exit(1)
                        
                except ValueError:
                    print("❌ Invalid price values. Please provide numeric values.")
                    sys.exit(1)
                    
            elif command == "clear":
                if not modifier.clear_all_trades():
                    sys.exit(1)
                    
            else:
                print(f"❌ Unknown command: {command}")
                print("Available commands: list, add, remove, clear")
                sys.exit(1)
    
    except Exception as e:
        if "another instance is already running" in str(e):
            print("❌ Another instance of the script is already running. Please wait for it to complete.")
            
            # Optionally, show info about running instance
            lock_file = 'trades_modifier_global.lock'
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