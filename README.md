# IT Asset Tracker

A complete web-based asset tracking system built with Flask, deployed on Ubuntu with nginx and gunicorn.

## 🚀 Quick Start

**Access the application:**
- URL: http://10.15.0.53 (or your server IP)
- Username: `admin`
- Password: `admin123`

## 📦 Features

### Core Features
- ✅ Asset Management (Add, Edit, View, Delete)
- ✅ Employee Management
- ✅ Asset Assignment Tracking
- ✅ QR Code Generation for assets
- ✅ Status Tracking (Available, In Use, In Repair, Retired)
- ✅ Warranty Expiry Tracking & Alerts
- ✅ Purchase Cost & Date Tracking
- ✅ Asset History & Audit Log
- ✅ Search & Filter by Category, Status
- ✅ Dashboard with Statistics
- ✅ Reports & Analytics

### Categories Supported
- Laptops, Desktops, Monitors
- Keyboards, Mice, Peripherals
- Servers, Network Equipment
- Printers, Phones, Tablets
- And more...

## 🏗️ Architecture

```
/var/www/tracker/          # Application root
├── app.py                 # Flask application
├── wsgi.py               # WSGI entry point
├── requirements.txt      # Python dependencies
├── templates/            # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── assets.html
│   ├── view_asset.html
│   ├── edit_asset.html
│   ├── add_asset.html
│   ├── employees.html
│   ├── reports.html
│   └── qr_code.html
├── venv/                 # Python virtual environment
└── assets.db            # SQLite database

/etc/nginx/sites-available/tracker    # nginx configuration
/etc/systemd/system/tracker.service   # systemd service
```

## 🔧 System Services

### Check Application Status
```bash
sudo systemctl status tracker
```

### Restart Application
```bash
sudo systemctl restart tracker
```

### View Application Logs
```bash
sudo journalctl -u tracker -f
```

### Check nginx Status
```bash
sudo systemctl status nginx
```

### Restart nginx
```bash
sudo systemctl restart nginx
```

## 📊 Database

- **Type:** SQLite
- **Location:** `/var/www/tracker/assets.db`
- **Backup:** `cp /var/www/tracker/assets.db ~/assets_backup_$(date +%Y%m%d).db`

## 🔐 Security Notes

**IMPORTANT:** Change the default admin password!
1. Log in with `admin` / `admin123`
2. Create a new admin user
3. Delete or change the default admin password

## 🛠️ Maintenance

### Add Dependencies
```bash
cd /var/www/tracker
source venv/bin/activate
pip install package-name
pip freeze > requirements.txt
sudo systemctl restart tracker
```

### Update Application
1. Edit files in `/var/www/tracker/`
2. Restart service: `sudo systemctl restart tracker`

### Database Backup Script
```bash
#!/bin/bash
BACKUP_DIR="/home/webuser/backups"
mkdir -p $BACKUP_DIR
cp /var/www/tracker/assets.db $BACKUP_DIR/assets_$(date +%Y%m%d_%H%M%S).db
```

## 🌐 Production Enhancements

### Add SSL Certificate (Let's Encrypt)
```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### Switch to PostgreSQL
1. Install PostgreSQL: `sudo apt-get install postgresql`
2. Update `app.py` database URI
3. Migrate data from SQLite

## 📝 Usage Tips

1. **Asset Tags:** Use unique identifiers (e.g., LAP001, MON042)
2. **Serial Numbers:** Record for warranty claims
3. **QR Codes:** Print and attach to physical assets
4. **Regular Audits:** Export reports monthly
5. **Warranty Alerts:** Check dashboard for expiring warranties

## 🤝 Support

- Check logs: `/var/log/nginx/tracker_error.log`
- Application logs: `sudo journalctl -u tracker`
- Database location: `/var/www/tracker/assets.db`

## 📄 License

Built for internal IT asset management.

---

**Deployed on:** Ubuntu 24.04
**Web Server:** nginx
**App Server:** gunicorn  
**Framework:** Flask with SQLAlchemy
