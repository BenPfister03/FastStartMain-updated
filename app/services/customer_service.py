from typing import Optional

from sqlmodel import Session

from app.models import Customer, Payment
from app.models.user import UserBase
from app.repositories.user import UserRepository
from app.utilities.security import encrypt_password, verify_password
from datetime import date

DEPOSIT = 20.0  # business rule: customers must pay a deposit to join


class CustomerService:
    def __init__(self, db: Session):
        self.db = db
        self.user_repo = UserRepository(db)

    def register(self, username: str, email: str, password: str) -> Customer:
        """Create a user account and customer profile, and record the joining deposit."""
        user = self.user_repo.create(UserBase(
            username=username, email=email,
            password=encrypt_password(password), role="customer"))
        customer = Customer(id=user.id)
        self.db.add(customer)
        self.db.add(Payment(customer_id=user.id, payment_date=date.today(), amount=DEPOSIT))
        self.db.commit()
        self.db.refresh(customer)
        return customer

    def authenticate(self, username: str, password: str) -> Optional[Customer]:
        user = self.user_repo.get_by_username(username)
        if not user or not verify_password(plaintext_password=password, encrypted_password=user.password):
            return None
        return self.db.get(Customer, user.id)
