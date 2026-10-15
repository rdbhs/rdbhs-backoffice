from django.forms import ModelForm

from .models import ProductivityGoal


class ProductivityGoalCreateForm(ModelForm):
    class Meta:
        model = ProductivityGoal
        fields = (
            "employee",
            "employment_type",
            "minimum_hours",
            "effective_from",
            "notes",
        )

    def _post_clean(self):
        self.instance._validate_timeline_creation = True
        try:
            super()._post_clean()
        finally:
            self.instance._validate_timeline_creation = False

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.create_in_timeline()
            self.save_m2m()
        return instance
