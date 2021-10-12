from django.contrib.auth import get_user_model
from django.views.generic import RedirectView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin

from billing import models

User = get_user_model()


class IndexView(RedirectView):
    pattern_name = "account_login"


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "example/profile.html"

    @staticmethod
    def state_note(customer):
        """Convenience to avoid doing lots of logic in the template"""
        # TODO: on soft delete, clear some of this out of Customer
        if customer.state == "free_default.new":
            return ""
        elif customer.state == "free_default.canceled":
            return f"Subscription expired on {customer.current_period_end}"
        elif customer.state == "free_default.canceled.incomplete":
            return ""
        elif customer.state == "paid.paying":
            return f"Subscription renews on {customer.current_period_end}."
        elif customer.state == "paid.will_cancel":
            return f"Subscription cancelled. Access available until {customer.current_period_end}."
        elif customer.state == "paid.canceled":
            return f"Subscription will expire on {customer.current_period_end}."
        elif customer.state == "free_private.indefinite":
            return f"Staff plan, no expiration."
        elif customer.state == "free_private.will_expire":
            return f"Staff plan expires on {customer.current_period_end}."
        elif customer.state in (
            "free_default.past_due.requires_payment_method",
            "free_default.incomplete.requires_payment_method",
            "paid.past_due.requires_payment_method",
        ):
            return "There is a problem with your credit card. Please provide a new one or try again."
        else:
            return "There is an issue with your subscription. Please contact support."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        customer = self.request.user.customer
        state = customer.state

        ctx["can_create_subscription"] = state in (
            "free_default.new",
            "free_default.canceled",
            "free_default.canceled.incomplete",
            "paid.canceled",
        )
        if ctx["can_create_subscription"]:
            ctx["plan_id"] = (
                models.Plan.objects.filter(type=models.Plan.Type.PAID_PUBLIC).first().id
            )

        ctx["requires_payment_method"] = state in (
            "free_default.past_due.requires_payment_method",
            "free_default.incomplete.requires_payment_method",
            "paid.past_due.requires_payment_method",
        )

        ctx["can_cancel"] = state == "paid.paying"
        if state == "paid.paying":
            ctx["cc_info"] = customer.cc_info
        ctx["can_reactivate"] = state == "paid.will_cancel"
        ctx["state_note"] = self.state_note(customer)
        ctx[
            "current_plan"
        ] = f"{customer.plan.name} (${customer.plan.display_price}/mo)"
        return ctx
