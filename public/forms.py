from django import forms


class TrackForm(forms.Form):
    query = forms.CharField(
        label="Код клиента или артикул",
        max_length=64,
        widget=forms.TextInput(
            attrs={"placeholder": "GW-0142 или артикул", "autocomplete": "off", "required": True}
        ),
    )
    last4 = forms.RegexField(
        label="Последние 4 цифры телефона",
        regex=r"^\d{4}$",
        widget=forms.TextInput(
            attrs={
                "placeholder": "4567",
                "inputmode": "numeric",
                "maxlength": "4",
                "pattern": r"\d{4}",
                "autocomplete": "off",
                "required": True,
            }
        ),
    )

    def clean_query(self) -> str:
        return self.cleaned_data["query"].strip()
