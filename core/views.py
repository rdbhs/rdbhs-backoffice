from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.http import HttpResponseRedirect
from django.views.generic import FormView
from viewflow.views import CreateModelView, DetailModelView

from .forms import ProductivityGoalCreateForm
from .models import PayrollPeriod, ProductivityGoal
from .payroll import generate_payroll_periods


class GeneratePayrollPeriodsView(FormView):
    model = PayrollPeriod
    viewset = None
    form_class = forms.Form
    template_name = "core/payrollperiod_generate.html"
    http_method_names = ["get", "post", "head", "options"]

    def dispatch(self, request, *args, **kwargs):
        if not self.viewset.has_add_permission(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["business_timezone"] = settings.PAYROLL_TIME_ZONE
        return context

    def form_valid(self, form):
        try:
            result = generate_payroll_periods()
        except OperationalError as error:
            if getattr(error.__cause__, "sqlstate", None) != "55P03":
                raise
            messages.error(self.request, "The payroll calendar is busy. Please try again.")
        else:
            level = messages.WARNING if result.conflicts else messages.SUCCESS
            messages.add_message(
                self.request,
                level,
                f"Periods: {result.created} created, {result.existing} existing, "
                f"{len(result.conflicts)} conflicts.",
            )
            for conflict in result.conflicts:
                records = ", ".join(str(pk) for pk in conflict.record_ids)
                messages.warning(
                    self.request,
                    f"Period starting {conflict.start_date}: conflict with records "
                    f"{records}. Existing dates were preserved.",
                )
        return HttpResponseRedirect(self.viewset.reverse("index"))


class EmployeeDetailView(DetailModelView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        goals_viewset = next(
            viewset
            for viewset in self.viewset.parent.viewsets
            if getattr(viewset, "model", None) is ProductivityGoal
        )
        query = urlencode({"employee": self.object.pk})
        context["productivity_goal_rows"] = [
            {
                "goal": goal,
                "edit_url": (
                    f"{goals_viewset.reverse('change', args=[goal.pk])}?{query}"
                    if goals_viewset.has_change_permission(self.request.user, goal)
                    else None
                ),
            }
            for goal in self.object.productivity_goals.all()
        ]
        if goals_viewset.has_add_permission(self.request.user):
            context["add_productivity_goal_url"] = (
                f"{goals_viewset.reverse('add')}?{query}"
            )
        return context


class ProductivityGoalCreateView(CreateModelView):
    form_class = ProductivityGoalCreateForm

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)

    def get_initial(self):
        initial = super().get_initial()
        employee = self.viewset.get_employee_context(self.request)
        if employee is not None:
            initial["employee"] = employee.pk
        return initial
