from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.templatetags.static import static
from django.test import TestCase
from django.urls import reverse

from ..forms import ProductivityGoalCreateForm
from ..models import Employee, EmploymentType, ProductivityGoal


class ProductivityGoalFormErrorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username="admin")
        cls.employee = Employee.objects.create(
            first_name="John", last_name="Smith", email="john@example.com"
        )
        cls.employment_type = EmploymentType.objects.create(
            code="FT", name="Full time", default_minimum_hours=40
        )
        cls.goal = ProductivityGoal.objects.create(
            employee=cls.employee,
            employment_type=cls.employment_type,
            minimum_hours=40,
            effective_from=date(2026, 1, 1),
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.data = {
            "employee": self.employee.pk,
            "employment_type": self.employment_type.pk,
            "minimum_hours": 30,
            "effective_from": "2026-06-01",
            "notes": "Keep these notes",
        }

    def assert_general_error(self, response, message):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "viewflow/views/form.html")
        self.assertContains(
            response,
            f'<div class="vf-form__errors"><div class="vf-form__error">{message}</div></div>',
            html=True,
        )
        self.assertContains(response, message, count=1)
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        for field, value in self.data.items():
            self.assertEqual(str(form[field].value()), str(value))

    def test_native_form_includes_available_viewflow_resources(self):
        response = self.client.get(reverse("core:productivity-goals:add"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "viewflow/views/form.html")
        for asset in ("viewflow/css/viewflow.min.css", "viewflow/js/viewflow.min.js"):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset))
                self.assertContains(response, static(asset))

    def test_overlap_error_on_edit_is_visible_once_and_preserves_values(self):
        ProductivityGoal.objects.create(
            employee=self.employee,
            employment_type=self.employment_type,
            minimum_hours=40,
            effective_from=date(2026, 12, 1),
        )
        response = self.client.post(
            reverse("core:productivity-goals:change", args=[self.goal.pk]),
            self.data,
        )
        self.assert_general_error(
            response, "This productivity goal overlaps another goal for this employee."
        )
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.minimum_hours, 40)

    def test_save_validation_error_on_create_is_visible_once(self):
        message = "This productivity goal overlaps another goal for this employee."
        with patch.object(
            ProductivityGoalCreateForm, "save", side_effect=ValidationError(message)
        ):
            response = self.client.post(
                reverse("core:productivity-goals:add"), self.data
            )
        self.assert_general_error(response, message)
        self.assertEqual(ProductivityGoal.objects.count(), 1)

    def test_field_errors_keep_native_field_presentation(self):
        self.data["minimum_hours"] = -1
        response = self.client.post(reverse("core:productivity-goals:add"), self.data)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("minimum_hours", form.errors)
        self.assertFalse(form.non_field_errors())
        self.assertContains(
            response, 'error="Ensure this value is greater than or equal to 0."'
        )
        self.assertNotContains(response, '<div class="vf-form__errors">')
        self.assertContains(response, "Keep these notes")
