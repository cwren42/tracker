#!/usr/bin/env python3
"""
SSH Terminal Manager
Provides web-based SSH access to Linux servers with session logging
"""

import os
import sys
import json
import logging
import threading
import time
import base64
from datetime import datetime
from pathlib import Path
import paramiko
from cryptography.fernet import Fernet

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SSHTerminalSession:
    """Manages an SSH session for a web terminal"""
    
    def __init__(self, session_id, asset, username, password=None, private_key=None):
        self.session_id = session_id
        self.asset = asset
        self.username = username
        self.password = password
        self.private_key = private_key
        self.client = None
        self.channel = None
        self.is_connected = False
        self.last_activity = time.time()
        self.log_file = None
        self.setup_logging()
    
    def setup_logging(self):
        """Setup session logging for compliance"""
        log_dir = Path('/var/www/tracker/ssh_logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f"{self.asset.name}_{self.username}_{timestamp}_{self.session_id[:8]}.log"
        self.log_file = log_dir / log_filename
        
        with open(self.log_file, 'w') as f:
            f.write(f"SSH Session Log\n")
            f.write(f"Asset: {self.asset.name} ({self.asset.ip_address})\n")
            f.write(f"User: {self.username}\n")
            f.write(f"Started: {datetime.now().isoformat()}\n")
            f.write("=" * 80 + "\n\n")
    
    def connect(self):
        """Establish SSH connection"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            host = self.asset.ip_address or self.asset.name
            port = 22
            
            logger.info(f"Connecting to {host}:{port} as {self.username}")
            
            if self.private_key:
                # Use private key authentication
                key = paramiko.RSAKey.from_private_key_file(self.private_key)
                self.client.connect(
                    hostname=host,
                    port=port,
                    username=self.username,
                    pkey=key,
                    timeout=10
                )
            else:
                # Use password authentication
                self.client.connect(
                    hostname=host,
                    port=port,
                    username=self.username,
                    password=self.password,
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False
                )
            
            # Open interactive shell
            self.channel = self.client.invoke_shell(
                term='xterm-256color',
                width=120,
                height=30
            )
            
            self.is_connected = True
            logger.info(f"SSH connection established to {host}")
            
            return True
        
        except paramiko.AuthenticationException:
            logger.error("SSH Authentication failed")
            return False
        except paramiko.SSHException as e:
            logger.error(f"SSH error: {e}")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def send(self, data):
        """Send data to SSH session"""
        if self.channel and self.is_connected:
            try:
                self.channel.send(data)
                self.last_activity = time.time()
                
                # Log input (be careful with passwords)
                with open(self.log_file, 'a') as f:
                    f.write(data)
                
                return True
            except Exception as e:
                logger.error(f"Error sending data: {e}")
                return False
        return False
    
    def recv(self, size=4096):
        """Receive data from SSH session"""
        if self.channel and self.is_connected:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(size)
                    self.last_activity = time.time()
                    
                    # Log output
                    with open(self.log_file, 'ab') as f:
                        f.write(data)
                    
                    return data
                return b''
            except Exception as e:
                logger.error(f"Error receiving data: {e}")
                return b''
        return b''
    
    def resize(self, width, height):
        """Resize terminal"""
        if self.channel:
            try:
                self.channel.resize_pty(width=width, height=height)
            except Exception as e:
                logger.error(f"Error resizing terminal: {e}")
    
    def close(self):
        """Close SSH session"""
        logger.info(f"Closing SSH session {self.session_id}")
        
        self.is_connected = False
        
        if self.channel:
            self.channel.close()
        
        if self.client:
            self.client.close()
        
        # Write session end to log
        if self.log_file:
            with open(self.log_file, 'a') as f:
                f.write(f"\n\n{'=' * 80}\n")
                f.write(f"Session ended: {datetime.now().isoformat()}\n")
    
    def is_alive(self):
        """Check if session is still alive"""
        if not self.is_connected:
            return False
        
        if self.channel and self.channel.closed:
            return False
        
        # Check for timeout (30 minutes idle)
        if time.time() - self.last_activity > 1800:
            logger.warning(f"Session {self.session_id} timed out")
            return False
        
        return True

class SSHTerminalManager:
    """Manages multiple SSH terminal sessions"""
    
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        self.encryption_key = self.load_or_create_encryption_key()
        
        # Start cleanup thread
        cleanup_thread = threading.Thread(target=self.cleanup_dead_sessions, daemon=True)
        cleanup_thread.start()
    
    def load_or_create_encryption_key(self):
        """Load or create encryption key for storing credentials"""
        key_file = Path('/var/www/tracker/.ssh_encryption_key')
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            # Create new key
            key = Fernet.generate_key()
            key_file.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(key_file.parent, 0o700)
            
            with open(key_file, 'wb') as f:
                f.write(key)
            
            os.chmod(key_file, 0o600)
            logger.info("Created new SSH encryption key")
            return key
    
    def encrypt_password(self, password):
        """Encrypt password for storage"""
        f = Fernet(self.encryption_key)
        return f.encrypt(password.encode()).decode()
    
    def decrypt_password(self, encrypted):
        """Decrypt password"""
        f = Fernet(self.encryption_key)
        return f.decrypt(encrypted.encode()).decode()
    
    def create_session(self, session_id, asset, username, password=None, private_key=None):
        """Create a new SSH session"""
        with self.lock:
            if session_id in self.sessions:
                logger.warning(f"Session {session_id} already exists")
                return None
            
            session = SSHTerminalSession(
                session_id=session_id,
                asset=asset,
                username=username,
                password=password,
                private_key=private_key
            )
            
            if session.connect():
                self.sessions[session_id] = session
                logger.info(f"Created SSH session {session_id} for {asset.name}")
                return session
            else:
                return None
    
    def get_session(self, session_id):
        """Get an existing session"""
        with self.lock:
            return self.sessions.get(session_id)
    
    def close_session(self, session_id):
        """Close and remove a session"""
        with self.lock:
            session = self.sessions.pop(session_id, None)
            if session:
                session.close()
                logger.info(f"Closed session {session_id}")
                return True
            return False
    
    def cleanup_dead_sessions(self):
        """Periodically cleanup dead sessions"""
        while True:
            time.sleep(60)  # Check every minute
            
            with self.lock:
                dead_sessions = []
                
                for session_id, session in self.sessions.items():
                    if not session.is_alive():
                        dead_sessions.append(session_id)
                
                for session_id in dead_sessions:
                    session = self.sessions.pop(session_id)
                    session.close()
                    logger.info(f"Cleaned up dead session {session_id}")
    
    def get_active_session_count(self):
        """Get count of active sessions"""
        with self.lock:
            return len(self.sessions)

# Global instance
ssh_manager = SSHTerminalManager()
