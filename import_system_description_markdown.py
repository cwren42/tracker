"""Import the incoming system description markdown into the system_description table."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app
from soc2_artifact_service import import_system_description_from_markdown


def main():
    with app.app_context():
        result = import_system_description_from_markdown()
        print(
            f"✓ Imported system description sections: {result['sections']} total, "
            f"{result['matched']} matched headings, {result['updated']} updated"
        )
        return True


if __name__ == '__main__':
    if not main():
        sys.exit(1)