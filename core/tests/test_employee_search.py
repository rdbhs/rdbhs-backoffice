from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ..models import Employee, EmploymentType, ProductivityGoal


class EmployeeSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        cls.john = Employee.objects.create(
            first_name="John", last_name="Smith", email="john@example.com"
        )
        cls.jane = Employee.objects.create(
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
            is_active=False,
        )
        cls.other = Employee.objects.create(
            first_name="John", last_name="Jones", email="other@example.com"
        )
        cls.full_time = EmploymentType.objects.create(
            code="FT", name="Full time", default_minimum_hours=40
        )
        part_time = EmploymentType.objects.create(
            code="PT", name="Part time", default_minimum_hours=20
        )
        cls.goals = {}
        for employee, employment_type in (
            (cls.john, cls.full_time),
            (cls.jane, part_time),
            (cls.other, cls.full_time),
        ):
            cls.goals[employee.pk] = ProductivityGoal.objects.create(
                employee=employee,
                employment_type=employment_type,
                minimum_hours=employment_type.default_minimum_hours,
                effective_from=date(2026, 1, 1),
            )

    def setUp(self):
        self.client.force_login(self.user)

    def test_name_search_in_both_lists(self):
        cases = (
            ("jOhN", (self.john, self.other)),
            ("sMiTh", (self.john, self.jane)),
            ("oh", (self.john, self.other)),
            ("mit", (self.john, self.jane)),
            ("John Smith", (self.john,)),
            ("Smith John", (self.john,)),
            ("jo smi", (self.john,)),
            ('"John Smith"', ()),
            ("Missing", ()),
            ("", (self.john, self.jane, self.other)),
        )
        for viewset in ("employees", "productivity-goals"):
            for term, employees in cases:
                with self.subTest(viewset=viewset, term=term):
                    response = self.client.get(
                        reverse(f"core:{viewset}:index"), {"_search": term}
                    )
                    self.assertEqual(response.status_code, 200)
                    expected = (
                        employees
                        if viewset == "employees"
                        else [self.goals[employee.pk] for employee in employees]
                    )
                    self.assertCountEqual(response.context["object_list"], expected)
                    self.assertContains(response, "<vf-page-search ")

    def test_employee_search_respects_active_filter(self):
        response = self.client.get(
            reverse("core:employees:index"),
            {"_search": "smith", "is_active": "True"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["object_list"]), [self.john])

    def test_goal_search_respects_employment_type_filter(self):
        response = self.client.get(
            reverse("core:productivity-goals:index"),
            {"_search": "smith", "employment_type": self.full_time.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["object_list"]), [self.goals[self.john.pk]]
        )

    def test_search_preserves_ordering_and_pagination(self):
        employees = []
        goals = []
        for index in range(26):
            employee = Employee.objects.create(
                first_name="Matching",
                last_name=f"Employee{index:02}",
                email=f"matching{index}@example.com",
            )
            employees.append(employee)
            goals.append(
                ProductivityGoal.objects.create(
                    employee=employee,
                    employment_type=self.full_time,
                    minimum_hours=40,
                    effective_from=date(2026, 1, 1),
                )
            )
        for viewset, ordering, expected in (
            ("employees", "-last_name", employees[0]),
            ("productivity-goals", "-employee", goals[0]),
        ):
            with self.subTest(viewset=viewset):
                response = self.client.get(
                    reverse(f"core:{viewset}:index"),
                    {"_search": "matching", "_orderby": ordering, "page": 2},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paginator"].count, 26)
                self.assertEqual(response.context["paginator"].per_page, 25)
                self.assertEqual(list(response.context["object_list"]), [expected])
