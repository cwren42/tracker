#!/usr/bin/env python3
"""
Cirque IT Asset Tracker - Linux Monitoring Agent
Lightweight agent for RedHat/CentOS/Ubuntu Linux servers
"""

import os
import sys
import json
import time
import socket
import subprocess
import platform
import psutil
import requests
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
AGENT_ID = None
CONFIG_FILE = "/etc/cirque-agent/config.json"
STATE_FILE = "/var/lib/cirque-agent/state.json"

class LinuxMonitoringAgent:
    def __init__(self, config_path=CONFIG_FILE):
        self.config = self.load_config(config_path)
        self.agent_id = self.config.get('agent_id') or self.generate_agent_id()
        self.server_url = self.config.get('server_url', 'https://tracker.corp.cirque.com')
        self.api_key = self.config.get('api_key')
        self.check_interval = self.config.get('check_interval', 300)  # 5 minutes default
        self.asset_id = self.config.get('asset_id')
        
    def load_config(self, config_path):
        """Load agent configuration from file"""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
        return {}
    
    def save_config(self):
        """Save agent configuration"""
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump({
                'agent_id': self.agent_id,
                'server_url': self.server_url,
                'api_key': self.api_key,
                'check_interval': self.check_interval,
                'asset_id': self.asset_id
            }, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)
    
    def generate_agent_id(self):
        """Generate unique agent ID based on hostname and MAC"""
        hostname = socket.gethostname()
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff) 
                       for elements in range(0,2*6,2)][::-1])
        return f"linux-{hostname}-{mac}"
    
    def collect_system_info(self):
        """Collect basic system information"""
        try:
            uname = platform.uname()
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            
            return {
                'hostname': socket.gethostname(),
                'fqdn': socket.getfqdn(),
                'os': uname.system,
                'os_version': uname.release,
                'os_distribution': self.get_distribution(),
                'architecture': uname.machine,
                'kernel': uname.release,
                'uptime_seconds': int(time.time() - psutil.boot_time()),
                'boot_time': boot_time.isoformat(),
                'cpu_count': psutil.cpu_count(),
                'memory_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
                'agent_version': VERSION,
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            print(f"Error collecting system info: {e}")
            return {}
    
    def get_distribution(self):
        """Get Linux distribution info"""
        try:
            # Try /etc/os-release first (modern Linux)
            if os.path.exists('/etc/os-release'):
                with open('/etc/os-release', 'r') as f:
                    for line in f:
                        if line.startswith('PRETTY_NAME='):
                            return line.split('=')[1].strip().strip('"')
            
            # Try lsb_release command
            result = subprocess.run(['lsb_release', '-d'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.split(':')[1].strip()
            
            # Fallback to checking specific files
            if os.path.exists('/etc/redhat-release'):
                with open('/etc/redhat-release', 'r') as f:
                    return f.read().strip()
            elif os.path.exists('/etc/debian_version'):
                with open('/etc/debian_version', 'r') as f:
                    return f"Debian {f.read().strip()}"
        except Exception as e:
            print(f"Error detecting distribution: {e}")
        
        return "Unknown"
    
    def collect_metrics(self):
        """Collect current system metrics"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Network I/O
            net_io = psutil.net_io_counters()
            
            # Load average
            load_avg = os.getloadavg()
            
            return {
                'cpu_percent': cpu_percent,
                'cpu_count': psutil.cpu_count(),
                'load_average_1': load_avg[0],
                'load_average_5': load_avg[1],
                'load_average_15': load_avg[2],
                'memory_total_mb': memory.total // (1024**2),
                'memory_used_mb': memory.used // (1024**2),
                'memory_free_mb': memory.available // (1024**2),
                'memory_percent': memory.percent,
                'disk_total_gb': disk.total / (1024**3),
                'disk_used_gb': disk.used / (1024**3),
                'disk_free_gb': disk.free / (1024**3),
                'disk_percent': disk.percent,
                'network_bytes_sent': net_io.bytes_sent,
                'network_bytes_recv': net_io.bytes_recv,
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            print(f"Error collecting metrics: {e}")
            return {}
    
    def collect_disk_info(self):
        """Collect information about all mounted filesystems"""
        disks = []
        try:
            for partition in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    disks.append({
                        'device': partition.device,
                        'mountpoint': partition.mountpoint,
                        'fstype': partition.fstype,
                        'total_gb': round(usage.total / (1024**3), 2),
                        'used_gb': round(usage.used / (1024**3), 2),
                        'free_gb': round(usage.free / (1024**3), 2),
                        'percent': usage.percent
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"Error collecting disk info: {e}")
        
        return disks
    
    def check_service_status(self, service_name):
        """Check if a systemd service is running"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service_name],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() == 'active'
        except Exception:
            return None
    
    def get_running_services(self):
        """Get list of active systemd services"""
        services = []
        try:
            result = subprocess.run(
                ['systemctl', 'list-units', '--type=service', '--state=running', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n')[1:]:  # Skip header
                    if '.service' in line:
                        parts = line.split()
                        if parts:
                            services.append(parts[0].replace('.service', ''))
        except Exception as e:
            print(f"Error getting services: {e}")
        
        return services
    
    def check_package_updates(self):
        """Check for available package updates"""
        updates = {'available': 0, 'security': 0}
        
        try:
            # RedHat/CentOS/Rocky
            if os.path.exists('/usr/bin/yum') or os.path.exists('/usr/bin/dnf'):
                cmd = 'dnf' if os.path.exists('/usr/bin/dnf') else 'yum'
                result = subprocess.run(
                    [cmd, 'check-update', '-q'],
                    capture_output=True, text=True, timeout=30
                )
                # yum check-update returns 100 if updates are available
                if result.returncode == 100:
                    updates['available'] = len([l for l in result.stdout.split('\n') if l.strip() and not l.startswith(' ')])
                
                # Check for security updates
                security_result = subprocess.run(
                    [cmd, 'updateinfo', 'list', 'security', '-q'],
                    capture_output=True, text=True, timeout=30
                )
                if security_result.returncode == 0:
                    updates['security'] = len([l for l in security_result.stdout.split('\n') if l.strip()])
            
            # Debian/Ubuntu
            elif os.path.exists('/usr/bin/apt-get'):
                # Update package lists
                subprocess.run(['apt-get', 'update', '-qq'], timeout=60, check=True)
                
                result = subprocess.run(
                    ['apt-get', '-s', 'upgrade'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'upgraded,' in line:
                            parts = line.split()
                            if parts[0].isdigit():
                                updates['available'] = int(parts[0])
                
                # Check for security updates
                security_result = subprocess.run(
                    ['apt-get', '-s', 'upgrade', '-o', 'Dir::Etc::SourceList=/etc/apt/sources.list.d/security.list'],
                    capture_output=True, text=True, timeout=30
                )
                # Parse security updates
                for line in security_result.stdout.split('\n'):
                    if 'upgraded,' in line:
                        parts = line.split()
                        if parts[0].isdigit():
                            updates['security'] = int(parts[0])
        
        except Exception as e:
            print(f"Error checking updates: {e}")
        
        return updates
    
    def send_heartbeat(self):
        """Send heartbeat with system info to tracker server"""
        try:
            data = {
                'agent_id': self.agent_id,
                'asset_id': self.asset_id,
                'system_info': self.collect_system_info(),
                'metrics': self.collect_metrics(),
                'disks': self.collect_disk_info(),
                'services_running': len(self.get_running_services()),
                'updates': self.check_package_updates()
            }
            
            headers = {
                'Content-Type': 'application/json',
                'X-API-Key': self.api_key
            }
            
            response = requests.post(
                f"{self.server_url}/api/linux-agent/heartbeat",
                json=data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                print(f"Heartbeat sent successfully at {datetime.now().isoformat()}")
                return True
            else:
                print(f"Heartbeat failed: HTTP {response.status_code}")
                return False
        
        except Exception as e:
            print(f"Error sending heartbeat: {e}")
            return False
    
    def run_check(self, check_config):
        """Execute a specific monitoring check"""
        check_type = check_config.get('type')
        result = {'success': False, 'message': '', 'value': None}
        
        try:
            if check_type == 'cpu':
                cpu_percent = psutil.cpu_percent(interval=1)
                result = {
                    'success': True,
                    'value': cpu_percent,
                    'message': f"CPU usage: {cpu_percent}%"
                }
            
            elif check_type == 'memory':
                memory = psutil.virtual_memory()
                result = {
                    'success': True,
                    'value': memory.percent,
                    'message': f"Memory usage: {memory.percent}%"
                }
            
            elif check_type == 'disk':
                path = check_config.get('path', '/')
                disk = psutil.disk_usage(path)
                result = {
                    'success': True,
                    'value': disk.percent,
                    'message': f"Disk usage ({path}): {disk.percent}%"
                }
            
            elif check_type == 'service':
                service_name = check_config.get('service_name')
                is_running = self.check_service_status(service_name)
                result = {
                    'success': True,
                    'value': 'running' if is_running else 'stopped',
                    'message': f"Service {service_name} is {'running' if is_running else 'stopped'}"
                }
            
            elif check_type == 'port':
                port = check_config.get('port')
                host = check_config.get('host', 'localhost')
                is_open = self.check_port(host, port)
                result = {
                    'success': True,
                    'value': 'open' if is_open else 'closed',
                    'message': f"Port {port} on {host} is {'open' if is_open else 'closed'}"
                }
            
            elif check_type == 'custom_script':
                script = check_config.get('script')
                script_result = subprocess.run(
                    script, shell=True, capture_output=True, 
                    text=True, timeout=check_config.get('timeout', 60)
                )
                result = {
                    'success': script_result.returncode == 0,
                    'value': script_result.returncode,
                    'message': script_result.stdout.strip() or script_result.stderr.strip()
                }
        
        except Exception as e:
            result = {
                'success': False,
                'message': f"Check failed: {str(e)}",
                'value': None
            }
        
        return result
    
    def check_port(self, host, port):
        """Check if a port is open"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, int(port)))
            sock.close()
            return result == 0
        except Exception:
            return False
    
    def run_daemon(self):
        """Run agent as a daemon"""
        print(f"Starting Cirque Linux Agent v{VERSION}")
        print(f"Agent ID: {self.agent_id}")
        print(f"Server: {self.server_url}")
        print(f"Check interval: {self.check_interval} seconds")
        
        while True:
            try:
                self.send_heartbeat()
            except Exception as e:
                print(f"Error in daemon loop: {e}")
            
            time.sleep(self.check_interval)


def main():
    import argparse
    import uuid
    
    parser = argparse.ArgumentParser(description='Cirque Linux Monitoring Agent')
    parser.add_argument('--install', action='store_true', help='Install agent')
    parser.add_argument('--config', help='Configuration file path')
    parser.add_argument('--server-url', help='Tracker server URL')
    parser.add_argument('--api-key', help='API key for authentication')
    parser.add_argument('--asset-id', help='Asset ID to associate with')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--test', action='store_true', help='Test connection')
    
    args = parser.parse_args()
    
    if args.install:
        print("Installing Cirque Linux Agent...")
        agent = LinuxMonitoringAgent()
        
        if args.server_url:
            agent.server_url = args.server_url
        if args.api_key:
            agent.api_key = args.api_key
        if args.asset_id:
            agent.asset_id = args.asset_id
        
        agent.save_config()
        
        # Create systemd service
        service_content = f"""[Unit]
Description=Cirque IT Asset Tracker - Linux Monitoring Agent
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 {os.path.abspath(__file__)} --daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
        
        with open('/etc/systemd/system/cirque-agent.service', 'w') as f:
            f.write(service_content)
        
        os.system('systemctl daemon-reload')
        os.system('systemctl enable cirque-agent.service')
        
        print("✓ Agent installed successfully")
        print("Start with: systemctl start cirque-agent")
        return
    
    if args.test:
        print("Testing connection to tracker server...")
        agent = LinuxMonitoringAgent(args.config or CONFIG_FILE)
        if agent.send_heartbeat():
            print("✓ Connection test successful")
        else:
            print("✗ Connection test failed")
        return
    
    if args.daemon:
        agent = LinuxMonitoringAgent(args.config or CONFIG_FILE)
        agent.run_daemon()
    else:
        print("Use --install to install the agent or --daemon to run it")
        parser.print_help()


if __name__ == '__main__':
    main()
