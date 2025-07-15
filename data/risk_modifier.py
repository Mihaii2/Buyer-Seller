import json
import os
import sys
import time
import msvcrt
import atexit


class GlobalLock:
    """Global lock to ensure only one instance of the script runs at a time"""
    
    def __init__(self, lock_file: str = 'risk_modifier_global.lock'):
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


class RiskModifier:
    def __init__(self, risk_file: str = 'risk_amount.json'):
        self.risk_file = risk_file
        self._initialize_file()
    
    def _initialize_file(self):
        """Initialize risk file with default structure if it doesn't exist"""
        if not os.path.exists(self.risk_file):
            with open(self.risk_file, 'w') as f:
                json.dump({"available_risk": 0.0}, f, indent=2)
    
    def get_current_risk(self) -> float:
        """Get current risk amount"""
        try:
            with FileLocker(self.risk_file) as f:
                f.seek(0)
                data = json.load(f)
                return data.get('available_risk', 0.0)
        except (json.JSONDecodeError, FileNotFoundError):
            return 0.0
    
    def set_risk_amount(self, new_amount: float) -> bool:
        """Set new risk amount"""
        try:
            with FileLocker(self.risk_file) as f:
                f.seek(0)
                data = json.load(f)
                
                old_amount = data.get('available_risk', 0.0)
                data['available_risk'] = new_amount
                
                # Write back the updated risk amount
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                
                print(f"✅ Risk amount updated: ${old_amount} → ${new_amount}")
                return True
                
        except Exception as e:
            print(f"❌ Failed to update risk amount: {str(e)}")
            return False
    
    def add_risk_amount(self, amount_to_add: float) -> bool:
        """Add to current risk amount"""
        try:
            with FileLocker(self.risk_file) as f:
                f.seek(0)
                data = json.load(f)
                
                old_amount = data.get('available_risk', 0.0)
                new_amount = old_amount + amount_to_add
                data['available_risk'] = new_amount
                
                # Write back the updated risk amount
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                
                print(f"✅ Risk amount updated: ${old_amount} + ${amount_to_add} = ${new_amount}")
                return True
                
        except Exception as e:
            print(f"❌ Failed to add to risk amount: {str(e)}")
            return False
    
    def subtract_risk_amount(self, amount_to_subtract: float) -> bool:
        """Subtract from current risk amount"""
        try:
            with FileLocker(self.risk_file) as f:
                f.seek(0)
                data = json.load(f)
                
                old_amount = data.get('available_risk', 0.0)
                new_amount = old_amount - amount_to_subtract
                
                if new_amount < 0:
                    print(f"❌ Cannot subtract ${amount_to_subtract} from ${old_amount} - would result in negative risk")
                    return False
                
                data['available_risk'] = new_amount
                
                # Write back the updated risk amount
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                
                print(f"✅ Risk amount updated: ${old_amount} - ${amount_to_subtract} = ${new_amount}")
                return True
                
        except Exception as e:
            print(f"❌ Failed to subtract from risk amount: {str(e)}")
            return False


def main():
    print("=== Risk Modifier Script ===")
    
    # Try to acquire global lock
    try:
        with GlobalLock() as lock:
            print("✅ Global lock acquired - script is running exclusively")
            modifier = RiskModifier()
            
            # Command line argument handling
            if len(sys.argv) < 2:
                print("Usage:")
                print("  python risk_modifier.py get                    - Get current risk amount")
                print("  python risk_modifier.py set <amount>           - Set risk amount")
                print("  python risk_modifier.py add <amount>           - Add to risk amount")
                print("  python risk_modifier.py subtract <amount>      - Subtract from risk amount")
                print("\nExamples:")
                print("  python risk_modifier.py get")
                print("  python risk_modifier.py set 1000.0")
                print("  python risk_modifier.py add 500.0")
                print("  python risk_modifier.py subtract 200.0")
                return
            
            command = sys.argv[1].lower()
            
            if command == "get":
                current_risk = modifier.get_current_risk()
                print(f"💰 Current available risk: ${current_risk}")
                
            elif command == "set":
                if len(sys.argv) != 3:
                    print("❌ Usage: python risk_modifier.py set <amount>")
                    sys.exit(1)
                
                try:
                    amount = float(sys.argv[2])
                    if amount < 0:
                        print("❌ Risk amount cannot be negative")
                        sys.exit(1)
                    
                    if not modifier.set_risk_amount(amount):
                        sys.exit(1)
                        
                except ValueError:
                    print("❌ Invalid amount. Please provide a numeric value.")
                    sys.exit(1)
                    
            elif command == "add":
                if len(sys.argv) != 3:
                    print("❌ Usage: python risk_modifier.py add <amount>")
                    sys.exit(1)
                
                try:
                    amount = float(sys.argv[2])
                    if amount < 0:
                        print("❌ Amount to add cannot be negative")
                        sys.exit(1)
                    
                    if not modifier.add_risk_amount(amount):
                        sys.exit(1)
                        
                except ValueError:
                    print("❌ Invalid amount. Please provide a numeric value.")
                    sys.exit(1)
                    
            elif command == "subtract":
                if len(sys.argv) != 3:
                    print("❌ Usage: python risk_modifier.py subtract <amount>")
                    sys.exit(1)
                
                try:
                    amount = float(sys.argv[2])
                    if amount < 0:
                        print("❌ Amount to subtract cannot be negative")
                        sys.exit(1)
                    
                    if not modifier.subtract_risk_amount(amount):
                        sys.exit(1)
                        
                except ValueError:
                    print("❌ Invalid amount. Please provide a numeric value.")
                    sys.exit(1)
                    
            else:
                print(f"❌ Unknown command: {command}")
                print("Available commands: get, set, add, subtract")
                sys.exit(1)
    
    except Exception as e:
        if "another instance is already running" in str(e):
            print("❌ Another instance of the script is already running. Please wait for it to complete.")
            
            # Optionally, show info about running instance
            lock_file = 'risk_modifier_global.lock'
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