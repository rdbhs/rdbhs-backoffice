from allauth.account.forms import LoginForm as AllauthLoginForm
from django import forms


class LoginForm(AllauthLoginForm):
    """Apply Tabler styling while leaving authentication to allauth."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css_class = (
                "form-check-input"
                if isinstance(field.widget, forms.CheckboxInput)
                else "form-control"
            )
            field.widget.attrs.setdefault("class", css_class)
