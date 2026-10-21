"""Tutorial 5 integration tests (question 1).

Each test drives the real route -> service -> database path and then checks the
database rows directly, which is what makes them integration tests.
"""
from sqlmodel import Session, select

from app.models import Game, Listing, Payment, Rental, User
from app.utilities.security import create_access_token


def make_user(engine, username, role="regular_user"):
    with Session(engine) as session:
        user = User(username=username, email=f"{username}@example.com", password="pw", role=role)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def headers(user_id, role):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user_id), 'role': role})}"}


def make_game(engine, platform="NSW"):
    with Session(engine) as session:
        game = Game(title="Mario Kart", platform=platform, genre="Racing", release_year=2017)
        session.add(game)
        session.commit()
        session.refresh(game)
        return game.id


def test_create_user_via_signup(api_env):
    client, engine = api_env

    response = client.post("/api/signup", json={"username": "alice", "password": "secret"})

    assert response.status_code == 201
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "alice")).one()
    assert user.role == "regular_user"
    assert user.password != "secret"  # stored hashed


def test_authenticate_returns_token(api_env):
    client, _ = api_env
    client.post("/api/signup", json={"username": "alice", "password": "secret"})

    response = client.post("/api/auth", json={"username": "alice", "password": "secret"})

    assert response.status_code == 200
    assert response.json()["token"]


def test_get_all_users_json(api_env):
    client, _ = api_env
    client.post("/api/signup", json={"username": "alice", "password": "secret"})
    client.post("/api/signup", json={"username": "bob", "password": "secret"})

    response = client.get("/api/users")

    assert response.status_code == 200
    assert {u["username"] for u in response.json()} == {"alice", "bob"}
    assert all(set(u) == {"id", "username", "email"} for u in response.json())


def test_staff_create_listing(api_env):
    """Staff lists a game for a customer: row carries staff-supplied owner and game ids."""
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    customer_id = make_user(engine, "bob")
    game_id = make_game(engine)

    response = client.post(
        "/api/listings",
        headers=headers(staff_id, "admin"),
        json={"game_id": game_id, "owner": customer_id, "condition": "good", "price": 120},
    )

    assert response.status_code == 201
    with Session(engine) as session:
        listing = session.get(Listing, response.json()["listingId"])
    assert listing.owner_id == customer_id
    assert listing.game_id == game_id
    assert listing.status == "available"


def test_staff_confirm_rental(api_env):
    """Rental row is created with the right ids and the listing becomes unavailable."""
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    owner_id = make_user(engine, "owner")
    renter_id = make_user(engine, "renter")
    game_id = make_game(engine)
    listing = client.post(
        "/api/listings",
        headers=headers(owner_id, "regular_user"),
        json={"game_id": game_id, "condition": "fair", "price": 80},
    ).json()

    response = client.post(
        "/api/rentals",
        headers=headers(staff_id, "admin"),
        json={"listing_id": listing["listingId"], "customer_id": renter_id},
    )

    assert response.status_code == 201
    with Session(engine) as session:
        rental = session.get(Rental, response.json()["rentalId"])
        stored_listing = session.get(Listing, listing["listingId"])
    assert rental.listing_id == listing["listingId"]
    assert rental.customer_id == renter_id
    assert rental.status == "active"
    assert stored_listing.status == "rented"


def test_staff_return_rental(api_env):
    """Return marks the rental returned, frees the listing and records the payment."""
    client, engine = api_env
    staff_id = make_user(engine, "staff", "admin")
    owner_id = make_user(engine, "owner")
    renter_id = make_user(engine, "renter")
    game_id = make_game(engine)
    staff = headers(staff_id, "admin")
    listing = client.post(
        "/api/listings",
        headers=headers(owner_id, "regular_user"),
        json={"game_id": game_id, "condition": "good", "price": 80},
    ).json()
    rental = client.post(
        "/api/rentals", headers=staff,
        json={"listing_id": listing["listingId"], "customer_id": renter_id},
    ).json()

    response = client.put(
        f"/api/rentals/{rental['rentalId']}", headers=staff, json={"payment": {"amount": 95.5}}
    )

    assert response.status_code == 201
    assert response.json()["message"] == "rental updated"
    with Session(engine) as session:
        stored_rental = session.get(Rental, rental["rentalId"])
        stored_listing = session.get(Listing, listing["listingId"])
        payment = session.get(Payment, stored_rental.payment_id)
    assert stored_rental.status == "returned"
    assert stored_listing.status == "available"
    assert payment.amount == 95.5
    assert payment.customer_id == renter_id
