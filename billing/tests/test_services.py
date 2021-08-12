"""Tests related to individual services"""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import stripe
from .. import factories, utils


class MockStripeCustomer:
    def __init__(self, user, good_key, good_value):
        self.email = user.email
        self.user_pk_key = utils.user_pk_key
        self.user_pk_value = user.pk
        if not good_key:
            self.user_pk_key = "bad_user_pk"
        if not good_value:
            self.user_pk_value = user.pk + 1
        self.metadata = SimpleNamespace()
        setattr(self.metadata, self.user_pk_key, self.user_pk_value)


@pytest.fixture
def user():
    return factories.UserFactory(pk=1000)


@pytest.fixture
def mock_stripe_customer_list(monkeypatch):
    mock_stripe_customer = Mock()
    monkeypatch.setattr(stripe, "Customer", mock_stripe_customer)
    return mock_stripe_customer.list


@pytest.mark.django_db
@pytest.mark.parametrize(
    "customer_spec,expected_errors,expected_type",
    [
        ([], 0, None),
        ([(True, False)], 1, None),
        ([(True, False), (True, False)], 3, None),
        ([(True, True)], 0, MockStripeCustomer),
        ([(False, False)], 0, MockStripeCustomer),
        ([(False, False), (False, False)], 1, MockStripeCustomer),
        ([(True, True), (True, True)], 1, MockStripeCustomer),
        ([(True, True), (True, False)], 1, MockStripeCustomer),
    ],
    ids=[
        "0 customers with matching email -> None",
        "1 customer with matching email, with matching user_pk_key but wrong value -> None (logs error)",
        "2 customers with matching email, both with matching user_pk_key and both with wrong value -> None (logs 3 errors)",
        "1 customer with matching email, with matching user_pk_key and value -> customer",
        "1 customer with matching email, but no matching user_pk_key -> customer [and add the user_pk_key in stripe]",
        "2 customers with matching email, but no matching user_pk_key -> customer (log error) [and add the user_pk_key in stripe to last customer]",
        "2 customers with matching email, both with matching user_pk_key and value -> customer (log error)",
        "2 customers with matching email, both with matching user_pk_key but one with wrong value -> customer (log error)",
    ],
)
def test_get_customer(
    user,
    caplog,
    mock_stripe_customer_list,
    customer_spec,
    expected_errors,
    expected_type,
):
    """A customer is returned from Stripe only if there is a positive match on metadata pk"""
    customer_list = []
    for customer in customer_spec:
        customer_list.append(
            MockStripeCustomer(user, good_key=customer[0], good_value=customer[1])
        )

    mock_stripe_customer_list.return_value.data = customer_list
    stripe_customer = utils.stripe_get_customer(user)
    if expected_type is None:
        assert stripe_customer is None
    else:
        assert type(stripe_customer) == expected_type
    assert len(caplog.records) == expected_errors
