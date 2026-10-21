from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse


class NoEmailTests(TestCase):
    def test_delivery_fails_explicitly(self):
        # Django's test runner normally substitutes the in-memory backend.
        with override_settings(MAILERS={"default": {
            "BACKEND": "rdbhs_backoffice.mail.DisabledEmailBackend",
        }}):
            with self.assertRaisesMessage(ImproperlyConfigured, "Outbound email is disabled"):
                EmailMessage("Test", "Body", "sender@example.com", ["user@example.com"]).send()

    def test_email_reset_routes_and_login_link_are_absent(self):
        for path in (
            "/accounts/password_reset/", "/accounts/password_reset/done/",
            "/accounts/reset/example/token/", "/accounts/reset/done/",
        ):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        # Admin's catch-all redirects anonymous requests; no reset view is registered.
        with self.assertRaises(NoReverseMatch):
            reverse("admin_password_reset")
        self.assertNotContains(self.client.get("/admin/login/"), "password_reset")
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Reset password")

    def test_local_login_and_administrator_password_reset(self):
        users = get_user_model()
        users.objects.create_superuser(username="admin", password="Initial-admin-password-823!")
        user = users.objects.create_user(username="user", password="Initial-user-password-823!")
        self.assertTrue(self.client.login(username="admin", password="Initial-admin-password-823!"))
        response = self.client.post(reverse("admin:auth_user_password_change", args=[user.pk]), {
            "password1": "Replacement-user-password-956!",
            "password2": "Replacement-user-password-956!",
            "usable_password": "true",
        })
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Replacement-user-password-956!"))
