"""Panel forms. They validate input only; every write goes through ``logistics.services``."""

from decimal import Decimal

from django import forms

from logistics.models import Client, Truck
from logistics.phones import InvalidPhone, normalize_phone

PHONE_ERROR = "Неверный номер телефона. Проверьте цифры"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%dT%H:%M")


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


class TruckForm(forms.ModelForm):
    """Plain truck fields. Status and its timestamps change only through services."""

    class Meta:
        model = Truck
        fields = [
            "route",
            "plate",
            "driver_phone",
            "loading_starts",
            "departure_date",
            "eta",
            "capacity_places",
            "is_public",
            "comment",
        ]
        labels = {"capacity_places": "Вместимость, мест"}
        widgets = {
            "capacity_places": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "loading_starts": DateTimeLocalInput(),
            "departure_date": DateInput(),
            "eta": DateTimeLocalInput(),
            "driver_phone": forms.TextInput(attrs={"inputmode": "tel"}),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional in the form: an empty value keeps the current (or default) capacity.
        self.fields["capacity_places"].required = False

    def clean_capacity_places(self):
        value = self.cleaned_data.get("capacity_places")
        return self.instance.capacity_places if value is None else value


class RuDecimalField(forms.DecimalField):
    """Accepts «12,5» and «1 500» (spaces / NBSP as thousands) besides «12.5»."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(" ", "").replace(" ", "").replace(",", ".")
        return super().to_python(value)


def _decimal_field(label, **kwargs):
    return RuDecimalField(
        label=label,
        required=False,
        min_value=Decimal("0"),
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
        **kwargs,
    )


class CargoForm(forms.Form):
    """Add one cargo; the client is found by phone or created with ``name``."""

    phone = forms.CharField(
        label="Телефон клиента",
        max_length=32,
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
    )
    name = forms.CharField(label="Имя нового клиента", max_length=200, required=False)
    article = forms.CharField(label="Артикул", max_length=64)
    description = forms.CharField(label="Описание", max_length=255, required=False)
    places = forms.IntegerField(
        label="Мест", min_value=1, initial=1, widget=forms.NumberInput(attrs={"min": 1})
    )
    weight_kg = _decimal_field("Вес, кг", max_digits=10, decimal_places=2)
    volume_m3 = _decimal_field("Объём, м³", max_digits=10, decimal_places=3)
    amount_due = _decimal_field("К оплате, ₽", max_digits=12, decimal_places=2)
    truck = forms.ModelChoiceField(
        label="Машина",
        queryset=Truck.objects.none(),
        required=False,
        empty_label="Без машины (принят на складе)",
    )

    def __init__(self, *args, with_truck=False, **kwargs):
        super().__init__(*args, **kwargs)
        if with_truck:
            self.fields["truck"].queryset = Truck.objects.filter(
                status=Truck.Status.LOADING
            ).order_by("departure_date", "number")
        else:
            del self.fields["truck"]

    def clean_phone(self):
        try:
            return normalize_phone(self.cleaned_data["phone"])
        except InvalidPhone as exc:
            raise forms.ValidationError(PHONE_ERROR) from exc

    def clean_amount_due(self):
        value = self.cleaned_data.get("amount_due")
        return value if value is not None else Decimal("0")


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "phone", "notes"]
        widgets = {
            "phone": forms.TextInput(attrs={"inputmode": "tel"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        error_messages = {"phone": {"unique": "Этот телефон уже у другого клиента"}}

    def clean_phone(self):
        try:
            return normalize_phone(self.cleaned_data["phone"])
        except InvalidPhone as exc:
            raise forms.ValidationError(PHONE_ERROR) from exc


class ManifestUploadForm(forms.Form):
    file = forms.FileField(
        label="Файл манифеста (.xlsx)",
        widget=forms.ClearableFileInput(attrs={"accept": f".xlsx,{XLSX_MIME}"}),
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Нужен файл .xlsx. Сохраните таблицу в формате Excel")
        if upload.size > MAX_MANIFEST_BYTES:
            raise forms.ValidationError("Файл должен быть не больше 2 МБ")
        return upload
