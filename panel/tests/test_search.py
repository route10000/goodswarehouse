import pytest

from logistics import services

pytestmark = pytest.mark.django_db


def test_truck_number_opens_truck_card(mgr, truck):
    response = mgr.get("/panel/search/", {"q": f" {truck.number.lower()} "})

    assert response.status_code == 302
    assert response["Location"] == f"/panel/trucks/{truck.pk}/"


def test_client_code_opens_client_card(mgr, ivan):
    response = mgr.get("/panel/search/", {"q": ivan.code.lower()})

    assert response["Location"] == f"/panel/clients/{ivan.pk}/"


def test_phone_in_any_format_opens_client_card(mgr, ivan):
    response = mgr.get("/panel/search/", {"q": "8 (914) 123-45-67"})

    assert response["Location"] == f"/panel/clients/{ivan.pk}/"


def test_exact_article_opens_filtered_cargo_list(mgr, loaded_truck):
    response = mgr.get("/panel/search/", {"q": "a1"})

    assert response.status_code == 302
    assert response["Location"] == "/panel/cargo/?q=a1"


def test_partial_query_shows_grouped_results(mgr, loaded_truck, ivan):
    services.add_cargo(ivan, "IV-77", description="Иваново полотенце")

    response = mgr.get("/panel/search/", {"q": "Иван"})

    assert response.status_code == 200
    page = response.content.decode()
    assert "Клиенты" in page and "Грузы" in page and "Машины" in page
    assert ivan.code in page
    assert "IV-77" in page


def test_results_are_capped_at_ten_per_group(mgr, truck):
    for n in range(12):
        client, _ = services.get_or_create_client(f"+7914000{n:04d}", f"Сидоров {n}")
        services.add_cargo(client, f"SID-{n}")

    response = mgr.get("/panel/search/", {"q": "Сидоров"})

    assert len(response.context["clients"]) == 10


def test_unknown_phone_falls_back_to_results(mgr, ivan):
    response = mgr.get("/panel/search/", {"q": "+79990000000"})

    assert response.status_code == 200
    assert "Ничего не нашли" in response.content.decode()


def test_unknown_truck_number_falls_back_to_results(mgr):
    response = mgr.get("/panel/search/", {"q": "R-2020-999"})

    assert response.status_code == 200


def test_empty_query_renders_page(mgr):
    response = mgr.get("/panel/search/")

    assert response.status_code == 200
