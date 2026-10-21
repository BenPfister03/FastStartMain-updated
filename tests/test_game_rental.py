
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.main import app
from app.database import get_session
from app.models import User
from app.utilities.security import create_access_token


@pytest.fixture
def api_env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        yield client, engine

    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


def make_user(engine, username, role="regular_user"):
    with Session(engine) as session:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password="test-password",
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def token_for(user_id, role):
    return create_access_token({"sub": str(user_id), "role": role})


def auth_header(user_id, role):
    return {"Authorization": f"Bearer {token_for(user_id, role)}"}


def add_game(client, headers, title="Test Game", platform="NSW"):
    return client.post(
        "/api/games",
        headers=headers,
        json={
            "title": title,
            "platform": platform,
            "genre": "Racing",
            "release_year": 2020,
        },
    )


def add_listing(client, headers, game_id, price=150):
    return client.post(
        "/api/listings",
        headers=headers,
        json={
            "game_id": game_id,
            "condition": "Good",
            "price": price,
        },
    )


def test_staff_can_create_game(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    headers = auth_header(staff_id, "admin")

    response = add_game(client, headers)

    assert response.status_code == 201
    assert response.json()["title"] == "Test Game"
    assert response.json()["platform"] == "NSW"


def test_game_creation_rejects_invalid_platform(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")

    response = add_game(
        client,
        auth_header(staff_id, "admin"),
        platform="INVALID",
    )

    assert response.status_code == 422


def test_customer_can_create_listing_and_filter_by_platform(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")

    game_response = add_game(
        client, auth_header(staff_id, "admin")
    )
    game_id = game_response.json()["id"]

    listing_response = add_listing(
        client, auth_header(customer_id, "regular_user"), game_id
    )

    assert listing_response.status_code == 201
    assert listing_response.json()["status"] == "available"

    response = client.get("/api/listings?platform=NSW")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["game"]["title"] == "Test Game"


def test_customer_cannot_list_nonexistent_game(api_env):
    client, engine = api_env
    customer_id = make_user(engine, "bob")

    response = add_listing(
        client, auth_header(customer_id, "regular_user"), 999
    )

    assert response.status_code == 404


def test_staff_can_create_payment(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")

    response = client.post(
        "/api/payment",
        headers=auth_header(staff_id, "admin"),
        json={"amount": 150},
    )

    assert response.status_code == 201
    assert response.json()["amount"] == 150
    assert "paymentId" in response.json()


def test_rental_return_restores_listing_availability(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")

    staff_headers = auth_header(staff_id, "admin")
    customer_headers = auth_header(customer_id, "regular_user")

    game = add_game(client, staff_headers).json()
    listing = add_listing(
        client, customer_headers, game["id"]
    ).json()

    rental_response = client.post(
        "/api/rentals",
        headers=staff_headers,
        json={
            "listing_id": listing["id"],
            "customer_id": customer_id,
        },
    )

    assert rental_response.status_code == 201
    rental_id = rental_response.json()["id"]

    available = client.get("/api/listings?platform=NSW")
    assert available.json() == []

    payment_response = client.post(
        "/api/payment",
        headers=staff_headers,
        json={"amount": 150},
    )
    payment_id = payment_response.json()["paymentId"]

    return_response = client.put(
        f"/api/rentals/{rental_id}",
        headers=staff_headers,
        json={"payment_id": payment_id},
    )

    assert return_response.status_code == 201
    assert return_response.json()["status"] == "returned"
    assert return_response.json()["payment_id"] == payment_id

    available = client.get("/api/listings?platform=NSW")
    assert len(available.json()) == 1
    assert available.json()[0]["status"] == "available"


def test_owner_can_sell_listing(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")

    game = add_game(
        client, auth_header(staff_id, "admin")
    ).json()
    listing = add_listing(
        client,
        auth_header(customer_id, "regular_user"),
        game["id"],
    ).json()

    response = client.post(
        f"/api/listings/{listing['id']}/sell",
        headers=auth_header(customer_id, "regular_user"),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "sold"

    available = client.get("/api/listings?platform=NSW")
    assert available.json() == []


def test_other_customer_cannot_sell_listing(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    owner_id = make_user(engine, "owner")
    other_id = make_user(engine, "other")

    game = add_game(
        client, auth_header(staff_id, "admin")
    ).json()
    listing = add_listing(
        client, auth_header(owner_id, "regular_user"), game["id"]
    ).json()

    response = client.post(
        f"/api/listings/{listing['id']}/sell",
        headers=auth_header(other_id, "regular_user"),
    )

    assert response.status_code == 403

def test_staff_cannot_rent_nonexistent_listing(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")

    response = client.post(
        "/api/rentals",
        headers=auth_header(staff_id, "admin"),
        json={
            "listing_id": 999,
            "customer_id": staff_id,
        },
    )

    assert response.status_code == 404


def test_staff_cannot_return_nonexistent_rental(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")

    response = client.put(
        "/api/rentals/999",
        headers=auth_header(staff_id, "admin"),
        json={"payment_id": 999},
    )

    assert response.status_code == 404


def test_customer_cannot_sell_nonexistent_listing(api_env):
    client, engine = api_env
    customer_id = make_user(engine, "bob")

    response = client.post(
        "/api/listings/999/sell",
        headers=auth_header(customer_id, "regular_user"),
    )

    assert response.status_code == 404


def test_customer_cannot_sell_listing_twice(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")

    game = add_game(
        client, auth_header(staff_id, "admin")
    ).json()

    listing = add_listing(
        client,
        auth_header(customer_id, "regular_user"),
        game["id"],
    ).json()

    headers = auth_header(customer_id, "regular_user")
    url = f"/api/listings/{listing['id']}/sell"

    first_response = client.post(url, headers=headers)
    assert first_response.status_code == 201

    second_response = client.post(url, headers=headers)
    assert second_response.status_code == 400