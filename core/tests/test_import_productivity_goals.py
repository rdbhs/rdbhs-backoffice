import csv
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..models import Employee, EmploymentType, ProductivityGoal


class ImportProductivityGoalsTests(TestCase):
    columns = ("first_name", "last_name", "employment_status", "goal")

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "goals.csv"

    def import_rows(self, rows, columns=None):
        with self.path.open("w", encoding="utf-8", newline="") as source:
            writer = csv.writer(source)
            writer.writerow(self.columns if columns is None else columns)
            writer.writerows(rows)
        output = StringIO()
        call_command("import_productivity_goals", str(self.path), stdout=output)
        return output.getvalue()

    def employee(self, **overrides):
        return Employee.objects.create(**{
            "first_name": "John", "last_name": "Smith", "email": "original@example.com",
            **overrides,
        })

    def employment_type(self, **overrides):
        return EmploymentType.objects.create(**{
            "name": "Full time", "code": "FULL_TIME", "default_minimum_hours": 40,
            **overrides,
        })

    def test_successful_import_creates_employee_type_and_goal(self):
        output = self.import_rows([(" John ", " Smith ", " Full time ", " 40 ")])
        employee = Employee.objects.get()
        self.assertEqual(employee.first_name, "John")
        self.assertEqual(employee.last_name, "Smith")
        self.assertEqual(employee.email, "jsmith@rd-bhs.com")
        self.assertTrue(employee.is_active)
        employment_type = EmploymentType.objects.get()
        self.assertEqual(employment_type.name, "Full time")
        self.assertEqual(employment_type.code, "FULL_TIME")
        self.assertEqual(employment_type.default_minimum_hours, 40)
        self.assertTrue(employment_type.is_active)
        goal = ProductivityGoal.objects.get()
        self.assertEqual(goal.employee, employee)
        self.assertEqual(goal.employment_type, employment_type)
        self.assertEqual(goal.minimum_hours, 40)
        self.assertEqual(goal.effective_from, date(2025, 8, 1))
        self.assertIsNone(goal.effective_to)
        self.assertEqual(output, (
            "Import completed\nRows processed: 1\nEmployees created: 1\n"
            "Employment types created: 1\nProductivity goals created: 1\n"
            "Productivity goals skipped: 0\n"
        ))

    def test_email_normalization(self):
        self.import_rows([
            ("José", "De León", "Full time", "40"),
            ("Mary", "Johnson", "Full time", "40"),
            ("Anne", "O'Neil-2", "Full time", "40"),
        ])
        self.assertSetEqual(set(Employee.objects.values_list("email", flat=True)), {
            "jdeleon@rd-bhs.com", "mjohnson@rd-bhs.com", "aoneil2@rd-bhs.com",
        })

    def test_employment_type_code_normalization(self):
        self.import_rows([
            ("John", "Smith", "Part time/Intern", "20"),
            ("Mary", "Johnson", " -- Témp /  intérn -- ", "10"),
        ])
        self.assertSetEqual(set(EmploymentType.objects.values_list("code", flat=True)), {
            "PART_TIME_INTERN", "TEMP_INTERN",
        })

    def test_existing_employee_and_type_are_reused_without_changes(self):
        employee = self.employee(is_active=False)
        employment_type = self.employment_type(default_minimum_hours=99, is_active=False)
        output = self.import_rows([("JOHN", "smith", "FULL TIME", "30")])
        employee.refresh_from_db()
        employment_type.refresh_from_db()
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(EmploymentType.objects.count(), 1)
        self.assertEqual(employee.email, "original@example.com")
        self.assertEqual(employee.first_name, "John")
        self.assertFalse(employee.is_active)
        self.assertEqual(employment_type.name, "Full time")
        self.assertEqual(employment_type.default_minimum_hours, 99)
        self.assertFalse(employment_type.is_active)
        goal = ProductivityGoal.objects.get()
        self.assertEqual(goal.employee, employee)
        self.assertEqual(goal.employment_type, employment_type)
        self.assertEqual(goal.minimum_hours, 30)
        self.assertIn("Employees created: 0", output)
        self.assertIn("Employment types created: 0", output)

    def test_repeated_import_skips_identical_goals(self):
        rows = [("John", "Smith", "Full time", "40")]
        self.import_rows(rows)
        goal = ProductivityGoal.objects.get()
        output = self.import_rows(rows)
        self.assertEqual(ProductivityGoal.objects.get().pk, goal.pk)
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(EmploymentType.objects.count(), 1)
        self.assertIn("Productivity goals created: 0", output)
        self.assertIn("Productivity goals skipped: 1", output)

    def test_conflicting_existing_hours_fail(self):
        self.import_rows([("John", "Smith", "Full time", "40")])
        with self.assertRaisesRegex(CommandError, "CSV row 2: Conflicting productivity goal"):
            self.import_rows([("John", "Smith", "Full time", "20")])
        self.assertEqual(ProductivityGoal.objects.get().minimum_hours, 40)

    def test_conflicting_existing_type_fails_and_new_type_is_rolled_back(self):
        self.import_rows([("John", "Smith", "Full time", "40")])
        with self.assertRaisesRegex(CommandError, "CSV row 2: Conflicting productivity goal"):
            self.import_rows([("John", "Smith", "Part time", "40")])
        self.assertEqual(EmploymentType.objects.count(), 1)

    def test_invalid_goal_fails(self):
        for value in ("0", "-1", "1.5", "abc", "+2"):
            with self.subTest(goal=value):
                with self.assertRaisesRegex(CommandError, "CSV row 2: goal must be a positive integer"):
                    self.import_rows([("John", "Smith", "Full time", value)])
                self.assertFalse(Employee.objects.exists())

    def test_missing_values_fail(self):
        for index in range(4):
            with self.subTest(column=self.columns[index]):
                row = ["John", "Smith", "Full time", "40"]
                row[index] = " "
                with self.assertRaisesRegex(CommandError, "CSV row 2: Missing required values"):
                    self.import_rows([row])

    def test_exact_columns_required(self):
        for columns in (self.columns[:-1], (*self.columns, "notes"), (*self.columns[:-1], "first_name")):
            with self.subTest(columns=columns):
                with self.assertRaisesRegex(CommandError, "CSV row 1: Expected exactly these columns"):
                    self.import_rows([], columns=columns)

    def test_invalid_row_width_fails(self):
        with self.assertRaisesRegex(CommandError, "CSV row 2: Expected four values"):
            self.import_rows([("John", "Smith", "Full time", "40", "extra")])

    def test_duplicate_employee_name_fails(self):
        self.employee()
        self.employee(first_name="JOHN", last_name="SMITH", email="other@example.com")
        with self.assertRaisesRegex(CommandError, "CSV row 2: Multiple Employees match"):
            self.import_rows([("John", "Smith", "Full time", "40")])
        self.assertFalse(ProductivityGoal.objects.exists())

    def test_generated_email_collision_fails(self):
        self.employee(first_name="Jane", email="jsmith@rd-bhs.com")
        with self.assertRaisesRegex(CommandError, "CSV row 2: Generated email jsmith@rd-bhs.com"):
            self.import_rows([("John", "Smith", "Full time", "40")])
        self.assertEqual(Employee.objects.count(), 1)

    def test_generated_type_code_collision_fails(self):
        self.employment_type(name="Different", code="PART_TIME")
        with self.assertRaisesRegex(CommandError, "CSV row 2: Generated EmploymentType code PART_TIME"):
            self.import_rows([("John", "Smith", "Part time", "20")])
        self.assertFalse(Employee.objects.exists())
        self.assertEqual(EmploymentType.objects.count(), 1)

    def test_failure_rolls_back_complete_import(self):
        with self.assertRaisesRegex(CommandError, "CSV row 3: goal must be a positive integer"):
            self.import_rows([
                ("John", "Smith", "Full time", "40"),
                ("Mary", "Johnson", "Part time", "bad"),
            ])
        self.assertFalse(Employee.objects.exists())
        self.assertFalse(EmploymentType.objects.exists())
        self.assertFalse(ProductivityGoal.objects.exists())

    def test_import_uses_timeline_and_rolls_back_neighbor_changes(self):
        employee = self.employee()
        employment_type = self.employment_type()
        previous = ProductivityGoal(
            employee=employee, employment_type=employment_type,
            minimum_hours=40, effective_from=date(2025, 1, 1),
        ).create_in_timeline()
        following = ProductivityGoal(
            employee=employee, employment_type=employment_type,
            minimum_hours=40, effective_from=date(2026, 1, 1),
        ).create_in_timeline()
        rows = [("John", "Smith", "Full time", "30")]
        with self.assertRaisesRegex(CommandError, "CSV row 3"):
            self.import_rows([*rows, ("Mary", "Johnson", "Part time", "bad")])
        previous.refresh_from_db()
        self.assertEqual(previous.effective_to, date(2025, 12, 31))
        self.assertEqual(ProductivityGoal.objects.count(), 2)
        self.import_rows(rows)
        previous.refresh_from_db()
        following.refresh_from_db()
        imported = ProductivityGoal.objects.get(effective_from=date(2025, 8, 1))
        self.assertEqual(previous.effective_to, date(2025, 7, 31))
        self.assertEqual(imported.effective_to, date(2025, 12, 31))
        self.assertIsNone(following.effective_to)
