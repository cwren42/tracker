#!/usr/bin/env python3
"""
Cirque RMM SSH Terminal Manager
Provides web-based SSH access to managed assets
"""

import json
import logging
import re
import paramiko
import threading
import time
from datetime import datetime
from io import StringIO

# Strip ANSI/VT100 escape sequences
_ANSI_ESCAPE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SSHSession:
    """Manages an SSH session to a remote host"""
    
    def __init__(self, session_id, hostname, username, password=None, key=None, port=22, shell='auto'):
        self.session_id = session_id
        self.hostname = hostname
        self.username = username
        self.password = password
        self.key = key
        self.port = port
        self.shell = shell  # 'auto', 'powershell', 'cmd', 'bash'
        
        self.client = None
        self.channel = None
        self.connected = False
        self.output_buffer = []
        self.lock = threading.Lock()
        
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()
    
    def connect(self):
        """Establish SSH connection"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect
            if self.key:
                # Use key authentication
                key_file = StringIO(self.key)
                pkey = paramiko.RSAKey.from_private_key(key_file)
                self.client.connect(
                    self.hostname,
                    port=self.port,
                    username=self.username,
                    pkey=pkey,
                    timeout=10,
                    banner_timeout=10,
                    auth_timeout=10
                )
            else:
                # Use password authentication
                self.client.connect(
                    self.hostname,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=10,
                    banner_timeout=10,
                    auth_timeout=10
                )
            
            # Open shell channel
            self.channel = self.client.invoke_shell(term='xterm', width=220, height=50)
            self.channel.setblocking(0)
            
            self.connected = True
            logger.info(f"SSH session {self.session_id} connected to {self.hostname}")
            
            # Launch PowerShell if requested
            if self.shell == 'powershell':
                time.sleep(0.5)  # Wait for shell banner
                self.channel.send('powershell.exe\r\n')
            
            # Start output reader thread
            self.reader_thread = threading.Thread(target=self._read_output)
            self.reader_thread.daemon = True
            self.reader_thread.start()
            
            return True
        
        except Exception as e:
            logger.error(f"SSH connection failed: {e}")
            self.connected = False
            return False
    
    def _read_output(self):
        """Read output from SSH channel (runs in background thread)"""
        while self.connected and self.channel:
            try:
                if self.channel.recv_ready():
                    raw = self.channel.recv(4096).decode('utf-8', errors='replace')
                    # Normalise Windows CRLF → LF, strip ANSI escape codes
                    data = _ANSI_ESCAPE.sub('', raw.replace('\r\n', '\n').replace('\r', '\n'))
                    
                    with self.lock:
                        self.output_buffer.append({
                            'timestamp': datetime.utcnow().isoformat(),
                            'data': data
                        })
                        self.last_activity = datetime.utcnow()
                        
                        # Keep buffer size reasonable
                        if len(self.output_buffer) > 1000:
                            self.output_buffer.pop(0)
                
                time.sleep(0.01)  # Small delay to prevent CPU spinning
            
            except Exception as e:
                logger.error(f"Error reading SSH output: {e}")
                break
    
    def send_input(self, data):
        """Send input to SSH channel"""
        try:
            if self.channel and self.connected:
                self.channel.send(data)
                self.last_activity = datetime.utcnow()
                return True
            return False
        
        except Exception as e:
            logger.error(f"Error sending SSH input: {e}")
            return False
    
    def get_output(self, since_index=0):
        """Get output buffer since specified index"""
        with self.lock:
            return self.output_buffer[since_index:]
    
    def resize(self, width, height):
        """Resize terminal"""
        try:
            if self.channel and self.connected:
                self.channel.resize_pty(width=width, height=height)
                return True
            return False
        
        except Exception as e:
            logger.error(f"Error resizing terminal: {e}")
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        self.connected = False
        
        if self.channel:
            try:
                self.channel.close()
            except:
                pass
        
        if self.client:
            try:
                self.client.close()
            except:
                pass
        
        logger.info(f"SSH session {self.session_id} disconnected")
    
    def is_alive(self):
        """Check if session is still alive"""
        if not self.connected or not self.channel:
            return False
        
        try:
            transport = self.client.get_transport()
            return transport and transport.is_active()
        except:
            return False


class SSHManager:
    """Manages multiple SSH sessions"""
    
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_dead_sessions)
        self.cleanup_thread.daemon = True
        self.cleanup_thread.start()
    
    def create_session(self, session_id, hostname, username, password=None, key=None, port=22, shell='auto'):
        """Create a new SSH session"""
        with self.lock:
            # Close existing session if any
            if session_id in self.sessions:
                self.sessions[session_id].disconnect()
            
            # Create new session
            session = SSHSession(session_id, hostname, username, password, key, port, shell)
            self.sessions[session_id] = session
            
            # Connect
            if session.connect():
                return session
            else:
                del self.sessions[session_id]
                return None
    
    def get_session(self, session_id):
        """Get an existing session"""
        with self.lock:
            return self.sessions.get(session_id)
    
    def close_session(self, session_id):
        """Close and remove a session"""
        with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id].disconnect()
                del self.sessions[session_id]
                return True
            return False
    
    def _cleanup_dead_sessions(self):
        """Cleanup dead or idle sessions (background thread)"""
        while True:
            try:
                time.sleep(60)  # Check every minute
                
                now = datetime.utcnow()
                to_remove = []
                
                with self.lock:
                    for session_id, session in self.sessions.items():
                        # Remove if not alive
                        if not session.is_alive():
                            to_remove.append(session_id)
                            continue
                        
                        # Remove if idle for > 30 minutes
                        idle_minutes = (now - session.last_activity).total_seconds() / 60
                        if idle_minutes > 30:
                            logger.info(f"Closing idle session {session_id}")
                            to_remove.append(session_id)
                
                # Remove sessions outside of lock
                for session_id in to_remove:
                    self.close_session(session_id)
            
            except Exception as e:
                logger.error(f"Error in cleanup thread: {e}")


# Global SSH manager instance
ssh_manager = SSHManager()


def get_ssh_manager():
    """Get the global SSH manager instance"""
    return ssh_manager
