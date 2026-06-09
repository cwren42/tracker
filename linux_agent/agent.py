#!/usr/bin/env python3
"""
Cirque RMM Linux Agent
Lightweight monitoring agent for Linux servers (RedHat, Ubuntu, Debian)
"""

import asyncio
import json
import os
import platform
import pty
import fcntl
import termios
import struct
import psutil
import requests
import socket
import subprocess
import sys
import time
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path

import websockets
import websockets.exceptions

# Disable SSL warnings when using self-signed certificates
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
AGENT_VERSION = "1.0.0"
CONFIG_FILE = "/etc/cirque-rmm/agent.conf"
LOG_FILE = "/var/log/cirque-rmm-agent.log"
STATE_FILE = "/var/run/cirque-rmm-agent.state"

# Public Cloudflare fallback for HTTP reporting when the configured/internal
# gateway_url is unreachable (LAN-only host name failing to resolve on a cloud VM,
# etc.). Mirrors the Windows agent's internal->public failover. Overridable via
# the "tracker_url_public" config key.
DEFAULT_PUBLIC_TRACKER_URL = "https://tracker.cirquetools.com"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class LinuxMonitoringAgent:
    def __init__(self, config_path=CONFIG_FILE):
        self.config = self.load_config(config_path)
        self.agent_id = self.get_agent_id()
        self.hostname = socket.gethostname()
        self.fqdn = socket.getfqdn()
        # Active base URL for HTTP reporting. Starts at the configured/internal
        # gateway_url; report_to_gateway() fails over to the public URL on a
        # connection error and sticks with whichever last worked.
        self.active_url = self.config.get('gateway_url', '').rstrip('/')
        self.public_url = self.config.get(
            'tracker_url_public', DEFAULT_PUBLIC_TRACKER_URL).rstrip('/')
    
    def load_config(self, path):
        """Load agent configuration"""
        default_config = {
            'gateway_url': 'https://tracker.corp.cirque.com',
            'tracker_url_public': DEFAULT_PUBLIC_TRACKER_URL,
            'api_key': '',
            'asset_id': None,
            'check_interval': 300,  # 5 minutes
            'enabled': True,
            # Remote-control WebSocket is opt-in. Leave gateway_ws_url unset to
            # run telemetry-only (no live-control connection attempts).
            'gateway_ws_url': '',
        }
        
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
        
        return default_config
    
    def get_agent_id(self):
        """Generate or retrieve unique agent ID"""
        id_file = "/etc/cirque-rmm/agent.id"
        
        if os.path.exists(id_file):
            with open(id_file, 'r') as f:
                return f.read().strip()
        
        # Generate new ID from machine-id or create one
        if os.path.exists('/etc/machine-id'):
            with open('/etc/machine-id', 'r') as f:
                agent_id = f'linux-{f.read().strip()[:16]}'
        else:
            import uuid
            agent_id = f'linux-{str(uuid.uuid4())[:16]}'
        
        # Save agent ID
        os.makedirs(os.path.dirname(id_file), exist_ok=True)
        with open(id_file, 'w') as f:
            f.write(agent_id)
        
        return agent_id
    
    def collect_system_info(self):
        """Collect basic system information"""
        try:
            # Get OS details
            os_info = self.get_os_info()
            
            # Get hardware info
            cpu_count = psutil.cpu_count(logical=True)
            cpu_freq = psutil.cpu_freq()
            mem = psutil.virtual_memory()
            
            # Get network interfaces
            network = self.get_network_info()
            
            # Get uptime
            uptime = self.get_uptime()
            
            # Detect virtualization
            virtualization = self.detect_virtualization()
            
            return {
                'agent_id': self.agent_id,
                'hostname': self.hostname,
                'fqdn': self.fqdn,
                'platform': 'linux',
                'os': os_info,
                'virtualization': virtualization,
                'cpu': {
                    'count': cpu_count,
                    'frequency_mhz': cpu_freq.current if cpu_freq else None,
                    'model': self.get_cpu_model()
                },
                'memory': {
                    'total_gb': round(mem.total / (1024**3), 2),
                    'available_gb': round(mem.available / (1024**3), 2)
                },
                'network': network,
                'uptime_seconds': uptime,
                'agent_version': AGENT_VERSION,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Error collecting system info: {e}")
            return {}
    
    def detect_virtualization(self):
        """Detect if running in a virtual machine"""
        try:
            # Try systemd-detect-virt
            result = subprocess.run(['systemd-detect-virt'], capture_output=True, text=True)
            if result.returncode == 0:
                virt_type = result.stdout.strip()
                if virt_type and virt_type != 'none':
                    return {'is_virtual': True, 'type': virt_type}
        except:
            pass
        
        # Try dmidecode
        try:
            result = subprocess.run(['dmidecode', '-s', 'system-manufacturer'], capture_output=True, text=True)
            if result.returncode == 0:
                manufacturer = result.stdout.strip().lower()
                if any(vm in manufacturer for vm in ['qemu', 'vmware', 'virtualbox', 'xen', 'kvm', 'microsoft']):
                    return {'is_virtual': True, 'type': manufacturer}
        except:
            pass
        
        return {'is_virtual': False, 'type': 'physical'}
    
    def get_os_info(self):
        """Get operating system information"""
        try:
            # Try to read /etc/os-release
            if os.path.exists('/etc/os-release'):
                os_info = {}
                with open('/etc/os-release', 'r') as f:
                    for line in f:
                        if '=' in line:
                            key, value = line.strip().split('=', 1)
                            os_info[key] = value.strip('"')
                
                return {
                    'name': os_info.get('NAME', 'Linux'),
                    'version': os_info.get('VERSION', 'Unknown'),
                    'id': os_info.get('ID', 'linux'),
                    'version_id': os_info.get('VERSION_ID', 'unknown'),
                    'pretty_name': os_info.get('PRETTY_NAME', 'Linux')
                }
            else:
                # Fallback to platform module
                return {
                    'name': platform.system(),
                    'version': platform.release(),
                    'pretty_name': platform.platform()
                }
        except Exception as e:
            logger.error(f"Error getting OS info: {e}")
            return {'name': 'Linux', 'version': 'Unknown'}
    
    def get_cpu_model(self):
        """Get CPU model name"""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('model name'):
                        return line.split(':', 1)[1].strip()
        except:
            pass
        return 'Unknown'
    
    def get_network_info(self):
        """Get network interface information"""
        try:
            interfaces = []
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            
            for iface, addr_list in addrs.items():
                if iface == 'lo':
                    continue
                
                iface_info = {
                    'name': iface,
                    'is_up': stats[iface].isup if iface in stats else False,
                    'addresses': []
                }
                
                for addr in addr_list:
                    if addr.family == socket.AF_INET:  # IPv4
                        iface_info['addresses'].append({
                            'type': 'ipv4',
                            'address': addr.address,
                            'netmask': addr.netmask
                        })
                    elif addr.family == psutil.AF_LINK:  # MAC
                        iface_info['mac'] = addr.address
                
                if iface_info['addresses']:
                    interfaces.append(iface_info)
            
            return interfaces
        except Exception as e:
            logger.error(f"Error getting network info: {e}")
            return []
    
    def get_uptime(self):
        """Get system uptime in seconds"""
        try:
            with open('/proc/uptime', 'r') as f:
                return int(float(f.read().split()[0]))
        except:
            return 0
    
    def collect_telemetry(self):
        """Collect current telemetry data"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1, percpu=False)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Network IO
            net_io = psutil.net_io_counters()
            
            # Disk IO
            disk_io = psutil.disk_io_counters()
            
            # Load average
            load_avg = os.getloadavg()
            
            return {
                'agent_id': self.agent_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'cpu': {
                    'usage_percent': cpu_percent,
                    'load_1min': load_avg[0],
                    'load_5min': load_avg[1],
                    'load_15min': load_avg[2]
                },
                'memory': {
                    'total_mb': mem.total // (1024**2),
                    'used_mb': mem.used // (1024**2),
                    'available_mb': mem.available // (1024**2),
                    'usage_percent': mem.percent
                },
                'disk': {
                    'total_gb': disk.total // (1024**3),
                    'used_gb': disk.used // (1024**3),
                    'free_gb': disk.free // (1024**3),
                    'usage_percent': disk.percent
                },
                'network': {
                    'bytes_sent': net_io.bytes_sent,
                    'bytes_recv': net_io.bytes_recv,
                    'packets_sent': net_io.packets_sent,
                    'packets_recv': net_io.packets_recv
                },
                'disk_io': {
                    'read_bytes': disk_io.read_bytes,
                    'write_bytes': disk_io.write_bytes
                } if disk_io else None
            }
        except Exception as e:
            logger.error(f"Error collecting telemetry: {e}")
            return {}
    
    def check_service(self, service_name):
        """Check if a systemd service is running"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() == 'active'
        except:
            return False
    
    def check_port(self, port, protocol='tcp'):
        """Check if a port is listening"""
        try:
            connections = psutil.net_connections(kind=protocol)
            for conn in connections:
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    return True
            return False
        except:
            return False
    
    def get_package_count(self):
        """Get count of installed packages"""
        try:
            # Try RPM (RedHat/CentOS)
            result = subprocess.run(['rpm', '-qa'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return len(result.stdout.strip().split('\n'))
        except:
            pass
        
        try:
            # Try dpkg (Debian/Ubuntu)
            result = subprocess.run(['dpkg', '-l'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return len([l for l in result.stdout.split('\n') if l.startswith('ii')])
        except:
            pass
        
        return 0
    
    def get_pending_updates(self):
        """Get count of pending updates"""
        try:
            # Try yum check-update (RedHat/CentOS)
            result = subprocess.run(
                ['yum', 'check-update', '-q'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 100:  # Updates available
                lines = [l for l in result.stdout.split('\n') if l and not l.startswith('Security:')]
                return len(lines)
            return 0
        except:
            pass
        
        try:
            # Try apt (Debian/Ubuntu)
            subprocess.run(['apt-get', 'update', '-qq'], capture_output=True, timeout=30)
            result = subprocess.run(
                ['apt-get', '-s', 'upgrade'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'upgraded' in line:
                        parts = line.split()
                        if parts and parts[0].isdigit():
                            return int(parts[0])
            return 0
        except:
            pass
        
        return None
    
    def run_check(self, check_config):
        """Run a monitoring check based on configuration"""
        check_type = check_config.get('check_type')
        
        try:
            if check_type == 'cpu':
                cpu_percent = psutil.cpu_percent(interval=1)
                threshold = float(check_config.get('critical_threshold', 90))
                return {
                    'success': cpu_percent < threshold,
                    'value': cpu_percent,
                    'message': f'CPU usage: {cpu_percent}%'
                }
            
            elif check_type == 'memory':
                mem = psutil.virtual_memory()
                threshold = float(check_config.get('critical_threshold', 90))
                return {
                    'success': mem.percent < threshold,
                    'value': mem.percent,
                    'message': f'Memory usage: {mem.percent}%'
                }
            
            elif check_type == 'disk':
                disk = psutil.disk_usage('/')
                threshold = float(check_config.get('critical_threshold', 90))
                return {
                    'success': disk.percent < threshold,
                    'value': disk.percent,
                    'message': f'Disk usage: {disk.percent}%'
                }
            
            elif check_type == 'service':
                service_name = check_config.get('service_name')
                is_active = self.check_service(service_name)
                return {
                    'success': is_active,
                    'value': 'active' if is_active else 'inactive',
                    'message': f'Service {service_name}: {"running" if is_active else "not running"}'
                }
            
            elif check_type == 'port':
                port = int(check_config.get('port', 0))
                is_listening = self.check_port(port)
                return {
                    'success': is_listening,
                    'value': port,
                    'message': f'Port {port}: {"listening" if is_listening else "not listening"}'
                }
            
            else:
                return {
                    'success': False,
                    'error': f'Unknown check type: {check_type}'
                }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def report_to_gateway(self, data, endpoint='/api/rmm/telemetry'):
        """Send data to the gateway, with internal->public URL failover.

        Tries the currently-active base URL first (initially the configured
        gateway_url). On a connection/timeout error it transparently retries
        against the public Cloudflare URL and, if that succeeds, sticks with it
        for subsequent calls. This mirrors the Windows agent so a Linux box works
        on the LAN or in the cloud without a manual GATEWAY_URL override.
        """
        payload = {
            'agent_id': self.agent_id,
            'hostname': self.hostname,
            **data
        }

        # Build the candidate base-URL list: active first, then public fallback
        # (de-duplicated, and only if a public URL is configured).
        bases = [self.active_url]
        if self.public_url and self.public_url not in bases:
            bases.append(self.public_url)

        last_conn_error = None
        for base in bases:
            url = f"{base}{endpoint}"
            try:
                logger.debug(f"Reporting to {url}")
                response = requests.post(
                    url,
                    json=payload,
                    timeout=10,
                    headers={'Content-Type': 'application/json'},
                    verify=False  # Disable SSL verification for self-signed certs
                )

                # We reached a server. Remember this base as active even if the
                # app-level response is an error (it's reachable, just unhappy).
                if base != self.active_url:
                    logger.info(f"Failed over HTTP reporting to {base}")
                    self.active_url = base

                if response.status_code == 200:
                    result = response.json()
                    if result.get('ok'):
                        logger.info(f"Successfully reported to {endpoint}: {result.get('message', '')}")
                        return True
                    logger.warning(f"Gateway returned error: {result.get('error')}")
                    return False
                logger.warning(f"Gateway returned status {response.status_code}: {response.text}")
                return False

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                # Connection-level failure: try the next base URL.
                last_conn_error = e
                logger.warning(f"Connection problem reaching {url}: {e}")
                continue
            except Exception as e:
                logger.error(f"Error reporting to gateway ({url}): {e}")
                return False

        logger.error(f"All gateway URLs unreachable (last error: {last_conn_error})")
        return False
    
    def run_once(self):
        """Run a single monitoring cycle"""
        logger.info(f"Running monitoring cycle for agent {self.agent_id}")
        
        # Collect and report system info (less frequently)
        sys_info = self.collect_system_info()
        self.report_to_gateway(sys_info, '/api/rmm/system-info')
        
        # Collect and report telemetry
        telemetry = self.collect_telemetry()
        self.report_to_gateway(telemetry, '/api/rmm/telemetry')
        
        logger.info("Monitoring cycle complete")
    
    def run_daemon(self):
        """Run as a daemon — HTTP telemetry loop + (optional) WebSocket gateway.

        The live-control WebSocket is OPT-IN: it is only attempted when a
        dedicated `gateway_ws_url` is set in the config. There is no Linux
        remote-control gateway today (the real gateway is Windows-only), so by
        default we run telemetry-only and never attempt a WS connection — this
        avoids an endless 404 retry storm against the HTTP tracker, which does
        not serve /ws/agent/<id>.
        """
        logger.info(f"Starting Cirque RMM Agent {AGENT_VERSION}")
        logger.info(f"Agent ID: {self.agent_id}")
        logger.info(f"Hostname: {self.hostname}")

        ws_base = self.config.get('gateway_ws_url', '').strip()
        if not ws_base:
            # Telemetry-only: just run the HTTP loop in the foreground. Log once.
            logger.info("remote-control gateway not configured (gateway_ws_url unset); "
                        "running telemetry-only")
            self._http_loop()
            return

        # Remote-control configured: HTTP loop in a thread, WS in the main thread.
        t = threading.Thread(target=self._http_loop, daemon=True)
        t.start()
        asyncio.run(self._ws_loop(ws_base))

    def _http_loop(self):
        """Periodically POST telemetry/system-info over HTTP."""
        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"HTTP loop error: {e}")
            time.sleep(self.config.get('check_interval', 60))

    async def _ws_loop(self, ws_base):
        """Maintain a persistent WebSocket connection to the configured gateway.

        Only called when `gateway_ws_url` is explicitly configured (see
        run_daemon). The WS base is taken from config and NOT derived from the
        HTTP tracker URL, so we never point live-control at a server that can't
        serve it.
        """
        token = self.config.get('agent_token', '')
        ws_base = ws_base.rstrip('/')
        url = f"{ws_base}/ws/agent/{self.agent_id}?token={token}"

        backoff = 5
        while True:
            try:
                ssl_ctx = True  # use default SSL verification
                # Allow self-signed certs
                import ssl
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

                logger.info(f"Connecting to gateway WebSocket: {ws_base}/ws/agent/{self.agent_id}")
                async with websockets.connect(url, ssl=ssl_ctx, ping_interval=30, ping_timeout=10) as ws:
                    backoff = 5  # reset on successful connect
                    logger.info("Gateway WebSocket connected")
                    await self._ws_session(ws)
            except websockets.exceptions.InvalidStatusCode as e:
                logger.warning(f"Gateway WS rejected: {e} — retrying in {backoff}s")
            except Exception as e:
                logger.warning(f"Gateway WS error: {e} — retrying in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    # Active PTY shells: session_id -> (master_fd, proc)
    _shells: dict = {}

    async def _ws_session(self, ws):
        """Handle one WebSocket session with the gateway."""
        recv_task = asyncio.create_task(self._recv_loop(ws))
        try:
            await recv_task
        except Exception as e:
            logger.warning(f"WS session ended: {e}")
        finally:
            recv_task.cancel()
            # Kill any open shells
            for sid, (master_fd, proc) in list(self._shells.items()):
                try:
                    proc.kill()
                    os.close(master_fd)
                except Exception:
                    pass
            self._shells.clear()

    async def _recv_loop(self, ws):
        """Receive and dispatch messages from the gateway."""
        loop = asyncio.get_event_loop()
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get('type')
            if t == 'hello':
                logger.info("Gateway said hello")
            elif t == 'ping':
                await ws.send(json.dumps({'type': 'pong'}))
            elif t == 'shell_start':
                await self._shell_start(ws, msg, loop)
            elif t == 'shell_input':
                self._shell_write(msg)
            elif t == 'shell_stop':
                self._shell_stop(msg.get('session_id'))
            elif t == 'shell_resize':
                self._shell_resize(msg)

    async def _shell_start(self, ws, msg, loop):
        """Spawn a PTY bash shell and start streaming output."""
        session_id = msg.get('session_id')
        shell = msg.get('shell', 'bash')

        # Kill existing shell for this session if any
        self._shell_stop(session_id)

        try:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                ['/bin/bash', '-i'],
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                close_fds=True, preexec_fn=os.setsid,
            )
            os.close(slave_fd)
            # Keep master in blocking mode — reads happen in a thread via run_in_executor

            self._shells[session_id] = (master_fd, proc)

            await ws.send(json.dumps({'type': 'shell_started', 'session_id': session_id}))
            logger.info(f"Shell started for session {session_id}")

            # Stream output in background
            asyncio.get_event_loop().create_task(
                self._stream_output(ws, session_id, master_fd, proc)
            )
        except Exception as e:
            await ws.send(json.dumps({'type': 'error', 'error': f'shell_start failed: {e}', 'session_id': session_id}))
            logger.error(f"shell_start error: {e}")

    async def _stream_output(self, ws, session_id, master_fd, proc):
        """Read PTY output (blocking, in executor) and forward to gateway."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await loop.run_in_executor(None, self._read_pty, master_fd)
                if data is None:
                    break  # EIO — shell exited
                if data:
                    await ws.send(json.dumps({
                        'type': 'shell_output',
                        'session_id': session_id,
                        'data': data.decode('utf-8', errors='replace'),
                    }))
        except Exception as e:
            logger.debug(f"stream_output ended: {e}")
        finally:
            self._shell_stop(session_id)
            try:
                await ws.send(json.dumps({'type': 'shell_exited', 'session_id': session_id}))
            except Exception:
                pass

    def _read_pty(self, master_fd):
        """Blocking read from PTY master. Returns bytes on data, None on EIO (shell exited)."""
        import errno as _errno
        try:
            return os.read(master_fd, 4096)
        except OSError as e:
            if e.errno == _errno.EIO:
                return None  # Normal: slave side closed (shell exited)
            raise  # Re-raise unexpected errors so _stream_output catches them

    def _shell_write(self, msg):
        """Write input data to the PTY."""
        session_id = msg.get('session_id')
        data = msg.get('data', '')
        if session_id in self._shells:
            master_fd, _ = self._shells[session_id]
            try:
                os.write(master_fd, data.encode('utf-8', errors='replace'))
            except Exception as e:
                logger.debug(f"shell_write error: {e}")

    def _shell_stop(self, session_id):
        """Kill a running shell."""
        if session_id in self._shells:
            master_fd, proc = self._shells.pop(session_id)
            try:
                proc.kill()
            except Exception:
                pass
            try:
                os.close(master_fd)
            except Exception:
                pass

    def _shell_resize(self, msg):
        """Resize the PTY terminal."""
        session_id = msg.get('session_id')
        rows = msg.get('rows', 24)
        cols = msg.get('cols', 80)
        if session_id in self._shells:
            master_fd, _ = self._shells[session_id]
            try:
                winsize = struct.pack('HHHH', rows, cols, 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Cirque RMM Linux Agent')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--config', default=CONFIG_FILE, help='Config file path')
    parser.add_argument('--info', action='store_true', help='Show system info and exit')
    
    args = parser.parse_args()
    
    agent = LinuxMonitoringAgent(config_path=args.config)
    
    if args.info:
        info = agent.collect_system_info()
        telemetry = agent.collect_telemetry()
        print(json.dumps({
            'system_info': info,
            'telemetry': telemetry
        }, indent=2))
        return 0
    
    if args.once:
        agent.run_once()
        return 0

    try:
        agent.run_daemon()
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
    return 0


if __name__ == '__main__':
    sys.exit(main())
