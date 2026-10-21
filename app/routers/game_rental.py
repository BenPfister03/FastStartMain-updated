"""Game-rental API routes for COMP 3613 Tutorial 5 (follows the Tutorial 5 API spec)."""
import jwt
from jwt.exceptions import InvalidTokenError
from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from app.config import get_settings
from app.dependencies.session import SessionDep
from app.models import Game, Listing, Payment, Rental, User
from app.repositories.user import UserRepository
from app.services.auth_service import AuthService
from . import api_router

PLATFORMS = {"NSW", "PS5", "XBOX", "PC"}
CONDITIONS = {"good", "fair", "bad"}
STAFF_ROLES = {"admin", "staff"}


class SignupBody(BaseModel):
    username: str
    password: str
    email: str | None = None  # optional: the spec only sends username + password


class AuthBody(BaseModel):
    username: str
    password: str


class ListingBody(BaseModel):
    game_id: int
    condition: str
    price: float = PydanticField(gt=0)
    owner: int | None = None  # staff may list a game on behalf of a customer


class GameBody(BaseModel):
    title: str
    platform: str
    genre: str = ""
    release_year: int | None = None


class PaymentBody(BaseModel):
    amount: float = PydanticField(gt=0)


class RentalBody(BaseModel):
    listing_id: int
    customer_id: int


class ReturnBody(BaseModel):
    payment_id: int | None = None
    paymentId: int | None = None  # spec spelling
    payment: float | dict[str, float] | None = None  # float or {"amount": float}


class StatusBody(BaseModel):
    status: str


def _token_from(request: Request) -> str | None:
    token = request.cookies.get("access_token")
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() in {"bearer", "jwt"} and value.strip():  # spec uses "JWT <token>"
        token = value.strip()
    return token


def current_user(request: Request, db: Session) -> User:
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        settings = get_settings()
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        user_id = int(payload.get("sub", ""))
    except (InvalidTokenError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="not authenticated")
    user = UserRepository(db).get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def require_staff(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Staff access required")
    return user


def _listing_json(listing: Listing, game: Game | None) -> dict:
    game_json = None
    if game:
        game_json = {**game.model_dump(), "gameId": game.id}
    return {
        "id": listing.id, "listingId": listing.id,
        "game_id": listing.game_id, "gameId": listing.game_id,
        "owner_id": listing.owner_id, "ownerId": listing.owner_id,
        "condition": listing.condition, "price": listing.price,
        "status": listing.status, "game": game_json,
    }


@api_router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignupBody, db: SessionDep):
    email = body.email or f"{body.username}@example.com"
    if db.exec(select(User).where(User.username == body.username)).first():
        raise HTTPException(status_code=400, detail="username already taken")
    if db.exec(select(User).where(User.email == email)).first():
        raise HTTPException(status_code=400, detail="email already taken")
    user = AuthService(UserRepository(db)).register_user(body.username, email, body.password)
    return {"message": "account created", "id": user.id, "username": user.username, "email": user.email}


@api_router.post("/auth")
def authenticate(body: AuthBody, db: SessionDep):
    token = AuthService(UserRepository(db)).authenticate_user(body.username, body.password)
    if not token:
        raise HTTPException(status_code=400, detail="invalid credentials")
    return {"token": token, "access_token": token, "token_type": "bearer"}


@api_router.post("/games", status_code=status.HTTP_201_CREATED)
def create_game(body: GameBody, request: Request, db: SessionDep):
    require_staff(request, db)
    platform = body.platform.upper()
    if platform not in PLATFORMS:
        raise HTTPException(status_code=422, detail="platform must be NSW, PS5, XBOX, or PC")
    game = Game(title=body.title, platform=platform, genre=body.genre, release_year=body.release_year)
    db.add(game); db.commit(); db.refresh(game)
    return game


@api_router.post("/listings", status_code=status.HTTP_201_CREATED)
def create_listing(body: ListingBody, request: Request, db: SessionDep):
    user = current_user(request, db)
    if user.role not in {"regular_user", "customer"} | STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Customer access required")
    if not db.get(Game, body.game_id):
        raise HTTPException(status_code=404, detail=f"game id {body.game_id} not found")
    condition = body.condition.lower()
    if condition not in CONDITIONS:
        raise HTTPException(status_code=400, detail=f"{body.condition} not a valid condition")
    owner_id = user.id
    if body.owner is not None and user.role in STAFF_ROLES:
        if db.get(User, body.owner) is None:
            raise HTTPException(status_code=404, detail=f"owner id {body.owner} not found")
        owner_id = body.owner
    listing = Listing(game_id=body.game_id, owner_id=owner_id, condition=condition, price=body.price)
    db.add(listing); db.commit(); db.refresh(listing)
    return {"message": "listing created", **_listing_json(listing, None)}


@api_router.get("/listings")
def get_listings(db: SessionDep, platform: str | None = None):
    query = select(Listing).where(Listing.status == "available")
    if platform:
        normalized = platform.upper()
        if normalized not in PLATFORMS:
            raise HTTPException(status_code=422, detail="platform must be NSW, PS5, XBOX, or PC")
        query = query.join(Game, Listing.game_id == Game.id).where(Game.platform == normalized)
    return [_listing_json(listing, db.get(Game, listing.game_id)) for listing in db.exec(query).all()]


@api_router.post("/payment", status_code=status.HTTP_201_CREATED)
def create_payment(body: PaymentBody, request: Request, db: SessionDep):
    staff = require_staff(request, db)
    payment = Payment(amount=body.amount, customer_id=staff.id)
    db.add(payment); db.commit(); db.refresh(payment)
    return {"message": "payment created", "paymentId": payment.id, "amount": payment.amount}


@api_router.post("/rentals", status_code=status.HTTP_201_CREATED)
def create_rental(body: RentalBody, request: Request, db: SessionDep):
    require_staff(request, db)
    listing = db.get(Listing, body.listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Bad listing id given")
    if listing.status != "available":
        raise HTTPException(status_code=400, detail="listing is not available")
    customer = db.get(User, body.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    rental = Rental(listing_id=listing.id, customer_id=customer.id)
    listing.status = "rented"
    db.add(rental); db.add(listing); db.commit(); db.refresh(rental)
    return {"message": "rental created", "rentalId": rental.id, **rental.model_dump()}


@api_router.put("/rentals/{rental_id}", status_code=status.HTTP_201_CREATED)
def return_rental(rental_id: int, body: ReturnBody, request: Request, db: SessionDep):
    require_staff(request, db)
    rental = db.get(Rental, rental_id)
    if rental is None:
        raise HTTPException(status_code=404, detail="Rental not found")
    if rental.status == "returned":
        raise HTTPException(status_code=400, detail="Rental already returned")
    payment_id = body.payment_id if body.payment_id is not None else body.paymentId
    amount = body.payment.get("amount") if isinstance(body.payment, dict) else body.payment
    if amount is not None and amount <= 0:
        raise HTTPException(status_code=422, detail="payment amount must be greater than 0")
    if payment_id is None and amount is not None:
        payment = Payment(amount=amount, customer_id=rental.customer_id, purpose="rental_return")
        db.add(payment); db.commit(); db.refresh(payment)
        payment_id = payment.id
    if payment_id is not None and db.get(Payment, payment_id) is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    listing = db.get(Listing, rental.listing_id)
    if listing is not None:
        listing.status = "available"
        db.add(listing)
    rental.status = "returned"
    rental.payment_id = payment_id
    db.add(rental); db.commit(); db.refresh(rental)
    return {"message": "rental updated", "rentalId": rental.id, **rental.model_dump()}


def _sell(listing_id: int, request: Request, db: Session) -> dict:
    user = current_user(request, db)
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.owner_id != user.id:
        raise HTTPException(status_code=403, detail="You can only sell your own listing")
    if listing.status != "available":
        raise HTTPException(status_code=400, detail="Only available listings can be sold")
    listing.status = "sold"
    db.add(listing); db.commit(); db.refresh(listing)
    return {"message": "listing removed from customer", "listing_id": listing.id, "status": listing.status}


@api_router.post("/listings/{listing_id}/sell", status_code=status.HTTP_201_CREATED)
def sell_listing(listing_id: int, request: Request, db: SessionDep):
    return _sell(listing_id, request, db)


@api_router.put("/listing/{listing_id}")
@api_router.put("/listings/{listing_id}")
def confirm_sell_listing(listing_id: int, body: StatusBody, request: Request, db: SessionDep):
    """Spec route: UPDATE /listing/<listingId> with {"status": "sold"}."""
    if body.status != "sold":
        raise HTTPException(status_code=400, detail="status must be 'sold'")
    return _sell(listing_id, request, db)
