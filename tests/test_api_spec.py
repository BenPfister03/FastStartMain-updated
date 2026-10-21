"""Checks that the API matches the Tutorial 5 API specification table."""
from sqlmodel import Session

from app.models import User
from app.utilities.security import create_access_token


def make_user(engine, username, role="regular_user"):
    with Session(engine) as session:
        user = User(username=username, email=f"{username}@example.com", password="pw", role=role)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def jwt_header(user_id, role):
    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"JWT {token}"}  # spec header format


def new_game(client, staff_headers):
    return client.post(
        "/api/games", headers=staff_headers,
        json={"title": "Zelda", "platform": "NSW", "genre": "Adventure", "release_year": 2017},
    ).json()["id"]


def test_signup_success_and_duplicate(api_env):
    client, _ = api_env
    ok = client.post("/api/signup", json={"username": "alice", "password": "secret"})
    assert ok.status_code == 201
    assert ok.json()["message"] == "account created"

    dup = client.post("/api/signup", json={"username": "alice", "password": "secret"})
    assert dup.status_code == 400
    assert dup.json()["error"] == "username already taken"


def test_auth_bad_credentials(api_env):
    client, _ = api_env
    client.post("/api/signup", json={"username": "alice", "password": "secret"})

    response = client.post("/api/auth", json={"username": "alice", "password": "nope"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid credentials"


def test_listing_requires_token(api_env):
    client, _ = api_env

    response = client.post("/api/listings", json={"game_id": 1, "condition": "good", "price": 10})

    assert response.status_code == 401
    assert response.json()["error"] == "not authenticated"


def test_listing_bad_game_and_bad_condition(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")
    game_id = new_game(client, jwt_header(staff_id, "admin"))
    customer = jwt_header(customer_id, "regular_user")

    bad_game = client.post("/api/listings", headers=customer, json={"game_id": 999, "condition": "good", "price": 10})
    assert bad_game.status_code == 404
    assert bad_game.json()["error"] == "game id 999 not found"

    bad_condition = client.post("/api/listings", headers=customer, json={"game_id": game_id, "condition": "mint", "price": 10})
    assert bad_condition.status_code == 400
    assert bad_condition.json()["error"] == "mint not a valid condition"


def test_get_listings_shape(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")
    game_id = new_game(client, jwt_header(staff_id, "admin"))
    created = client.post(
        "/api/listings", headers=jwt_header(customer_id, "regular_user"),
        json={"game_id": game_id, "condition": "good", "price": 55},
    )
    assert created.json()["message"] == "listing created"

    body = client.get("/api/listings?platform=NSW").json()

    assert len(body) == 1
    item = body[0]
    for key in ("listingId", "ownerId", "gameId", "condition", "status", "price", "game"):
        assert key in item
    assert item["game"]["gameId"] == game_id
    assert item["game"]["platform"] == "NSW"


def test_payment_message(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")

    response = client.post("/api/payment", headers=jwt_header(staff_id, "admin"), json={"amount": 20})

    assert response.status_code == 201
    assert response.json()["message"] == "payment created"
    assert "paymentId" in response.json()


def test_rental_messages_and_bad_listing_id(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")
    staff = jwt_header(staff_id, "admin")
    game_id = new_game(client, staff)
    listing = client.post(
        "/api/listings", headers=jwt_header(customer_id, "regular_user"),
        json={"game_id": game_id, "condition": "good", "price": 55},
    ).json()

    bad = client.post("/api/rentals", headers=staff, json={"listing_id": 999, "customer_id": customer_id})
    assert bad.status_code == 404
    assert bad.json()["error"] == "Bad listing id given"

    ok = client.post("/api/rentals", headers=staff, json={"listing_id": listing["listingId"], "customer_id": customer_id})
    assert ok.status_code == 201
    assert ok.json()["message"] == "rental created"

    again = client.post("/api/rentals", headers=staff, json={"listing_id": listing["listingId"], "customer_id": customer_id})
    assert again.status_code == 400  # already rented


def test_return_rental_with_payment_id_spelling_from_spec(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")
    staff = jwt_header(staff_id, "admin")
    game_id = new_game(client, staff)
    listing = client.post(
        "/api/listings", headers=jwt_header(customer_id, "regular_user"),
        json={"game_id": game_id, "condition": "good", "price": 55},
    ).json()
    rental = client.post("/api/rentals", headers=staff, json={"listing_id": listing["listingId"], "customer_id": customer_id}).json()
    payment = client.post("/api/payment", headers=staff, json={"amount": 55}).json()

    response = client.put(f"/api/rentals/{rental['rentalId']}", headers=staff, json={"paymentId": payment["paymentId"]})

    assert response.status_code == 201
    assert response.json()["message"] == "rental updated"
    assert response.json()["payment_id"] == payment["paymentId"]


def test_confirm_sell_with_spec_route(api_env):
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")
    other_id = make_user(engine, "other")
    game_id = new_game(client, jwt_header(staff_id, "admin"))
    owner = jwt_header(customer_id, "regular_user")
    listing = client.post("/api/listings", headers=owner, json={"game_id": game_id, "condition": "good", "price": 55}).json()
    url = f"/api/listing/{listing['listingId']}"

    forbidden = client.put(url, headers=jwt_header(other_id, "regular_user"), json={"status": "sold"})
    assert forbidden.status_code == 403

    bad_status = client.put(url, headers=owner, json={"status": "listed"})
    assert bad_status.status_code == 400

    ok = client.put(url, headers=owner, json={"status": "sold"})
    assert ok.status_code == 200
    assert ok.json()["message"] == "listing removed from customer"
    assert client.get("/api/listings?platform=NSW").json() == []
