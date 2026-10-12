from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Employee, EmploymentType, ProductivityGoal


class EmployeeCrudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="operator", email="operator@example.com", password="test-password"
        )
        cls.employee = Employee.objects.create(
            first_name="Jane", last_name="Doe", email="jane@example.com"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_urls_and_get_pages(self):
        routes = {
            "list": "/employees/",
            "create": "/employees/add/",
            "detail": f"/employees/{self.employee.pk}/",
            "update": f"/employees/{self.employee.pk}/edit/",
            "delete": f"/employees/{self.employee.pk}/delete/",
        }
        for action, path in routes.items():
            with self.subTest(action=action):
                args = (
                    [self.employee.pk]
                    if action in {"detail", "update", "delete"}
                    else []
                )
                self.assertEqual(reverse(f"employee-{action}", args=args), path)
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                expected = {
                    "list": "crud_views/view_list_table.html",
                    "detail": "core/employee/detail.html",
                    "create": "crud_views/view_create.html",
                    "update": "crud_views/view_update.html",
                    "delete": "crud_views/view_delete.html",
                }
                self.assertTemplateUsed(response, expected[action])
                self.assertContains(response, "container-fluid")
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())

    def test_create_update_and_delete(self):
        response = self.client.post(
            reverse("employee-create"),
            {
                "first_name": "John",
                "last_name": "Smith",
                "email": "john@example.com",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("employee-list"))
        employee = Employee.objects.get(email="john@example.com")
        response = self.client.post(
            reverse("employee-update", args=[employee.pk]),
            {
                "first_name": "Jonathan",
                "last_name": "Smith",
                "email": "john@example.com",
            },
        )
        self.assertRedirects(response, reverse("employee-list"))
        employee.refresh_from_db()
        self.assertEqual(employee.first_name, "Jonathan")
        self.assertFalse(employee.is_active)
        response = self.client.post(reverse("employee-delete", args=[employee.pk]))
        self.assertRedirects(response, reverse("employee-list"))
        self.assertFalse(Employee.objects.filter(pk=employee.pk).exists())

    def test_duplicate_email_validation(self):
        response = self.client.post(
            reverse("employee-create"),
            {
                "first_name": "Other",
                "last_name": "Person",
                "email": self.employee.email,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(Employee.objects.count(), 1)

    def test_search_status_sorting_and_pagination(self):
        Employee.objects.bulk_create(
            [
                Employee(
                    first_name="Other",
                    last_name=f"Person {i:02}",
                    email=f"other{i}@example.com",
                    is_active=False,
                )
                for i in range(25)
            ]
        )
        response = self.client.get(
            reverse("employee-list"),
            {"search": "jane", "is_active": "true", "sort": "email"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["filter"].qs), [self.employee])
        response = self.client.get(
            reverse("employee-list"), {"sort": "-last_name", "page": 2}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["table"].page.number, 2)
        self.assertEqual(len(response.context["table"].page.object_list), 6)

    def test_protected_employee_cannot_be_deleted(self):
        employment_type = EmploymentType.objects.create(
            code="TEST", name="Test", default_minimum_hours=40
        )
        ProductivityGoal.objects.create(
            employee=self.employee,
            employment_type=employment_type,
            minimum_hours=40,
            effective_from=date(2026, 1, 1),
        )
        response = self.client.post(reverse("employee-delete", args=[self.employee.pk]))
        self.assertContains(response, "cannot be deleted")
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())

    def test_access_requires_model_permissions(self):
        self.client.logout()
        response = self.client.get(reverse("employee-list"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("account_login")))
        user = get_user_model().objects.create_user(username="viewer")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("employee-list")).status_code, 403)


class CatalogAndGoalCrudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="catalog-operator",
            email="catalog@example.com",
            password="test-password",
        )
        cls.employee = Employee.objects.create(
            first_name="Jane", last_name="Doe", email="jane@example.com"
        )
        cls.employment_type = EmploymentType.objects.create(
            code="FULL_TIME", name="Full time", default_minimum_hours=60
        )
        cls.goal = ProductivityGoal.objects.create(
            employee=cls.employee,
            employment_type=cls.employment_type,
            minimum_hours=40,
            effective_from=date(2026, 1, 1),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_generated_urls_and_pages(self):
        for name, prefix, obj in [
            ("employment_type", "employment-types", self.employment_type),
            ("productivity_goal", "productivity-goals", self.goal),
        ]:
            for action, suffix in [
                ("list", ""),
                ("create", "add/"),
                ("detail", f"{obj.pk}/"),
                ("update", f"{obj.pk}/edit/"),
                ("delete", f"{obj.pk}/delete/"),
            ]:
                with self.subTest(model=name, action=action):
                    args = [obj.pk] if action in {"detail", "update", "delete"} else []
                    url = reverse(f"{name}-{action}", args=args)
                    self.assertEqual(url, f"/{prefix}/{suffix}")
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    template = {"list": "list_table", "detail": "detail_custom"}.get(action, action)
                    self.assertTemplateUsed(response, f"crud_views/view_{template}.html")
                    self.assertContains(response, "container-fluid")
                    self.assertContains(response, 'aria-current="page"')
        self.assertTrue(ProductivityGoal.objects.filter(pk=self.goal.pk).exists())

    def test_employment_type_writes_and_protected_delete(self):
        data = {
            "code": "PART_TIME",
            "name": "Part time",
            "default_minimum_hours": 40,
            "is_active": "on",
        }
        response = self.client.post(reverse("employment_type-create"), data)
        self.assertRedirects(response, reverse("employment_type-list"))
        obj = EmploymentType.objects.get(code="PART_TIME")
        data.update(name="Part time revised", default_minimum_hours=45)
        response = self.client.post(
            reverse("employment_type-update", args=[obj.pk]), data
        )
        self.assertRedirects(response, reverse("employment_type-list"))
        obj.refresh_from_db()
        self.assertEqual(obj.default_minimum_hours, 45)
        response = self.client.post(reverse("employment_type-delete", args=[obj.pk]))
        self.assertRedirects(response, reverse("employment_type-list"))
        self.assertFalse(EmploymentType.objects.filter(pk=obj.pk).exists())
        response = self.client.post(
            reverse("employment_type-delete", args=[self.employment_type.pk])
        )
        self.assertContains(response, "cannot be deleted")
        self.assertTrue(
            EmploymentType.objects.filter(pk=self.employment_type.pk).exists()
        )

    def test_goal_writes_and_date_constraint_validation(self):
        data = {
            "employee": self.employee.pk,
            "employment_type": self.employment_type.pk,
            "minimum_hours": 35,
            "effective_from": "2026-02-01",
            "effective_to": "",
        }
        response = self.client.post(reverse("productivity_goal-create"), data)
        self.assertRedirects(response, reverse("productivity_goal-list"))
        obj = ProductivityGoal.objects.get(effective_from=date(2026, 2, 1))
        self.assertEqual(obj.minimum_hours, 35)
        self.assertIsNone(obj.effective_to)
        self.goal.refresh_from_db()
        self.assertIsNone(self.goal.effective_to)
        data.update(minimum_hours=42, effective_to="2026-02-28")
        response = self.client.post(
            reverse("productivity_goal-update", args=[obj.pk]), data
        )
        self.assertRedirects(response, reverse("productivity_goal-list"))
        obj.refresh_from_db()
        self.assertEqual(obj.minimum_hours, 42)
        data["effective_to"] = "2026-01-01"
        response = self.client.post(
            reverse("productivity_goal-update", args=[obj.pk]), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        obj.refresh_from_db()
        self.assertEqual(obj.effective_to, date(2026, 2, 28))
        response = self.client.post(reverse("productivity_goal-delete", args=[obj.pk]))
        self.assertRedirects(response, reverse("productivity_goal-list"))
        self.assertFalse(ProductivityGoal.objects.filter(pk=obj.pk).exists())

    def test_filters_sorting_and_pagination(self):
        EmploymentType.objects.bulk_create(
            [
                EmploymentType(
                    code=f"TYPE_{i}",
                    name=f"Type {i:02}",
                    default_minimum_hours=40,
                    is_active=False,
                )
                for i in range(21)
            ]
        )
        ProductivityGoal.objects.bulk_create(
            [
                ProductivityGoal(
                    employee=self.employee,
                    employment_type=self.employment_type,
                    minimum_hours=40,
                    effective_from=date(2026, 2, 1),
                )
                for _ in range(21)
            ]
        )
        response = self.client.get(
            reverse("employment_type-list"), {"search": "FULL", "is_active": "true"}
        )
        self.assertEqual(list(response.context["filter"].qs), [self.employment_type])
        response = self.client.get(
            reverse("productivity_goal-list"),
            {
                "search": "jane@example.com",
                "employment_type": self.employment_type.pk,
                "effective_from": "2026-01-01",
                "sort": "employee",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["filter"].qs), [self.goal])
        for name, sort in [
            ("employment_type", "-name"),
            ("productivity_goal", "-employment_type"),
        ]:
            with self.subTest(model=name):
                response = self.client.get(
                    reverse(f"{name}-list"), {"page": 2, "sort": sort}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["table"].page.number, 2)
                self.assertEqual(len(response.context["table"].page.object_list), 2)

    def test_employee_details_and_related_query_optimization(self):
        response = self.client.get(reverse("employee-detail", args=[self.employee.pk]))
        self.assertContains(response, "Created at")
        self.assertContains(response, "Updated at")
        self.assertContains(response, "Productivity goals")
        self.assertContains(response, "Full time")
        with self.assertNumQueries(1):
            goals = list(ProductivityGoal.objects.all())
            for goal in goals:
                str(goal)
                str(goal.employment_type)

    def test_access_requires_permissions(self):
        user = get_user_model().objects.create_user(username="catalog-viewer")
        self.client.force_login(user)
        for name, obj in [
            ("employment_type", self.employment_type),
            ("productivity_goal", self.goal),
        ]:
            for action in ["list", "create", "detail", "update", "delete"]:
                with self.subTest(model=name, action=action):
                    args = [obj.pk] if action in {"detail", "update", "delete"} else []
                    self.assertEqual(
                        self.client.get(
                            reverse(f"{name}-{action}", args=args)
                        ).status_code,
                        403,
                    )


class TablerModelFormTests(TestCase):
    def test_widget_defaults_and_saved_dates(self):
        from .forms import EmployeeForm, EmploymentTypeForm, ProductivityGoalForm

        self.assertIn('type="email"', str(EmployeeForm()["email"]))
        self.assertIn('class="form-check-input"', str(EmployeeForm()["is_active"]))
        catalog = EmploymentTypeForm()
        self.assertIn('type="number"', str(catalog["default_minimum_hours"]))
        self.assertIn('class="form-control"', str(catalog["name"]))
        self.assertIn('rows="3"', str(catalog["description"]))
        goal = ProductivityGoalForm(instance=ProductivityGoal(
            effective_from=date(2026, 2, 1), effective_to=date(2026, 2, 28)
        ))
        self.assertIn('class="form-select"', str(goal["employee"]))
        for name, value in [("effective_from", "2026-02-01"), ("effective_to", "2026-02-28")]:
            self.assertIn('type="date"', str(goal[name]))
            self.assertIn(f'value="{value}"', str(goal[name]))

    def test_explicit_widget_configuration_is_preserved(self):
        from django import forms
        from .forms import EmploymentTypeForm

        class CustomForm(EmploymentTypeForm):
            class Meta(EmploymentTypeForm.Meta):
                widgets = {
                    "description": forms.Textarea(attrs={"class": "custom", "rows": 7}),
                }

        widget = CustomForm().fields["description"].widget
        self.assertEqual(widget.attrs["class"], "custom")
        self.assertEqual(widget.attrs["rows"], 7)
        self.assertEqual(EmploymentTypeForm().fields["description"].widget.attrs["rows"], 3)


class AuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="local-operator", email="operator@example.com",
            password="test-password",
        )

    def test_login_page_and_local_login(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, "Sign in with Microsoft")
        self.assertContains(response, "Username")
        self.assertTemplateUsed(response, "account/login.html")
        response = self.client.post(reverse("account_login"), {
            "login": self.user.username, "password": "test-password",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employee-list"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_preserves_next_and_rejects_external_redirects(self):
        for target, expected in [
            (reverse("employment_type-list"), reverse("employment_type-list")),
            ("https://example.com/", reverse("employee-list")),
        ]:
            with self.subTest(target=target):
                self.client.logout()
                response = self.client.get(reverse("account_login"), {"next": target})
                if target == expected:
                    self.assertContains(response, f'value="{target}"')
                response = self.client.post(reverse("account_login"), {
                    "login": self.user.username, "password": "test-password",
                    "next": target,
                })
                self.assertEqual(response.url, expected)

    def test_incorrect_credentials(self):
        response = self.client.post(reverse("account_login"), {
            "login": self.user.username, "password": "incorrect-password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("account_logout")).status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        response = self.client.post(reverse("account_logout"))
        self.assertRedirects(response, reverse("account_login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_frontend_permissions_and_admin_login(self):
        from django.contrib.auth.models import Permission

        for name in ["employee", "employment_type", "productivity_goal"]:
            response = self.client.get(reverse(f"{name}-list"))
            self.assertEqual(response.url,
                f'{reverse("account_login")}?next={reverse(f"{name}-list")}')
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("employee-list")).status_code, 403)
        self.user.user_permissions.add(Permission.objects.get(codename="view_employee"))
        response = self.client.get(reverse("employee-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'action="/accounts/logout/"')
        self.assertContains(response, self.user.email)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)

    def test_csrf_protection(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("account_login"), {
            "login": self.user.username, "password": "test-password",
        }).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("account_logout")).status_code, 403)

    def test_microsoft_provider_uses_tenant_and_actual_callback(self):
        from urllib.parse import parse_qs, urlsplit
        from django.test import override_settings

        tenant = "11111111-2222-3333-4444-555555555555"
        providers = {"microsoft": {"APPS": [{
            "client_id": "test-client", "secret": "test-secret",
            "settings": {"tenant": tenant},
        }]}}
        with override_settings(SOCIALACCOUNT_PROVIDERS=providers):
            response = self.client.get(reverse("account_login"))
            self.assertContains(response, 'action="/accounts/microsoft/login/"')
            response = self.client.post(reverse("microsoft_login"))
            self.assertEqual(response.status_code, 302)
            url = urlsplit(response.url)
            self.assertEqual(url.netloc, "login.microsoftonline.com")
            self.assertEqual(url.path, f"/{tenant}/oauth2/v2.0/authorize")
            query = parse_qs(url.query)
            self.assertEqual(query["response_type"], ["code"])
            self.assertEqual(query["redirect_uri"],
                ["http://testserver/accounts/microsoft/login/callback/"])
            self.assertIn("User.Read", query["scope"][0])
            self.assertIn("state", query)
        self.assertEqual(reverse("microsoft_callback"),
            "/accounts/microsoft/login/callback/")

    def test_matching_email_does_not_implicitly_link_accounts(self):
        from allauth.account.models import EmailAddress
        from allauth.socialaccount.internal.flows.signup import process_auto_signup_email
        from allauth.socialaccount.models import SocialAccount, SocialLogin
        from django.test import RequestFactory
        from django.conf import settings

        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION)
        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT)
        pending = SocialLogin(
            user=get_user_model()(email=self.user.email),
            account=SocialAccount(provider="microsoft", uid="test-microsoft-id"),
            email_addresses=[EmailAddress(email=self.user.email, verified=True)],
        )
        allowed, response = process_auto_signup_email(
            RequestFactory().get("/accounts/microsoft/login/callback/"), pending,
        )
        self.assertFalse(allowed)
        self.assertIsNone(response)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(SocialAccount.objects.exists())
