from app import app, db
from license_service import license_service
from sync_scheduler import start_sync_scheduler
import alert_service as _alert_svc

# Initialize license service with app context
with app.app_context():
    license_service.verify_on_startup()
    license_service.start_periodic_check()
    start_sync_scheduler(app)
    _alert_svc.start_background_thread()

if __name__ == "__main__":
    app.run()
