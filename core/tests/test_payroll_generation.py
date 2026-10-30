from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.db import (
    NotSupportedError,
    OperationalError,
    connection,
    connections,
    transaction,
)
from django.db.models import QuerySet
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from ..models import CompanySettings, PayrollPeriod
from ..payroll import generate_payroll_periods


NOW_2026 = datetime(2026, 10, 30, 12, tzinfo=datetime_timezone.utc)
ANCHOR = date(2024, 12, 29)


def generate_at_2026():
    with patch("core.payroll.timezone.now", return_value=NOW_2026):
        return generate_payroll_periods()


@skipUnless(connection.vendor == "postgresql", "Generation uses PostgreSQL locks")
class PayrollGenerationTests(TestCase):
    def test_first_generation_has_exact_rules_and_complete_coverage(self):
        result = generate_at_2026()
        self.assertEqual((result.created, result.existing, result.conflicts), (79, 0, ()))
        periods = list(PayrollPeriod.objects.all())
        self.assertEqual(periods[0].start_date, ANCHOR)
        self.assertEqual(periods[0].end_date, date(2025, 1, 11))
        self.assertEqual(periods[0].payment_date, date(2025, 1, 17))
        self.assertEqual(periods[-1].start_date, date(2027, 12, 26))
        self.assertEqual(periods[-1].end_date, date(2028, 1, 8))
        self.assertEqual(periods[-1].payment_date, date(2028, 1, 14))
        for i, period in enumerate(periods):
            self.assertEqual((period.end_date - period.start_date).days + 1, 14)
            self.assertEqual(period.payment_date - period.end_date, timedelta(days=6))
            if i:
                self.assertEqual(period.start_date, periods[i - 1].end_date + timedelta(days=1))
        for year in (2026, 2027):
            for day in (date(year, 1, 1), date(year, 12, 31)):
                self.assertEqual(sum(p.start_date <= day <= p.end_date for p in periods), 1)

    def test_second_execution_does_not_insert_update_or_delete(self):
        generate_at_2026()
        before = list(PayrollPeriod.objects.values())
        with CaptureQueriesContext(connection) as queries:
            result = generate_at_2026()
        self.assertEqual((result.created, result.existing, result.conflicts), (0, 79, ()))
        self.assertEqual(list(PayrollPeriod.objects.values()), before)
        self.assertFalse(any(
            query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for query in queries
        ))

    def test_only_missing_periods_are_created_including_historical_gaps(self):
        generate_at_2026()
        removed = list(PayrollPeriod.objects.all())[::20]
        missing_starts = {p.start_date for p in removed}
        PayrollPeriod.objects.filter(pk__in=[p.pk for p in removed]).delete()
        before = list(PayrollPeriod.objects.values())
        result = generate_at_2026()
        self.assertEqual((result.created, result.existing), (len(removed), 79 - len(removed)))
        self.assertFalse(result.conflicts)
        self.assertEqual(list(PayrollPeriod.objects.exclude(start_date__in=missing_starts).values()), before)
        self.assertEqual(PayrollPeriod.objects.count(), 79)

    def test_business_year_uses_django_time_in_new_york_not_utc(self):
        # UTC is already in 2027; the original business timezone is still in 2026.
        with timezone.override("UTC"), patch(
            "core.payroll.timezone.now",
            return_value=datetime(2027, 1, 1, 2, tzinfo=datetime_timezone.utc),
        ):
            self.assertEqual(generate_payroll_periods().created, 79)
        self.assertEqual(PayrollPeriod.objects.latest("end_date").end_date, date(2028, 1, 8))
        with patch(
            "core.payroll.timezone.now",
            return_value=datetime(2027, 1, 1, 6, tzinfo=datetime_timezone.utc),
        ):
            result = generate_payroll_periods()
        self.assertEqual((result.created, result.existing), (26, 79))
        self.assertEqual(PayrollPeriod.objects.latest("end_date").end_date, date(2029, 1, 6))

    def test_corrected_payment_and_missing_slot_are_preserved_and_reported(self):
        generate_at_2026()
        corrected = PayrollPeriod.objects.first()
        corrected.payment_date += timedelta(days=1)
        corrected.save(update_fields=["payment_date"])
        before = PayrollPeriod.objects.values().get(pk=corrected.pk)
        PayrollPeriod.objects.latest("start_date").delete()
        result = generate_at_2026()
        self.assertEqual((result.created, result.existing, len(result.conflicts)), (1, 77, 1))
        self.assertEqual(result.conflicts[0].start_date, ANCHOR)
        self.assertEqual(result.conflicts[0].record_ids, (corrected.pk,))
        self.assertEqual(PayrollPeriod.objects.values().get(pk=corrected.pk), before)
        with CaptureQueriesContext(connection) as queries:
            repeated = generate_at_2026()
        self.assertEqual((repeated.created, repeated.existing, len(repeated.conflicts)), (0, 78, 1))
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))

    def test_off_grid_and_reversed_manual_dates_are_not_replaced(self):
        for start, end in (
            (date(2024, 12, 30), date(2025, 1, 12)),
            (date(2025, 1, 12), date(2024, 12, 30)),
        ):
            with self.subTest(start=start, end=end):
                PayrollPeriod.objects.all().delete()
                manual = PayrollPeriod.objects.create(
                    start_date=start, end_date=end, payment_date=date(2025, 1, 22)
                )
                before = PayrollPeriod.objects.values().get(pk=manual.pk)
                result = generate_at_2026()
                self.assertEqual((result.created, result.existing, len(result.conflicts)), (77, 0, 2))
                self.assertEqual([c.start_date for c in result.conflicts], [ANCHOR, date(2025, 1, 12)])
                self.assertEqual(PayrollPeriod.objects.values().get(pk=manual.pk), before)
                self.assertFalse(PayrollPeriod.objects.filter(start_date=ANCHOR).exists())

    def test_duplicate_existing_records_are_reported_without_deduplication(self):
        data = dict(start_date=ANCHOR, end_date=date(2025, 1, 11), payment_date=date(2025, 1, 17))
        first = PayrollPeriod.objects.create(**data)
        second = PayrollPeriod.objects.create(**data)
        result = generate_at_2026()
        self.assertEqual((result.created, result.existing, len(result.conflicts)), (78, 0, 1))
        self.assertEqual(result.conflicts[0].record_ids, (first.pk, second.pk))
        self.assertEqual(PayrollPeriod.objects.filter(start_date=ANCHOR).count(), 2)

    def test_out_of_horizon_records_and_company_settings_are_untouched(self):
        period = PayrollPeriod.objects.create(
            start_date=date(2030, 1, 1), end_date=date(2030, 1, 20), payment_date=date(2030, 1, 25)
        )
        settings = CompanySettings.objects.create(
            effective_from=date(2020, 1, 1), appointment_cutoff_days=7,
            appointment_cutoff_time="10:00", timezone="America/Chihuahua",
        )
        before_period = PayrollPeriod.objects.values().get(pk=period.pk)
        before_settings = CompanySettings.objects.values().get(pk=settings.pk)
        self.assertEqual(generate_at_2026().created, 79)
        self.assertEqual(PayrollPeriod.objects.values().get(pk=period.pk), before_period)
        self.assertEqual(CompanySettings.objects.values().get(pk=settings.pk), before_settings)

    def test_insertion_error_rolls_back_all_generated_records(self):
        original = QuerySet.bulk_create

        def fail_after_first_insert(queryset, objects, *args, **kwargs):
            original(queryset, objects[:1], *args, **kwargs)
            raise RuntimeError("Synthetic failure after an insert")

        with patch.object(QuerySet, "bulk_create", new=fail_after_first_insert):
            with self.assertRaisesMessage(RuntimeError, "Synthetic failure"):
                generate_at_2026()
        self.assertFalse(PayrollPeriod.objects.exists())


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class GeneratePayrollPeriodsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="calendar-operator")

    def setUp(self):
        self.url = reverse("core:payroll-periods:generate")
        self.index = reverse("core:payroll-periods:index")

    def grant(self, *actions):
        self.user.user_permissions.set(Permission.objects.filter(
            content_type__app_label="core",
            codename__in=[f"{action}_payrollperiod" for action in actions],
        ))
        self.client.force_login(self.user)

    def test_list_action_and_confirmation_have_no_generation_side_effects(self):
        self.grant("add", "view")
        with patch("core.views.generate_payroll_periods") as generate:
            self.assertContains(self.client.get(self.index), self.url)
            response = self.client.get(self.url)
            self.assertContains(response, "Generate periods")
            self.assertContains(response, "America/New_York")
            self.assertContains(response, 'name="csrfmiddlewaretoken"')
            self.assertTemplateUsed(response, "viewflow/views/form.html")
            self.assertEqual(self.client.head(self.url).status_code, 200)
            generate.assert_not_called()
        self.assertFalse(PayrollPeriod.objects.exists())

    def test_post_requires_csrf_and_displays_created_and_existing_counts(self):
        self.grant("add", "view")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertFalse(PayrollPeriod.objects.exists())
        self.assertEqual(client.get(self.url).status_code, 200)
        data = {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
        with patch("core.payroll.timezone.now", return_value=NOW_2026):
            response = client.post(self.url, data)
            self.assertRedirects(response, self.index, fetch_redirect_response=False)
            self.assertIn("Periods: 79 created, 0 existing, 0 conflicts.", [str(m) for m in get_messages(response.wsgi_request)])
            response = client.post(self.url, data)
        self.assertIn("Periods: 0 created, 79 existing, 0 conflicts.", [str(m) for m in get_messages(response.wsgi_request)])
        self.assertEqual(PayrollPeriod.objects.count(), 79)

    def test_permission_is_add_not_view_change_or_delete(self):
        for actions in ((), ("view",), ("change",), ("delete",)):
            self.grant(*actions)
            with self.subTest(permissions=actions):
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.post(self.url).status_code, 403)
                if actions in (("view",), ("change",)):
                    self.assertNotContains(self.client.get(self.index), self.url)
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertFalse(PayrollPeriod.objects.exists())
        self.grant("add")
        with patch("core.payroll.timezone.now", return_value=NOW_2026):
            self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(PayrollPeriod.objects.count(), 79)

    def test_conflict_messages_identify_the_preserved_record(self):
        self.grant("add", "view")
        manual = PayrollPeriod.objects.create(
            start_date=ANCHOR, end_date=date(2025, 1, 11), payment_date=date(2025, 1, 18)
        )
        with patch("core.payroll.timezone.now", return_value=NOW_2026):
            response = self.client.post(self.url)
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("78 created, 0 existing, 1 conflicts", text)
        self.assertIn(f"conflict with records {manual.pk}", text)
        manual.refresh_from_db()
        self.assertEqual(manual.payment_date, date(2025, 1, 18))

    def test_lock_timeout_has_a_retry_message_and_no_inserts(self):
        self.grant("add", "view")
        cause = Exception("Synthetic lock conflict")
        cause.sqlstate = "55P03"
        error = OperationalError("Synthetic lock timeout")
        error.__cause__ = cause
        with patch("core.views.generate_payroll_periods", side_effect=error):
            response = self.client.post(self.url)
        self.assertRedirects(response, self.index, fetch_redirect_response=False)
        self.assertIn("The payroll calendar is busy. Please try again.", [str(m) for m in get_messages(response.wsgi_request)])
        self.assertFalse(PayrollPeriod.objects.exists())

    def test_manual_crud_does_not_generate_other_periods(self):
        self.grant("add", "view")
        response = self.client.post(reverse("core:payroll-periods:add"), {
            "start_date": "2026-01-01", "end_date": "2026-01-14", "payment_date": "2026-01-20",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PayrollPeriod.objects.count(), 1)


@skipUnless(connection.vendor == "postgresql", "Concurrency requires PostgreSQL")
class PayrollGenerationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        clock = patch("core.payroll.timezone.now", return_value=NOW_2026)
        clock.start()
        self.addCleanup(clock.stop)

    def worker(self, operation):
        connections.close_all()
        try:
            return operation()
        finally:
            connections.close_all()

    def assert_generator_waiting(self):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE relation = %s::regclass "
                    "AND mode = 'ShareRowExclusiveLock' AND NOT granted)",
                    [PayrollPeriod._meta.db_table],
                )
                if cursor.fetchone()[0]:
                    return
            sleep(0.01)
        self.fail("Second generator did not wait for the conflicting table lock")

    def test_concurrent_empty_table_generation_serializes_without_duplicates(self):
        first_inserting = Event()
        release = Event()
        original = QuerySet.bulk_create

        def hold_first_insert(queryset, objects, *args, **kwargs):
            first_inserting.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Synthetic test synchronization timed out")
            return original(queryset, objects, *args, **kwargs)

        with patch.object(QuerySet, "bulk_create", new=hold_first_insert):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.worker, generate_payroll_periods)
                try:
                    self.assertTrue(first_inserting.wait(timeout=3))
                    second = pool.submit(self.worker, generate_payroll_periods)
                    self.assert_generator_waiting()
                finally:
                    release.set()
                results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual([r.created for r in results], [79, 0])
        self.assertEqual([r.existing for r in results], [0, 79])
        self.assertEqual(PayrollPeriod.objects.count(), 79)
        self.assertEqual(PayrollPeriod.objects.values("start_date").distinct().count(), 79)

    def test_generation_waits_for_manual_correction_and_does_not_overwrite_it(self):
        period = PayrollPeriod.objects.create(
            start_date=ANCHOR, end_date=date(2025, 1, 11), payment_date=date(2025, 1, 17)
        )
        corrected = Event()
        release = Event()

        def correction():
            with transaction.atomic():
                PayrollPeriod.objects.filter(pk=period.pk).update(payment_date=date(2025, 1, 18))
                corrected.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("Synthetic test synchronization timed out")

        with ThreadPoolExecutor(max_workers=2) as pool:
            manual = pool.submit(self.worker, correction)
            try:
                self.assertTrue(corrected.wait(timeout=3))
                generated = pool.submit(self.worker, generate_payroll_periods)
                self.assert_generator_waiting()
            finally:
                release.set()
            manual.result(timeout=10)
            result = generated.result(timeout=10)
        self.assertEqual((result.created, result.existing, len(result.conflicts)), (78, 0, 1))
        period.refresh_from_db()
        self.assertEqual(period.payment_date, date(2025, 1, 18))

    def test_unsupported_isolation_fails_before_any_generation_writes(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            with self.assertRaisesMessage(NotSupportedError, "read committed"):
                generate_at_2026()
        self.assertFalse(PayrollPeriod.objects.exists())
