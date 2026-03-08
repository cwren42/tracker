#!/bin/bash
#
# Cirque RMM Agent Installer
# Usage: curl -sL https://tracker.corp.cirque.com/agent/install.sh | sudo bash
#

set -e

GATEWAY_URL="${GATEWAY_URL:-https://tracker.corp.cirque.com}"
API_KEY="${API_KEY:-}"
AGENT_VERSION="1.0.0"

echo "============================================"
echo "Cirque RMM Agent Installer v${AGENT_VERSION}"
echo "============================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This installer must be run as root"
    exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    OS_VERSION=$VERSION_ID
else
    echo "Error: Cannot detect OS"
    exit 1
fi

echo "Detected OS: $OS $OS_VERSION"

# Install Python 3 and required packages
echo ""
echo "Installing dependencies..."

if [ "$OS" = "rhel" ] || [ "$OS" = "centos" ] || [ "$OS" = "rocky" ]; then
    # RedHat/CentOS
    yum install -y python3 python3-pip
elif [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    # Debian/Ubuntu
    apt-get update
    apt-get install -y python3 python3-pip
else
    echo "Warning: Unsupported OS, attempting to continue..."
fi

# Install Python dependencies
echo ""
echo "Installing Python packages..."
pip3 install psutil requests

# Download agent
echo ""
echo "Downloading agent..."
mkdir -p /usr/local/lib/cirque-rmm
curl -sL "${GATEWAY_URL}/agent/download" -o /usr/local/lib/cirque-rmm/agent.py

# Make executable
chmod +x /usr/local/lib/cirque-rmm/agent.py

# Create symlink
ln -sf /usr/local/lib/cirque-rmm/agent.py /usr/local/bin/cirque-rmm-agent

# Create config directory
echo ""
echo "Creating configuration...
mkdir -p /etc/cirque-rmm
chmod 777 /etc/cirque-rmm  # Allow agent to write agent.id file

# Create config file if it doesn't exist
if [ ! -f /etc/cirque-rmm/agent.conf ]; then
    cat > /etc/cirque-rmm/agent.conf <<EOF
{
  "gateway_url": "${GATEWAY_URL}",
  "api_key": "${API_KEY}",
  "asset_id": null,
  "check_interval": 300,
  "enabled": true
}
EOF
fi

# Create log directory
mkdir -p /var/log
touch /var/log/cirque-rmm-agent.log
chmod 666 /var/log/cirque-rmm-agent.log  # Allow agent to write logs

# Install systemd service
echo ""
echo "Installing systemd service..."
curl -sL "${GATEWAY_URL}/agent/service" -o /etc/systemd/system/cirque-rmm-agent.service

# Reload systemd
systemctl daemon-reload

# Enable and start service
echo ""
echo "Starting agent..."
systemctl enable cirque-rmm-agent
systemctl start cirque-rmm-agent

# Check status
sleep 2
if systemctl is-active --quiet cirque-rmm-agent; then
    echo ""
    echo "============================================"
    echo "✓ Installation successful!"
    echo "============================================"
    echo ""
    echo "Agent ID: $(cat /etc/cirque-rmm/agent.id 2>/dev/null || echo 'Not yet generated')"
    echo "Status: $(systemctl is-active cirque-rmm-agent)"
    echo ""
    echo "Commands:"
    echo "  Check status: systemctl status cirque-rmm-agent"
    echo "  View logs: journalctl -u cirque-rmm-agent -f"
    echo "  Test agent: cirque-rmm-agent --info"
    echo ""
    echo "Configuration: /etc/cirque-rmm/agent.conf"
    echo ""
else
    echo ""
    echo "Warning: Agent installed but not running"
    echo "Check logs: journalctl -u cirque-rmm-agent -n 50"
fi
