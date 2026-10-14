"""Notifications: in-app alerts for everyone, plus email (and optional SMS) for people who need to act fast.

Sending never breaks a booking: every channel is wrapped so a failure is logged, not raised.
"""
import base64
import logging
import re
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail

from .models import Notification, Order

log = logging.getLogger(__name__)


def to_e164(phone):
    """0771234567 -> +263771234567. Returns '' if it does not look like a Zimbabwe mobile number."""
    digits = re.sub(r"[^\d]", "", phone or "")
    if digits.startswith("263") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+263" + digits[1:]
    return ""


def _send_sms(phone, text):
    if settings.SMS_PROVIDER != "twilio":
        return
    number = to_e164(phone)
    if not number:
        return
    sid, token = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
    data = urllib.parse.urlencode({"To": number, "From": settings.TWILIO_FROM, "Body": text[:300]}).encode()
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    with urllib.request.urlopen(req, timeout=15):  # noqa: S310 (fixed https URL)
        pass


def notify(user, text, url="", email=False, sms=False):
    """Always creates an in-app alert; optionally also emails and/or texts the user."""
    Notification.objects.create(user=user, text=text[:300], url=url)
    if email and user.email:
        try:
            send_mail("GZU Connect", text, None, [user.email], fail_silently=False)
        except Exception:
            log.exception("Email notification failed for user %s", user.pk)
    if sms and getattr(user, "profile", None) and user.profile.phone:
        try:
            _send_sms(user.profile.phone, "GZU Connect: " + text)
        except Exception:
            log.exception("SMS notification failed for user %s", user.pk)


# ---- events (called after the database transaction commits) ----
def order_paid(order_id):
    o = Order.objects.select_related("customer", "provider").get(pk=order_id)
    if o.provider:
        notify(o.provider, f"New paid order #{o.pk}: {o.title} (${o.vendor_amount} to you). {o.detail}",
               url="/jobs/", email=True, sms=True)
    notify(o.customer, f"Payment received for order #{o.pk}. Your booking is confirmed.", url=f"/orders/{o.pk}/")


def order_advanced(order_id):
    o = Order.objects.select_related("customer", "provider").get(pk=order_id)
    notify(o.customer, f"Order #{o.pk} ({o.title}) is now: {o.stage_label}.", url=f"/orders/{o.pk}/", email=True)
    if o.stage == Order.COMPLETED and o.provider:
        notify(o.provider, f"Order #{o.pk} complete. ${o.vendor_amount} was added to your wallet.", url="/wallet/")


def order_cancelled(order_id, by_user_id):
    o = Order.objects.select_related("customer", "provider").get(pk=order_id)
    if o.provider and by_user_id == o.customer_id:
        notify(o.provider, f"Order #{o.pk} was cancelled by the customer.", url="/jobs/", email=True)
    elif by_user_id != o.customer_id:
        notify(o.customer, f"Order #{o.pk} was declined by the provider. ${o.amount} was refunded to your wallet.",
               url="/wallet/", email=True)
