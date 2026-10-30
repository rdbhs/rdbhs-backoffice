"""Explicit, append-only generation of the anchored payroll calendar."""

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import NotSupportedError, connections, router, transaction
from django.utils import timezone

from .models import PayrollPeriod


ANCHOR_DATE = date(2024, 12, 29)
PERIOD_LENGTH = timedelta(days=14)


@dataclass(frozen=True)
class PayrollPeriodConflict:
    start_date: date
    record_ids: tuple[int, ...]


@dataclass(frozen=True)
class PayrollGenerationResult:
    created: int
    existing: int
    conflicts: tuple[PayrollPeriodConflict, ...]


def generate_payroll_periods() -> PayrollGenerationResult:
    """Fill missing slots through next year, preserving and reporting conflicts.

    An exact, sole match is existing. Any other intersecting record (including
    a duplicate or corrected date) conflicts with that slot, which is skipped.
    All remaining inserts commit together, or none do if an error occurs.
    """
    today = timezone.localdate(timezone=ZoneInfo(settings.PAYROLL_TIME_ZONE))
    horizon = date(today.year + 1, 12, 31)
    database = router.db_for_write(PayrollPeriod)
    connection = connections[database]
    if connection.vendor != "postgresql":
        raise NotSupportedError("Payroll generation requires PostgreSQL locking.")

    with transaction.atomic(using=database):
        # Row locks cannot protect an empty table or gaps. Serialize generators
        # and manual writes, while allowing ordinary readers to continue.
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise NotSupportedError(
                    "Payroll generation requires read committed isolation."
                )
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute(
                f"LOCK TABLE {connection.ops.quote_name(PayrollPeriod._meta.db_table)} "
                "IN SHARE ROW EXCLUSIVE MODE"
            )

        periods = PayrollPeriod.objects.using(database)
        records = list(periods.all())
        missing = []
        existing = 0
        conflicts = []
        start = ANCHOR_DATE
        while start <= horizon:
            end = start + timedelta(days=13)
            payment = end + timedelta(days=6)
            # Treat reversed manual ranges conservatively too; never rewrite
            # them or insert a replacement on top of their date span.
            intersecting = [
                record
                for record in records
                if min(record.start_date, record.end_date) <= end
                and max(record.start_date, record.end_date) >= start
            ]
            if len(intersecting) == 1 and (
                intersecting[0].start_date,
                intersecting[0].end_date,
                intersecting[0].payment_date,
            ) == (start, end, payment):
                existing += 1
            elif intersecting:
                conflicts.append(
                    PayrollPeriodConflict(
                        start_date=start,
                        record_ids=tuple(sorted(record.pk for record in intersecting)),
                    )
                )
            else:
                missing.append(
                    PayrollPeriod(start_date=start, end_date=end, payment_date=payment)
                )
            start += PERIOD_LENGTH

        if missing:
            periods.bulk_create(missing)
        return PayrollGenerationResult(len(missing), existing, tuple(conflicts))
