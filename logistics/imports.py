"""Truck manifest import from ``.xlsx`` (SPEC §10).

``parse_manifest`` reads a workbook into an ``ImportPreview`` without writing anything;
``apply_manifest`` loads a clean preview into a ``loading`` truck in one transaction.
The preview is plain data (``to_dict`` / ``from_dict``) so the panel can keep it in
the session between the preview and the confirmation instead of the file itself.
"""

import io
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zipfile import BadZipFile

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils.exceptions import InvalidFileException

from logistics import services
from logistics.models import Cargo, Client, Truck
from logistics.phones import InvalidPhone, normalize_phone

TEMPLATE_HEADERS = ["артикул", "телефон", "имя", "описание", "мест", "вес", "объём", "сумма"]
REQUIRED = ("артикул", "телефон")
# Normalized header → row attribute. «объём» and «объем» both normalize to «объем».
COLUMNS = {
    "артикул": "article",
    "телефон": "phone",
    "имя": "name",
    "описание": "description",
    "мест": "places",
    "вес": "weight",
    "объем": "volume",
    "сумма": "amount",
}
DECIMALS = {
    # attribute: (label, model field)
    "weight": ("Вес", "weight_kg"),
    "volume": ("Объём", "volume_m3"),
    "amount": ("Сумма", "amount_due"),
}
NBSP = " "
FILE_UNREADABLE = "Не удалось прочитать файл. Сохраните его как .xlsx"
FILE_EMPTY = "В файле нет строк с грузами"


@dataclass
class ManifestRow:
    line: int
    article: str = ""
    phone: str = ""
    name: str = ""
    description: str = ""
    places: int | None = 1
    weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None
    amount_due: Decimal | None = Decimal("0")
    new_client: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportPreview:
    rows: list[ManifestRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # file-level problems

    @property
    def error_count(self) -> int:
        return sum(1 for row in self.rows if row.errors)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors) or self.error_count > 0

    @property
    def new_client_count(self) -> int:
        return len({row.phone for row in self.rows if row.new_client})

    def to_dict(self) -> dict:
        def plain(value):
            return str(value) if isinstance(value, Decimal) else value

        return {
            "errors": list(self.errors),
            "rows": [{k: plain(v) for k, v in asdict(row).items()} for row in self.rows],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImportPreview":
        rows = []
        for raw in data.get("rows", []):
            values = dict(raw)
            for name in ("weight_kg", "volume_m3", "amount_due"):
                if values.get(name) is not None:
                    values[name] = Decimal(values[name])
            rows.append(ManifestRow(**values))
        return cls(rows=rows, errors=list(data.get("errors", [])))


# ---------- cell parsing ----------


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace(NBSP, " ").strip()


def _header(value) -> str:
    return _text(value).lower().replace("ё", "е")


def _places(value) -> int:
    message = "Мест должно быть целым числом от 1"
    if value is None or _text(value) == "":
        return 1
    if isinstance(value, bool):
        raise ValueError(message)
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(message)
        value = int(value)
    if isinstance(value, int):
        number = value
    else:
        try:
            number = int(_text(value).replace(" ", ""))
        except ValueError as exc:
            raise ValueError(message) from exc
    if number < 1:
        raise ValueError(message)
    return number


def _decimal(value, label: str, model_field: str) -> Decimal | None:
    if value is None or _text(value) == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}: не число")
    text = _text(value).replace(" ", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{label}: не число") from exc
    if not number.is_finite():
        raise ValueError(f"{label}: не число")
    if number < 0:
        raise ValueError(f"{label}: не может быть меньше 0")
    db_field = Cargo._meta.get_field(model_field)
    number = number.quantize(Decimal(1).scaleb(-db_field.decimal_places), ROUND_HALF_UP)
    if len(number.as_tuple().digits) > db_field.max_digits:
        raise ValueError(f"{label}: слишком большое число")
    return number


def _parse_row(line: int, cells: dict) -> ManifestRow:
    row = ManifestRow(line=line)
    row.article = _text(cells.get("article"))
    row.name = _text(cells.get("name"))
    row.description = _text(cells.get("description"))
    raw_phone = _text(cells.get("phone"))

    if not row.article:
        row.errors.append("Нет артикула")
    elif len(row.article) > Cargo._meta.get_field("article").max_length:
        row.errors.append("Артикул длиннее 64 символов")
    if not raw_phone:
        row.errors.append("Нет телефона")
    else:
        try:
            row.phone = normalize_phone(raw_phone)
        except InvalidPhone:
            row.phone = raw_phone
            row.errors.append(f"Неверный телефон: {raw_phone}")
    if len(row.name) > Client._meta.get_field("name").max_length:
        row.errors.append("Имя длиннее 200 символов")
    if len(row.description) > Cargo._meta.get_field("description").max_length:
        row.errors.append("Описание длиннее 255 символов")

    try:
        row.places = _places(cells.get("places"))
    except ValueError as exc:
        row.places = None
        row.errors.append(str(exc))
    for attr, (label, model_field) in DECIMALS.items():
        try:
            value = _decimal(cells.get(attr), label, model_field)
        except ValueError as exc:
            row.errors.append(str(exc))
            value = None
        else:
            if value is None and model_field == "amount_due":
                value = Decimal("0")  # an empty amount means nothing to pay
        setattr(row, model_field, value)
    return row


# ---------- public API ----------


def parse_manifest(file) -> ImportPreview:
    """Read an uploaded workbook. Never raises for bad content: errors go into the preview."""
    try:
        workbook = load_workbook(file, read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, KeyError, ValueError, OSError):
        return ImportPreview(errors=[FILE_UNREADABLE])
    try:
        rows = list(workbook.active.iter_rows(values_only=True))
    finally:
        workbook.close()

    header_row = next((r for r in rows if any(_text(c) for c in r)), None)
    if header_row is None:
        return ImportPreview(errors=[FILE_EMPTY])
    header_index = rows.index(header_row)
    positions: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        attr = COLUMNS.get(_header(cell))
        if attr and attr not in positions:
            positions[attr] = index
    missing = [name for name in REQUIRED if COLUMNS[name] not in positions]
    if missing:
        return ImportPreview(errors=[f"Нет колонки «{name}»" for name in missing])

    preview = ImportPreview()
    seen: dict[str, int] = {}
    for offset, values in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(_text(c) for c in values):
            continue
        cells = {
            attr: (values[index] if index < len(values) else None)
            for attr, index in positions.items()
        }
        row = _parse_row(offset, cells)
        if row.article:
            key = row.article.casefold()
            if key in seen:
                row.errors.append(f"Артикул повторяется (строка {seen[key]})")
            else:
                seen[key] = row.line
        preview.rows.append(row)

    if not preview.rows:
        return ImportPreview(errors=[FILE_EMPTY])

    valid_phones = {row.phone for row in preview.rows if row.phone.startswith("+")}
    known = set(Client.objects.filter(phone__in=valid_phones).values_list("phone", flat=True))
    for row in preview.rows:
        row.new_client = row.phone in valid_phones and row.phone not in known
    return preview


def apply_manifest(preview: ImportPreview, truck: Truck) -> int:
    """Create clients and cargo for every row, all or nothing. Returns the cargo count."""
    if preview.has_errors:
        raise ValueError("Манифест с ошибками нельзя импортировать")
    with transaction.atomic():
        status = Truck.objects.select_for_update().values_list("status", flat=True).get(pk=truck.pk)
        if status != Truck.Status.LOADING:
            raise services.InvalidTransition(f"Машина {truck.number} уже не грузится")
        for row in preview.rows:
            client, _ = services.get_or_create_client(row.phone, row.name)
            services.add_cargo(
                client,
                row.article,
                description=row.description,
                places=row.places,
                weight_kg=row.weight_kg,
                volume_m3=row.volume_m3,
                amount_due=row.amount_due if row.amount_due is not None else Decimal("0"),
                truck=truck,
            )
    return len(preview.rows)


def build_template() -> bytes:
    """Empty manifest with the expected header row."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Манифест"
    sheet.append(TEMPLATE_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for letter, width in zip("ABCDEFGH", (18, 18, 22, 32, 8, 10, 10, 12), strict=True):
        sheet.column_dimensions[letter].width = width
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
