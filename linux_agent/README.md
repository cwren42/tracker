# Cirque RMM Linux Agent

Lightweight monitoring agent for Linux servers (RedHat, Ubuntu, Debian).

## Features

- System information collection (OS, CPU, memory, network)
- Real-time telemetry (CPU, memory, disk, network I/O)
- Service monitoring (systemd services)
- Port monitoring
- Package and update tracking
- Configurable check execution
- Systemd integration with auto-restart
- Secure reporting to RMM gateway

## Installation

### Quick Install (Recommended)

```bash
curl -sL https://tracker.corp.cirque.com/agent/install.sh | sudo bash
```

### Manual Installation

1. Install dependencies:
```bash
# RedHat/CentOS
sudo yum install -y python3 python3-pip

# Ubuntu/Debian
sudo apt-get install -y python3 python3-pip
```

2. Install Python packages:
```bash
sudo pip3 install psutil requests
```

3. Copy agent:
```bash
sudo mkdir -p /usr/local/lib/cirque-rmm
sudo cp agent.py /usr/local/lib/cirque-rmm/
sudo chmod +x /usr/local/lib/cirque-rmm/agent.py
sudo ln -s /usr/local/lib/cirque-rmm/agent.py /usr/local/bin/cirque-rmm-agent
```

4. Create configuration:
```bash
sudo mkdir -p /etc/cirque-rmm
sudo cat > /etc/cirque-rmm/agent.conf <<EOF
{
  "gateway_url": "https://tracker.corp.cirque.com",
  "api_key": "your-api-key-here",
  "asset_id": null,
  "check_interval": 300,
  "enabled": true
}
EOF
```

5. Install systemd service:
```bash
sudo cp cirque-rmm-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cirque-rmm-agent
sudo systemctl start cirque-rmm-agent
```

## Monitoring Executor (Server-Side)

The monitoring executor runs on the tracker server and executes checks against assets with assigned monitoring profiles.

### Install Monitoring Executor

```bash
# Quick install
sudo bash /var/www/tracker/linux_agent/install-monitoring-executor.sh

# Or with custom settings
TRACKER_PATH=/var/www/tracker CHECK_INTERVAL=300 sudo bash install-monitoring-executor.sh
```

## Usage

### Test Agent

```bash
# Show system info and telemetry
sudo cirque-rmm-agent --info

# Run one monitoring cycle
sudo cirque-rmm-agent --once
```

### Service Management

```bash
# Check status
sudo systemctl status cirque-rmm-agent

# View logs
sudo journalctl -u cirque-rmm-agent -f

# Restart
sudo systemctl restart cirque-rmm-agent

# Stop
sudo systemctl stop cirque-rmm-agent
```

## Configuration

Edit `/etc/cirque-rmm/agent.conf`:

```json
{
  "gateway_url": "https://tracker.corp.cirque.com",
  "api_key": "your-api-key-here",
  "asset_id": null,
  "check_interval": 300,
  "enabled": true
}
```

- `gateway_url`: URL of the RMM gateway
- `api_key`: Authentication API key
- `asset_id`: Asset ID from tracker (auto-registered if null)
- `check_interval`: Seconds between monitoring cycles (default: 300)
- `enabled`: Enable/disable agent

## Monitoring Checks

The agent supports these check types:

- **cpu**: CPU usage percentage
- **memory**: Memory usage percentage
- **disk**: Disk usage percentage
- **service**: Systemd service status
- **port**: Port listening status

Checks are configured in the tracker web interface and pushed to the agent.

## Security

- Agent runs as root (required for system monitoring)
- API key authentication
- HTTPS communication only
- No incoming connections (outbound only)

## Logs

- Service logs: `/var/log/cirque-rmm-agent.log`
- Systemd journal: `journalctl -u cirque-rmm-agent`
- State file: `/var/run/cirque-rmm-agent.state`

## Uninstall

```bash
sudo systemctl stop cirque-rmm-agent
sudo systemctl disable cirque-rmm-agent
sudo rm /etc/systemd/system/cirque-rmm-agent.service
sudo rm -rf /etc/cirque-rmm
sudo rm -rf /usr/local/lib/cirque-rmm
sudo rm /usr/local/bin/cirque-rmm-agent
sudo systemctl daemon-reload
```
