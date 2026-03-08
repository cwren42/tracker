"""
Migration: Add Device Monitoring Profiles System
Allows different monitoring configurations per device type (AD servers, web servers, etc.)
"""
import sqlite3

DB_PATH = '/var/www/tracker/assets.db'

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Monitoring Profile - Template for what to monitor on a device type
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            device_type TEXT NOT NULL,  -- Server, Desktop, Laptop, Network, Linux
            os_family TEXT,  -- Windows, Linux, Proxmox, Network
            severity_level TEXT DEFAULT 'standard',  -- critical, high, standard, low
            check_interval_minutes INTEGER DEFAULT 15,  -- How often to run checks
            enabled INTEGER DEFAULT 1,
            is_template INTEGER DEFAULT 1,  -- Built-in template vs custom
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    
    # 2. Monitoring Check - Individual check definition (CPU, disk, service, etc.)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_check (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type TEXT NOT NULL,  -- cpu, memory, disk, service, port, process, certificate, url, ping
            name TEXT NOT NULL,
            description TEXT,
            script_type TEXT,  -- powershell, bash, python, wmi, api
            script_content TEXT,  -- Script to execute the check
            timeout_seconds INTEGER DEFAULT 30,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    
    # 3. Profile-Check Association with thresholds
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profile_check (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            check_id INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1,
            check_interval_override INTEGER,  -- Override profile default interval
            warning_threshold TEXT,  -- JSON: {"cpu": 80, "memory": 85}
            critical_threshold TEXT,  -- JSON: {"cpu": 95, "memory": 95}
            parameters TEXT,  -- JSON: {"service_name": "DNS", "port": 53}
            FOREIGN KEY (profile_id) REFERENCES monitoring_profile(id) ON DELETE CASCADE,
            FOREIGN KEY (check_id) REFERENCES monitoring_check(id) ON DELETE CASCADE,
            UNIQUE(profile_id, check_id)
        )
    """)
    
    # 4. Asset-Profile Assignment
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asset_monitoring_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            assigned_at TEXT DEFAULT (datetime('now')),
            assigned_by INTEGER,
            notes TEXT,
            FOREIGN KEY (asset_id) REFERENCES asset(id) ON DELETE CASCADE,
            FOREIGN KEY (profile_id) REFERENCES monitoring_profile(id),
            FOREIGN KEY (assigned_by) REFERENCES user(id),
            UNIQUE(asset_id)  -- One profile per asset
        )
    """)
    
    # 5. Monitoring Alerts/Results
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_alert (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            check_id INTEGER NOT NULL,
            severity TEXT NOT NULL,  -- info, warning, critical
            status TEXT DEFAULT 'open',  -- open, acknowledged, resolved
            message TEXT NOT NULL,
            details TEXT,  -- JSON with full check results
            triggered_at TEXT DEFAULT (datetime('now')),
            acknowledged_at TEXT,
            acknowledged_by INTEGER,
            resolved_at TEXT,
            resolved_by INTEGER,
            FOREIGN KEY (asset_id) REFERENCES asset(id) ON DELETE CASCADE,
            FOREIGN KEY (check_id) REFERENCES monitoring_check(id),
            FOREIGN KEY (acknowledged_by) REFERENCES user(id),
            FOREIGN KEY (resolved_by) REFERENCES user(id)
        )
    """)
    
    # 6. Maintenance Windows
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_window (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            window_type TEXT DEFAULT 'patching',  -- patching, maintenance, backup
            day_of_week INTEGER,  -- 0=Sunday, 1=Monday, etc. NULL=every day
            start_time TEXT NOT NULL,  -- HH:MM format
            end_time TEXT NOT NULL,  -- HH:MM format
            timezone TEXT DEFAULT 'America/Denver',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    
    # 7. Asset-Maintenance Window Association
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asset_maintenance_window (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            window_id INTEGER NOT NULL,
            assigned_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (asset_id) REFERENCES asset(id) ON DELETE CASCADE,
            FOREIGN KEY (window_id) REFERENCES maintenance_window(id) ON DELETE CASCADE,
            UNIQUE(asset_id, window_id)
        )
    """)
    
    # 8. Monitoring Check History (for trending)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_check_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            check_id INTEGER NOT NULL,
            status TEXT NOT NULL,  -- success, warning, critical, error
            value_numeric REAL,  -- For metrics like CPU %, disk %
            value_text TEXT,  -- For status checks
            execution_time_ms INTEGER,
            checked_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (asset_id) REFERENCES asset(id) ON DELETE CASCADE,
            FOREIGN KEY (check_id) REFERENCES monitoring_check(id)
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_profile_check_profile ON profile_check(profile_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_asset_profile_asset ON asset_monitoring_profile(asset_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alert_asset ON monitoring_alert(asset_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alert_status ON monitoring_alert(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alert_severity ON monitoring_alert(severity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_check_history_asset ON monitoring_check_history(asset_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_check_history_checked ON monitoring_check_history(checked_at)")
    
    conn.commit()
    
    # Insert standard monitoring checks
    insert_standard_checks(cursor)
    
    # Insert pre-built monitoring profiles
    insert_monitoring_profiles(cursor)
    
    # Insert default maintenance windows
    insert_maintenance_windows(cursor)
    
    conn.commit()
    conn.close()
    print("✓ Monitoring profiles system created successfully")


def insert_standard_checks(cursor):
    """Insert common monitoring checks"""
    
    checks = [
        # === Windows Checks ===
        ('cpu', 'CPU Usage', 'Monitor CPU utilization percentage', 'powershell', 
         '(Get-Counter "\\Processor(_Total)\\% Processor Time").CounterSamples.CookedValue', 30),
        
        ('memory', 'Memory Usage', 'Monitor RAM utilization percentage', 'powershell',
         '$os = Get-CimInstance Win32_OperatingSystem; [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 2)', 30),
        
        ('disk', 'Disk Space', 'Monitor disk free space on C: drive', 'powershell',
         'Get-PSDrive C | Select-Object @{N="Used%";E={[math]::Round(($_.Used/($_.Used+$_.Free))*100,2)}}', 30),
        
        ('service', 'Windows Service Status', 'Check if a Windows service is running', 'powershell',
         'Get-Service -Name {{service_name}} | Select-Object Status', 30),
        
        ('port', 'Port Listening', 'Check if a port is listening', 'powershell',
         'Test-NetConnection -ComputerName localhost -Port {{port}} -InformationLevel Quiet', 10),
        
        ('process', 'Process Running', 'Check if a process is running', 'powershell',
         'Get-Process -Name {{process_name}} -ErrorAction SilentlyContinue', 30),
        
        ('certificate', 'Certificate Expiry', 'Check SSL certificate expiration', 'powershell',
         '$cert = Get-ChildItem Cert:\\LocalMachine\\My | Where-Object {$_.Subject -like "*{{domain}}*"}; if($cert){($cert.NotAfter - (Get-Date)).Days}else{-1}', 60),
        
        ('event_log', 'Event Log Errors', 'Check for critical events in last hour', 'powershell',
         'Get-EventLog -LogName {{log_name}} -EntryType Error -After (Get-Date).AddHours(-1) | Measure-Object | Select-Object -ExpandProperty Count', 60),
        
        ('url', 'HTTP/HTTPS Endpoint', 'Check if web endpoint is responding', 'powershell',
         'try{(Invoke-WebRequest -Uri "{{url}}" -TimeoutSec 10 -UseBasicParsing).StatusCode}catch{$_.Exception.Response.StatusCode.value__}', 30),
        
        # === Active Directory Checks ===
        ('ad_replication', 'AD Replication Status', 'Check AD replication health', 'powershell',
         'repadmin /replsummary | Select-String "error|fail"', 60),
        
        ('ad_sysvol', 'SYSVOL Replication', 'Check SYSVOL replication status', 'powershell',
         'Get-Service DFSR | Select-Object Status', 30),
        
        ('ad_dcdiag', 'DC Diagnostics', 'Run DCDIAG health checks', 'powershell',
         'dcdiag /test:dns /test:replications | Select-String "failed|error"', 300),
        
        ('dns_service', 'DNS Service', 'Check DNS server service', 'powershell',
         'Get-Service DNS | Select-Object Status', 30),
        
        # === Certificate Authority Checks ===
        ('ca_service', 'Certificate Services', 'Check Active Directory Certificate Services', 'powershell',
         'Get-Service CertSvc | Select-Object Status', 30),
        
        ('ca_database', 'CA Database Size', 'Check certificate database size', 'powershell',
         'certutil -view -restrict "Disposition=20" csv | Measure-Object | Select-Object -ExpandProperty Count', 120),
        
        ('ca_crl', 'CRL Publication', 'Check if CRL is up to date', 'powershell',
         'certutil -verify | Select-String "ERROR"', 60),
        
        # === Web Server Checks ===
        ('iis_service', 'IIS Service', 'Check IIS World Wide Web Publishing Service', 'powershell',
         'Get-Service W3SVC | Select-Object Status', 30),
        
        ('iis_app_pool', 'IIS Application Pool', 'Check IIS application pool status', 'powershell',
         'Import-Module WebAdministration; (Get-WebAppPoolState "{{pool_name}}").Value', 30),
        
        ('ssl_cert_expiry', 'SSL Certificate Days', 'Check SSL certificate days until expiry', 'powershell',
         '$cert = Get-ChildItem IIS:\\SslBindings | Select-Object -First 1 -ExpandProperty Thumbprint; $cert2 = Get-ChildItem Cert:\\LocalMachine\\My\\$cert; ($cert2.NotAfter - (Get-Date)).Days', 120),
        
        # === Linux Checks ===
        ('linux_cpu', 'Linux CPU Usage', 'Monitor CPU usage on Linux', 'bash',
         "top -bn1 | grep 'Cpu(s)' | sed 's/.*, *\\([0-9.]*\\)%* id.*/\\1/' | awk '{print 100 - $1}'", 30),
        
        ('linux_memory', 'Linux Memory Usage', 'Monitor memory usage on Linux', 'bash',
         "free | grep Mem | awk '{print ($3/$2) * 100.0}'", 30),
        
        ('linux_disk', 'Linux Disk Usage', 'Monitor disk usage on Linux', 'bash',
         "df -h / | tail -1 | awk '{print $5}' | sed 's/%//'", 30),
        
        ('systemd_service', 'Systemd Service', 'Check systemd service status', 'bash',
         'systemctl is-active {{service_name}}', 30),
        
        ('linux_updates', 'Available Updates', 'Check for available system updates', 'bash',
         'yum check-update --quiet | grep -v "^$" | wc -l', 300),
        
        ('selinux_status', 'SELinux Status', 'Check SELinux enforcement status', 'bash',
         'getenforce', 60),
        
        ('linux_load', 'System Load Average', 'Check 1-minute load average', 'bash',
         "uptime | awk '{print $(NF-2)}' | sed 's/,//'", 30),
        
        # === Proxmox Checks ===
        ('proxmox_cluster', 'Proxmox Cluster Status', 'Check cluster health', 'bash',
         'pvecm status | grep "Quorate:" | awk \'{print $2}\'', 60),
        
        ('proxmox_vms', 'Running VMs Count', 'Count running virtual machines', 'bash',
         'qm list | grep running | wc -l', 60),
        
        ('proxmox_storage', 'Storage Pool Usage', 'Check storage pool utilization', 'bash',
         'pvesm status | tail -n +2 | awk \'{sum+=$5} END {print sum/NR}\'', 60),
        
        ('proxmox_backup', 'Last Backup Status', 'Check if backups are current', 'bash',
         'vzdump list | tail -1 | awk \'{print $1}\'', 300),
        
        # === Network Checks ===
        ('ping', 'Ping Response', 'Check if device responds to ping', 'bash',
         'ping -c 1 -W 2 {{target}} > /dev/null && echo 0 || echo 1', 10),
        
        ('uptime', 'System Uptime', 'Check system uptime in days', 'bash',
         "cat /proc/uptime | awk '{print int($1/86400)}'", 300),
    ]
    
    for check_type, name, desc, script_type, script, timeout in checks:
        cursor.execute("""
            INSERT OR IGNORE INTO monitoring_check 
            (check_type, name, description, script_type, script_content, timeout_seconds)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (check_type, name, desc, script_type, script, timeout))


def insert_monitoring_profiles(cursor):
    """Insert pre-configured monitoring profiles for common server types"""
    
    profiles = [
        # Windows Active Directory Domain Controller
        {
            'name': 'Windows AD Domain Controller',
            'description': 'Monitoring for Active Directory domain controllers',
            'device_type': 'Server',
            'os_family': 'Windows',
            'severity': 'critical',
            'interval': 10,
            'checks': [
                ('CPU Usage', {'warning': 75, 'critical': 90}),
                ('Memory Usage', {'warning': 80, 'critical': 95}),
                ('Disk Space', {'warning': 80, 'critical': 90}),
                ('AD Replication Status', {'warning': 1, 'critical': 1}),
                ('SYSVOL Replication', {'warning': 'Stopped', 'critical': 'Stopped'}),
                ('DNS Service', {'warning': 'Stopped', 'critical': 'Stopped'}),
                ('Event Log Errors', {'warning': 10, 'critical': 25}),
               ]
        },
        
        # Certificate Authority Server
        {
            'name': 'Windows Certificate Authority',
            'description': 'Monitoring for Active Directory Certificate Services',
            'device_type': 'Server',
            'os_family': 'Windows',
            'severity': 'high',
            'interval': 15,
            'checks': [
                ('CPU Usage', {'warning': 75, 'critical': 90}),
                ('Memory Usage', {'warning': 80, 'critical': 95}),
                ('Disk Space', {'warning': 80, 'critical': 90}),
                ('Certificate Services', {'warning': 'Stopped', 'critical': 'Stopped'}),
                ('CRL Publication', {'warning': 1, 'critical': 1}),
                ('Event Log Errors', {'warning': 10, 'critical': 25}),
            ]
        },
        
        # Windows Web Server (IIS)
        {
            'name': 'Windows IIS Web Server',
            'description': 'Monitoring for IIS web servers',
            'device_type': 'Server',
            'os_family': 'Windows',
            'severity': 'high',
            'interval': 5,
            'checks': [
                ('CPU Usage', {'warning': 80, 'critical': 95}),
                ('Memory Usage', {'warning': 85, 'critical': 95}),
                ('Disk Space', {'warning': 85, 'critical': 95}),
                ('IIS Service', {'warning': 'Stopped', 'critical': 'Stopped'}),
                ('SSL Certificate Days', {'warning': 30, 'critical': 7}),
                ('HTTP/HTTPS Endpoint', {'warning': 500, 'critical': 503}),
            ]
        },
        
        # Linux RedHat Server
        {
            'name': 'RedHat Linux Server',
            'description': 'Monitoring for RedHat/CentOS/Rocky Linux servers',
            'device_type': 'Server',
            'os_family': 'Linux',
            'severity': 'high',
            'interval': 10,
            'checks': [
                ('Linux CPU Usage', {'warning': 75, 'critical': 90}),
                ('Linux Memory Usage', {'warning': 80, 'critical': 95}),
                ('Linux Disk Usage', {'warning': 80, 'critical': 90}),
                ('System Load Average', {'warning': 5.0, 'critical': 10.0}),
                ('Available Updates', {'warning': 50, 'critical': 100}),
                ('SELinux Status', {'warning': 'Permissive', 'critical': 'Disabled'}),
            ]
        },
        
        # Proxmox Host
        {
            'name': 'Proxmox VE Host',
            'description': 'Monitoring for Proxmox Virtual Environment hosts',
            'device_type': 'Server',
            'os_family': 'Proxmox',
            'severity': 'critical',
            'interval': 5,
            'checks': [
                ('Linux CPU Usage', {'warning': 80, 'critical': 95}),
                ('Linux Memory Usage', {'warning': 85, 'critical': 95}),
                ('Linux Disk Usage', {'warning': 80, 'critical': 90}),
                ('Proxmox Cluster Status', {'warning': 'No', 'critical': 'No'}),
                ('Storage Pool Usage', {'warning': 80, 'critical': 90}),
                ('System Load Average', {'warning': 8.0, 'critical': 16.0}),
            ]
        },
        
        # Windows Workstation
        {
            'name': 'Windows Workstation',
            'description': 'Standard monitoring for Windows desktops/laptops',
            'device_type': 'Desktop',
            'os_family': 'Windows',
            'severity': 'standard',
            'interval': 30,
            'checks': [
                ('CPU Usage', {'warning': 85, 'critical': 98}),
                ('Memory Usage', {'warning': 90, 'critical': 98}),
                ('Disk Space', {'warning': 85, 'critical': 95}),
            ]
        },
        
        # Generic Linux Workstation
        {
            'name': 'Linux Workstation',
            'description': 'Standard monitoring for Linux desktops',
            'device_type': 'Desktop',
            'os_family': 'Linux',
            'severity': 'standard',
            'interval': 30,
            'checks': [
                ('Linux CPU Usage', {'warning': 85, 'critical': 98}),
                ('Linux Memory Usage', {'warning': 90, 'critical': 98}),
                ('Linux Disk Usage', {'warning': 85, 'critical': 95}),
            ]
        },
    ]
    
    for profile in profiles:
        cursor.execute("""
            INSERT OR IGNORE INTO monitoring_profile 
            (name, description, device_type, os_family, severity_level, check_interval_minutes, is_template)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (profile['name'], profile['description'], profile['device_type'], 
              profile['os_family'], profile['severity'], profile['interval']))
        
        profile_id = cursor.lastrowid
        if profile_id == 0:
            # Profile already exists, get its ID
            cursor.execute("SELECT id FROM monitoring_profile WHERE name = ?", (profile['name'],))
            result = cursor.fetchone()
            if result:
                profile_id = result[0]
        
        # Associate checks with profile
        for check_name, thresholds in profile['checks']:
            cursor.execute("SELECT id FROM monitoring_check WHERE name = ?", (check_name,))
            check_result = cursor.fetchone()
            if check_result:
                check_id = check_result[0]
                import json
                cursor.execute("""
                    INSERT OR IGNORE INTO profile_check 
                    (profile_id, check_id, warning_threshold, critical_threshold)
                    VALUES (?, ?, ?, ?)
                """, (profile_id, check_id, 
                      json.dumps(thresholds.get('warning')), 
                      json.dumps(thresholds.get('critical'))))


def insert_maintenance_windows(cursor):
    """Insert default maintenance windows"""
    
    windows = [
        ('Production Servers - Sunday Night', 'Critical production servers patching window', 'patching', 
         0, '02:00', '05:00', 'America/Denver'),
        ('Non-Production Servers - Weeknights', 'Non-critical servers patching window', 'patching',
         None, '22:00', '06:00', 'America/Denver'),
        ('Workstations - Business Hours', 'Workstation patching during work hours', 'patching',
         None, '08:00', '17:00', 'America/Denver'),
        ('Backup Window - Nightly', 'Nightly backup execution window', 'backup',
         None, '23:00', '04:00', 'America/Denver'),
    ]
    
    for name, desc, win_type, dow, start, end, tz in windows:
        cursor.execute("""
            INSERT OR IGNORE INTO maintenance_window 
            (name, description, window_type, day_of_week, start_time, end_time, timezone)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, desc, win_type, dow, start, end, tz))


if __name__ == '__main__':
    migrate()
