from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from allauth.account.models import EmailAddress, EmailConfirmation
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from rdbhs_backoffice.sso import CorporateMicrosoftProvider


TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_TENANT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OBJECT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OTHER_OBJECT = "dddddddd-dddd-dddd-dddd-dddddddddddd"
UID = f"{TENANT}:{OBJECT}"


def link(user_id, *, tenant=TENANT, object_id=OBJECT, apply=False):
    output = StringIO()
    call_command(
        "link_entra_identity", user_id=user_id, tenant_id=tenant,
        object_id=object_id, apply=apply, stdout=output,
    )
    return output.getvalue()


@override_settings(ENTRA_TENANT_ID=TENANT)
class LinkEntraIdentityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pilot", email="shared@example.com", password="local-password",
            is_staff=True, first_name="Pilot", last_name="Operator",
        )
        self.other = User.objects.create_user(
            username="other", email=self.user.email, password="other-password"
        )
        self.inactive = User.objects.create_user(username="inactive", is_active=False)
        group = Group.objects.create(name="Existing group")
        permission = Permission.objects.get(
            content_type__app_label="core", codename="view_employee"
        )
        self.user.groups.add(group)
        group.permissions.add(permission)
        self.user.user_permissions.add(permission)
        self.unchanged_models = (
            User, Group, Permission, User.groups.through, User.user_permissions.through,
            Group.permissions.through, SocialApp, SocialToken, EmailAddress, EmailConfirmation,
        )
        self.before = self.snapshot()

    def snapshot(self):
        return [list(model.objects.order_by("pk").values()) for model in self.unchanged_models]

    def tearDown(self):
        # Covers password hashes, flags, profile, permissions, users and other allauth rows.
        self.assertEqual(self.snapshot(), self.before)

    def test_default_simulation_has_no_writes(self):
        with CaptureQueriesContext(connection) as queries:
            output = link(self.user.pk)
        self.assertIn("Simulation successful", output)
        self.assertFalse(SocialAccount.objects.exists())
        self.assertFalse(any(
            q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for q in queries
        ))

    def test_apply_creates_only_the_explicit_canonical_association(self):
        output = link(self.user.pk, tenant=TENANT.upper(), object_id=OBJECT.upper(), apply=True)
        self.assertIn("Association created", output)
        account = SocialAccount.objects.get()
        self.assertEqual((account.provider, account.uid, account.user_id),
                         ("microsoft", UID, self.user.pk))
        self.assertEqual(account.extra_data, {})
        provider = CorporateMicrosoftProvider(request=None, app=SocialApp(provider="microsoft"))
        self.assertEqual(account.uid, provider.extract_uid({"id": OBJECT.upper()}))
        self.assertNotIn(UID, output)
        self.assertNotIn(self.user.email, output)
        self.assertTrue(self.user.check_password("local-password"))

    def test_repetition_and_simulation_of_existing_link_do_not_update_it(self):
        account = SocialAccount.objects.create(
            provider="microsoft", uid=UID, user=self.user, extra_data={"existing": True}
        )
        before = SocialAccount.objects.values().get(pk=account.pk)
        for apply in (False, True, True):
            with self.subTest(apply=apply):
                self.assertIn("already exists", link(self.user.pk, apply=apply))
                self.assertEqual(SocialAccount.objects.values().get(pk=account.pk), before)
                self.assertEqual(SocialAccount.objects.count(), 1)

    def test_nonexistent_or_inactive_user_is_rejected_even_for_existing_link(self):
        for user_id in (999999, self.inactive.pk):
            for apply in (False, True):
                with self.subTest(user_id=user_id, apply=apply), self.assertRaises(CommandError):
                    link(user_id, apply=apply)
        self.assertFalse(SocialAccount.objects.exists())
        SocialAccount.objects.create(provider="microsoft", uid=UID, user=self.inactive)
        with self.assertRaisesMessage(CommandError, "must be active"):
            link(self.inactive.pk, apply=True)

    def test_wrong_tenant_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "does not match"):
            link(self.user.pk, tenant=OTHER_TENANT, apply=True)
        self.assertFalse(SocialAccount.objects.exists())

    def test_invalid_or_missing_uuid_configuration_is_rejected(self):
        for kwargs in ({"tenant": "not-a-uuid"}, {"object_id": "not-a-uuid"},
                       {"tenant": ""}, {"object_id": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CommandError):
                link(self.user.pk, apply=True, **kwargs)
        for configured in ("", "invalid", None):
            with self.subTest(configured=configured), override_settings(ENTRA_TENANT_ID=configured):
                with self.assertRaises(CommandError):
                    link(self.user.pk, apply=True)
        self.assertFalse(SocialAccount.objects.exists())

    def test_identity_owned_by_another_user_is_never_reassigned(self):
        account = SocialAccount.objects.create(provider="microsoft", uid=UID, user=self.other)
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaisesMessage(CommandError, "another user"):
                link(self.user.pk, apply=apply)
        account.refresh_from_db()
        self.assertEqual(account.user_id, self.other.pk)
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_user_with_another_microsoft_identity_is_rejected(self):
        account = SocialAccount.objects.create(
            provider="microsoft", uid=f"{TENANT}:{OTHER_OBJECT}", user=self.user
        )
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaisesMessage(CommandError, "another Microsoft"):
                link(self.user.pk, apply=apply)
        self.assertEqual(SocialAccount.objects.get().pk, account.pk)

    def test_provider_boundaries_are_preserved(self):
        SocialAccount.objects.create(provider="other", uid=UID, user=self.user)
        link(self.user.pk, apply=True)
        self.assertEqual(SocialAccount.objects.count(), 2)
        self.assertEqual(SocialAccount.objects.get(provider="microsoft").user_id, self.user.pk)

    def test_concurrent_winner_for_another_user_is_rejected_after_get_or_create(self):
        winner = SocialAccount(provider="microsoft", uid=UID, user=self.other)
        with patch.object(SocialAccount.objects, "get_or_create", return_value=(winner, False)):
            with self.assertRaisesMessage(CommandError, "another user"):
                link(self.user.pk, apply=True)
        self.assertFalse(SocialAccount.objects.exists())

    def test_concurrent_same_user_winner_is_idempotent(self):
        winner = SocialAccount(provider="microsoft", uid=UID, user=self.user)
        with patch.object(SocialAccount.objects, "get_or_create", return_value=(winner, False)):
            self.assertIn("already exists", link(self.user.pk, apply=True))
        self.assertFalse(SocialAccount.objects.exists())

    def test_unresolved_integrity_conflict_rolls_back_and_hides_database_details(self):
        with patch.object(SocialAccount.objects, "get_or_create", side_effect=IntegrityError("private details")):
            with self.assertRaisesMessage(CommandError, "Concurrent association conflict") as error:
                link(self.user.pk, apply=True)
        self.assertNotIn("private details", str(error.exception))
        self.assertFalse(SocialAccount.objects.exists())


@override_settings(ENTRA_TENANT_ID=TENANT)
@skipUnlessDBFeature("has_select_for_update")
class ConcurrentEntraLinkTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="pilot")
        self.other = get_user_model().objects.create_user(username="other")

    def attempts(self, arguments):
        barrier = Barrier(2)

        def attempt(args):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return link(*args[:1], object_id=args[1], apply=True)
                except CommandError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(attempt, arguments))

    def test_same_identity_and_user_is_created_once(self):
        results = self.attempts([(self.user.pk, OBJECT)] * 2)
        self.assertNotIn("rejected", results)
        self.assertEqual(sum("Association created" in r for r in results), 1)
        self.assertEqual(SocialAccount.objects.get().user_id, self.user.pk)
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_same_identity_competing_users_never_reassigns_winner(self):
        results = self.attempts([(self.user.pk, OBJECT), (self.other.pk, OBJECT)])
        self.assertEqual(results.count("rejected"), 1)
        winner = self.user if results[0] != "rejected" else self.other
        self.assertEqual(SocialAccount.objects.get().user_id, winner.pk)
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_different_identities_competing_for_same_user_create_only_one(self):
        results = self.attempts([(self.user.pk, OBJECT), (self.user.pk, OTHER_OBJECT)])
        self.assertEqual(results.count("rejected"), 1)
        expected = OBJECT if results[0] != "rejected" else OTHER_OBJECT
        self.assertEqual(SocialAccount.objects.get().uid, f"{TENANT}:{expected}")
        self.assertEqual(SocialAccount.objects.count(), 1)
