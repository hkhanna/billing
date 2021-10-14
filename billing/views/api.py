import logging
from datetime import datetime as dt
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
import stripe

from .. import serializers, models, services

User = get_user_model()
logger = logging.getLogger(__name__)


class CreateSubscriptionAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = serializers.CreateSubscriptionSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        customer = request.user.customer

        # If the customer doesn't have a Stripe customer_id, create a new Stripe Customer.
        if not customer.customer_id:
            stripe_customer = services.stripe_create_customer(request.user)
            if stripe_customer is None:
                raise ValidationError(
                    "These was a problem connecting to Stripe. Please try again."
                )
            customer.customer_id = stripe_customer.id
            customer.save()

        try:
            subscription = services.stripe_create_subscription(
                customer_id=customer.customer_id,
                payment_method_id=serializer.validated_data["payment_method_id"],
                price_id=serializer.plan.price_id,
            )
        except stripe.error.CardError as e:
            raise ValidationError(e.error.message)

        customer.subscription_id = subscription.id
        customer.plan = serializer.plan
        services.stripe_customer_sync_metadata_email(request.user, customer.customer_id)
        if subscription.status == "active":
            customer.current_period_end = dt.fromtimestamp(
                subscription.current_period_end, tz=timezone.utc
            )
            customer.payment_state = models.Customer.PaymentState.OK
            customer.save()
            return Response(status=201)
        else:
            logger.info(
                f"User.id={request.user.id} payment failed in CreateSubscriptionAPIView"
            )
            customer.current_period_end = None
            customer.payment_state = (
                models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD
            )
            customer.save()
            raise ValidationError(
                "Payment could not be processed. Please try again or use another card."
            )


class CureFailedCardAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if "payment_method_id" not in request.data:
            raise ValidationError("No payment_method_id provided.")

        customer = request.user.customer
        # Make sure there is a subscription and the payment state is set to PAYMENT_REQUIRES_PAYMENT_METHOD
        if (
            customer.subscription_id
            and customer.payment_state
            == models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD
        ):
            try:
                services.stripe_replace_card(
                    customer.customer_id,
                    customer.subscription_id,
                    request.data["payment_method_id"],
                )
                customer.save()
                invoice = services.stripe_retry_latest_invoice(customer.customer_id)
                if invoice["status"] == "paid":
                    customer.current_period_end = dt.fromtimestamp(
                        invoice["lines"]["data"][0]["period"]["end"], tz=timezone.utc
                    )
                    customer.payment_state = models.Customer.PaymentState.OK
                    customer.save()
            except stripe.error.CardError as e:
                # N.B. stripe.Invoice.pay raises a CardError if the payment doesn't go through.
                raise ValidationError(e.error.message)
        else:
            raise ValidationError("You cannot cure a failed payment for this customer.")

        return Response(status=201)


class CancelSubscriptionAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not request.user.customer.cancel_subscription(immediate=False):
            raise ValidationError("No active subscription to cancel.")
        else:
            return Response(status=201)


class ReactivateSubscriptionAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        customer = request.user.customer
        # Make sure there is an active subscription that will be canceled at the end of the period
        if customer.state == "paid.will_cancel":
            services.stripe_reactivate_subscription(customer.subscription_id)
            request.user.customer.payment_state = models.Customer.PaymentState.OK
            request.user.customer.save()
            return Response(status=201)
        else:
            raise ValidationError("You cannot reactivate this subscription.")


class ReplaceCardAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if "payment_method_id" not in request.data:
            raise ValidationError("No payment_method_id provided.")

        customer = request.user.customer
        # Make sure there is an active subscription
        if (
            customer.subscription_id
            and customer.payment_state != models.Customer.PaymentState.OFF
        ):
            try:
                services.stripe_replace_card(
                    customer.customer_id,
                    customer.subscription_id,
                    request.data["payment_method_id"],
                )
            except stripe.error.CardError as e:
                raise ValidationError(e.error.message)
            request.user.customer.save()
            return Response(status=201)
        else:
            raise ValidationError("You cannot replace card for this customer.")
