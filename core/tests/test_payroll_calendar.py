from datetime import date, time

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.forms import modelform_factory
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ..models import CompanySettings, PayrollPeriod


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class PayrollCalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="payroll-operator")

    def cases(self):
        return (
            (
                PayrollPeriod,
                "payroll-periods",
                {
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-15",
                    "payment_date": "2026-10-20",
                },
                {"payment_date": "2026-10-21"},
            ),
            (
                CompanySettings,
                "company-settings",
                {
                    "effective_from": "2026-10-01",
                    "effective_to": "",
                    "appointment_cutoff_days": 3,
                    "appointment_cutoff_time": "17:00:00",
                    "timezone": "America/Chihuahua",
                },
                {
                    "effective_to": "2026-12-31",
                    "appointment_cutoff_days": 5,
                    "appointment_cutoff_time": "16:30:00",
                    "timezone": "America/Mexico_City",
                },
            ),
        )

    def grant(self, *actions):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="core",
                codename__in=[
                    f"{action}_{model._meta.model_name}"
                    for model, _, _, _ in self.cases()
                    for action in actions
                ],
            )
        )
        self.client.force_login(self.user)

    def create_object(self, model, data):
        form = modelform_factory(model, fields="__all__")(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def assert_saved_values(self, obj, data):
        obj.refresh_from_db()
        for field, value in data.items():
            actual = getattr(obj, field)
            if value == "":
                self.assertIsNone(actual)
            elif isinstance(actual, (date, time)):
                self.assertEqual(actual.isoformat(), value)
            else:
                self.assertEqual(actual, value)

    def test_viewflow_crud_uses_native_forms_and_navigation(self):
        self.grant("add", "view", "change", "delete")
        for model, namespace, data, changes in self.cases():
            with self.subTest(model=model.__name__):
                index = reverse(f"core:{namespace}:index")
                add = reverse(f"core:{namespace}:add")
                response = self.client.get(index)
                self.assertContains(response, add)
                self.assertContains(response, "Payroll Periods")
                self.assertContains(response, "Company Settings")
                response = self.client.get(add)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "viewflow/views/form.html")
                self.assertEqual(set(response.context["form"].fields), set(data))
                response = self.client.post(add, data)
                self.assertEqual(response.status_code, 302)
                obj = model.objects.get()
                self.assert_saved_values(obj, data)
                detail = reverse(f"core:{namespace}:detail", args=[obj.pk])
                self.assertEqual(response.url, detail)
                response = self.client.get(index)
                self.assertEqual(list(response.context["object_list"]), [obj])
                self.assertEqual(self.client.get(detail).status_code, 200)
                change = reverse(f"core:{namespace}:change", args=[obj.pk])
                self.assertEqual(self.client.get(change).status_code, 200)
                updated = {**data, **changes}
                self.assertEqual(self.client.post(change, updated).status_code, 302)
                self.assert_saved_values(obj, updated)
                delete = reverse(f"core:{namespace}:delete", args=[obj.pk])
                self.assertEqual(self.client.get(delete).status_code, 200)
                self.assertTrue(model.objects.filter(pk=obj.pk).exists())
                self.assertEqual(self.client.post(delete).status_code, 302)
                self.assertFalse(model.objects.exists())

    def test_django_admin_crud(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.grant("add", "view", "change", "delete")
        for model, _, data, changes in self.cases():
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))
                name = f"admin:core_{model._meta.model_name}"
                self.assertEqual(self.client.get(reverse(f"{name}_changelist")).status_code, 200)
                add = reverse(f"{name}_add")
                self.assertEqual(self.client.get(add).status_code, 200)
                self.assertEqual(self.client.post(add, data).status_code, 302)
                obj = model.objects.get()
                self.assert_saved_values(obj, data)
                change = reverse(f"{name}_change", args=[obj.pk])
                self.assertEqual(self.client.get(change).status_code, 200)
                updated = {**data, **changes}
                self.assertEqual(self.client.post(change, updated).status_code, 302)
                self.assert_saved_values(obj, updated)
                delete = reverse(f"{name}_delete", args=[obj.pk])
                self.assertEqual(self.client.get(delete).status_code, 200)
                self.assertEqual(self.client.post(delete, {"post": "yes"}).status_code, 302)
                self.assertFalse(model.objects.exists())

    def test_users_without_permissions_cannot_access_or_mutate_records(self):
        self.client.force_login(self.user)
        for model, namespace, data, changes in self.cases():
            obj = self.create_object(model, data)
            for action in ("index", "add", "detail", "change", "delete"):
                args = [obj.pk] if action in ("detail", "change", "delete") else []
                url = reverse(f"core:{namespace}:{action}", args=args)
                with self.subTest(model=model.__name__, action=action):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    if action in ("add", "change", "delete"):
                        self.assertEqual(self.client.post(url, {**data, **changes}).status_code, 403)
            self.assertEqual(model.objects.count(), 1)
            self.assert_saved_values(obj, data)

    def test_view_permission_is_read_only(self):
        self.grant("view")
        for model, namespace, data, changes in self.cases():
            obj = self.create_object(model, data)
            index = reverse(f"core:{namespace}:index")
            self.assertEqual(self.client.get(index).status_code, 200)
            self.assertEqual(
                self.client.get(reverse(f"core:{namespace}:detail", args=[obj.pk])).status_code,
                200,
            )
            for action in ("add", "change", "delete"):
                url = reverse(f"core:{namespace}:{action}", args=[] if action == "add" else [obj.pk])
                with self.subTest(model=model.__name__, action=action):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url, {**data, **changes}).status_code, 403)
            self.assert_saved_values(obj, data)

    def test_default_write_permissions_are_independent(self):
        for action in ("add", "change", "delete"):
            self.grant(action)
            for model, namespace, data, changes in self.cases():
                with self.subTest(model=model.__name__, permission=action):
                    obj = self.create_object(model, data)
                    for other in {"add", "change", "delete"} - {action}:
                        url = reverse(f"core:{namespace}:{other}", args=[] if other == "add" else [obj.pk])
                        self.assertEqual(self.client.post(url, data).status_code, 403)
                    url = reverse(f"core:{namespace}:{action}", args=[] if action == "add" else [obj.pk])
                    self.assertEqual(self.client.post(url, {**data, **changes}).status_code, 302)
                    if action == "add":
                        self.assertEqual(model.objects.count(), 2)
                    elif action == "change":
                        self.assert_saved_values(obj, {**data, **changes})
                    else:
                        self.assertFalse(model.objects.exists())
                    model.objects.all().delete()

    def test_anonymous_users_cannot_access_crud(self):
        for model, namespace, data, _ in self.cases():
            obj = self.create_object(model, data)
            for action in ("index", "add", "detail", "change", "delete"):
                url = reverse(f"core:{namespace}:{action}", args=[obj.pk] if action in ("detail", "change", "delete") else [])
                with self.subTest(model=model.__name__, action=action):
                    response = self.client.get(url)
                    self.assertIn(response.status_code, (302, 403))
                    if response.status_code == 302:
                        self.assertTrue(response.url.startswith("/accounts/login/"))
            self.assertEqual(model.objects.count(), 1)

    def test_forms_enforce_only_native_field_requirements(self):
        self.grant("add")
        for model, namespace, data, _ in self.cases():
            with self.subTest(model=model.__name__):
                response = self.client.post(reverse(f"core:{namespace}:add"), {})
                self.assertEqual(response.status_code, 200)
                required = set(data) - {"effective_to"}
                self.assertEqual(set(response.context["form"].errors), required)
                self.assertFalse(model.objects.exists())
        form_class = modelform_factory(CompanySettings, fields="__all__")
        data = self.cases()[1][2]
        self.assertTrue(form_class(data={**data, "appointment_cutoff_days": 0}).is_valid())
        self.assertFalse(form_class(data={**data, "appointment_cutoff_days": -1}).is_valid())
        self.assertFalse(form_class(data={**data, "timezone": "x" * 65}).is_valid())

    def test_no_date_order_overlap_or_timezone_business_rules_are_added(self):
        for model, _, data, _ in self.cases():
            if model is PayrollPeriod:
                data = {**data, "end_date": "2026-09-01", "payment_date": "2026-08-01"}
            else:
                data = {**data, "effective_to": "2026-09-01", "timezone": "unvalidated-timezone"}
            for _ in range(2):
                self.create_object(model, data)
            self.assertEqual(model.objects.count(), 2)

    def test_create_requires_csrf(self):
        self.grant("add", "view")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for model, namespace, data, _ in self.cases():
            with self.subTest(model=model.__name__):
                url = reverse(f"core:{namespace}:add")
                self.assertEqual(client.post(url, data).status_code, 403)
                self.assertEqual(client.get(url).status_code, 200)
                response = client.post(url, {**data, "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(model.objects.count(), 1)
