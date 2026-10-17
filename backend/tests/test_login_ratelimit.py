"""登录限流：连续失败后 429，成功登录清零。"""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import auth
from app.core import metrics
from app.core.database import get_session
from app.core.ratelimit import SlidingWindowLimiter
from app.core.security import hash_password
from app.models.auth import User


def test_limiter_blocks_after_limit_and_resets() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.check("acct") == (True, 0)

    limiter.hit("acct")
    limiter.hit("acct")
    allowed, retry_after = limiter.check("acct")
    assert allowed is False
    assert retry_after >= 1

    limiter.reset("acct")
    assert limiter.check("acct")[0] is True


def test_limiter_window_expires() -> None:
    limiter = SlidingWindowLimiter(limit=1, window_seconds=0.02)
    limiter.hit("acct")
    assert limiter.check("acct")[0] is False
    time.sleep(0.03)
    assert limiter.check("acct")[0] is True


def _client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                email="owner@example.com",
                hashed_password=hash_password("correct-password"),
                org_id=1,
                role="owner",
            )
        )
        session.commit()

    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_session] = lambda: Session(engine)
    return TestClient(app)


def test_login_returns_429_after_repeated_failures() -> None:
    auth._login_limiter.clear()
    metrics.reset()
    client = _client()

    for _ in range(5):
        response = client.post(
            "/auth/login",
            data={"username": "owner@example.com", "password": "wrong"},
        )
        assert response.status_code == 401

    blocked = client.post(
        "/auth/login",
        data={"username": "owner@example.com", "password": "wrong"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1

    rendered = metrics.render()
    assert 'aiops_login_failures_total{reason="bad_credentials"} 5' in rendered
    assert 'aiops_login_failures_total{reason="rate_limited"} 1' in rendered


def test_successful_login_clears_failures() -> None:
    auth._login_limiter.clear()
    client = _client()

    for _ in range(3):
        client.post("/auth/login", data={"username": "owner@example.com", "password": "bad"})

    ok = client.post(
        "/auth/login",
        data={"username": "owner@example.com", "password": "correct-password"},
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    # 计数已清零：再错一次应该是 401 而不是 429
    again = client.post(
        "/auth/login",
        data={"username": "owner@example.com", "password": "bad"},
    )
    assert again.status_code == 401
