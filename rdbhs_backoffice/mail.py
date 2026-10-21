"""Explicitly disable outbound email for Backoffice v1."""

from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class DisabledEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if any(email_messages):
            raise ImproperlyConfigured("Outbound email is disabled for RDBHS Backoffice v1.")
        return 0
