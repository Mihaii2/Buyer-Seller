#!/usr/bin/env python3
from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import os
import sys
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes to allow frontend connections

class TradingBotManager:
    def __init__(self, script_path: str = "price_going_up_optional_volume_script.py"):
        self.script_path = script_path
        self.active_bots: Dict[str, Dict] = {}
        self.bot_lock = threading.Lock()
        
    def validate_script_exists(self) -> bool:
        """Check if the trading bot script exists"""
        return os.path.exists(self.script_path)
    
    def build_command(self, params: Dict) -> List[str]:
        """Build the command to execute the trading bot"""
        if not self.validate_script_exists():
            raise FileNotFoundError(f"Trading bot script not found at: {self.script_path}")
        
        # Required parameters
        ticker = params.get('ticker', '').upper()
        lower_price = params.get('lower_price')
        higher_price = params.get('higher_price')
        
        if not ticker or lower_price is None or higher_price is None:
            raise ValueError("Missing required parameters: ticker, lower_price, higher_price")
        
        # Build base command
        cmd = [
            "powershell.exe",
            "-Command",
            f"python {self.script_path} {ticker} {lower_price} {higher_price}"
        ]
        
        # Add optional parameters
        volume_requirements = params.get('volume_requirements', [])
        for vol_req in volume_requirements:
            cmd[2] += f" --volume {vol_req}"
        
        if params.get('pivot_adjustment'):
            cmd[2] += f" --pivot-adjustment {params['pivot_adjustment']}"
        
        if params.get('recent_interval'):
            cmd[2] += f" --recent-interval {params['recent_interval']}"
        
        if params.get('historical_interval'):
            cmd[2] += f" --historical-interval {params['historical_interval']}"
        
        if params.get('momentum_increase'):
            cmd[2] += f" --momentum-increase {params['momentum_increase']}"
        
        if params.get('day_high_max_percent_off'):
            cmd[2] += f" --day-high-max-percent-off {params['day_high_max_percent_off']}"
        
        if params.get('time_in_pivot'):
            cmd[2] += f" --time-in-pivot {params['time_in_pivot']}"
        
        if params.get('time_in_pivot_positions'):
            cmd[2] += f" --time-in-pivot-positions {params['time_in_pivot_positions']}"
        
        if params.get('data_server'):
            cmd[2] += f" --data-server {params['data_server']}"
        
        if params.get('trade_server'):
            cmd[2] += f" --trade-server {params['trade_server']}"
        
        return cmd
    
    def start_bot(self, params: Dict) -> Dict:
        """Start a new trading bot instance"""
        try:
            # Generate unique bot ID
            bot_id = str(uuid.uuid4())
            
            # Build command
            cmd = self.build_command(params)
            
            # Start the process in a new PowerShell window
            # Use CREATE_NEW_CONSOLE to open in new terminal window
            # Don't redirect stdout/stderr so output appears in the terminal
            process = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            
            # Store bot information
            with self.bot_lock:
                self.active_bots[bot_id] = {
                    'id': bot_id,
                    'ticker': params.get('ticker', '').upper(),
                    'lower_price': params.get('lower_price'),
                    'higher_price': params.get('higher_price'),
                    'process': process,
                    'started_at': datetime.now().isoformat(),
                    'status': 'running',
                    'command': ' '.join(cmd),
                    'params': params
                }
            
            logger.info(f"Started bot {bot_id} for {params.get('ticker', 'UNKNOWN')}")
            
            return {
                'success': True,
                'bot_id': bot_id,
                'message': f"Trading bot started for {params.get('ticker', 'UNKNOWN')}",
                'started_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to start bot: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def stop_bot(self, bot_id: str) -> Dict:
        """Stop a specific trading bot"""
        with self.bot_lock:
            if bot_id not in self.active_bots:
                return {
                    'success': False,
                    'error': f"Bot {bot_id} not found"
                }
            
            bot_info = self.active_bots[bot_id]
            process = bot_info['process']
            
            try:
                # Terminate the process
                process.terminate()
                # Wait a bit for graceful shutdown
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate gracefully
                    process.kill()
                    process.wait()
                
                bot_info['status'] = 'stopped'
                bot_info['stopped_at'] = datetime.now().isoformat()
                
                logger.info(f"Stopped bot {bot_id}")
                
                return {
                    'success': True,
                    'message': f"Bot {bot_id} stopped successfully"
                }
                
            except Exception as e:
                logger.error(f"Error stopping bot {bot_id}: {str(e)}")
                return {
                    'success': False,
                    'error': str(e)
                }
    
    def get_bot_status(self, bot_id: str) -> Optional[Dict]:
        """Get status of a specific bot"""
        with self.bot_lock:
            if bot_id not in self.active_bots:
                return None
            
            bot_info = self.active_bots[bot_id].copy()
            process = bot_info['process']
            
            # Check if process is still running
            if process.poll() is None:
                bot_info['status'] = 'running'
            else:
                bot_info['status'] = 'stopped'
                bot_info['exit_code'] = process.returncode
            
            # Remove the process object from the returned info
            del bot_info['process']
            
            return bot_info
    
    def get_all_bots(self) -> List[Dict]:
        """Get status of all bots"""
        with self.bot_lock:
            bots = []
            for bot_id in self.active_bots:
                bot_info = self.get_bot_status(bot_id)
                if bot_info:
                    bots.append(bot_info)
            return bots
    
    def cleanup_finished_bots(self):
        """Clean up finished bot processes"""
        with self.bot_lock:
            finished_bots = []
            for bot_id, bot_info in self.active_bots.items():
                if bot_info['process'].poll() is not None:
                    finished_bots.append(bot_id)
            
            for bot_id in finished_bots:
                logger.info(f"Cleaning up finished bot {bot_id}")
                # Keep the info but mark as finished
                self.active_bots[bot_id]['status'] = 'finished'
                self.active_bots[bot_id]['finished_at'] = datetime.now().isoformat()

# Initialize the bot manager
bot_manager = TradingBotManager()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'script_exists': bot_manager.validate_script_exists(),
        'script_path': bot_manager.script_path
    })

@app.route('/start_bot', methods=['POST'])
def start_bot():
    """Start a new trading bot instance"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No JSON data provided'
            }), 400
        
        result = bot_manager.start_bot(data)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"Error in start_bot endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/stop_bot/<bot_id>', methods=['POST'])
def stop_bot(bot_id):
    """Stop a specific trading bot"""
    try:
        result = bot_manager.stop_bot(bot_id)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 404
            
    except Exception as e:
        logger.error(f"Error in stop_bot endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/bot_status/<bot_id>', methods=['GET'])
def get_bot_status(bot_id):
    """Get status of a specific bot"""
    try:
        bot_info = bot_manager.get_bot_status(bot_id)
        
        if bot_info:
            return jsonify({
                'success': True,
                'bot': bot_info
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': f'Bot {bot_id} not found'
            }), 404
            
    except Exception as e:
        logger.error(f"Error in get_bot_status endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/bots', methods=['GET'])
def get_all_bots():
    """Get status of all bots"""
    try:
        bots = bot_manager.get_all_bots()
        return jsonify({
            'success': True,
            'bots': bots,
            'count': len(bots)
        }), 200
        
    except Exception as e:
        logger.error(f"Error in get_all_bots endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/stop_all_bots', methods=['POST'])
def stop_all_bots():
    """Stop all running bots"""
    try:
        results = []
        bots = bot_manager.get_all_bots()
        
        for bot in bots:
            if bot['status'] == 'running':
                result = bot_manager.stop_bot(bot['id'])
                results.append({
                    'bot_id': bot['id'],
                    'ticker': bot['ticker'],
                    'result': result
                })
        
        return jsonify({
            'success': True,
            'stopped_bots': results,
            'count': len(results)
        }), 200
        
    except Exception as e:
        logger.error(f"Error in stop_all_bots endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Background task to clean up finished bots
def cleanup_task():
    """Background task to periodically clean up finished bots"""
    while True:
        try:
            bot_manager.cleanup_finished_bots()
            time.sleep(30)  # Run every 30 seconds
        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}")
            time.sleep(30)

# Start cleanup task in background thread
cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    # Check if script exists on startup
    if not bot_manager.validate_script_exists():
        logger.warning(f"Trading bot script not found at: {bot_manager.script_path}")
        logger.warning("Please ensure the script is in the same directory or update the path")
    
    logger.info("Starting Trading Bot Proxy Server...")
    logger.info("Available endpoints:")
    logger.info("  GET  /health - Health check")
    logger.info("  POST /start_bot - Start a new bot")
    logger.info("  POST /stop_bot/<bot_id> - Stop a specific bot")
    logger.info("  GET  /bot_status/<bot_id> - Get bot status")
    logger.info("  GET  /bots - Get all bots")
    logger.info("  POST /stop_all_bots - Stop all bots")
    
    app.run(host='0.0.0.0', port=5003, debug=True)