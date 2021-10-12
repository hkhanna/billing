import logging
from urllib.parse import urlparse
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
import stripe

from .. import models, settings, services

User = get_user_model()
logger = logging.getLogger(__name__)

for setting in ("CHECKOUT_SUCCESS_URL", "CHECKOUT_CANCEL_URL", "PORTAL_RETURN_URL"):
    missing = []
    if getattr(settings, setting) is None:
        missing.append(setting)
    if len(missing) > 0:
        missing = ", ".join(missing)
        raise ImproperlyConfigured(
            f"Checkout views need {missing} settings configured."
        )


class CreateCheckoutSessionView(LoginRequiredMixin, View):
    def post(self, request):
        # Redirect to cancel url if no price id or if price id not in Plan
        plan = models.Plan.objects.filter(
            id=request.POST.get("plan_id", None), type=models.Plan.Type.PAID_PUBLIC
        ).first()
        if not plan:
            logger.error(
                f"In CreateCheckoutSessionView, invalid plan_id provided: {request.POST.get('plan_id', None)}"
            )
            messages.error(request, "Invalid billing plan.")
            return redirect(settings.CHECKOUT_CANCEL_URL)

        # User must not have an active billing plan
        # If a user is trying to switch between paid plans, this is the wrong endpoint.
        customer = request.user.customer
        if customer.state not in ("free_default.new", "free_default.canceled"):
            logger.error(
                f"User.id={request.user.id} attempted to create a checkout session while having an active billing plan."
            )
            messages.error(request, "User already has a subscription.")
            return redirect(settings.CHECKOUT_CANCEL_URL)

        success_url = reverse("billing_checkout:checkout_success")
        success_url = f"{request.scheme}://{request.get_host()}{success_url}"
        success_url += "?session_id={CHECKOUT_SESSION_ID}"

        # If it's not an absolute URL, make it one.
        cancel_url = settings.CHECKOUT_CANCEL_URL
        if not urlparse(cancel_url).netloc:
            cancel_url = f"{request.scheme}://{request.get_host()}{cancel_url}"

        # Send either customer_id or customer_email (Stripe does not allow both)
        if customer.customer_id:
            customer_email = None
        else:
            customer_email = request.user.email

        # Create Session if all is well.
        session = stripe.checkout.Session.create(
            success_url=success_url,
            cancel_url=cancel_url,
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": plan.price_id, "quantity": 1}],
            client_reference_id=request.user.pk,
            # Only one of customer or customer_email may be provided
            customer=customer.customer_id,
            customer_email=customer_email,
        )
        return redirect(session.url, permanent=False)


class CheckoutSuccessView(LoginRequiredMixin, View):
    def get(self, request):
        session_id = request.GET.get("session_id")
        if not session_id:
            messages.error(request, "No session id provided.")
            return redirect(settings.CHECKOUT_CANCEL_URL)

        try:
            session = stripe.checkout.Session.retrieve(session_id, expand=["customer"])
        except stripe.error.InvalidRequestError as e:
            messages.error(request, "Invalid session id provided.")
            return redirect(settings.CHECKOUT_CANCEL_URL)

        # Gut check the client_reference_id is correct and customer id is expected.
        if str(session.client_reference_id) != str(request.user.pk):
            msg = f"User.id={request.user.id} does not match session.client_reference_id={session.client_reference_id}"
            logger.error(msg)
            messages.error(
                request,
                "There was a problem processing your request. Please try again later.",
            )
            return redirect(settings.CHECKOUT_CANCEL_URL)

        customer = request.user.customer
        if customer.customer_id and (session.customer.id != customer.customer_id):
            msg = f"customer_id={customer.customer_id} on user.customer does not match session.customer.id={session.customer.id}"
            logger.error(msg)
            messages.error(
                request,
                "There was a problem processing your request. Please try again later.",
            )
            return redirect(settings.CHECKOUT_CANCEL_URL)

        # If users change their email on the checkout page, this will change it back
        # on the Stripe Customer.
        services.stripe_customer_sync_metadata_email(request.user, session.customer.id)
        messages.success(request, "Successfully subscribed!")

        return redirect(settings.CHECKOUT_SUCCESS_URL)


class CreatePortalView(LoginRequiredMixin, View):
    def post(self, request):

        # If it's not an absolute URL, make it one.
        return_url = settings.PORTAL_RETURN_URL
        if not urlparse(return_url).netloc:
            return_url = f"{request.scheme}://{request.get_host()}{return_url}"

        customer_id = request.user.customer.customer_id

        # TODO make sure user should be able to access the portal

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )

        return redirect(session.url, permanent=False)
