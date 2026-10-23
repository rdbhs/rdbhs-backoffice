"""Exercise the real allauth callback flow with Microsoft HTTP calls mocked."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.internal.flows.login import pre_social_login
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin, SocialToken
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied
from django.core import mail
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from rdbhs_backoffice.sso import CorporateSocialAccountAdapter


TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
CLIENT_ID = "33333333-3333-3333-3333-333333333333"
OBJECT_ID = "44444444-4444-4444-4444-444444444444"
PROVIDERS = {
    "microsoft": {
        "provider_class": "rdbhs_backoffice.sso.CorporateMicrosoftProvider",
        "OAUTH_PKCE_ENABLED": True,
        "APPS": [{
            "client_id": CLIENT_ID,
            "secret": "local-test-placeholder",
            "settings": {"tenant": TENANT},
        }],
    },
}
SSO_SETTINGS = dict(
    SSO_ENABLED=True,
    ENTRA_TENANT_ID=TENANT,
    ENTRA_CLIENT_ID=CLIENT_ID,
    ENTRA_CLIENT_SECRET="local-test-placeholder",
    SOCIALACCOUNT_PROVIDERS=PROVIDERS,
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
)


@override_settings(**SSO_SETTINGS)
class MicrosoftLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="operator", email="operator@example.com", password="test-password"
        )

    def authorize_identity(self, user=None, tenant=TENANT):
        # Fixture simulates an association approved through an authoritative source.
        # There is deliberately no public enrollment/connection route.
        return SocialAccount.objects.create(
            user=user or self.user, provider="microsoft", uid=f"{tenant}:{OBJECT_ID}"
        )

    def start(self, next_url=None, client=None):
        response = (client or self.client).post(
            reverse("microsoft_login"), {"next": next_url} if next_url else {}
        )
        self.assertEqual(response.status_code, 302)
        return response, parse_qs(urlsplit(response.url).query)["state"][0]

    def callback(self, data=None, next_url=None, client=None, state=None):
        client = client or self.client
        if state is None:
            _, state = self.start(next_url, client)
        profile = {"id": OBJECT_ID, "mail": self.user.email, "givenName": "External"}
        if data is not None:
            profile = data
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value.json.return_value = profile
        with (
            patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", autospec=True, return_value={"access_token": "mock-token"}) as exchange,
            patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=session),
            patch("allauth.account.adapter.DefaultAccountAdapter.send_mail", side_effect=AssertionError("Unexpected outbound email")),
        ):
            response = client.get(reverse("microsoft_callback"), {"code": "mock-code", "state": state})
        oauth_client = exchange.call_args.args[0]
        self.assertEqual(oauth_client.access_token_url, f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token")
        self.assertTrue(exchange.call_args.kwargs["pkce_code_verifier"])
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], "https://graph.microsoft.com/v1.0/me")
        return response

    def assert_denied(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 0)
        self.assertEqual(SocialToken.objects.count(), 0)

    def test_password_login_logout_and_safe_next(self):
        response = self.client.post("/accounts/login/?next=https://evil.example/", {
            "username": self.user.username, "password": "test-password",
        })
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        response = self.client.post("/accounts/logout/")
        self.assertRedirects(response, "/accounts/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_viewflow_template_keeps_password_and_posts_sso_with_csrf(self):
        response = self.client.get("/accounts/login/?next=/employees/")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "Continue with Microsoft")
        self.assertContains(response, 'method="post" action="/accounts/microsoft/login/"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="next" value="/employees/"')

    def test_oauth_start_is_post_csrf_tenant_scoped_and_pkce(self):
        self.assertEqual(self.client.get(reverse("microsoft_login")).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(reverse("microsoft_login")).status_code, 403)
        csrf_client.get("/accounts/login/")
        response = csrf_client.post(reverse("microsoft_login"), {
            "csrfmiddlewaretoken": csrf_client.cookies["csrftoken"].value,
        })
        parsed = urlsplit(response.url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "login.microsoftonline.com")
        self.assertEqual(parsed.path, f"/{TENANT}/oauth2/v2.0/authorize")
        self.assertEqual(query["client_id"], [CLIENT_ID])
        self.assertEqual(query["scope"], ["User.Read"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://testserver/accounts/microsoft/login/callback/"])
        self.assertNotIn("local-test-placeholder", response.url)

    def test_linked_identity_login_keeps_identity_permissions_password_and_email(self):
        association = self.authorize_identity()
        permission = Permission.objects.get(codename="view_employee", content_type__app_label="core")
        self.user.user_permissions.add(permission)
        original_password = self.user.password
        response = self.callback({"id": OBJECT_ID, "mail": "changed@example.com"}, next_url="/core/")
        self.assertRedirects(response, "/core/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        self.user.refresh_from_db()
        association.refresh_from_db()
        self.assertEqual(association.user_id, self.user.pk)
        self.assertEqual(association.uid, f"{TENANT}:{OBJECT_ID}")
        self.assertEqual(self.user.email, "operator@example.com")
        self.assertEqual(self.user.password, original_password)
        self.assertTrue(self.user.has_perm("core.view_employee"))
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(SocialAccount.objects.count(), 1)
        self.assertEqual(SocialToken.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.client.post("/accounts/logout/")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Persistent ID still works even without an email in subsequent profiles.
        self.assertEqual(self.callback({"id": OBJECT_ID}).status_code, 302)
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_unknown_identity_and_unique_email_are_not_trusted(self):
        # A unique local match is insufficient without independently approved mapping.
        for profile in (
            {"id": OBJECT_ID, "mail": self.user.email},
            {"id": OBJECT_ID, "userPrincipalName": self.user.email},
            {"id": OBJECT_ID, "mail": self.user.email, "email_verified": True},
            {"id": OBJECT_ID, "mail": "unknown@example.com"},
            {"id": OBJECT_ID},
        ):
            with self.subTest(profile=profile):
                self.assert_denied(self.callback(profile))

    def test_ambiguous_case_insensitive_email_is_denied(self):
        get_user_model().objects.create_user(username="duplicate", email="OPERATOR@example.com")
        self.assertEqual(self.callback().status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(SocialAccount.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_inactive_linked_user_is_denied(self):
        self.authorize_identity()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.callback().status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_tenant_and_malformed_identity(self):
        self.authorize_identity()
        for profile in ({"id": OBJECT_ID, "tid": OTHER_TENANT}, {"id": "bad-id"}, {}):
            with self.subTest(profile=profile):
                self.assertEqual(self.callback(profile).status_code, 403)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_association_from_other_tenant_is_not_used(self):
        self.authorize_identity(tenant=OTHER_TENANT)
        self.assertEqual(self.callback().status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_existing_identity_never_moves_to_matching_email_user(self):
        owner = get_user_model().objects.create_user(username="owner")
        association = self.authorize_identity(user=owner)
        self.assertEqual(self.callback().status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(owner.pk))
        association.refresh_from_db()
        self.assertEqual(association.user_id, owner.pk)
        # A requested connection cannot transfer the identity to another user.
        self.assertEqual(self.client.post(reverse("microsoft_login"), {"process": "connect"}).status_code, 403)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.authorize_identity(user=self.user)

    def test_unsafe_next_is_discarded(self):
        self.authorize_identity()
        response = self.callback(next_url="https://evil.example/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_callback_state_is_required_and_cannot_be_replayed(self):
        self.authorize_identity()
        self.assertEqual(self.client.get(reverse("microsoft_callback"), {"code": "mock"}).status_code, 403)
        _, state = self.start()
        self.assertEqual(self.callback(state=state).status_code, 302)
        self.client.post("/accounts/logout/")
        self.assertEqual(self.client.get(reverse("microsoft_callback"), {"code": "mock", "state": state}).status_code, 403)

    def test_cancellation_uses_local_error_page(self):
        _, state = self.start()
        response = self.client.get(reverse("microsoft_callback"), {"error": "access_denied", "state": state})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "/accounts/login/", status_code=403)

    def test_no_public_account_flows(self):
        for path in ("signup/", "password/reset/", "password_reset/", "email/", "social/connections/", "social/signup/"):
            self.assertEqual(self.client.get(f"/accounts/{path}").status_code, 404)

    @override_settings(SSO_ENABLED=False)
    def test_disabled_sso_keeps_local_login(self):
        self.assertNotContains(self.client.get("/accounts/login/"), "Continue with Microsoft")
        self.assertEqual(self.client.post(reverse("microsoft_login")).status_code, 404)
        self.assertEqual(self.client.get(reverse("microsoft_callback")).status_code, 404)
        self.test_password_login_logout_and_safe_next()

    def test_viewflow_permissions_still_apply(self):
        self.authorize_identity()
        self.callback()
        url = reverse("core:employees:list")
        self.assertEqual(self.client.get(url).status_code, 403)
        permission = Permission.objects.get(codename="view_employee", content_type__app_label="core")
        self.user.user_permissions.add(permission)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_unapproved_provider_configuration_is_rejected(self):
        adapter = CorporateSocialAccountAdapter()
        request = RequestFactory().get("/")
        for change in (
            {"settings": {"tenant": OTHER_TENANT}},
            {"settings": {"tenant": TENANT, "login_url": "https://evil.example"}},
            {"client_id": OTHER_TENANT},
            {"secret": "unapproved-secret"},
        ):
            providers = deepcopy(PROVIDERS)
            providers["microsoft"]["APPS"][0].update(change)
            with self.subTest(changed_fields=list(change)), override_settings(SOCIALACCOUNT_PROVIDERS=providers):
                with self.assertRaises(PermissionDenied):
                    adapter.get_provider(request, "microsoft")

    def test_database_socialapps_never_override_authorized_registration(self):
        for _ in range(2):
            SocialApp.objects.create(
                provider="microsoft", client_id=CLIENT_ID, secret="database-secret",
                settings={"tenant": OTHER_TENANT, "login_url": "https://evil.example"},
            )
        adapter = CorporateSocialAccountAdapter()
        with self.assertNumQueries(0):
            app = adapter.get_app(RequestFactory().get("/"), "microsoft")
        self.assertIsNone(app.pk)
        self.assertEqual(app.client_id, CLIENT_ID)
        self.assertEqual(app.secret, "local-test-placeholder")
        self.assertEqual(app.settings, {"tenant": TENANT})
        response, _ = self.start()
        self.assertEqual(urlsplit(response.url).netloc, "login.microsoftonline.com")
        self.authorize_identity()
        self.assertEqual(self.callback().status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_missing_or_duplicate_settings_apps_never_fall_back_to_database(self):
        SocialApp.objects.create(provider="microsoft", client_id=CLIENT_ID, secret="database-secret")
        adapter = CorporateSocialAccountAdapter()
        configured = PROVIDERS["microsoft"]["APPS"][0]
        for apps in ([], [configured, configured]):
            providers = deepcopy(PROVIDERS)
            providers["microsoft"]["APPS"] = apps
            with self.subTest(app_count=len(apps)), override_settings(SOCIALACCOUNT_PROVIDERS=providers):
                with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                    adapter.get_app(RequestFactory().get("/"), "microsoft")

    def test_explicit_unapproved_client_is_rejected_without_database_lookup(self):
        with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
            CorporateSocialAccountAdapter().get_app(RequestFactory().get("/"), "microsoft", client_id=OTHER_TENANT)


@override_settings(**SSO_SETTINGS)
class ConcurrentFirstLoginTests(TransactionTestCase):
    def test_simultaneous_unapproved_matches_never_create_or_link(self):
        user = get_user_model().objects.create_user(username="operator", email="operator@example.com")
        barrier = Barrier(2)

        def attempt(_):
            close_old_connections()
            try:
                request = RequestFactory().get("/")
                request.user = AnonymousUser()
                request.session = {}
                login = SocialLogin(
                    user=get_user_model()(email=user.email),
                    account=SocialAccount(provider="microsoft", uid=f"{TENANT}:{OBJECT_ID}"),
                )
                login.state = {"process": "login"}
                barrier.wait(timeout=10)
                try:
                    pre_social_login(request, login)
                except ImmediateHttpResponse as error:
                    return error.response.status_code
                self.fail("Unapproved concurrent login was accepted")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(attempt, range(2))), [403, 403])
        self.assertEqual(SocialAccount.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 1)
