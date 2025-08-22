#!/usr/bin/env python3
import pytz
from datetime import datetime, timedelta, time as dt_time
import requests
import time
import argparse
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import json
from typing import List, Dict, Optional, Tuple
import statistics
import random

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create unique log file name
rand_id = random.randint(1000, 9999)
log_filename = f"stock_data_server_{rand_id}.log"

# File handler
file_handler = logging.FileHandler(log_filename, mode='a', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

logger.info(f"Logging to file: {log_filename}")

class StockTradingBot:
    def __init__(self, data_server_url: str = "http://localhost:5001", 
                 trade_server_url: str = "http://localhost:5002"):
        self.data_server_url = data_server_url
        self.trade_server_url = trade_server_url
        self.running = False
        self.pivot_entry_time = None  # Track when price first entered pivot range
        
    def get_ticker_data(self, symbol: str) -> Optional[List[Dict]]:
        """Get historical data for a ticker from the data server"""
        try:
            response = requests.get(f"{self.data_server_url}/data/{symbol}")
            if response.status_code == 200:
                data = response.json()
                return data.get('data', [])
            else:
                logger.error(f"Failed to get data for {symbol}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
            return None
    
    def get_latest_data(self, symbol: str) -> Optional[Dict]:
        """Get the latest data point for a ticker"""
        try:
            response = requests.get(f"{self.data_server_url}/data/{symbol}/latest")
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get latest data for {symbol}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching latest data for {symbol}: {str(e)}")
            return None
    
    def filter_unique_prices(self, data: List[Dict]) -> List[Dict]:
        """Filter out duplicate price/volume combinations, keeping only unique entries"""
        seen = set()
        unique_data = []
        
        for record in data:
            # Create a key from price and volume to identify duplicates
            key = (record.get('currentPrice'), record.get('volume'))
            if key not in seen and record.get('currentPrice') is not None:
                seen.add(key)
                unique_data.append(record)
        
        return unique_data
    
    def get_data_in_time_range(self, data: List[Dict], start_seconds: int, end_seconds: int) -> List[Dict]:
        """Get data within a specific time range (seconds ago)"""
        now = datetime.now()
        start_time = now - timedelta(seconds=start_seconds)  # Further back in time
        end_time = now - timedelta(seconds=end_seconds)      # More recent time
        
        logger.info(f"Time range: {start_time.strftime('%H:%M:%S.%f')} to {end_time.strftime('%H:%M:%S.%f')} (current: {now.strftime('%H:%M:%S.%f')})")
        
        filtered_data = []
        for record in data:
            try:
                timestamp_str = record['timestamp']
                
                # Handle different timestamp formats
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1]
                elif '+' in timestamp_str:
                    timestamp_str = timestamp_str.split('+')[0]
                elif timestamp_str.endswith('+00:00'):
                    timestamp_str = timestamp_str[:-6]
                
                # Parse the timestamp
                try:
                    record_time = datetime.fromisoformat(timestamp_str)
                except ValueError:
                    # Try alternative parsing if fromisoformat fails
                    record_time = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S.%f')
                
                # Check if the record time is within our range
                # record_time should be between start_time (older) and end_time (newer)
                if record_time >= start_time and record_time <= end_time:
                    filtered_data.append(record)
                    logger.debug(f"Record {record_time.strftime('%H:%M:%S.%f')} is within range")
                else:
                    logger.debug(f"Record {record_time.strftime('%H:%M:%S.%f')} is outside range")
                    
            except (ValueError, KeyError) as e:
                logger.debug(f"Failed to parse timestamp {record.get('timestamp', 'N/A')}: {e}")
                continue
        
        logger.info(f"Time range filter: {start_seconds}s to {end_seconds}s ago, found {len(filtered_data)} records")
        return filtered_data
    
    def calculate_average_price(self, data: List[Dict]) -> Optional[float]:
        """Calculate average price from data records"""
        if not data:
            return None
        
        prices = [record['currentPrice'] for record in data if record.get('currentPrice') is not None]
        if not prices:
            return None
        
        return statistics.mean(prices)

    def get_minutes_since_market_open(self) -> Optional[int]:
        """Get the number of minutes since market opened today"""
        et = pytz.timezone('US/Eastern')
        now = datetime.now(et)
        
        # Check if market is currently open
        if not is_market_open():
            return None
        
        # Get today's market open time
        market_open_today = now.replace(hour=9, minute=30, second=0, microsecond=0)
        
        # Calculate minutes since market opened
        minutes_since_open = (now - market_open_today).total_seconds() / 60
        return int(minutes_since_open)
    
    def calculate_volume_increase_in_timeframe(self, data: List[Dict], minutes: int) -> Optional[int]:
        """Calculate volume increase in the last X minutes"""
        logger.info(f"   Calculating volume increase for timeframe: {minutes} minutes")
        
        if minutes == -1:  # Entire day - calculate total volume for the day
            total_volume = sum(record.get('volume', 0) for record in data if record.get('volume') is not None)
            logger.info(f"   Total daily volume calculated: {total_volume}")
            return total_volume if total_volume > 0 else None
        
        # Check if market has been open long enough for this timeframe
        minutes_since_open = self.get_minutes_since_market_open()
        if minutes_since_open is not None and minutes_since_open < minutes:
            logger.info(f"   Market has only been open for {minutes_since_open} minutes, "
                    f"adjusting timeframe from {minutes} to {minutes_since_open} minutes")
            minutes = minutes_since_open  # Use actual time since market open

        now = datetime.now()
        cutoff_time = now - timedelta(minutes=minutes)
        
        # Sort data by timestamp to get chronological order
        timestamped_data = []
        for record in data:
            try:
                timestamp_str = record['timestamp']
                
                # Handle different timestamp formats
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1]
                elif '+' in timestamp_str:
                    timestamp_str = timestamp_str.split('+')[0]
                elif timestamp_str.endswith('+00:00'):
                    timestamp_str = timestamp_str[:-6]
                
                # Parse the timestamp
                try:
                    record_time = datetime.fromisoformat(timestamp_str)
                except ValueError:
                    # Try alternative parsing if fromisoformat fails
                    record_time = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S.%f')
                
                timestamped_data.append((record_time, record))
                        
            except (ValueError, KeyError) as e:
                logger.debug(f"Failed to parse timestamp {record.get('timestamp', 'N/A')}: {e}")
                continue
        
        # Sort by timestamp
        timestamped_data.sort(key=lambda x: x[0])

        logger.info(f"   First record and last in timeframe in format {timestamped_data[0][0].strftime('%H:%M:%S')} | Price: {timestamped_data[0][1].get('currentPrice')} | Volume: {timestamped_data[0][1].get('volume')}")
        logger.info(f"   Last record in timeframe in format {timestamped_data[-1][0].strftime('%H:%M:%S')} | Price: {timestamped_data[-1][1].get('currentPrice')} | Volume: {timestamped_data[-1][1].get('volume')}")

        # Find the volume at the cutoff time (start of the timeframe)
        volume_at_cutoff = None
        current_volume = None
        first_available_volume = None

        for record_time, record in timestamped_data:
            volume = record.get('volume')
            if volume is not None:
                # Keep track of the first available volume
                if first_available_volume is None:
                    first_available_volume = volume
                
                if record_time <= cutoff_time:
                    volume_at_cutoff = volume  # Keep updating until we pass the cutoff
                elif record_time > cutoff_time:
                    # This is within our timeframe, keep the latest volume
                    current_volume = volume

        # Use fallback logic if we don't have volume at cutoff
        if volume_at_cutoff is None:
            if first_available_volume is not None:
                logger.info(f"No volume at cutoff time, using first available volume: {first_available_volume}")
                volume_at_cutoff = first_available_volume
            else:
                logger.info("No volume data available at all")
                return None

        # If we still don't have current volume, use the latest available
        if current_volume is None:
            # Find the latest volume from all data
            for record_time, record in reversed(timestamped_data):
                volume = record.get('volume')
                if volume is not None:
                    current_volume = volume
                    logger.info(f"Using latest available volume as current: {current_volume}")
                    break

        if volume_at_cutoff is None or current_volume is None:
            logger.info(f"Insufficient data to calculate volume increase for {minutes} minutes - "
                    f"volume_at_cutoff: {volume_at_cutoff}, current_volume: {current_volume}")
            return None
        
        # Calculate the increase
        logger.info(f"   Volume at cutoff ({cutoff_time.strftime('%H:%M:%S')}): {volume_at_cutoff}")
        logger.info(f"   Current/latest volume: {current_volume}")
        volume_increase = current_volume - volume_at_cutoff
        logger.info(f"Volume increase in last {minutes} minutes: {volume_increase} "
                f"(from {volume_at_cutoff} to {current_volume})")
        return max(0, volume_increase)  # Return 0 if volume decreased
    
    def check_volume_requirements(self, data: List[Dict], volume_requirements: List[Tuple[int, int]], 
                             volume_multiplier: float = 1.0) -> bool:
        """Check if volume requirements are met"""
        if not volume_requirements:
            logger.info("   No volume requirements specified - PASSED")
            return True
        
        logger.info(f"   Checking {len(volume_requirements)} volume requirement(s) with {volume_multiplier}x multiplier:")
        
        all_passed = True
        for i, (minutes, required_volume) in enumerate(volume_requirements, 1):
            actual_volume_increase = self.calculate_volume_increase_in_timeframe(data, minutes)
            logger.info(f"   Raw calculated increase for {minutes if minutes != -1 else 'day'}: {actual_volume_increase}")
            
            # If we couldn't calculate volume increase, fail the check
            if actual_volume_increase is None:
                logger.info(f"   Requirement {i}: Could not calculate volume increase for {minutes} minutes - FAILED")
                all_passed = False
                continue
            
            adjusted_required = int(required_volume * volume_multiplier)
            passed = actual_volume_increase >= adjusted_required
            all_passed = all_passed and passed
            
            timeframe_str = "entire day" if minutes == -1 else f"{minutes} minutes"
            logger.info(f"   Requirement {i} ({timeframe_str}): {actual_volume_increase:,} >= {adjusted_required:,} - {'PASSED' if passed else 'FAILED'}")
        
        logger.info(f"   Overall volume requirements: {'PASSED' if all_passed else 'FAILED'}")
        return all_passed
    
    def check_price_momentum(self, data: List[Dict], recent_interval_seconds: int = 20, 
                           historical_interval_seconds: int = 600, 
                           required_increase_percent: float = 0.05) -> bool:
        """Check if price momentum condition is met"""
        unique_data = self.filter_unique_prices(data)
        
        logger.info(f"Total unique data points: {len(unique_data)}")
        
        # Get data for recent interval
        recent_data = self.get_data_in_time_range(unique_data, recent_interval_seconds, 0)
        
        # Get data for historical interval
        historical_data = self.get_data_in_time_range(unique_data, historical_interval_seconds, recent_interval_seconds)
        
        logger.info(f"Recent data points (last {recent_interval_seconds}s): {len(recent_data)}")
        logger.info(f"Historical data points ({recent_interval_seconds}s-{historical_interval_seconds}s ago): {len(historical_data)}")
        
        # Debug: Show some sample timestamps
        if len(unique_data) > 0:
            logger.info(f"Sample timestamps from data:")
            for i, record in enumerate(unique_data[-5:]):  # Show last 5 records
                logger.info(f"  Record {i}: {record.get('timestamp', 'N/A')} - Price: {record.get('currentPrice', 'N/A')}")
        
        if not recent_data:
            logger.info("No recent data available for momentum check")
            return False
        
        recent_avg = self.calculate_average_price(recent_data)
        
        if not historical_data:
            logger.info("No historical data available, but recent data exists - condition passed")
            return True
        
        historical_avg = self.calculate_average_price(historical_data)
        
        if recent_avg is None or historical_avg is None:
            return False
        
        # Check if recent average is at least the required percentage higher than historical average
        required_increase = historical_avg * (required_increase_percent / 100.0)
        momentum_met = recent_avg >= historical_avg + required_increase
        
        logger.info(f"Momentum check: recent_avg={recent_avg:.4f}, historical_avg={historical_avg:.4f}, "
                   f"required_increase={required_increase:.4f} ({required_increase_percent}%), met={momentum_met}")
        
        return momentum_met
    
    def check_day_high_condition(self, current_price: float, day_high: float, max_percent_off: float = 0.5) -> bool:
        """Check if current price is at most max_percent_off% down from day's high"""
        if day_high is None or current_price is None:
            logger.info(f"   Day high condition check failed - missing data: current_price={current_price}, day_high={day_high}")
            return False
        
        max_drop = day_high * (max_percent_off / 100.0)
        min_acceptable_price = day_high - max_drop
        current_drop = day_high - current_price
        current_drop_percent = (current_drop / day_high) * 100
        
        condition_met = current_price >= min_acceptable_price
        
        logger.info(f"   Day high: {day_high:.4f}")
        logger.info(f"   Current price: {current_price:.4f}")
        logger.info(f"   Current drop: {current_drop:.4f} ({current_drop_percent:.2f}%)")
        logger.info(f"   Max allowed drop: {max_drop:.4f} ({max_percent_off}%)")
        logger.info(f"   Min acceptable price: {min_acceptable_price:.4f}")
        logger.info(f"   Condition: {'PASSED' if condition_met else 'FAILED'}")
        
        return condition_met
    
    def check_day_low_condition(self, day_low: float, max_day_low: float) -> bool:
        """Check if day's low is at most max_day_low"""
        if day_low is None or max_day_low is None:
            logger.info(f"   Day low condition check skipped - missing data: day_low={day_low}, max_day_low={max_day_low}")
            return True  # Skip check if no limit set or no data
        
        condition_met = day_low <= max_day_low
        
        logger.info(f"   Day low: {day_low:.4f}")
        logger.info(f"   Max allowed day low: {max_day_low:.4f}")
        logger.info(f"   Condition: {'PASSED' if condition_met else 'FAILED'}")
        
        return condition_met
    
    def get_pivot_position(self, current_price: float, lower_price: float, higher_price: float) -> str:
        """Determine which part of the pivot range the current price is in"""
        pivot_range = higher_price - lower_price
        price_position = (current_price - lower_price) / pivot_range
        
        if price_position <= 0.5:
            return "lower"
        elif price_position <= 0.75:
            return "middle"
        else:
            return "upper"
    
    def should_apply_time_in_pivot_requirement(self, pivot_position: str, time_in_pivot_positions: List[str]) -> bool:
        """Check if time-in-pivot requirement should be applied for current position"""
        if not time_in_pivot_positions:
            return False  # No requirement if no positions specified
        
        if "any" in time_in_pivot_positions:
            return True
        
        return pivot_position in time_in_pivot_positions
    
    def check_time_in_pivot_requirement(self, current_price: float, lower_price: float, higher_price: float,
                                       time_in_pivot_seconds: int, time_in_pivot_positions: List[str]) -> bool:
        """Check if price has been in specified pivot positions for required time"""
        if time_in_pivot_seconds <= 0:
            return True  # No time requirement
        
        current_time = datetime.now()
        
        # Check if price is currently in pivot range
        if current_price < lower_price or current_price > higher_price:
            self.pivot_entry_time = None
            logger.info(f"Price {current_price} not in pivot range, resetting timer")
            return False
        
        # Get current pivot position
        pivot_position = self.get_pivot_position(current_price, lower_price, higher_price)
        
        # Check if we need to apply time requirement for this position
        if not self.should_apply_time_in_pivot_requirement(pivot_position, time_in_pivot_positions):
            logger.info(f"Time-in-pivot requirement not applicable for position '{pivot_position}' - condition passed")
            return True
        
        # If we don't have an entry time, set it now
        if self.pivot_entry_time is None:
            self.pivot_entry_time = current_time
            logger.info(f"Price entered pivot range at position '{pivot_position}' at {current_time.strftime('%H:%M:%S')}")
        
        # Check if we've been in the pivot long enough
        time_in_pivot = (current_time - self.pivot_entry_time).total_seconds()
        condition_met = time_in_pivot >= time_in_pivot_seconds
        
        logger.info(f"Time-in-pivot check: position='{pivot_position}', time_in_pivot={time_in_pivot:.1f}s, "
                   f"required={time_in_pivot_seconds}s, met={condition_met}")
        
        return condition_met
    
    def execute_trade(self, ticker: str, lower_price: float, higher_price: float) -> bool:
        """Execute the trade by sending POST request to trade server"""
        try:
            payload = {
                "ticker": ticker,
                "lower_price": lower_price,
                "higher_price": higher_price
            }
            
            response = requests.post(f"{self.trade_server_url}/execute_trade", json=payload)
            
            if response.status_code == 200:
                logger.info(f"Trade executed successfully for {ticker}")
                return True
            else:
                logger.error(f"Trade execution failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error executing trade: {str(e)}")
            time.sleep(10)
            return False
    
    def monitor_and_trade(self, ticker: str, lower_price: float, higher_price: float,
                     volume_requirements: List[Tuple[int, int]], pivot_adjustment: float = 0.0,
                     recent_interval_seconds: int = 20, historical_interval_seconds: int = 600,
                     required_increase_percent: float = 0.05, day_high_max_percent_off: float = 0.5,
                     time_in_pivot_seconds: int = 0, time_in_pivot_positions: List[str] = None, 
                     volume_multipliers: List[float] = None, max_day_low: float = None):
        """Main monitoring and trading logic"""
        adjusted_higher_price = higher_price * (1 + pivot_adjustment)
        
        if time_in_pivot_positions is None:
            time_in_pivot_positions = []
        
        logger.info(f"Starting monitoring for {ticker}")
        logger.info(f"Pivot range: {lower_price} - {adjusted_higher_price}")
        logger.info(f"Volume requirements: {volume_requirements}")
        logger.info(f"Momentum settings: recent={recent_interval_seconds}s, historical={historical_interval_seconds}s, "
                   f"required_increase={required_increase_percent}%")
        logger.info(f"Day high max percent off: {day_high_max_percent_off}%")
        logger.info(f"Time-in-pivot requirement: {time_in_pivot_seconds}s for positions {time_in_pivot_positions}")
        logger.info(f"Max day low: {max_day_low}")
        
        wait_for_market_open()

        self.running = True
        
        cycle_count = 0
        start_time = datetime.now()
        
        while self.running:
            try:
                cycle_count += 1
                cycle_start = datetime.now()
                logger.info(f"\n{'='*60}")
                logger.info(f"MONITORING CYCLE #{cycle_count} - {cycle_start.strftime('%H:%M:%S.%f')[:-3]}")
                logger.info(f"{'='*60}")
                
                # Get current data
                latest_data = self.get_latest_data(ticker)
                if not latest_data:
                    logger.warning(f"No latest data available for {ticker}")
                    time.sleep(5)
                    continue
                
                current_price = latest_data.get('currentPrice')
                day_high = latest_data.get('dayHigh')
                day_low = latest_data.get('dayLow')
                
                if current_price is None:
                    logger.warning(f"No current price available for {ticker}")
                    time.sleep(5)
                    continue
                
                if current_price < lower_price or current_price > adjusted_higher_price:
                    if current_price < lower_price:
                        logger.info(f"Price {current_price} is BELOW pivot range (min: {lower_price}, max: {adjusted_higher_price}) - difference: {lower_price - current_price:.4f}")
                    else:
                        logger.info(f"Price {current_price} is ABOVE pivot range (min: {lower_price}, max: {adjusted_higher_price}) - difference: {current_price - adjusted_higher_price:.4f}")
                    self.pivot_entry_time = None  # Reset timer when out of range
                    time.sleep(5)
                    continue

                logger.info(f"✓ Price {current_price} is IN pivot range [{lower_price}, {adjusted_higher_price}]")
                
                # Get historical data for analysis
                historical_data = self.get_ticker_data(ticker)
                if not historical_data:
                    logger.warning(f"No historical data available for {ticker}")
                    time.sleep(5)
                    continue
                
                ## Determine pivot position and volume multiplier
                pivot_position = self.get_pivot_position(current_price, lower_price, adjusted_higher_price)
                pivot_range = adjusted_higher_price - lower_price
                price_position_percent = ((current_price - lower_price) / pivot_range) * 100

                if volume_multipliers is None:
                    volume_multipliers = [1.0, 0.75, 0.5]

                if pivot_position == "lower":
                    volume_multiplier = volume_multipliers[0]
                elif pivot_position == "middle":
                    volume_multiplier = volume_multipliers[1]
                else:
                    volume_multiplier = volume_multipliers[2]


                logger.info(f"📊 PIVOT ANALYSIS:")
                logger.info(f"   Current price: {current_price}")
                logger.info(f"   Pivot range: {lower_price} - {adjusted_higher_price} (span: {pivot_range:.4f})")
                logger.info(f"   Position in range: {price_position_percent:.1f}% ({pivot_position} section)")
                logger.info(f"   Volume multiplier: {volume_multiplier}x")
                
                # Check all conditions
                conditions_met = True
                failed_conditions = []

                logger.info("=== CHECKING ALL CONDITIONS ===")

                # 1. Check day high condition
                logger.info("1. Checking day high condition...")
                if not self.check_day_high_condition(current_price, day_high, day_high_max_percent_off):
                    conditions_met = False
                    failed_conditions.append("day_high")
                    logger.info("   ❌ Day high condition FAILED")
                else:
                    logger.info("   ✓ Day high condition PASSED")
                    
                # 2. Check day low condition  
                if conditions_met:
                    logger.info("2. Checking day low condition...")
                    if not self.check_day_low_condition(day_low, max_day_low):
                        conditions_met = False
                        failed_conditions.append("day_low")
                        logger.info("   ❌ Day low condition FAILED")
                    else:
                        logger.info("   ✓ Day low condition PASSED")
                else:
                    logger.info("2. Skipping day low check (previous condition failed)")

                # 3. Check price momentum
                if conditions_met:
                    logger.info("3. Checking price momentum...")
                    if not self.check_price_momentum(historical_data, recent_interval_seconds, 
                                                historical_interval_seconds, required_increase_percent):
                        conditions_met = False
                        failed_conditions.append("momentum")
                        logger.info("   ❌ Price momentum condition FAILED")
                    else:
                        logger.info("   ✓ Price momentum condition PASSED")
                else:
                    logger.info("3. Skipping price momentum check (previous condition failed)")

                # 4. Check volume requirements
                if conditions_met:
                    logger.info("4. Checking volume requirements...")
                    if not self.check_volume_requirements(historical_data, volume_requirements, volume_multiplier):
                        conditions_met = False
                        failed_conditions.append("volume")
                        logger.info("   ❌ Volume requirements FAILED")
                    else:
                        logger.info("   ✓ Volume requirements PASSED")
                else:
                    logger.info("4. Skipping volume check (previous condition failed)")

                # 5. Check time-in-pivot requirement
                if conditions_met:
                    logger.info("5. Checking time-in-pivot requirement...")
                    if not self.check_time_in_pivot_requirement(current_price, lower_price, adjusted_higher_price,
                                                            time_in_pivot_seconds, time_in_pivot_positions):
                        conditions_met = False
                        failed_conditions.append("time_in_pivot")
                        logger.info("   ❌ Time-in-pivot requirement FAILED")
                    else:
                        logger.info("   ✓ Time-in-pivot requirement PASSED")
                else:
                    logger.info("5. Skipping time-in-pivot check (previous condition failed)")

                # Summary of results
                if conditions_met:
                    logger.info("🎉 ALL CONDITIONS MET! Executing trade...")
                    if self.execute_trade(ticker, lower_price, higher_price):
                        logger.info(f"✅ Trade executed successfully for {ticker}")
                        break
                    else:
                        logger.error(f"❌ Trade execution failed for {ticker}")
                else:
                    logger.info(f"❌ CONDITIONS NOT MET - Failed: {', '.join(failed_conditions)}")
                
                time.sleep(2)  # Check every 2 seconds
                
            except KeyboardInterrupt:
                logger.info("Stopping due to keyboard interrupt")
                time.sleep(10)
                break
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                time.sleep(5)
        
        self.running = False

def parse_volume_requirements(volume_args: List[str]) -> List[Tuple[int, int]]:
    """Parse volume requirement arguments"""
    requirements = []
    
    for arg in volume_args:
        try:
            if '=' in arg:
                time_part, volume_part = arg.split('=', 1)
                if time_part.lower() == 'day':
                    minutes = -1
                else:
                    minutes = int(time_part)
                volume = int(volume_part)
                requirements.append((minutes, volume))
            else:
                logger.error(f"Invalid volume requirement format: {arg}")
        except ValueError:
            logger.error(f"Invalid volume requirement format: {arg}")
            time.sleep(10)
    
    return requirements

def parse_pivot_positions(positions_str: str) -> List[str]:
    """Parse pivot position string into list of positions"""
    if not positions_str:
        return []
    
    valid_positions = ["lower", "middle", "upper", "any"]
    positions = [pos.strip().lower() for pos in positions_str.split(',')]
    
    # Validate positions
    for pos in positions:
        if pos not in valid_positions:
            logger.error(f"Invalid pivot position: {pos}. Valid options: {', '.join(valid_positions)}")
            return []
    
    return positions
  
def is_market_open() -> bool:
    """Check if the market is currently open (9:30 AM - 4:00 PM ET, Monday-Friday)"""
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)
    
    # Check if it's a weekday (Monday=0, Sunday=6)
    if now.weekday() > 4:  # Saturday or Sunday
        return False
    
    # Market hours: 9:30 AM - 4:00 PM ET
    market_open = dt_time(9, 30)
    market_close = dt_time(16, 0)
    current_time = now.time()
    
    return market_open <= current_time <= market_close
  
def minutes_until_market_open() -> int:
    """Calculate minutes until next market open"""
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)
    
    # If it's weekend, calculate time until Monday 9:30 AM
    if now.weekday() > 4:  # Saturday or Sunday
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:  # Sunday
            days_until_monday = 1
        next_open = now.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(days=days_until_monday)
    else:
        # It's a weekday
        market_open_today = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now.time() < dt_time(9, 30):
            # Before market open today
            next_open = market_open_today
        else:
            # After market close today, next open is tomorrow (if it's a weekday)
            if now.weekday() == 4:  # Friday
                next_open = market_open_today + timedelta(days=3)  # Monday
            else:
                next_open = market_open_today + timedelta(days=1)  # Next day
    
    return int((next_open - now).total_seconds() / 60)

def wait_for_market_open():
    """Wait until market opens with precise timing"""
    if is_market_open():
        logger.info("Market is already open!")
        return
    
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)
    
    # Calculate next market open time
    if now.weekday() > 4:  # Weekend
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:  # Sunday
            days_until_monday = 1
        next_open = now.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(days=days_until_monday)
    else:
        # Weekday
        market_open_today = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now.time() < dt_time(9, 30):
            next_open = market_open_today
        else:
            # After market close, next open is next business day
            if now.weekday() == 4:  # Friday
                next_open = market_open_today + timedelta(days=3)  # Monday
            else:
                next_open = market_open_today + timedelta(days=1)
    
    # Calculate wait time
    wait_seconds = (next_open - now).total_seconds()
    hours = int(wait_seconds // 3600)
    minutes = int((wait_seconds % 3600) // 60)
    
    logger.info(f"Market closed. Waiting {hours}h {minutes}m until market open at {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Sleep until market open (with 1 second buffer to ensure market is open)
    time.sleep(wait_seconds + 1)
    
    logger.info("Market is now open!")


def main():
    parser = argparse.ArgumentParser(description='Stock Trading Bot')
    parser.add_argument('ticker', help='Stock ticker symbol')
    parser.add_argument('lower_price', type=float, help='Lower pivot price')
    parser.add_argument('higher_price', type=float, help='Higher pivot price')
    parser.add_argument('--volume', action='append', default=[], 
                       help='Volume requirements in format "minutes=volume" or "day=volume". Can be specified multiple times.')
    parser.add_argument('--pivot-adjustment', choices=['0.0', '0.5', '1.0'], default='0.0',
                       help='Increase upper pivot price by 0.0%, 0.5%, or 1.0%')
    parser.add_argument('--recent-interval', type=int, default=20,
                       help='Recent time interval in seconds for momentum check (default: 20)')
    parser.add_argument('--historical-interval', type=int, default=600,
                       help='Historical time interval in seconds for momentum check (default: 600 = 10 minutes)')
    parser.add_argument('--momentum-increase', type=float, default=0.05,
                       help='Required price increase percentage for momentum check (default: 0.05)')
    parser.add_argument('--day-high-max-percent-off', type=float, default=0.5,
                       help='Maximum percentage the current price can be below day high (default: 0.5)')
    parser.add_argument('--time-in-pivot', type=int, default=0,
                       help='Required time in seconds that price must be in pivot range (default: 0 = no requirement)')
    parser.add_argument('--time-in-pivot-positions', type=str, default='',
                       help='Comma-separated list of pivot positions where time requirement applies. Options: lower, middle, upper, any (default: empty = no requirement)')
    parser.add_argument('--data-server', default='http://localhost:5001',
                       help='Data server URL')
    parser.add_argument('--trade-server', default='http://localhost:5002',
                       help='Trade server URL')
    parser.add_argument('--volume-multipliers', nargs=3, type=float, metavar=('LOWER', 'MIDDLE', 'UPPER'),
                    default=[1.0, 0.75, 0.5],
                    help='Volume multipliers for lower, middle, and upper pivot positions (default: 1.0 0.75 0.5)')
    parser.add_argument('--max-day-low', type=float, default=None,
                   help='Maximum day low price allowed (default: None = no limit)')
    
    args = parser.parse_args()
    
    # Parse volume requirements
    volume_requirements = parse_volume_requirements(args.volume)
    
    # Parse pivot positions
    time_in_pivot_positions = parse_pivot_positions(args.time_in_pivot_positions)
    
    # Convert pivot adjustment to decimal
    pivot_adjustment = float(args.pivot_adjustment) / 100.0
    
    # Create and run the bot
    bot = StockTradingBot(args.data_server, args.trade_server)
    
    try:
        bot.monitor_and_trade(
        ticker=args.ticker.upper(),
        lower_price=args.lower_price,
        higher_price=args.higher_price,
        volume_requirements=volume_requirements,
        pivot_adjustment=pivot_adjustment,
        recent_interval_seconds=args.recent_interval,
        historical_interval_seconds=args.historical_interval,
        required_increase_percent=args.momentum_increase,
        day_high_max_percent_off=args.day_high_max_percent_off,
        time_in_pivot_seconds=args.time_in_pivot,
        time_in_pivot_positions=time_in_pivot_positions,
        volume_multipliers=args.volume_multipliers,
        max_day_low=args.max_day_low
    )

        
        # Wait for user to stop the bot
        while bot.running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot failed with error: {str(e)}")
        time.sleep(10)

if __name__ == "__main__":
    main()