from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import TestCase

from .forms import ProductivityGoalCreateForm
from .models import Employee, EmploymentType, ProductivityGoal


class ProductivityGoalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(
            first_name="First", last_name="Employee", email="first@example.com"
        )
        cls.employment_type = EmploymentType.objects.create(
            code="FT", name="Full time", default_minimum_hours=40
        )

    def create_goal(self, start, end=None, **kwargs):
        instance = ProductivityGoal(
            employee=kwargs.pop("employee", self.employee),
            employment_type=self.employment_type,
            minimum_hours=40,
            effective_from=date.fromisoformat(start),
            effective_to=date.fromisoformat(end) if end else None,
            **kwargs,
        )
        return instance.create_in_timeline()

    def test_creation_ignores_assigned_end(self):
        for has_following in (False, True):
            for end in ("2026-05-31", "2026-11-30", "2027-01-01"):
                with self.subTest(has_following=has_following, end=end):
                    employee = Employee.objects.create(
                        first_name="Case",
                        last_name="Employee",
                        email=f"{has_following}-{end}@example.com",
                    )
                    if has_following:
                        self.create_goal("2026-12-01", employee=employee)
                    created = self.create_goal("2026-06-01", end, employee=employee)
                    created.refresh_from_db()
                    self.assertEqual(
                        created.effective_to,
                        date(2026, 11, 30) if has_following else None,
                    )

    def test_first_goal(self):
        first = self.create_goal("2026-01-01")
        self.assertIsNotNone(first.pk)
        self.assertIsNone(first.effective_to)

    def test_new_goal_closes_open_previous(self):
        previous = self.create_goal("2026-01-01")
        new = self.create_goal("2026-10-15")
        previous.refresh_from_db()
        self.assertEqual(previous.effective_to, date(2026, 10, 14))
        self.assertIsNone(new.effective_to)

    def test_new_goal_splits_historical_range(self):
        previous = self.create_goal("2026-01-01")
        following = self.create_goal("2026-12-01")
        new = self.create_goal("2026-06-01")
        previous.refresh_from_db()
        following.refresh_from_db()
        self.assertEqual(previous.effective_to, date(2026, 5, 31))
        self.assertEqual(new.effective_to, date(2026, 11, 30))
        self.assertEqual(following.effective_from, date(2026, 12, 1))
        self.assertIsNone(following.effective_to)

    def test_goal_before_future_goal(self):
        following = self.create_goal("2026-12-01")
        new = self.create_goal("2026-06-01")
        following.refresh_from_db()
        self.assertEqual(new.effective_to, date(2026, 11, 30))
        self.assertIsNone(following.effective_to)

    def test_duplicate_start_rejected(self):
        previous = self.create_goal("2026-01-01")
        with self.assertRaisesRegex(ValidationError, "already starts on this date"):
            self.create_goal("2026-01-01")
        previous.refresh_from_db()
        self.assertIsNone(previous.effective_to)
        self.assertEqual(ProductivityGoal.objects.count(), 1)

    def test_overlap_on_update_rejected(self):
        previous = self.create_goal("2026-01-01")
        self.create_goal("2026-06-01")
        previous.refresh_from_db()
        previous.effective_to = date(2026, 6, 1)
        with self.assertRaisesRegex(ValidationError, "overlaps another goal"):
            previous.full_clean()

    def test_unchanged_update_excludes_itself(self):
        existing = self.create_goal("2026-01-01")
        existing.minimum_hours = 30
        existing.full_clean()
        existing.save()
        self.assertEqual(existing.minimum_hours, 30)

    def test_invalid_end_rejected(self):
        existing = self.create_goal("2026-06-01")
        existing.effective_to = date(2026, 5, 31)
        with self.assertRaisesRegex(
            ValidationError, "Effective to must be on or after effective from"
        ):
            existing.full_clean()
        existing.refresh_from_db()
        self.assertIsNone(existing.effective_to)

    def test_different_employees_do_not_conflict(self):
        first = self.create_goal("2026-01-01")
        other = Employee.objects.create(
            first_name="Other", last_name="Employee", email="other@example.com"
        )
        second = self.create_goal("2026-01-01", employee=other)
        first.refresh_from_db()
        self.assertIsNone(first.effective_to)
        self.assertIsNone(second.effective_to)

    def test_plain_model_validation_rejects_overlap(self):
        self.create_goal("2026-01-01")
        overlapping = ProductivityGoal(
            employee=self.employee,
            employment_type=self.employment_type,
            minimum_hours=20,
            effective_from=date(2026, 6, 1),
        )
        with self.assertRaisesRegex(ValidationError, "overlaps another goal"):
            overlapping.full_clean()

    def test_creation_failure_rolls_back_previous_goal(self):
        previous = self.create_goal("2026-01-01")
        original_save = ProductivityGoal.save

        def fail_insert(instance, *args, **kwargs):
            if instance._state.adding:
                raise RuntimeError("Creation failed")
            return original_save(instance, *args, **kwargs)

        with patch.object(ProductivityGoal, "save", fail_insert):
            with self.assertRaisesRegex(RuntimeError, "Creation failed"):
                self.create_goal("2026-06-01")
        previous.refresh_from_db()
        self.assertIsNone(previous.effective_to)
        self.assertEqual(ProductivityGoal.objects.count(), 1)

    def test_create_form_previews_then_saves_timeline(self):
        previous = self.create_goal("2026-01-01")
        form = ProductivityGoalCreateForm(
            data={
                "employee": self.employee.pk,
                "employment_type": self.employment_type.pk,
                "minimum_hours": 20,
                "effective_from": "2026-06-01",
                "effective_to": "2026-05-31",
            }
        )
        self.assertEqual(
            list(form.fields),
            ["employee", "employment_type", "minimum_hours", "effective_from", "notes"],
        )
        self.assertTrue(form.is_valid(), form.errors)
        previous.refresh_from_db()
        self.assertIsNone(previous.effective_to)
        created = form.save()
        previous.refresh_from_db()
        self.assertEqual(previous.effective_to, date(2026, 5, 31))
        self.assertIsNotNone(created.pk)
        self.assertIsNone(created.effective_to)

    def test_update_form_rejects_overlap_without_adjusting_neighbors(self):
        previous = self.create_goal("2026-01-01")
        following = self.create_goal("2026-06-01")
        previous.refresh_from_db()
        form_class = modelform_factory(ProductivityGoal, fields="__all__")
        form = form_class(
            instance=previous,
            data={
                "employee": self.employee.pk,
                "employment_type": self.employment_type.pk,
                "minimum_hours": 40,
                "effective_from": "2026-01-01",
                "effective_to": "2026-06-01",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("overlaps another goal", str(form.errors))
        following.refresh_from_db()
        self.assertIsNone(following.effective_to)
