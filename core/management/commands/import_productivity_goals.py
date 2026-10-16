import csv
import re
import unicodedata
from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction

from core.models import Employee, EmploymentType, ProductivityGoal


def ascii_text(value):
    return (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )


class Command(BaseCommand):
    help = "Import initial productivity goals effective on 2025-08-01."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **options):
        columns = ("first_name", "last_name", "employment_status", "goal")
        start = date(2025, 8, 1)
        processed = employees_created = types_created = goals_created = (
            goals_skipped
        ) = 0
        row_number = 1
        try:
            with open(options["csv_path"], encoding="utf-8-sig", newline="") as source:
                with transaction.atomic():
                    reader = csv.reader(source, strict=True)
                    header = next(reader, [])
                    if len(header) != len(columns) or set(header) != set(columns):
                        raise CommandError(
                            "CSV row 1: Expected exactly these columns: "
                            + ", ".join(columns)
                        )
                    while True:
                        row_number = reader.line_num + 1
                        values = next(reader, None)
                        if values is None:
                            break
                        if len(values) != len(columns):
                            raise CommandError(
                                f"CSV row {row_number}: Expected four values."
                            )
                        row = dict(zip(header, (value.strip() for value in values)))
                        missing = [column for column in columns if not row[column]]
                        if missing:
                            raise CommandError(
                                f"CSV row {row_number}: Missing required values: "
                                + ", ".join(missing)
                            )
                        if (
                            not re.fullmatch(r"[0-9]+", row["goal"])
                            or int(row["goal"]) <= 0
                        ):
                            raise CommandError(
                                f"CSV row {row_number}: goal must be a positive integer."
                            )
                        hours = int(row["goal"])

                        employees = list(
                            Employee.objects.select_for_update().filter(
                                first_name__iexact=row["first_name"],
                                last_name__iexact=row["last_name"],
                            )
                        )
                        if len(employees) > 1:
                            raise CommandError(
                                f"CSV row {row_number}: Multiple Employees match "
                                f"{row['first_name']} {row['last_name']}."
                            )
                        if employees:
                            employee = employees[0]
                        else:
                            local = re.sub(
                                r"[^a-z0-9]",
                                "",
                                ascii_text(
                                    row["first_name"][0] + row["last_name"]
                                ).lower(),
                            )
                            if not local:
                                raise CommandError(
                                    f"CSV row {row_number}: Cannot generate an ASCII email."
                                )
                            email = f"{local}@rd-bhs.com"
                            if Employee.objects.filter(email__iexact=email).exists():
                                raise CommandError(
                                    f"CSV row {row_number}: Generated email {email} already belongs to another Employee."
                                )
                            employee = Employee(
                                first_name=row["first_name"],
                                last_name=row["last_name"],
                                email=email,
                                is_active=True,
                            )
                            employee.full_clean()
                            employee.save()
                            employees_created += 1

                        types = list(
                            EmploymentType.objects.filter(
                                name__iexact=row["employment_status"]
                            )
                        )
                        if len(types) > 1:
                            raise CommandError(
                                f"CSV row {row_number}: Multiple EmploymentTypes match {row['employment_status']}."
                            )
                        if types:
                            employment_type = types[0]
                        else:
                            code = re.sub(
                                r"[^A-Z0-9]+",
                                "_",
                                ascii_text(row["employment_status"]).upper(),
                            ).strip("_")
                            if not code:
                                raise CommandError(
                                    f"CSV row {row_number}: Cannot generate an EmploymentType code."
                                )
                            if EmploymentType.objects.filter(
                                code__iexact=code
                            ).exists():
                                raise CommandError(
                                    f"CSV row {row_number}: Generated EmploymentType code {code} conflicts with an existing type."
                                )
                            employment_type = EmploymentType(
                                name=row["employment_status"],
                                code=code,
                                default_minimum_hours=hours,
                                is_active=True,
                            )
                            employment_type.full_clean()
                            employment_type.save()
                            types_created += 1

                        existing = list(
                            employee.productivity_goals.filter(effective_from=start)
                        )
                        if len(existing) > 1:
                            raise CommandError(
                                f"CSV row {row_number}: Multiple goals already start on {start}."
                            )
                        if existing:
                            goal = existing[0]
                            if (
                                goal.employment_type_id != employment_type.pk
                                or goal.minimum_hours != hours
                            ):
                                raise CommandError(
                                    f"CSV row {row_number}: Conflicting productivity goal for {employee} on {start}; "
                                    "employment type or minimum hours differ."
                                )
                            goals_skipped += 1
                        else:
                            ProductivityGoal(
                                employee=employee,
                                employment_type=employment_type,
                                minimum_hours=hours,
                                effective_from=start,
                            ).create_in_timeline()
                            goals_created += 1
                        processed += 1
        except (
            OSError,
            UnicodeError,
            csv.Error,
            ValidationError,
            DatabaseError,
            ValueError,
        ) as error:
            raise CommandError(f"CSV row {row_number}: {error}") from error

        self.stdout.write(
            "Import completed\n"
            f"Rows processed: {processed}\n"
            f"Employees created: {employees_created}\n"
            f"Employment types created: {types_created}\n"
            f"Productivity goals created: {goals_created}\n"
            f"Productivity goals skipped: {goals_skipped}"
        )
