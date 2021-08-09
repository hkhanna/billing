from datetime import datetime as dt
import logging
import traceback

from django.utils import timezone

from . import models

try:
    from celery.utils.log import get_task_logger

    logger = get_task_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)


def process_stripe_event(event_id):
    """Handler for Stripe Events"""
    logger.info(f"StripeEvent.id={event_id} process_stripe_event task started")
    event = models.StripeEvent.objects.get(pk=event_id)
    try:
        event.status = models.StripeEvent.Status.PENDING
        event.save()
        # Successful renewal webhook
        if event.type == "invoice.paid":
            invoice = event.payload["data"]["object"]

            # billing_reason=subscription_cycle means its a renewal, not a new subscription.
            # See https://stackoverflow.com/questions/22601521/stripe-webhook-events-renewal-of-subscription
            if invoice["billing_reason"] == "subscription_cycle":
                logger.info(
                    f"StripeEvent.id={event_id} StripeEvent.type=invoice.paid processing renewal since billing_reason=subscription_cycle"
                )
                customer = models.Customer.objects.get(
                    subscription_id=invoice["subscription"]
                )
                period_end = dt.fromtimestamp(
                    invoice["lines"]["data"][0]["period"]["end"], tz=timezone.utc
                )
                customer.current_period_end = period_end
                customer.save()
            else:
                logger.info(
                    f"StripeEvent.id={event_id} StripeEvent.type=invoice.paid taking no action because billing_reason is not subscription_cycle"
                )
                event.info = "Subscription creation webhook. No action was taken."
            event.status = models.StripeEvent.Status.PROCESSED

        # Payment failure webhooks
        elif (
            event.type == "customer.subscription.updated"
            or event.type == "customer.subscription.deleted"
        ):
            subscription = event.payload["data"]["object"]
            customer = models.Customer.objects.get(subscription_id=subscription["id"])
            if subscription["status"] == "past_due":
                customer.payment_state = (
                    models.Customer.PaymentState.REQUIRES_PAYMENT_METHOD
                )
                customer.save()
            elif subscription["status"] == "canceled":
                customer.subscription_id = None
                customer.payment_state = models.Customer.PaymentState.OFF
                customer.save()
            elif subscription["status"] == "incomplete_expired":
                if customer.state != "free_default.incomplete.requires_payment_method":
                    logger.error(
                        f"StripeEvent.id={event_id} receiving incomplete_expired on a Customer that does not have the proper state."
                    )
                customer.subscription_id = None
                customer.payment_state = models.Customer.PaymentState.OFF
                customer.save()
            else:
                logger.info(
                    f"StripeEvent.id={event_id} StripeEvent.type=customer.subscription.updated taking no action "
                    f"because status is {subscription['status']} and not actionable"
                )
                event.info = "Payload 'status' is not actionable. No action was taken."
            event.status = models.StripeEvent.Status.PROCESSED

        # Payment method automatically updated by card network
        elif event.type == "payment_method.automatically_updated":
            payment_method = event.payload["data"]["object"]
            customer = models.Customer.objects.get(
                customer_id=payment_method["customer"]
            )
            cc_info = payment_method["card"]
            customer.cc_info = {
                k: cc_info[k]
                for k in cc_info
                if k in ("brand", "last4", "exp_month", "exp_year")
            }
            customer.save()
            event.status = models.StripeEvent.Status.PROCESSED
        else:
            logger.info(
                f"StripeEvent.id={event.id} StripeEvent.type={event.type} StripeEvent type not recognized"
            )
            event.status = models.StripeEvent.Status.ERROR
            event.info = f"StripeEvent type '{event.type}' not recognized."
    except Exception as e:
        logger.exception(f"StripeEvent.id={event.id} in error state")
        event.status = models.StripeEvent.Status.ERROR
        event.info = traceback.format_exc()
    finally:
        logger.debug(f"StripeEvent.id={event.id} Saving StripeEvent")
        event.save()


try:
    from celery import shared_task

    process_stripe_event = shared_task(process_stripe_event)
except ImportError:
    pass
