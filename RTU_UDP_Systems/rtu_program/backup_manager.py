"""
Backup Manager for RTU
Version: 1.1.0

Changes in 1.1.0:
- Added get_h01_backup_by_device() for device-specific backup retrieval
- Added has_h01_backup_by_device() for device-specific backup check
- Modified save_failed_packet() to parse device_type/device_number from packet

Changes in 1.0.9:
- Added save_failed_packet() for immediate backup on NETWORK_DOWN
- Backup data sent one-by-one with each new H01 transmission

Changes in 1.4.0:
- Added get_event_backup() for H05 event recovery
- Added mark_event_sent() for H05 event tracking
- Added has_event_backups() check
- Added auto recovery mode activation on communication restore
- Added backup statistics logging
- Changed DB path to absolute path (/home/pi/rtu_backup.db)

Changes in 1.3.0:
- Added mark_retry() method for response error handling
- Added retry count tracking for failed responses
- Max retry count: 3 (then save to DB backup)

Changes in 1.2.0:
- Added register_event() method for event backup (POWER_OUTAGE, etc.)
- Added events table in database
"""

import sqlite3
import struct
import time
import threading
import logging
import os
from collections import deque
from dataclasses import dataclass

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol_constants import *

# 패킷 헤더에서 rtu_id 위치: '>BHIQBBBBb' → offset 3, 4바이트
_RTU_ID_OFFSET = 3
_RTU_ID_SIZE   = 4

# Max retry count for response errors
RESPONSE_MAX_RETRY = 3

# Default backup DB path (absolute)
DEFAULT_DB_PATH = '/home/pi/rtu_backup.db'


@dataclass
class PendingAck:
    packet: bytes
    sequence: int
    sent_time: float
    device_type: int
    device_number: int
    retry_count: int = 0


class BackupManager:
    """Enhanced backup manager with H01/H05 recovery support"""
    
    VERSION = "1.1.0"
    
    def __init__(self, rtu_id: int, db_path: str = None):
        self.rtu_id = rtu_id
        
        # Use absolute path for DB
        if db_path is None:
            # Try /home/pi first, fallback to current directory
            if os.path.exists('/home/pi'):
                self.db_path = DEFAULT_DB_PATH
            else:
                self.db_path = os.path.join(os.getcwd(), 'rtu_backup.db')
        else:
            self.db_path = db_path
        
        self.pending = {}
        self.retry_queue = deque(maxlen=200)
        self.lock = threading.Lock()
        self.logger = logging.getLogger(__name__)
        
        self.consecutive_failures = 0
        self.communication_lost = False
        self.recovery_mode = False
        self.last_ack_time = time.time()
        
        # Statistics
        self.stats = {
            'h01_backed_up': 0,
            'h01_recovered': 0,
            'h05_backed_up': 0,
            'h05_recovered': 0,
            'total_retries': 0
        }
        
        self._db_conn = None  # Persistent DB connection
        self._init_db()
        self.logger.info(f"BackupManager v{self.VERSION} initialized (DB: {self.db_path})")

    def _get_conn(self):
        """Get or create persistent DB connection (call inside self.lock)."""
        if self._db_conn is None:
            self._db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._db_conn.execute("PRAGMA journal_mode=WAL")
            self._db_conn.execute("PRAGMA synchronous=NORMAL")
            self._db_conn.execute("PRAGMA busy_timeout=5000")
        else:
            # Verify connection is still valid
            try:
                self._db_conn.execute("SELECT 1")
            except Exception:
                self.logger.warning("DB connection invalid, reconnecting")
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._db_conn.execute("PRAGMA journal_mode=WAL")
                self._db_conn.execute("PRAGMA synchronous=NORMAL")
                self._db_conn.execute("PRAGMA busy_timeout=5000")
        return self._db_conn

    def close(self):
        """Close persistent DB connection."""
        with self.lock:
            if self._db_conn:
                try:
                    self._db_conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    self._db_conn.close()
                except Exception as e:
                    self.logger.warning(f"DB close error: {e}")
                self._db_conn = None
    
    def _init_db(self):
        """Initialize database"""
        with self.lock:
            conn = self._get_conn()
            
            # H01 backups table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY,
                    rtu_id INTEGER,
                    device_type INTEGER,
                    device_number INTEGER,
                    sequence INTEGER,
                    packet BLOB,
                    timestamp INTEGER,
                    sent INTEGER DEFAULT 0
                )
            ''')
            
            # H05 events table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    rtu_id INTEGER,
                    sequence INTEGER,
                    packet BLOB,
                    event_type TEXT,
                    timestamp INTEGER,
                    sent INTEGER DEFAULT 0
                )
            ''')
            
            # Create index for faster queries
            conn.execute('CREATE INDEX IF NOT EXISTS idx_backups_sent ON backups(sent)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_events_sent ON events(sent)')
            
            # Keep unsent backups on startup (recover after power loss)
            # Only clean up old sent records and expired unsent records
            cur = conn.execute('SELECT COUNT(*) FROM backups WHERE sent=0')
            h01_count = cur.fetchone()[0]
            cur = conn.execute('SELECT COUNT(*) FROM events WHERE sent=0')
            h05_count = cur.fetchone()[0]

            # Delete already-sent records (housekeeping)
            conn.execute('DELETE FROM backups WHERE sent=1')
            conn.execute('DELETE FROM events WHERE sent=1')

            # Delete expired unsent records (older than retention period)
            cutoff = int(time.time()) - BACKUP_RETENTION_HOURS * 3600
            expired_h01 = conn.execute('SELECT COUNT(*) FROM backups WHERE sent=0 AND timestamp < ?', (cutoff,)).fetchone()[0]
            expired_h05 = conn.execute('SELECT COUNT(*) FROM events WHERE sent=0 AND timestamp < ?', (cutoff,)).fetchone()[0]
            conn.execute('DELETE FROM backups WHERE sent=0 AND timestamp < ?', (cutoff,))
            conn.execute('DELETE FROM events WHERE sent=0 AND timestamp < ?', (cutoff,))

            remaining_h01 = h01_count - expired_h01
            remaining_h05 = h05_count - expired_h05

            if remaining_h01 > 0 or remaining_h05 > 0:
                self.recovery_mode = True
                self.logger.info(
                    f"Startup: {remaining_h01} H01 + {remaining_h05} H05 pending backups (recovery mode)")
            if expired_h01 > 0 or expired_h05 > 0:
                self.logger.info(
                    f"Startup: expired {expired_h01} H01 + {expired_h05} H05 records (>{BACKUP_RETENTION_HOURS}h)")

            conn.commit()

    def _patch_rtu_id(self, packet: bytes) -> bytes:
        """저장된 패킷의 rtu_id를 현재 rtu_id로 교체 (구 ID 백업 전송 방지)"""
        if len(packet) >= _RTU_ID_OFFSET + _RTU_ID_SIZE:
            stored_id = struct.unpack_from('>I', packet, _RTU_ID_OFFSET)[0]
            if stored_id != self.rtu_id:
                packet = bytearray(packet)
                struct.pack_into('>I', packet, _RTU_ID_OFFSET, self.rtu_id)
                packet = bytes(packet)
        return packet

    def clear_all(self):
        """RTU 시작 시 백업 DB 전체 초기화 (구 rtu_id 잔여 데이터 제거)"""
        try:
            conn = self._get_conn()
            conn.execute('DELETE FROM backups')
            conn.execute('DELETE FROM events')
            conn.commit()
            self.recovery_mode = False
            self.logger.info("Backup DB cleared on startup (fresh session)")
        except Exception as e:
            self.logger.error(f"Failed to clear backup DB: {e}")

    def _log_backup_status(self):
        """Log current backup status"""
        try:
            conn = self._get_conn()
            cur = conn.execute('SELECT COUNT(*) FROM backups WHERE sent=0')
            h01_count = cur.fetchone()[0]
            cur = conn.execute('SELECT COUNT(*) FROM events WHERE sent=0')
            h05_count = cur.fetchone()[0]
            pass  # Persistent connection, don't close
            
            if h01_count > 0 or h05_count > 0:
                self.logger.info(f"Pending backups: H01={h01_count}, H05={h05_count}")
                self.recovery_mode = True
        except Exception as e:
            self.logger.error(f"Failed to check backup status: {e}")
    
    def register_sent(self, sequence: int, packet: bytes,
                      device_type: int, device_number: int):
        """Register sent H01 packet for ACK tracking"""
        with self.lock:
            self.pending[sequence] = PendingAck(
                packet, sequence, time.time(), device_type, device_number)
    
    def register_event(self, sequence: int, packet: bytes, event_type: str):
        """Register H05 event packet for backup"""
        with self.lock:
            # Add to pending for ACK tracking
            self.pending[sequence] = PendingAck(
                packet, sequence, time.time(), DEVICE_RTU, 0)
            
            # Save to events table for persistence
            try:
                conn = self._get_conn()
                conn.execute('''
                    INSERT INTO events (rtu_id, sequence, packet, event_type, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                ''', (self.rtu_id, sequence, packet, event_type, int(time.time())))
                conn.commit()
                pass  # Persistent connection, don't close
                self.stats['h05_backed_up'] += 1
                self.logger.info(f"H05 event backed up: {event_type} (seq={sequence})")
            except Exception as e:
                self.logger.error(f"Failed to save event: {e}")
    
    def receive_ack(self, sequence: int):
        """Handle ACK (response=0, success)"""
        with self.lock:
            if sequence in self.pending:
                self.pending.pop(sequence)
                self.consecutive_failures = 0
                self.last_ack_time = time.time()
                
                if self.communication_lost:
                    self.communication_lost = False
                    self.recovery_mode = True  # Activate recovery mode
                    self.logger.info("Communication restored - Recovery mode activated")
                return True
        return False
    
    def mark_retry(self, sequence: int):
        """Handle response error (response=-1), schedule retry
        
        Returns:
            tuple: (retry_scheduled, retry_count) or (False, 0) if not found
        """
        with self.lock:
            if sequence not in self.pending:
                return False, 0
            
            entry = self.pending[sequence]
            entry.retry_count += 1
            self.stats['total_retries'] += 1
            
            if entry.retry_count <= RESPONSE_MAX_RETRY:
                # Schedule retry
                entry.sent_time = time.time()
                self.retry_queue.append(entry)
                self.logger.warning(f"Response error seq={sequence}, retry {entry.retry_count}/{RESPONSE_MAX_RETRY}")
                return True, entry.retry_count
            else:
                # Max retries exceeded, save to DB and remove
                self._save_to_db(entry)
                self.pending.pop(sequence)
                self.consecutive_failures += 1
                self.logger.error(f"Max retries exceeded seq={sequence}, saved to backup DB")
                
                if self.consecutive_failures >= 3:
                    self.communication_lost = True
                    self.logger.warning("Communication lost detected (3 consecutive failures)")
                
                return False, entry.retry_count
    
    def check_timeouts(self):
        """Check for timeouts"""
        failed = []
        current = time.time()
        
        with self.lock:
            to_remove = []
            
            for seq, entry in self.pending.items():
                if current - entry.sent_time > H01_ACK_TIMEOUT:
                    if entry.retry_count < MAX_RETRY_COUNT:
                        entry.retry_count += 1
                        entry.sent_time = current
                        if len(self.retry_queue) >= self.retry_queue.maxlen:
                            dropped = self.retry_queue[0]
                            self._save_to_db(dropped)
                            self.logger.warning(f"Retry queue full, saving oldest to DB (seq={dropped.sequence})")
                        self.retry_queue.append(entry)
                        self.stats['total_retries'] += 1
                        self.logger.debug(f"Timeout seq={seq}, retry {entry.retry_count}")
                    else:
                        failed.append(entry)
                        to_remove.append(seq)
                        self._save_to_db(entry)
            
            for seq in to_remove:
                self.pending.pop(seq, None)
            
            if failed:
                self.consecutive_failures += len(failed)
                if self.consecutive_failures >= 3:
                    self.communication_lost = True
                    self.logger.warning("Communication lost detected (timeout failures)")
        
        return failed
    
    def _save_to_db(self, entry):
        """Save H01 packet to database"""
        try:
            conn = self._get_conn()
            conn.execute('''
                INSERT INTO backups (rtu_id, device_type, device_number, sequence, packet, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (self.rtu_id, entry.device_type, entry.device_number,
                  entry.sequence, entry.packet, int(time.time())))
            conn.commit()
            pass  # Persistent connection, don't close
            self.stats['h01_backed_up'] += 1
            self.logger.info(f"H01 backed up: DEV{entry.device_type}-{entry.device_number} (seq={entry.sequence})")
        except Exception as e:
            self.logger.error(f"Failed to save backup: {e}")
    
    def save_failed_packet(self, packet: bytes):
        """Save failed packet directly to backup DB (called on NETWORK_DOWN)

        Args:
            packet: Protocol packet bytes that failed to send
        """
        if len(packet) < HEADER_SIZE:
            self.logger.warning(f"Packet too short for backup: {len(packet)} bytes (need {HEADER_SIZE})")
            return
        try:
            # Extract from header: >BHIQBBBBb (20 bytes)
            # B(1)=version, H(2)=sequence, I(4)=rtu_id, Q(8)=ts, B=dev_type, B=dev_num, ...
            import struct
            v = struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
            sequence = v[1]
            device_type = v[4]
            device_number = v[5]

            with self.lock:
                conn = self._get_conn()
                conn.execute('''
                    INSERT INTO backups (rtu_id, device_type, device_number, sequence, packet, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (self.rtu_id, device_type, device_number, sequence, packet, int(time.time())))
                conn.commit()
            self.stats['h01_backed_up'] += 1
            self.logger.info(f"Packet saved to backup DB - DEV{device_type}-{device_number} (seq={sequence})")
        except Exception as e:
            self.logger.error(f"Failed to save failed packet: {e}")
    
    def get_retries(self):
        """Get retry packets"""
        with self.lock:
            retries = list(self.retry_queue)
            self.retry_queue.clear()
            return retries
    
    # =========================================================================
    # H01 Backup Recovery
    # =========================================================================
    
    def get_h01_backup(self):
        """Get one H01 backup for recovery
        
        Returns:
            dict: Backup data or None
        """
        try:
            conn = self._get_conn()
            cur = conn.execute('''
                SELECT id, packet, device_type, device_number, sequence, timestamp 
                FROM backups WHERE sent=0 ORDER BY timestamp ASC LIMIT 1
            ''')
            row = cur.fetchone()
            pass  # Persistent connection, don't close
            
            if row:
                return {
                    'id': row[0],
                    'packet': self._patch_rtu_id(row[1]),
                    'device_type': row[2],
                    'device_number': row[3],
                    'sequence': row[4],
                    'timestamp': row[5]
                }
        except Exception as e:
            self.logger.error(f"Failed to get H01 backup: {e}")
        return None

    def get_h01_backup_by_device(self, device_type: int, device_number: int):
        """Get one H01 backup for specific device
        
        Args:
            device_type: Device type (DEVICE_INVERTER, DEVICE_RELAY, etc.)
            device_number: Device number
        
        Returns:
            dict: Backup data or None
        """
        try:
            conn = self._get_conn()
            cur = conn.execute('''
                SELECT id, packet, device_type, device_number, sequence, timestamp 
                FROM backups WHERE sent=0 AND device_type=? AND device_number=?
                ORDER BY timestamp ASC LIMIT 1
            ''', (device_type, device_number))
            row = cur.fetchone()
            pass  # Persistent connection, don't close
            
            if row:
                return {
                    'id': row[0],
                    'packet': self._patch_rtu_id(row[1]),
                    'device_type': row[2],
                    'device_number': row[3],
                    'sequence': row[4],
                    'timestamp': row[5]
                }
        except Exception as e:
            self.logger.error(f"Failed to get H01 backup by device: {e}")
        return None
    
    def has_h01_backup_by_device(self, device_type: int, device_number: int) -> bool:
        """Check if has pending H01 backup for specific device
        
        Args:
            device_type: Device type
            device_number: Device number
        
        Returns:
            bool: True if backup exists
        """
        try:
            conn = self._get_conn()
            cur = conn.execute(
                'SELECT COUNT(*) FROM backups WHERE sent=0 AND device_type=? AND device_number=?',
                (device_type, device_number))
            count = cur.fetchone()[0]
            pass  # Persistent connection, don't close
            return count > 0
        except:
            return False
    
    def mark_h01_sent(self, backup_id: int):
        """Mark H01 backup as sent"""
        try:
            conn = self._get_conn()
            conn.execute('UPDATE backups SET sent=1 WHERE id=?', (backup_id,))
            conn.commit()
            pass  # Persistent connection, don't close
            self.stats['h01_recovered'] += 1
        except Exception as e:
            self.logger.error(f"Failed to mark H01 sent: {e}")
    
    def has_h01_backups(self):
        """Check if has pending H01 backups"""
        try:
            conn = self._get_conn()
            cur = conn.execute('SELECT COUNT(*) FROM backups WHERE sent=0')
            count = cur.fetchone()[0]
            pass  # Persistent connection, don't close
            return count > 0
        except:
            return False
    
    def get_h01_backup_count(self):
        """Get pending H01 backup count"""
        try:
            conn = self._get_conn()
            cur = conn.execute('SELECT COUNT(*) FROM backups WHERE sent=0')
            count = cur.fetchone()[0]
            pass  # Persistent connection, don't close
            return count
        except:
            return 0
    
    # =========================================================================
    # H05 Event Recovery
    # =========================================================================
    
    def get_h05_backup(self):
        """Get one H05 event backup for recovery
        
        Returns:
            dict: Event data or None
        """
        try:
            conn = self._get_conn()
            cur = conn.execute('''
                SELECT id, packet, sequence, event_type, timestamp 
                FROM events WHERE sent=0 ORDER BY timestamp ASC LIMIT 1
            ''')
            row = cur.fetchone()
            pass  # Persistent connection, don't close
            
            if row:
                return {
                    'id': row[0],
                    'packet': row[1],
                    'sequence': row[2],
                    'event_type': row[3],
                    'timestamp': row[4]
                }
        except Exception as e:
            self.logger.error(f"Failed to get H05 backup: {e}")
        return None
    
    def mark_h05_sent(self, event_id: int):
        """Mark H05 event as sent"""
        try:
            conn = self._get_conn()
            conn.execute('UPDATE events SET sent=1 WHERE id=?', (event_id,))
            conn.commit()
            pass  # Persistent connection, don't close
            self.stats['h05_recovered'] += 1
        except Exception as e:
            self.logger.error(f"Failed to mark H05 sent: {e}")
    
    def has_h05_backups(self):
        """Check if has pending H05 backups"""
        try:
            conn = self._get_conn()
            cur = conn.execute('SELECT COUNT(*) FROM events WHERE sent=0')
            count = cur.fetchone()[0]
            pass  # Persistent connection, don't close
            return count > 0
        except:
            return False
    
    def get_h05_backup_count(self):
        """Get pending H05 backup count"""
        try:
            conn = self._get_conn()
            cur = conn.execute('SELECT COUNT(*) FROM events WHERE sent=0')
            count = cur.fetchone()[0]
            pass  # Persistent connection, don't close
            return count
        except:
            return 0
    
    # =========================================================================
    # Legacy compatibility methods
    # =========================================================================
    
    def get_backup(self):
        """Legacy: Get one backup (H01)"""
        return self.get_h01_backup()
    
    def mark_sent(self, backup_id: int):
        """Legacy: Mark backup as sent"""
        self.mark_h01_sent(backup_id)
    
    def has_backups(self):
        """Check if has any pending backups (H01 or H05)"""
        return self.has_h01_backups() or self.has_h05_backups()
    
    # =========================================================================
    # Utility methods
    # =========================================================================
    
    def cleanup(self, hours: int = BACKUP_RETENTION_HOURS):
        """Delete old sent backups"""
        cutoff = time.time() - hours * 3600
        try:
            conn = self._get_conn()
            conn.execute('DELETE FROM backups WHERE timestamp < ? AND sent=1', (cutoff,))
            conn.execute('DELETE FROM events WHERE timestamp < ? AND sent=1', (cutoff,))
            conn.commit()
            pass  # Persistent connection, don't close
        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")
    
    def is_lost(self):
        """Check if communication is lost"""
        return self.communication_lost
    
    def set_recovery(self, enabled: bool):
        """Set recovery mode"""
        self.recovery_mode = enabled
        if not enabled:
            self.logger.info("Recovery mode completed")
    
    def is_recovery(self):
        """Check if in recovery mode"""
        return self.recovery_mode
    
    def get_stats(self):
        """Get backup statistics"""
        return {
            **self.stats,
            'pending_h01': self.get_h01_backup_count(),
            'pending_h05': self.get_h05_backup_count(),
            'communication_lost': self.communication_lost,
            'recovery_mode': self.recovery_mode
        }
    
    def print_status(self):
        """Print backup status"""
        stats = self.get_stats()
        print(f"\n{'='*50}")
        print(f"BACKUP STATUS")
        print(f"{'='*50}")
        print(f"  Pending H01: {stats['pending_h01']}")
        print(f"  Pending H05: {stats['pending_h05']}")
        print(f"  H01 Backed Up: {stats['h01_backed_up']}")
        print(f"  H01 Recovered: {stats['h01_recovered']}")
        print(f"  H05 Backed Up: {stats['h05_backed_up']}")
        print(f"  H05 Recovered: {stats['h05_recovered']}")
        print(f"  Total Retries: {stats['total_retries']}")
        print(f"  Comm Lost: {stats['communication_lost']}")
        print(f"  Recovery Mode: {stats['recovery_mode']}")
        print(f"{'='*50}")
    
    # Compatibility methods for rtu_client.py
    def add_pending(self, sequence: int, packet: bytes):
        """Add pending packet for ACK tracking (simplified version)"""
        with self.lock:
            self.pending[sequence] = PendingAck(packet, sequence, time.time(), 0, 0)
    
    def remove_pending(self, sequence: int):
        """Remove pending packet (alias for receive_ack)"""
        self.receive_ack(sequence)
    
    def get_pending(self):
        """Get pending packets for retry (alias for get_retries)"""
        return self.get_retries()
