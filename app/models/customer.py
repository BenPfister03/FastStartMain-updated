from datetime import date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy.orm import object_session
from sqlmodel import Field, Relationship, SQLModel

from app.models.listing import Availability, Listing
from app.models.payment import Payment
from app.models.rental import Rental
from app.models.user import User

if TYPE_CHECKING:
    from app.models.game import Game


class Customer(SQLModel, table=True):
    """A customer is a User (id is shared with user.id) who can list and rent games."""

    id: Optional[int] = Field(default=None, foreign_key="user.id", primary_key=True)

    user: Optional[User] = Relationship()
    payments: List[Payment] = Relationship(back_populates="customer")
    listings: List[Listing] = Relationship(back_populates="owner")
    rentals: List[Rental] = Relationship(back_populates="renter")

    @property
    def username(self) -> str:
        return self.user.username

    def list_game(self, game: "Game", condition: str, price: float) -> Listing:
        """List a copy of a game this customer owns for rental."""
        db = object_session(self)
        listing = Listing(game_id=game.id, owner_id=self.id, condition=condition, price=price)
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing

    def rent_game(self, listing: Listing) -> Rental:
        """Rent an available listing that this customer does not own."""
        db = object_session(self)
        if listing.owner_id == self.id:
            raise ValueError("You cannot rent your own listing.")
        if listing.availability != Availability.AVAILABLE:
            raise ValueError("That listing is not available.")
        rental = Rental(listing_id=listing.id, renter_id=self.id, rental_date=date.today())
        listing.availability = Availability.RENTED
        db.add(rental)
        db.add(listing)
        db.commit()
        db.refresh(rental)
        return rental

    def return_game(self, rental: Rental, amt: float) -> Payment:
        """Return a rental with payment of the listing price plus any late fee."""
        db = object_session(self)
        if rental.renter_id != self.id:
            raise ValueError("That is not your rental.")
        if rental.return_date is not None:
            raise ValueError("That rental has already been returned.")
        due = rental.amount_due(date.today())
        if amt < due:
            raise ValueError(f"Payment of {amt:.2f} is less than the {due:.2f} due.")
        payment = Payment(rental_id=rental.id, customer_id=self.id,
                          payment_date=date.today(), amount=amt)
        rental.return_date = date.today()
        rental.listing.availability = Availability.AVAILABLE
        db.add(payment)
        db.add(rental)
        db.add(rental.listing)
        db.commit()
        db.refresh(payment)
        return payment

    def __str__(self) -> str:
        return f"(Customer id={self.id}, username={self.username})"
