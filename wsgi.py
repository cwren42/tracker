from app import app, db
from license_service import license_service
from sync_scheduler import start_sync_scheduler

# Initialize license service with app context
with app.app_context():
    license_service.verify_on_startup()
    license_service.start_periodic_check()
    start_sync_scheduler(app)

if __name__ == "__main__":
    app.run()
