from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from viewflow.views import CreateModelView, DetailModelView

from .forms import ProductivityGoalCreateForm
from .models import ProductivityGoal


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
