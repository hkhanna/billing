import pytest
import stripe
from unittest.mock import Mock


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    pass


@pytest.fixture(autouse=True)
def mock_stripe_customer(monkeypatch):
    """Fixture to monkeypatch the stripe.Customer.* methods"""
    mock = Mock()
    monkeypatch.setattr(stripe, "Customer", mock)
    return mock


@pytest.fixture(autouse=True)
def mock_stripe_payment_method(monkeypatch):
    """Fixture to monkeypatch the stripe.PaymentMethod.* methods"""
    mock = Mock()
    monkeypatch.setattr(stripe, "PaymentMethod", mock)
    return mock


@pytest.fixture(autouse=True)
def mock_stripe_subscription(monkeypatch):
    """Fixture to monkeypatch the stripe.Subscription.* methods"""
    mock = Mock()
    monkeypatch.setattr(stripe, "Subscription", mock)
    return mock
