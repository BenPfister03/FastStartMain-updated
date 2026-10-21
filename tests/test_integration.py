import jwt

from app.config import get_settings
from app.repositories.user import UserRepository
from app.services.auth_service import AuthService
from app.services.user_service import UserService
from app.utilities.security import verify_password


def test_save_password(test_session):
    repository = UserRepository(test_session)
    service = AuthService(repository)

    created = service.register_user("alice", "alice@example.com", "secret")
    saved = repository.get_by_username("alice")

    assert saved is not None
    assert created.id == saved.id
    assert saved.username == "alice"
    assert saved.email == "alice@example.com"
    assert saved.role == "regular_user"
    assert saved.password != "secret"
    assert verify_password("secret", saved.password)


def test_login(test_session):
    repository = UserRepository(test_session)
    service = AuthService(repository)
    service.register_user("alice", "alice@example.com", "secret")

    token = service.authenticate_user("alice", "secret")
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    assert payload["sub"] == str(repository.get_by_username("alice").id)
    assert payload["role"] == "regular_user"


def test_register_user(test_session):
    repository = UserRepository(test_session)
    auth_service = AuthService(repository)
    auth_service.register_user("alice", "alice@example.com", "secret")

    users = UserService(repository).get_all_users()

    assert len(users) == 1
    assert users[0].username == "alice"
    assert users[0].email == "alice@example.com"


def test_update_user(test_session):
    from app.schemas.user import UserUpdate

    repository = UserRepository(test_session)
    created = AuthService(repository).register_user("alice", "alice@example.com", "secret")

    repository.update_user(created.id, UserUpdate(username="alice2", email="alice2@example.com"))
    updated = repository.get_by_id(created.id)

    assert updated.username == "alice2"
    assert updated.email == "alice2@example.com"
    assert repository.get_by_username("alice") is None


def test_authenticate_rejects_bad_credentials(test_session):
    repository = UserRepository(test_session)
    service = AuthService(repository)
    service.register_user("alice", "alice@example.com", "secret")

    assert service.authenticate_user("alice", "wrong") is None
    assert service.authenticate_user("nobody", "secret") is None
