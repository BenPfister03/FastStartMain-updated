from sqlmodel import SQLModel

class SigninRequest(SQLModel):
    username: str
    password: str

class SignupRequest(SQLModel):
    username: str
    email: str
    password: str