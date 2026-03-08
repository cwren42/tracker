"""
SSH Terminal Manager for web-based SSH sessions
Provides SSH connectivity to Linux servers with session management
"""

import paramiko
import threading
import time
import io
from datetime import datetime, timedelta

class SSHSession:
    """Individual SSH session"""
    
    def __init__(self, session_id, ssh_client, channel):
        self.session_id = session_id
        self.ssh_client = ssh_client
        self.channel = channel
        self.output_buffer = io.StringIO()
        self.output_index = 0
        self.last_activity = datetime.now()
        self.running = True
        
        # Start output reader thread
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
    
    def _read_output(self):
        """Read output from channel continuously"""
        while self.running and not self.channel.closed:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096).decode('utf-8', errors='ignore')
                    self.output_buffer.write(data)
                    self.last_activity = datetime.now()
                else:
                    time.sleep(0.1)
            except Exception as e:
                print(f"Error reading SSH output: {e}")
                break
    
    def send_input(self, data):
        """Send input to channel"""
        try:
            self.channel.send(data)
            self.last_activity = datetime.now()
            return True
        except Exception as e:
            print(f"Error sending SSH input: {e}")
            return False
    
    def get_output(self, since_index=0):
        """Get output since given index"""
        try:
            current_output = self.output_buffer.getvalue()
            new_output = current_output[since_index:]
            return new_output
        except Exception as e:
            print(f"Error getting SSH output: {e}")
            return ""
    
    def close(self):
        """Close SSH session"""
        self.running = False
        try:
            if self.channel:
                self.channel.close()
            if self.ssh_client:
                self.ssh_client.close()
        except Exception as e:
            print(f"Error closing SSH session: {e}")
    
    def is_active(self):
        """Check if session is still active"""
        if self.channel.closed:
            return False
        
        # Check for timeout (30 minutes)
        if datetime.now() - self.last_activity > timedelta(minutes=30):
            return False
        
        return True


class SSHManager:
    """Manage multiple SSH sessions"""
    
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_inactive, daemon=True)
        self.cleanup_thread.start()
    
    def create_session(self, session_id, hostname, username, password=None, key=None, port=22):
        """Create new SSH session"""
        try:
            # Create SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect
            if password:
                ssh_client.connect(
                    hostname=hostname,
                    port=port,
                    username=username,
                    password=password,
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False
                )
            elif key:
                key_file = io.StringIO(key)
                pkey = paramiko.RSAKey.from_private_key(key_file)
                ssh_client.connect(
                    hostname=hostname,
                    port=port,
                    username=username,
                    pkey=pkey,
                    timeout=10
                )
            else:
                return None
            
            # Create shell channel
            channel = ssh_client.invoke_shell(term='xterm', width=80, height=24)
            channel.settimeout(0.1)
            
            # Create session
            session = SSHSession(session_id, ssh_client, channel)
            
            with self.lock:
                self.sessions[session_id] = session
            
            return session
        
        except Exception as e:
            print(f"Error creating SSH session: {e}")
            return None
    
    def get_session(self, session_id):
        """Get existing session"""
        with self.lock:
            return self.sessions.get(session_id)
    
    def close_session(self, session_id):
        """Close and remove session"""
        with self.lock:
            session = self.sessions.pop(session_id, None)
            if session:
                session.close()
                return True
            return False
    
    def _cleanup_inactive(self):
        """Cleanup inactive sessions periodically"""
        while True:
            try:
                time.sleep(60)  # Check every minute
                
                with self.lock:
                    inactive_sessions = [
                        sid for sid, session in self.sessions.items()
                        if not session.is_active()
                    ]
                    
                    for sid in inactive_sessions:
                        session = self.sessions.pop(sid)
                        session.close()
                        print(f"Cleaned up inactive session: {sid}")
            
            except Exception as e:
                print(f"Error in SSH cleanup: {e}")
                time.sleep(60)


# Global instance
_ssh_manager = None

def get_ssh_manager():
    """Get or create global SSH manager instance"""
    global _ssh_manager
    if _ssh_manager is None:
        _ssh_manager = SSHManager()
    return _ssh_manager
