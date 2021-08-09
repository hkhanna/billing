import json
from datetime import timedelta, datetime as dt
from unittest import mock, skip
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from . import models, factories, serializers

User = get_user_model()

# There are 6 types of tests when it comes to billing:
#
# One important rule: you may not call another app's URL namespace from these tests.
#
# 1. Django-y things like signals and model constraints.
# A customer is automatically created if a user does not have one, and it accomplishes this via signals.
# We also have some model constraints we want to test.
#
# 2. Customer information returned via the API.
# Serialized information about the Customer must be returned on various endpoints. While we can test the
# serializer here, the Customer information should be returned by an endpoint outside this app (like a user
# settings endpoint), so there should also be at least one test in whatever app contains the User model ensuring
# that Customer information comes through where its needed.
#
# 3. The limitations of various billing plans.
# In this billing app, we can test generically that Limits are resolved properly based on the active plan.
# It may be sensible to do additional testing in other apps for specific, real Limits, e.g., the maximum
# number of emails a user can send.
#
# 4. Users interacting with subscriptions for the first time.
# Like upgrading to a paid plan or canceling the paid plan.
#
# 5. Stripe webhook processing.
# Making sure that the right Stripe webhooks are processed in the right way.
#
# 6. Users taking action on the website due to a webhook.
# If a webhook comes in that says the credit card is expired, a user will come back to the website to take action.
# We don't test these since they're substantially captured by test cases in type 4. For example, if a credit card
# is declined on renewal, it looks very much like a credit card being declined initially.


@patch("billing.utils.stripe")
class CustomerAPITest(APITestCase):
    """Tests related to automatic Customer creation and model constraints. These are tests of type 1 and 2."""

    def test_save_user_create_customer(self, *args):
        """Saving a User without a Customer automatically creates a Customer with the free_default plan.
        This tests both the automatic creation of a Customer and the automatic creation of a free_default plan."""

        # Not using the UserFactory here to really emphasize that we're saving a User and triggering
        # the signal.
        user = User.objects.create_user(
            first_name="Firstname", last_name="Lastname", email="user@example.com"
        )
        self.assertEqual(user.customer.state, "free_default.new")
        self.assertTrue(models.Customer.objects.filter(user=user))
        self.assertEqual(
            1, models.Plan.objects.filter(type=models.Plan.Type.FREE_DEFAULT).count()
        )

    def test_save_user_create_customer_exists(self, *args):
        """Saving a User that has a Customer does not create a Customer."""
        user = factories.UserFactory()
        customer_id = user.customer.id
        user.save()
        customer = models.Customer.objects.get(user=user)
        self.assertEqual(customer_id, customer.id)

    def test_save_user_save_customer(self, *args):
        """Saving a User with a related Customer saves the Customer as well."""
        user = factories.UserFactory()
        customer_id = "cus_xyz"
        user.customer.customer_id = customer_id
        user.save()
        customer = models.Customer.objects.get(user=user)
        self.assertEqual(customer_id, customer.customer_id)

    def test_update_user_stripe(self, *args):
        """Updating a User's first_name, last_name, or email also updates it in Stripe."""
        user = factories.UserFactory(paying=True)
        user.first_name = "New First Name"
        user.save()
        args[0].Customer.modify.assert_called_once()
        args[0].reset_mock()
        user.last_name = "New Last Name"
        user.save()
        args[0].Customer.modify.assert_called_once()
        args[0].reset_mock()
        user.email = "new_email@example.com"
        user.save()
        args[0].Customer.modify.assert_called_once()
        args[0].reset_mock()

        # Don't call out to Stripe unless name or email changed
        args[0].Customer.modify.assert_not_called()

    def test_soft_delete_user_active_subscription(self, *args):
        """Soft deleting a User with an active Stripe subscription cancels the Subscription."""
        user = factories.UserFactory(paying=True)
        user.save()
        args[0].Subscription.modify.assert_not_called()
        self.assertEqual(models.Customer.PaymentState.OK, user.customer.payment_state)

        user.is_active = False
        user.save()
        args[0].Subscription.modify.assert_called_once()
        self.assertEqual(models.Customer.PaymentState.OFF, user.customer.payment_state)

    def test_delete_user_active_subscription(self, *args):
        """Hard deleting a User with an active Stripe subscription cancels the Subscription."""
        user = factories.UserFactory(paying=True)
        user.delete()
        args[0].Subscription.modify.assert_called_once()
        self.assertEqual(0, models.Customer.objects.count())

    def test_customer_payment_state_constraint(self, *args):
        """If the payment_state is NOT set to off, there MUST be a subscription id."""
        factories.UserFactory(
            customer__subscription_id=None,
            customer__payment_state=models.Customer.PaymentState.OFF,
        )
        factories.UserFactory(
            customer__subscription_id=factories.id("sub"),
            customer__payment_state=models.Customer.PaymentState.OFF,
        )
        factories.UserFactory(
            customer__subscription_id=factories.id("sub"),
            customer__payment_state=models.Customer.PaymentState.ERROR,
        )
        with self.assertRaises(IntegrityError):
            factories.UserFactory(
                customer__subscription_id=None,
                customer__payment_state=models.Customer.PaymentState.OK,
            )

    def test_customer_serializer(self, *args):
        """Customer serializer returns expected information"""
        user = factories.UserFactory()
        serializer = serializers.CustomerSerializer(instance=user.customer)
        self.assertJSONEqual(
            json.dumps(serializer.data),
            {
                "current_period_end": None,
                "payment_state": models.Customer.PaymentState.OFF,
                "cc_info": None,
                "state": "free_default.new",
                "plan": {
                    "name": "Default (Free)",
                    "display_price": 0,
                    "type": models.Plan.Type.FREE_DEFAULT,
                    "limits": {},
                },
            },
        )

    def test_customer_paying_serializer(self, *args):
        """Paying customer serializer returns expected information"""
        user = factories.UserFactory(paying=True)
        plan_limit1, plan_limit2 = factories.PlanLimitFactory.create_batch(
            plan=user.customer.plan, size=2
        )
        limit3 = (
            factories.LimitFactory()
        )  # Create 1 more limit so we can test that default comes through when PlanLimit not set.
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        df_planlimit = factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit used by free_default"
        )

        serializer = serializers.CustomerSerializer(instance=user.customer)
        self.assertJSONEqual(
            json.dumps(serializer.data),
            {
                "current_period_end": user.customer.current_period_end.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "payment_state": models.Customer.PaymentState.OK,
                "cc_info": user.customer.cc_info,
                "state": "paid.paying",
                "plan": {
                    "name": user.customer.plan.name,
                    "display_price": user.customer.plan.display_price,
                    "type": models.Plan.Type.PAID_PUBLIC,
                    "limits": {
                        plan_limit1.limit.name: plan_limit1.value,
                        plan_limit2.limit.name: plan_limit2.value,
                        limit3.name: limit3.default,
                        "Limit used by free_default": df_planlimit.limit.default,
                    },
                },
            },
        )

    def test_customer_paying_expired_serializer(self, *args):
        """Paying customer serializer with expired current_period_end should return free_default information"""
        user = factories.UserFactory(
            paying=True,
            customer__payment_state=models.Customer.PaymentState.OFF,
            customer__current_period_end=factories.fake.past_datetime(
                tzinfo=timezone.utc
            ),
        )
        plan_limit1, plan_limit2 = factories.PlanLimitFactory.create_batch(
            plan=user.customer.plan, size=2
        )
        limit3 = (
            factories.LimitFactory()
        )  # Create 1 more limit so we can test that default comes through when PlanLimit not set.
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        df_planlimit = factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit used by free_default"
        )

        serializer = serializers.CustomerSerializer(instance=user.customer)
        self.assertJSONEqual(
            json.dumps(serializer.data),
            {
                "current_period_end": user.customer.current_period_end.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "payment_state": models.Customer.PaymentState.OFF,
                "cc_info": user.customer.cc_info,
                "state": "free_default.canceled",
                "plan": {
                    "name": free_default_plan.name,
                    "display_price": free_default_plan.display_price,
                    "type": models.Plan.Type.FREE_DEFAULT,
                    "limits": {
                        plan_limit1.limit.name: plan_limit1.limit.default,
                        plan_limit2.limit.name: plan_limit2.limit.default,
                        limit3.name: limit3.default,
                        "Limit used by free_default": df_planlimit.value,
                    },
                },
            },
        )

    def test_customer_paying_no_date_serializer(self, *args):
        """Paying customer serializer with None for current_period_end should return free_default information.
        This can only happen if someone signs up but their signup was incomplete because their credit card was not
        accepted."""
        user = factories.UserFactory(
            paying=True,
            customer__payment_state=models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD,
            customer__current_period_end=None,
        )
        plan_limit1, plan_limit2 = factories.PlanLimitFactory.create_batch(
            plan=user.customer.plan, size=2
        )
        limit3 = (
            factories.LimitFactory()
        )  # Create 1 more limit so we can test that default comes through when PlanLimit not set.
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        df_planlimit = factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit used by free_default"
        )

        serializer = serializers.CustomerSerializer(instance=user.customer)
        self.assertJSONEqual(
            json.dumps(serializer.data),
            {
                "current_period_end": None,
                "payment_state": models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD,
                "cc_info": user.customer.cc_info,
                "state": "free_default.incomplete.requires_payment_method",
                "plan": {
                    "name": free_default_plan.name,
                    "display_price": free_default_plan.display_price,
                    "type": models.Plan.Type.FREE_DEFAULT,
                    "limits": {
                        plan_limit1.limit.name: plan_limit1.limit.default,
                        plan_limit2.limit.name: plan_limit2.limit.default,
                        limit3.name: limit3.default,
                        "Limit used by free_default": df_planlimit.value,
                    },
                },
            },
        )

    def test_customer_free_private_expired_serializer(self, *args):
        """Free private free plan with an expired current_period_end should return the free_default plan information."""
        user = factories.UserFactory(
            customer__plan=factories.PlanFactory(type=models.Plan.Type.FREE_PRIVATE),
            customer__current_period_end=factories.fake.past_datetime(
                tzinfo=timezone.utc
            ),
        )
        plan_limit1, plan_limit2 = factories.PlanLimitFactory.create_batch(
            plan=user.customer.plan, size=2
        )
        limit3 = (
            factories.LimitFactory()
        )  # Create 1 more limit so we can test that default comes through when PlanLimit not set.
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        df_planlimit = factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit used by free_default"
        )

        serializer = serializers.CustomerSerializer(instance=user.customer)
        self.assertJSONEqual(
            json.dumps(serializer.data),
            {
                "current_period_end": user.customer.current_period_end.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "payment_state": models.Customer.PaymentState.OFF,
                "cc_info": None,
                "state": "free_default.canceled",
                "plan": {
                    "name": free_default_plan.name,
                    "display_price": free_default_plan.display_price,
                    "type": models.Plan.Type.FREE_DEFAULT,
                    "limits": {
                        plan_limit1.limit.name: plan_limit1.limit.default,
                        plan_limit2.limit.name: plan_limit2.limit.default,
                        limit3.name: limit3.default,
                        "Limit used by free_default": df_planlimit.value,
                    },
                },
            },
        )

    def test_customer_free_private_no_date_serializer(self, *args):
        """Free private plan with None for current_period_end should still return free private information."""
        user = factories.UserFactory(
            customer__plan=factories.PlanFactory(type=models.Plan.Type.FREE_PRIVATE),
            customer__current_period_end=None,
        )
        plan_limit1, plan_limit2 = factories.PlanLimitFactory.create_batch(
            plan=user.customer.plan, size=2
        )
        limit3 = (
            factories.LimitFactory()
        )  # Create 1 more limit so we can test that default comes through when PlanLimit not set.
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        df_planlimit = factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit used by free_default"
        )

        serializer = serializers.CustomerSerializer(instance=user.customer)
        self.assertJSONEqual(
            json.dumps(serializer.data),
            {
                "current_period_end": None,
                "payment_state": models.Customer.PaymentState.OFF,
                "cc_info": None,
                "state": "free_private.indefinite",
                "plan": {
                    "name": user.customer.plan.name,
                    "display_price": user.customer.plan.display_price,
                    "type": models.Plan.Type.FREE_PRIVATE,
                    "limits": {
                        plan_limit1.limit.name: plan_limit1.value,
                        plan_limit2.limit.name: plan_limit2.value,
                        limit3.name: limit3.default,
                        "Limit used by free_default": df_planlimit.limit.default,
                    },
                },
            },
        )


class LimitTest(APITestCase):
    """This contains tests from type 3 above."""

    def setUp(self):
        self.user = factories.UserFactory(paying=True)
        self.client.force_login(self.user)
        factories.PlanLimitFactory(
            plan=self.user.customer.plan,
            value=1,
            limit__name="Limit 1",
            limit__default=99,
        )
        factories.PlanLimitFactory(
            plan=self.user.customer.plan,
            value=2,
            limit__name="Limit 2",
            limit__default=98,
        )
        factories.LimitFactory(name="Limit 3", default=97)

    def test_get_limit(self, *args):
        """Customer.get_limit returns the PlanLimit value."""
        value = self.user.customer.get_limit("Limit 1")
        self.assertEqual(value, 1)
        value = self.user.customer.get_limit("Limit 2")
        self.assertEqual(value, 2)

    def test_get_limit_default(self, *args):
        """Customer.get_limit returns the Limit default if the Plan does not have have that PlanLimit."""
        value = self.user.customer.get_limit("Limit 3")
        self.assertEqual(value, 97)

    def test_get_limit_nonexist(self, *args):
        """Attempting to get a non-existent limit will raise."""
        with self.assertRaises(ObjectDoesNotExist):
            self.user.customer.get_limit("Bad Limit")

    def test_get_limit_expired_plan(self, *args):
        """Getting a limit for an expired paid plan should return the limit from the free_default plan."""
        # Expire the plan
        self.user.customer.current_period_end = timezone.now() - timedelta(minutes=1)
        self.user.customer.save()

        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit 1"
        )  # Will get existing Limit
        value = self.user.customer.get_limit("Limit 1")
        self.assertEqual(50, value)

        # Because the free_default plan does not have Limit 2, it should use the default.
        value = self.user.customer.get_limit("Limit 2")
        self.assertEqual(98, value)

    def test_get_limit_paid_plan_with_no_date(self, *args):
        """A paid plan with no current_period_end should be treated as expired."""
        self.user.customer.current_period_end = None
        self.user.customer.save()
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit 1"
        )  # Will get existing Limit
        value = self.user.customer.get_limit("Limit 1")
        self.assertEqual(50, value)

    def test_get_limit_free_private_plan_expired(self, *args):
        """A free_private plan with an expired current_period_end should return the limits from the free_default plan."""
        plan = factories.PlanFactory(type=models.Plan.Type.FREE_PRIVATE)
        self.user = factories.UserFactory(
            paying=False,
            customer__plan=plan,
            customer__current_period_end=timezone.now() - timedelta(days=10),
        )
        self.client.force_login(self.user)
        factories.PlanLimitFactory(
            plan=plan, value=0, limit__name="Limit 1"
        )  # Will get existing Limit

        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit 1"
        )  # Will get existing Limit
        value = self.user.customer.get_limit("Limit 1")
        self.assertEqual(50, value)

        # Because the free_default plan does not have Limit 2, it should use the default.
        value = self.user.customer.get_limit("Limit 2")
        self.assertEqual(98, value)

    def test_get_limit_free_private_plan_with_no_date(self, *args):
        """A free_private plan with no current_period_end should NOT be treated as expired."""
        plan = factories.PlanFactory(type=models.Plan.Type.FREE_PRIVATE)
        self.user = factories.UserFactory(
            paying=False, customer__plan=plan, customer__current_period_end=None
        )
        self.client.force_login(self.user)
        factories.PlanLimitFactory(
            plan=plan, value=0, limit__name="Limit 1"
        )  # Will get existing Limit

        # These defaults won't be used.
        free_default_plan = models.Plan.objects.get(type=models.Plan.Type.FREE_DEFAULT)
        factories.PlanLimitFactory(
            plan=free_default_plan, value=50, limit__name="Limit 1"
        )

        value = self.user.customer.get_limit("Limit 1")
        self.assertEqual(0, value)


@patch("billing.utils.stripe")
class SubscriptionAPITest(APITestCase):
    """This contains tests type 4 from above."""

    def test_create_subscription(self, *args):
        """Create Subscription endpoint succeeds and should set the customer_id, plan, current_period_end,
        payment_state and card_info"""
        current_period_end = timezone.now() + timedelta(days=30)
        cc_info = factories.cc_info()
        args[0].Customer.create.return_value.id = factories.id("cus")
        args[0].Subscription.create.return_value.id = "sub_paid"
        args[0].Subscription.create.return_value.status = "active"
        args[
            0
        ].Subscription.create.return_value.current_period_end = (
            current_period_end.timestamp()
        )
        args[0].PaymentMethod.attach.return_value.card = cc_info
        paid_plan = factories.PlanFactory(paid=True)

        self.user = factories.UserFactory()
        self.client.force_login(self.user)

        url = reverse("billing:create-subscription")
        payload = {
            "payment_method_id": factories.id("payment"),
            "plan_id": paid_plan.id,
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        args[0].Customer.create.assert_called_once()
        args[0].Subscription.create.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(paid_plan, self.user.customer.plan)
        self.assertEqual(
            models.Customer.PaymentState.OK, self.user.customer.payment_state
        )
        self.assertEqual(
            current_period_end.timestamp(),
            self.user.customer.current_period_end.timestamp(),
        )
        self.assertEqual("sub_paid", self.user.customer.subscription_id)
        self.assertJSONEqual(json.dumps(self.user.customer.cc_info), cc_info)
        self.assertEqual("paid.paying", self.user.customer.state)

    def test_create_subscription_customer_id_exists(self, *args):
        """Creating a Subscription should not change the customer_id or create a new Stripe customer
        if the Customer already has a customer_id"""
        current_period_end = timezone.now() + timedelta(days=30)
        cc_info = factories.cc_info()
        args[0].Subscription.create.return_value.id = "sub_paid"
        args[0].Subscription.create.return_value.status = "active"
        args[
            0
        ].Subscription.create.return_value.current_period_end = (
            current_period_end.timestamp()
        )
        args[0].PaymentMethod.attach.return_value.card = cc_info
        paid_plan = factories.PlanFactory(paid=True)

        self.user = factories.UserFactory(customer__customer_id="cus_xyz")
        self.client.force_login(self.user)

        url = reverse("billing:create-subscription")
        payload = {
            "payment_method_id": factories.id("payment"),
            "plan_id": paid_plan.id,
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        args[0].Customer.create.assert_not_called()
        args[0].Subscription.create.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual("cus_xyz", self.user.customer.customer_id)
        self.assertEqual(paid_plan, self.user.customer.plan)
        self.assertEqual(
            models.Customer.PaymentState.OK, self.user.customer.payment_state
        )
        self.assertEqual(
            current_period_end.timestamp(),
            self.user.customer.current_period_end.timestamp(),
        )
        self.assertEqual("sub_paid", self.user.customer.subscription_id)
        self.assertJSONEqual(json.dumps(self.user.customer.cc_info), cc_info)
        self.assertEqual("paid.paying", self.user.customer.state)

    def test_create_subscription_customer_exists_on_stripe(self, *args):
        """If a User does not have a customer_id, but the customer is on Stripe, it should use that Customer."""
        self.user = factories.UserFactory()
        self.client.force_login(self.user)

        current_period_end = timezone.now() + timedelta(days=30)
        cc_info = factories.cc_info()
        args[0].Customer.list.return_value.data = [
            mock.MagicMock(
                **{"id": factories.id("cus"), "metadata.user_pk": str(self.user.pk)}
            )
        ]
        args[0].Subscription.create.return_value.id = "sub_paid"
        args[0].Subscription.create.return_value.status = "active"
        args[
            0
        ].Subscription.create.return_value.current_period_end = (
            current_period_end.timestamp()
        )
        args[0].PaymentMethod.attach.return_value.card = cc_info
        paid_plan = factories.PlanFactory(paid=True)

        url = reverse("billing:create-subscription")
        payload = {
            "payment_method_id": factories.id("payment"),
            "plan_id": paid_plan.id,
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        args[0].Customer.create.assert_not_called()
        args[0].Subscription.create.assert_called_once()

    def test_create_subscription_failed(self, *args):
        """Create Subscription endpoint attaches the payment method to the Customer but the charge fails. Should set
        the customer's payment_state and state but the plan and current_period_end should not be modified."""
        cc_info = factories.cc_info()
        args[0].Customer.create.return_value.id = factories.id("cus")
        args[0].Subscription.create.return_value.id = "sub_paid"
        args[0].Subscription.create.return_value.status = "incomplete"
        args[
            0
        ].Subscription.create.return_value.latest_invoice.payment_intent.status = (
            "requires_payment_method"
        )
        args[0].PaymentMethod.attach.return_value.card = cc_info

        self.user = factories.UserFactory()
        self.client.force_login(self.user)

        paid_plan = factories.PlanFactory(paid=True)
        url = reverse("billing:create-subscription")
        payload = {
            "payment_method_id": factories.id("payment"),
            "plan_id": paid_plan.id,
        }
        response = self.client.post(url, payload)
        self.assertEqual(400, response.status_code)
        args[0].Customer.create.assert_called_once()
        args[0].Subscription.create.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(paid_plan, self.user.customer.plan)
        self.assertEqual(
            models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD,
            self.user.customer.payment_state,
        )
        self.assertEqual(None, self.user.customer.current_period_end)
        self.assertEqual("sub_paid", self.user.customer.subscription_id)
        self.assertJSONEqual(json.dumps(self.user.customer.cc_info), cc_info)
        self.assertEqual(
            "free_default.incomplete.requires_payment_method", self.user.customer.state
        )

    def test_create_subscription_failed_cure(self, *args):
        """A card initially declined can be cured within 23 hours"""
        self.user = factories.UserFactory(
            paying=True,
            customer__subscription_id="sub_1",
            customer__payment_state=models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD,
        )
        self.client.force_login(self.user)

        new_cc_info = {
            "brand": "visa",
            "last4": "1111",
            "exp_month": 11,
            "exp_year": 2017,
        }
        mock_period_end = timezone.now() + timedelta(days=30)
        args[0].PaymentMethod.attach.return_value.card = new_cc_info
        args[0].Invoice.list.return_value = {
            "data": [{"id": "inv_id", "status": "open"}]
        }
        args[0].Invoice.pay.return_value = {
            "status": "paid",
            "lines": {"data": [{"period": {"end": mock_period_end.timestamp()}}]},
        }

        url = reverse("billing:cure-failed-card")
        payload = {"payment_method_id": "abc"}
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        args[0].PaymentMethod.attach.assert_called_once()
        args[0].Subscription.modify.assert_called_once()
        args[0].Invoice.list.assert_called_once()
        args[0].Invoice.pay.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(
            models.Customer.PaymentState.OK, self.user.customer.payment_state
        )
        self.assertEqual(models.Plan.Type.PAID_PUBLIC, self.user.customer.plan.type)
        self.assertEqual("sub_1", self.user.customer.subscription_id)
        self.assertJSONEqual(json.dumps(self.user.customer.cc_info), new_cc_info)
        self.assertEqual(
            mock_period_end.timestamp(),
            self.user.customer.current_period_end.timestamp(),
        )
        self.assertEqual("paid.paying", self.user.customer.state)

    def test_create_subscription_twice(self, *args):
        """Attempting to create a subscription when one is active fails"""
        # If current_period_end is in the future and payment_state is off, they should be re-activating, not creating a subscription.
        self.user = factories.UserFactory(
            paying=True, customer__payment_state=models.Customer.PaymentState.OFF
        )
        self.client.force_login(self.user)

        url = reverse("billing:create-subscription")
        payload = {"payment_method_id": "abc", "plan_id": self.user.customer.plan.id}
        response = self.client.post(url, payload)
        self.assertContains(response, "has a subscription", status_code=400)

    def test_nonpublic_plan(self, *args):
        """Billing Plans that are not public cannot be subscribed to via the API"""
        plan = factories.PlanFactory(type=models.Plan.Type.FREE_PRIVATE)

        self.user = factories.UserFactory()
        self.client.force_login(self.user)

        url = reverse("billing:create-subscription")
        payload = {"payment_method_id": "abc", "plan_id": plan.id}
        response = self.client.post(url, payload)
        self.assertContains(response, "plan does not exist", status_code=400)
        args[0].Subscription.create.assert_not_called()

    def test_cancel_subscription(self, *args):
        """Canceling a subscription sets payment_state to off, does not renew at the end of the billing period
        but otherwise does not affect the billing plan."""
        self.user = factories.UserFactory(paying=True)
        self.client.force_login(self.user)
        url = reverse("billing:cancel-subscription")
        response = self.client.post(url)
        self.assertEqual(201, response.status_code)
        self.user.customer.refresh_from_db()
        self.assertEqual(
            models.Customer.PaymentState.OFF, self.user.customer.payment_state
        )
        self.assertGreater(self.user.customer.current_period_end, timezone.now())
        self.assertEqual("paid.will_cancel", self.user.customer.state)

    def test_cancel_subscription_error(self, *args):
        """Cancelling a subscription with payment_state set to off will 400"""
        self.user = factories.UserFactory(
            paying=True, customer__payment_state=models.Customer.PaymentState.OFF
        )
        self.client.force_login(self.user)
        url = reverse("billing:cancel-subscription")
        response = self.client.post(url)
        self.assertContains(
            response, "No active subscription to cancel", status_code=400
        )

    def test_reactivate_subscription(self, *args):
        """Reactivating a subscription that will be canceled before the end of the billing cycle"""
        self.user = factories.UserFactory(
            paying=True, customer__payment_state=models.Customer.PaymentState.OFF
        )
        self.client.force_login(self.user)
        subscription_id = self.user.customer.subscription_id

        url = reverse("billing:reactivate-subscription")
        response = self.client.post(url)
        self.assertEqual(201, response.status_code)
        self.user.customer.refresh_from_db()
        self.assertEqual(
            models.Customer.PaymentState.OK, self.user.customer.payment_state
        )
        self.assertEqual(subscription_id, self.user.customer.subscription_id)
        args[0].Subscription.modify.assert_called_once()
        self.assertEqual("paid.paying", self.user.customer.state)

    def test_reactivate_subscription_error(self, *args):
        """Reactivating a subscription that will be canceled before the end of the billing cycle errors
        if there's an active subscription or one that is already canceled"""
        self.user = factories.UserFactory(
            paying=True, customer__payment_state=models.Customer.PaymentState.OFF
        )
        self.client.force_login(self.user)

        url = reverse("billing:reactivate-subscription")

        # First, the sub is already canceled
        self.user.customer.current_period_end = timezone.now() - timedelta(days=10)
        self.user.customer.save()
        self.assertEqual("free_default.canceled", self.user.customer.state)
        response = self.client.post(url)
        self.assertEqual(400, response.status_code)

        # The payment is not off
        self.user.customer.current_period_end = timezone.now() + timedelta(days=10)
        self.user.customer.payment_state = models.Customer.PaymentState.OK
        self.user.customer.save()
        self.assertEqual("paid.paying", self.user.customer.state)
        response = self.client.post(url)
        self.assertEqual(400, response.status_code)

        # There was never any subscription
        self.user.customer.plan = factories.PlanFactory(
            type=models.Plan.Type.FREE_DEFAULT
        )
        self.user.customer.current_period_end = None
        self.user.customer.payment_state = models.Customer.PaymentState.OFF
        self.user.customer.subscription_id = None
        self.user.customer.save()
        self.assertEqual("free_default.new", self.user.customer.state)
        response = self.client.post(url)
        self.assertEqual(400, response.status_code)

    def test_reactivate_canceled_subscription(self, *args):
        """Reactivating a subscription that was canceled and whose billing cycle expired creates a fresh subscription"""
        self.user = factories.UserFactory(
            paying=True,
            customer__current_period_end=timezone.now() - timedelta(days=10),
            customer__payment_state=models.Customer.PaymentState.OFF,
        )
        self.client.force_login(self.user)
        plan_id = self.user.customer.plan_id

        mock_current_period_end = timezone.now() + timedelta(days=30)
        new_cc_info = {
            "brand": "visa",
            "last4": "1111",
            "exp_month": 11,
            "exp_year": 2017,
        }
        args[0].Subscription.create.return_value = mock.MagicMock(
            **{
                "id": "new_sub",
                "status": "active",
                "current_period_end": mock_current_period_end.timestamp(),
            }
        )
        args[0].PaymentMethod.attach.return_value.card = new_cc_info
        url = reverse("billing:create-subscription")
        payload = {"payment_method_id": "abc", "plan_id": plan_id}
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        args[0].Subscription.create.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(plan_id, self.user.customer.plan_id)
        self.assertEqual(
            models.Customer.PaymentState.OK, self.user.customer.payment_state
        )
        self.assertEqual("new_sub", self.user.customer.subscription_id)
        self.assertEqual(
            mock_current_period_end.timestamp(),
            self.user.customer.current_period_end.timestamp(),
        )
        self.assertJSONEqual(json.dumps(self.user.customer.cc_info), new_cc_info)
        self.assertEqual("paid.paying", self.user.customer.state)

    def test_replace_card(self, *args):
        """Replace a credit card for an active subscription"""
        self.user = factories.UserFactory(paying=True)
        self.client.force_login(self.user)

        new_cc_info = {
            "brand": "visa",
            "last4": "1111",
            "exp_month": 11,
            "exp_year": 2017,
        }
        args[0].PaymentMethod.attach.return_value.card = new_cc_info
        url = reverse("billing:replace-card")
        payload = {"payment_method_id": "abc"}
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        args[0].PaymentMethod.attach.assert_called_once()
        args[0].Subscription.modify.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(
            models.Customer.PaymentState.OK, self.user.customer.payment_state
        )
        self.assertJSONEqual(json.dumps(self.user.customer.cc_info), new_cc_info)


@patch("billing.utils.stripe")
class StripeWebhookAPITest(APITestCase):
    """Stripe webhook functionality (tests type 5 from above)."""

    def setUp(self):
        # Customer that is coming up for renewal
        self.current_period_end = factories.fake.future_datetime(
            end_date="+5d", tzinfo=timezone.utc
        )
        self.user = factories.UserFactory(
            paying=True,
            customer__subscription_id="sub",
            customer__current_period_end=self.current_period_end,
        )
        self.customer = self.user.customer
        self.assertEqual("paid.paying", self.customer.state)  # Sanity check

    def test_create_event(self, stripe):
        """Create event"""
        url = reverse("billing:stripe-webhook")
        payload = {"id": "evt_test", "object": "event", "type": "test"}
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(models.StripeEvent.objects.count(), 1)

    def test_bad_json(self, stripe):
        """Malformed JSON"""
        url = reverse("billing:stripe-webhook")
        payload = "bad json"
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(models.StripeEvent.objects.count(), 0)

    def test_unrecognized_type(self, stripe):
        """Unrecognized event type"""
        url = reverse("billing:stripe-webhook")
        payload = {"id": "evt_test", "object": "event", "type": "bad.type"}
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            models.StripeEvent.Status.ERROR, models.StripeEvent.objects.first().status
        )

    def test_webhook_renewed(self, stripe):
        """A renewal was successfully processed for the next billing cycle"""
        # https://stripe.com/docs/billing/subscriptions/webhooks#tracking
        # Listen to an invoice webhook
        url = reverse("billing:stripe-webhook")
        mock_period_end = timezone.now() + timedelta(days=30)
        payload = {
            "id": "evt_test",
            "object": "event",
            "type": "invoice.paid",
            "data": {
                "object": {
                    # See https://stackoverflow.com/questions/22601521/stripe-webhook-events-renewal-of-subscription
                    # for why we need the billing_reason.
                    "billing_reason": "subscription_cycle",
                    "subscription": "sub",
                    "lines": {
                        "data": [{"period": {"end": mock_period_end.timestamp()}}]
                    },
                }
            },
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        self.customer.refresh_from_db()
        self.assertEqual(
            models.StripeEvent.Status.PROCESSED,
            models.StripeEvent.objects.first().status,
        )
        self.assertEqual(self.customer.current_period_end, mock_period_end)
        self.assertEqual("paid.paying", self.customer.state)

    def test_webhook_payment_failure(self, stripe):
        """A renewal payment failed"""
        # https://stripe.com/docs/billing/subscriptions/webhooks#payment-failures
        # https://stripe.com/docs/billing/subscriptions/overview#build-your-own-handling-for-recurring-charge-failures
        # Listen to customer.subscription.updated. status=past_due
        url = reverse("billing:stripe-webhook")
        payload = {
            "id": "evt_test",
            "object": "event",
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub", "status": "past_due"}},
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            models.StripeEvent.Status.PROCESSED,
            models.StripeEvent.objects.first().status,
        )
        self.assertEqual(self.customer.current_period_end, self.current_period_end)
        self.customer.refresh_from_db()
        self.assertEqual(
            self.customer.payment_state,
            models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD,
        )
        self.assertEqual("paid.past_due.requires_payment_method", self.customer.state)

    def test_webhook_payment_failure_permanent(self, stripe):
        """Renewal payment has permanently failed"""
        # Listen to customer.subscription.updated. status=canceled
        url = reverse("billing:stripe-webhook")
        payload = {
            "id": "evt_test",
            "object": "event",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub", "status": "canceled"}},
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            models.StripeEvent.Status.PROCESSED,
            models.StripeEvent.objects.first().status,
        )
        self.assertEqual(self.customer.current_period_end, self.current_period_end)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.payment_state, models.Customer.PaymentState.OFF)
        # Since the subscription doesn't expire for a couple days, it will be in a paid.canceled state.
        self.assertEqual("paid.canceled", self.customer.state)
        # If the current_period_end is in the past, it should be in a free_default.canceled state.
        self.customer.current_period_end = factories.fake.past_datetime(
            "-1d", tzinfo=timezone.utc
        )
        self.customer.save()
        self.assertEqual("free_default.canceled", self.customer.state)

    def test_webhook_incomplete_expired(self, stripe):
        """An initial payment failure not cured for 23 hours will cancel the subscription"""
        # Listen to customer.subscription.updated. status=incomplete_expired

        # The Customer has to be in the incomplete signup state.
        self.customer.current_period_end = None
        self.customer.payment_state = (
            models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD
        )
        self.customer.save()
        self.assertEqual(
            "free_default.incomplete.requires_payment_method", self.customer.state
        )

        url = reverse("billing:stripe-webhook")
        payload = {
            "id": "evt_test",
            "object": "event",
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub", "status": "incomplete_expired"}},
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            models.StripeEvent.Status.PROCESSED,
            models.StripeEvent.objects.first().status,
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.payment_state, models.Customer.PaymentState.OFF)
        self.assertEqual("free_default.canceled.incomplete", self.customer.state)

    def test_webhook_payment_method_automatically_updated(self, stripe):
        """A network can update a user's credit card automatically"""
        # Listen to payment_method.automatically_updated.
        # See https://stripe.com/docs/saving-cards#automatic-card-updates
        url = reverse("billing:stripe-webhook")
        new_card = {"brand": "amex", "exp_month": 8, "exp_year": 2021, "last4": 1234}
        payload = {
            "id": "evt_test",
            "object": "event",
            "type": "payment_method.automatically_updated",
            "data": {
                "object": {"customer": self.customer.customer_id, "card": new_card}
            },
        }
        response = self.client.post(url, payload)
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            models.StripeEvent.Status.PROCESSED,
            models.StripeEvent.objects.first().status,
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.payment_state, models.Customer.PaymentState.OK)
        self.assertEqual(new_card, self.customer.cc_info)
        self.assertEqual("paid.paying", self.customer.state)
