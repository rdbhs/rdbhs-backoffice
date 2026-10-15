from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, router, transaction


class EmploymentType(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    default_minimum_hours = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "employment type"
        verbose_name_plural = "employment types"

    def __str__(self):
        return self.name


class Employee(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "employee"
        verbose_name_plural = "employees"

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"


class ProductivityGoalQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related("employee", "employment_type")


class ProductivityGoalManager(models.Manager.from_queryset(ProductivityGoalQuerySet)):
    def get_queryset(self):
        return super().get_queryset().with_related()


class ProductivityGoal(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="productivity_goals"
    )
    employment_type = models.ForeignKey(
        EmploymentType, on_delete=models.PROTECT, related_name="productivity_goals"
    )
    minimum_hours = models.PositiveIntegerField()
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductivityGoalManager()

    def _timeline_neighbors(self):
        goals = (
            type(self)
            .objects.using(self._state.db)
            .filter(employee_id=self.employee_id)
        )
        previous = (
            goals.filter(effective_from__lt=self.effective_from)
            .filter(
                models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=self.effective_from)
            )
            .order_by("-effective_from")
            .first()
        )
        following = (
            goals.filter(effective_from__gt=self.effective_from)
            .order_by("effective_from")
            .first()
        )
        return previous, following

    def clean(self):
        super().clean()
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValidationError(
                {"effective_to": "Effective to must be on or after effective from."}
            )
        if not self.employee_id or not self.effective_from:
            return
        goals = (
            type(self)
            .objects.using(self._state.db)
            .filter(employee_id=self.employee_id)
        )
        if self.pk is not None:
            goals = goals.exclude(pk=self.pk)
        if goals.filter(effective_from=self.effective_from).exists():
            raise ValidationError(
                {"effective_from": "A productivity goal already starts on this date."}
            )
        end = self.effective_to
        if getattr(self, "_validate_timeline_creation", False):
            # The create form validates the planned timeline without writing it.
            previous, following = self._timeline_neighbors()
            if previous:
                goals = goals.exclude(pk=previous.pk)
            if following:
                end = following.effective_from - timedelta(days=1)
            else:
                end = None
        overlaps = goals.filter(
            models.Q(effective_to__isnull=True)
            | models.Q(effective_to__gte=self.effective_from)
        )
        if end is not None:
            overlaps = overlaps.filter(effective_from__lte=end)
        if overlaps.exists():
            raise ValidationError(
                "This productivity goal overlaps another goal for this employee."
            )

    def create_in_timeline(self):
        """Insert this unsaved goal and adjust its neighbors atomically."""
        if not self._state.adding:
            raise ValueError("Timeline creation requires a new productivity goal.")
        database = router.db_for_write(type(self), instance=self)
        self._state.db = database
        with transaction.atomic(using=database):
            Employee.objects.using(database).select_for_update().get(
                pk=self.employee_id
            )
            # Ignore caller-supplied end dates before validation, too.
            self.effective_to = None
            self._validate_timeline_creation = True
            try:
                self.full_clean()
            finally:
                self._validate_timeline_creation = False
            previous, following = self._timeline_neighbors()
            if previous:
                previous.effective_to = self.effective_from - timedelta(days=1)
                previous.save(update_fields=["effective_to", "updated_at"])
            if following:
                self.effective_to = following.effective_from - timedelta(days=1)
            else:
                self.effective_to = None
            self.full_clean()
            self.save(force_insert=True, using=database)
        return self

    class Meta:
        ordering = ["employee__last_name", "employee__first_name", "-effective_from"]
        verbose_name = "productivity goal"
        verbose_name_plural = "productivity goals"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="productivity_goal_valid_effective_dates",
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee} - {self.minimum_hours} hours from {self.effective_from}"
        )
