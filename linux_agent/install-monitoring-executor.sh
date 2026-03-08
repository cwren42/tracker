#!/bin/bash
#
# Cirque RMM Monitoring Executor Installer
# Usage: sudo bash install-monitoring-executor.sh
#
# This installs the monitoring check execution engine that runs checks
# against assets with assigned monitoring profiles.
#

set -e

TRACKER_PATH="${TRACKER_PATH:-/var/www/tracker}"
EXECUTOR_USER="${EXECUTOR_USER:-webuser}"
CHECK_INTERVAL="${CHECK_INTERVAL:-300}"

echo "============================================"
echo "Monitoring Executor Installer"
echo "============================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This installer must be run as root"
    exit 1
fi

# Verify tracker installation exists
if [ ! -f "$TRACKER_PATH/monitoring_executor.py" ]; then
    echo "Error: Tracker installation not found at $TRACKER_PATH"
    echo "Please set TRACKER_PATH environment variable if installed elsewhere"
    exit 1
fi

echo "Found tracker at: $TRACKER_PATH"

# Verify Python venv exists
if [ ! -f "$TRACKER_PATH/venv/bin/python3" ]; then
    echo "Error: Python virtual environment not found at $TRACKER_PATH/venv"
    exit 1
fi

# Verify user exists
if ! id "$EXECUTOR_USER" &>/dev/null; then
    echo "Error: User '$EXECUTOR_USER' does not exist"
    echo "Please set EXECUTOR_USER environment variable to the correct user"
    exit 1
fi

# Create systemd service
echo ""
echo "Creating systemd service..."
cat > /etc/systemd/system/monitoring-executor.service <<EOF
[Unit]
Description=Monitoring Check Execution Engine
After=network.target

[Service]
Type=simple
User=$EXECUTOR_USER
WorkingDirectory=$TRACKER_PATH
ExecStart=$TRACKER_PATH/venv/bin/python3 $TRACKER_PATH/monitoring_executor.py --interval $CHECK_INTERVAL
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

# Enable and start service
echo ""
echo "Starting monitoring executor..."
systemctl enable monitoring-executor
systemctl start monitoring-executor

# Check status
sleep 2
if systemctl is-active --quiet monitoring-executor; then
    echo ""
    echo "============================================"
    echo "✓ Installation successful!"
    echo "============================================"
    echo ""
    echo "Service: monitoring-executor.service"
    echo "Status: $(systemctl is-active monitoring-executor)"
    echo "Check Interval: ${CHECK_INTERVAL}s"
    echo ""
    echo "Commands:"
    echo "  Check status: systemctl status monitoring-executor"
    echo "  View logs: journalctl -u monitoring-executor -f"
    echo "  Run once: $TRACKER_PATH/venv/bin/python3 $TRACKER_PATH/monitoring_executor.py --once"
    echo ""
    echo "Configuration:"
    echo "  Executor Path: $TRACKER_PATH/monitoring_executor.py"
    echo "  Database: $TRACKER_PATH/assets.db"
    echo ""
else
    echo ""
    echo "Warning: Service installed but not running"
    echo "Check logs: journalctl -u monitoring-executor -n 50"
fi
