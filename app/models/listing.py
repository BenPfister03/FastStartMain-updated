from typing import Optional
from sqlmodel import Field, SQLModel


class Listing(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(foreign_key="game.id", index=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    condition: str
    price: float
    status: str = Field(default="available", index=True)  # available, rented, sold
