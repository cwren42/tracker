# Asset Tracker License Configuration

## Environment Variables

Add these to your environment or create `/var/www/tracker/.env` file:

```bash
# License Server Configuration
LICENSE_SERVER_URL=https://license.corp.cirque.com/api
LICENSE_API_KEY=sk_74211b5d5c4a78395cc5422902b37f09bc30d163e43fb3bba7892515324b05be

# Asset Tracker License Key (get from license.corp.cirque.com)
ASSET_TRACKER_LICENSE_KEY=LIC-XXXXX-XXXXX-XXXXX-XXXXX
```

## Setup Instructions

1. Get a license key from https://license.corp.cirque.com
2. Log into Asset Tracker as admin
3. Go to Settings (gear icon)
4. Scroll to "License Management" section  
5. Click "Add License"
6. Enter your license key and company name
7. Click "Save License"
8. Click "Verify Now" to validate with license server

## Features

- ✅ Automatic verification every 24 hours
- ✅ 1-day grace period when license server unreachable
- ✅ Startup validation
- ✅ API blocking when license expired (except license management endpoints)
- ✅ Manual verification available
- ✅ Dashboard and reports accessible with valid license
- ✅ Visual status indicators in settings

## Troubleshooting

### License Server Unreachable
- Check network connectivity: `curl -k https://license.corp.cirque.com/api`
- Verify API key is correct
- Check grace period status in Settings

### License Shows as Invalid
- Click "Verify Now" in Settings > License Management
- Check license expiry date
- Verify license key matches server

### API Still Blocked After Renewal
- Restart service: `sudo systemctl restart tracker.service`
- Check database status in Settings

## License Status Messages

- **Active**: License is valid and verified
- **Expired**: License has passed expiry date
- **Invalid**: License not found on server
- **Pending**: License saved but not yet verified
- **Grace Period**: Server unreachable, using grace period (1 day)
