from typing import Optional
from sqlmodel import Field, SQLModel


class Rental(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    listing_id: int = Field(foreign_key="listing.id", index=True)
    customer_id: int = Field(foreign_key="user.id", index=True)
    payment_id: Optional[int] = Field(default=None, foreign_key="payment.id")
    status: str = Field(default="active", index=True)  # active, returned
