#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog Supervisor for RTU Client
Monitors RTU process and restarts it if it crashes or freezes.
"""

import os
import sys
import time
import subprocess
import logging
import signal
from datetime import datetime

# Configuration
RTU_SCRIPT = "rtu_client.py"
HEARTBEAT_FILE = "/tmp/rtu_heartbeat"
CHECK_INTERVAL = 5      # Check every 5 seconds
HEARTBEAT_TIMEOUT = 30  # Restart if heartbeat older than 30 seconds
RESTART_DELAY = 5       # Wait 5 seconds before restarting

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WATCHDOG] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Watchdog")

class WatchdogSupervisor:
    def __init__(self):
        self.process = None
        self.running = True
        self.script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), RTU_SCRIPT)
        
        # Ensure heartbeat file is clean
        if os.path.exists(HEARTBEAT_FILE):
            try:
                os.remove(HEARTBEAT_FILE)
            except:
                pass

    def start_process(self):
        """Start the RTU client process"""
        logger.info(f"Starting {RTU_SCRIPT}...")
        try:
            # Use same python interpreter
            python_exe = sys.executable
            self.process = subprocess.Popen([python_exe, self.script_path])
            logger.info(f"Process started with PID {self.process.pid}")
            return True
        except Exception as e:
            logger.error(f"Failed to start process: {e}")
            return False

    def stop_process(self):
        """Stop the RTU client process"""
        if self.process:
            logger.info(f"Stopping process {self.process.pid}...")
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Process did not terminate, killing...")
                    self.process.kill()
            except Exception as e:
                logger.error(f"Error stopping process: {e}")
            self.process = None

    def check_heartbeat(self):
        """Check if heartbeat file is updated"""
        if not os.path.exists(HEARTBEAT_FILE):
            # If file doesn't exist yet, give it some grace period after start
            return True
            
        try:
            mtime = os.path.getmtime(HEARTBEAT_FILE)
            age = time.time() - mtime
            
            if age > HEARTBEAT_TIMEOUT:
                logger.warning(f"Heartbeat timeout! Last update was {age:.1f}s ago")
                return False
            return True
        except Exception as e:
            logger.error(f"Error checking heartbeat: {e}")
            return True # Assume OK on file error to avoid rapid restart loops

    def run(self):
        """Main supervisor loop"""
        logger.info("Watchdog Supervisor started")
        
        self.start_process()
        
        while self.running:
            try:
                # 1. Check if process is still alive
                if self.process.poll() is not None:
                    logger.warning(f"Process exited with code {self.process.returncode}")
                    self.process = None
                    time.sleep(RESTART_DELAY)
                    self.start_process()
                    continue
                
                # 2. Check heartbeat (Freeze detection)
                if not self.check_heartbeat():
                    logger.warning("Process frozen (heartbeat timeout). Restarting...")
                    self.stop_process()
                    time.sleep(RESTART_DELAY)
                    self.start_process()
                    continue
                
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Supervisor stopping...")
                self.stop_process()
                self.running = False
            except Exception as e:
                logger.error(f"Supervisor error: {e}")
                time.sleep(RESTART_DELAY)

if __name__ == "__main__":
    supervisor = WatchdogSupervisor()
    supervisor.run()
