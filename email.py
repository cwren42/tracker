"""Standalone SMTP direct-send reference with live Tracker values.

This file intentionally preserves access to Python's stdlib email package so it
does not break SMTP-related imports elsewhere in the application.
"""

import importlib
import importlib.util
from pathlib import Path
import sysconfig


def _load_stdlib_email_package():
    stdlib_email_dir = Path(sysconfig.get_path("stdlib")) / "email"
    spec = importlib.util.spec_from_file_location(
        "_tracker_stdlib_email",
        stdlib_email_dir / "__init__.py",
        submodule_search_locations=[str(stdlib_email_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError("Unable to load Python stdlib email package")
    spec.loader.exec_module(module)
    return module


_STDLIB_EMAIL = _load_stdlib_email_package()
__path__ = list(getattr(_STDLIB_EMAIL, "__path__", []))
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

for _name, _value in vars(_STDLIB_EMAIL).items():
    if _name in {"__name__", "__loader__", "__spec__", "__package__"}:
        continue
    globals().setdefault(_name, _value)


EmailMessage = importlib.import_module("email.message").EmailMessage
smtplib = importlib.import_module("smtplib")


SETTINGS = {
    "smtp_server": "cirque-com.mail.protection.outlook.com",
    "smtp_port": 25,
    "use_starttls": True,
    "use_ssl": False,
    "username": "",
    "password": "",
    "sender_email": "tracker@cirque.com",
    "helo": "tracker.cirque.com",
}


class DirectSendMailer:
    def __init__(self, settings):
        self.settings = settings

    def send_email(self, subject, recipients, text_body, html_body=None):
        if not recipients:
            raise ValueError("At least one recipient is required")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings["sender_email"]
        msg["To"] = ", ".join(recipient for recipient in recipients if recipient)
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        smtp_class = smtplib.SMTP_SSL if self.settings["use_ssl"] else smtplib.SMTP
        with smtp_class(
            self.settings["smtp_server"],
            self.settings["smtp_port"],
            timeout=30,
        ) as smtp:
            smtp.ehlo(self.settings["helo"])
            if self.settings["use_starttls"] and not self.settings["use_ssl"]:
                smtp.starttls()
                smtp.ehlo(self.settings["helo"])
            if self.settings["username"]:
                smtp.login(self.settings["username"], self.settings["password"])
            smtp.send_message(msg)
        return True


def get_settings():
    return dict(SETTINGS)


def send_email(subject, recipients, text_body, html_body=None):
    mailer = DirectSendMailer(get_settings())
    return mailer.send_email(subject, recipients, text_body, html_body)


if __name__ == "__main__":
    send_email(
        subject="Tracker Direct Send Test",
        recipients=["you@example.com"],
        text_body="This is a direct SMTP send test.",
        html_body="<p>This is a <strong>direct SMTP send</strong> test.</p>",
    )
    print("Email submitted via SMTP direct send.")