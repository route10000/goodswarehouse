"""Staff groups (SPEC §5): «Менеджер» gets everything, «Склад» pickup / call order / view."""

from django.contrib.auth.management import create_permissions
from django.db import migrations

MANAGER = "Менеджер"
WAREHOUSE = "Склад"
DOMAIN_APPS = ["logistics", "notify"]
WAREHOUSE_EXTRA = [
    ("logistics", "change_cargo"),
    ("logistics", "add_contactattempt"),
    ("logistics", "change_dailyorderline"),
]


def create_groups(apps, schema_editor):
    # Permissions are normally created by post_migrate, which has not run yet on a fresh DB.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    domain = Permission.objects.filter(content_type__app_label__in=DOMAIN_APPS)

    manager, _ = Group.objects.get_or_create(name=MANAGER)
    manager.permissions.set(domain)

    warehouse, _ = Group.objects.get_or_create(name=WAREHOUSE)
    extra = [domain.get(content_type__app_label=app, codename=code) for app, code in WAREHOUSE_EXTRA]
    warehouse.permissions.set([*domain.filter(codename__startswith="view_"), *extra])


def delete_groups(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=[MANAGER, WAREHOUSE]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("logistics", "0001_initial"),
        ("notify", "0001_initial"),
    ]

    operations = [migrations.RunPython(create_groups, delete_groups)]
