from typing import Optional
from sqlmodel import Field, SQLModel


class Game(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    platform: str = Field(index=True)
    genre: str = ""
    release_year: Optional[int] = None
