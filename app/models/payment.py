from typing import Optional
from sqlmodel import Field, SQLModel


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    amount: float
    customer_id: Optional[int] = Field(default=None, foreign_key="user.id")
    purpose: str = "rental"
