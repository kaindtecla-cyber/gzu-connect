"""Business logic: wallet, order placement and the order lifecycle.

Every money movement happens inside a database transaction with the wallet row locked,
so balances cannot go negative or be double-spent by two requests arriving together.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction

from django.utils import timezone

from . import notifications, paynow
from .models import Order, OrderItem, PaymentIntent, Profile, WalletTransaction

CENT = Decimal("0.01")


class BusinessError(Exception):
    """A rule was broken; the message is safe to show to the user."""


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def commission_for(amount):
    return money(Decimal(amount) * Decimal(settings.COMMISSION_RATE))


# ---------- wallet ----------
def _move(user, amount, kind, order=None, reference=""):
    """Apply a signed amount to the user's wallet. Must be called inside transaction.atomic()."""
    profile = Profile.objects.select_for_update().get(user=user)
    new_balance = profile.balance + amount
    if new_balance < 0:
        raise BusinessError("Not enough balance. Top up your wallet first.")
    profile.balance = new_balance
    profile.save(update_fields=["balance"])
    return WalletTransaction.objects.create(
        user=user, kind=kind, amount=amount, order=order, reference=reference, balance_after=new_balance)


def top_up(user, amount):
    amount = money(amount)
    if amount <= 0 or amount > Decimal(settings.MAX_TOPUP):
        raise BusinessError(f"Top-up must be between $0.01 and ${settings.MAX_TOPUP}.")
    with transaction.atomic():
        # Simulated EcoCash. Replace this reference with the payment gateway's confirmation.
        return _move(user, amount, WalletTransaction.TOPUP, reference="SIM-" + uuid.uuid4().hex[:8].upper())


# ---------- gateway top-ups (Paynow EcoCash / OneMoney) ----------
PAID_STATES = {"paid", "awaiting delivery", "delivered"}
FAILED_STATES = {"cancelled", "failed", "disputed", "refunded"}
POLL_EVERY_SECONDS = 3


def method_for_phone(phone):
    if phone.startswith(("077", "078")):
        return "ecocash"
    if phone.startswith("071"):
        return "onemoney"
    raise BusinessError("Use an EcoCash (077/078) or OneMoney (071) number.")


def start_topup(user, amount, phone, result_url, return_url):
    """Simulated mode credits instantly. Paynow mode returns a pending PaymentIntent to wait on."""
    amount = money(amount)
    if settings.PAYMENTS_MODE != "paynow":
        return None, top_up(user, amount)
    if amount <= 0 or amount > Decimal(settings.MAX_TOPUP):
        raise BusinessError(f"Top-up must be between $0.01 and ${settings.MAX_TOPUP}.")
    method = method_for_phone(phone)
    intent = PaymentIntent.objects.create(user=user, amount=amount, phone=phone, method=method,
                                          reference="GZUC-" + uuid.uuid4().hex[:12].upper())
    try:
        reply = paynow.initiate_mobile(intent.reference, amount, phone, method, settings.PAYNOW_AUTH_EMAIL,
                                       result_url, return_url)
    except paynow.PaynowError as exc:
        intent.status, intent.error = PaymentIntent.FAILED, str(exc)[:200]
        intent.save(update_fields=["status", "error"])
        raise BusinessError(f"Could not start the payment: {exc}")
    intent.poll_url, intent.instructions = reply["poll_url"], reply["instructions"][:300]
    intent.paynow_reference = reply["paynow_reference"][:40]
    intent.status = PaymentIntent.PENDING
    intent.save(update_fields=["poll_url", "instructions", "paynow_reference", "status"])
    return intent, None


def settle_intent(intent_id, gateway_status, gateway_amount=None, gateway_ref=""):
    """Apply a verified payment status. Safe to call many times: money is credited at most once."""
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(pk=intent_id)
        if intent.credited:
            return intent
        state = (gateway_status or "").strip().lower()
        if state in PAID_STATES:
            if gateway_amount not in (None, "") and money(gateway_amount) != intent.amount:
                intent.status, intent.error = PaymentIntent.FAILED, "Amount mismatch - not credited."
            else:
                _move(intent.user, intent.amount, WalletTransaction.TOPUP, reference=intent.reference)
                intent.status, intent.credited = PaymentIntent.PAID, True
                intent.paynow_reference = (gateway_ref or intent.paynow_reference)[:40]
        elif state in FAILED_STATES:
            intent.status = PaymentIntent.FAILED
        intent.save()
        return intent


def refresh_intent(intent):
    """Ask Paynow for the latest status (at most every few seconds) and settle it."""
    if intent.credited or intent.status != PaymentIntent.PENDING or not intent.poll_url:
        return intent
    now = timezone.now()
    if intent.last_polled and (now - intent.last_polled).total_seconds() < POLL_EVERY_SECONDS:
        return intent
    PaymentIntent.objects.filter(pk=intent.pk).update(last_polled=now)
    try:
        reply = paynow.poll(intent.poll_url)
    except paynow.PaynowError:
        return intent
    return settle_intent(intent.pk, reply.get("status"), reply.get("amount"), reply.get("paynowreference", ""))


# ---------- placing orders (created unpaid; the customer pays on the next screen) ----------
def _new_order(customer, kind, title, detail, amount, vendor=None, tutor=None, provider=None, document=None):
    amount = money(amount)
    if amount <= 0:
        raise BusinessError("The order total must be more than zero.")
    order = Order(customer=customer, kind=kind, vendor=vendor, tutor=tutor, provider=provider,
                  title=title[:140], detail=detail, amount=amount)
    if document is not None:
        order.document = document
    order.save()
    return order


def place_food_order(customer, vendor, quantities, location):
    """quantities: {MenuItem: qty}. Prices are read from the database, never from the browser."""
    lines = [(item, qty) for item, qty in quantities.items() if qty > 0]
    if not lines:
        raise BusinessError("Add at least one item.")
    with transaction.atomic():
        total = sum(item.price * qty for item, qty in lines)
        order = _new_order(customer, Order.FOOD, f"Food from {vendor.name}", f"Deliver to: {location}",
                           total, vendor=vendor, provider=vendor.owner)
        OrderItem.objects.bulk_create([
            OrderItem(order=order, name=item.name, unit_price=item.price, quantity=qty) for item, qty in lines])
    return order


def place_print_order(customer, vendor, document, pages, copies, colour, slot):
    rate = vendor.colour_price if colour else vendor.bw_price
    detail = f"{pages} pages x {copies} copies, {'colour' if colour else 'black & white'}. Collect: {slot} at {vendor.name}"
    return _new_order(customer, Order.PRINT, f"Print: {document.name.rsplit('/', 1)[-1]}", detail,
                      rate * pages * copies, vendor=vendor, provider=vendor.owner, document=document)


def place_laundry_order(customer, vendor, bag, slot, pickup_from):
    price = vendor.large_bag_price if bag == "large" else vendor.small_bag_price
    return _new_order(customer, Order.LAUNDRY, f"Laundry: {bag} bag with {vendor.name}",
                      f"Pickup: {slot} from {pickup_from}", price, vendor=vendor, provider=vendor.owner)


def place_tutor_order(customer, tutor, hours, slot):
    if tutor.user_id == customer.id:
        raise BusinessError("You cannot book yourself as a tutor.")
    return _new_order(customer, Order.TUTOR, f"Tutoring: {tutor.module_code} with {tutor.user.get_short_name() or tutor.user.get_username()}",
                      f"{hours} hour(s). Session: {slot}", tutor.hourly_rate * hours, tutor=tutor, provider=tutor.user)


# ---------- order lifecycle ----------
def _safely(fn, *args):
    """Notifications must never break a booking."""
    try:
        fn(*args)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Notification failed")


def _locked(order_id):
    return Order.objects.select_for_update().get(pk=order_id)


def pay_order(order_id, user):
    with transaction.atomic():
        order = _locked(order_id)
        if order.customer_id != user.id:
            raise BusinessError("This order is not yours.")
        if order.stage != Order.AWAITING:
            raise BusinessError("This order has already been paid or closed.")
        _move(user, -order.amount, WalletTransaction.PAYMENT, order=order)
        order.commission = commission_for(order.amount)
        order.stage = Order.CONFIRMED
        order.save(update_fields=["commission", "stage", "updated"])
        transaction.on_commit(lambda: _safely(notifications.order_paid, order.pk))
        return order


def advance_order(order_id, actor):
    """Provider moves the order forward. Payment is released to the provider on the final step."""
    with transaction.atomic():
        order = _locked(order_id)
        if not (actor.is_superuser or order.provider_id == actor.id):
            raise BusinessError("Only the vendor or tutor for this order can update it.")
        if not order.is_open:
            raise BusinessError("This order can no longer be updated.")
        order.stage += 1
        order.save(update_fields=["stage", "updated"])
        if order.stage == Order.COMPLETED and order.provider_id:
            _move(order.provider, order.vendor_amount, WalletTransaction.PAYOUT, order=order)
        transaction.on_commit(lambda: _safely(notifications.order_advanced, order.pk))
        return order


def cancel_order(order_id, actor):
    """Customer or provider may cancel before work starts; the customer is refunded in full."""
    with transaction.atomic():
        order = _locked(order_id)
        if not (actor.id in (order.customer_id, order.provider_id) or actor.is_superuser):
            raise BusinessError("You cannot cancel this order.")
        if not order.can_cancel:
            raise BusinessError("Orders can only be cancelled before work has started.")
        _move(order.customer, order.amount, WalletTransaction.REFUND, order=order)
        order.stage = Order.CANCELLED
        order.commission = Decimal("0.00")
        order.save(update_fields=["stage", "commission", "updated"])
        transaction.on_commit(lambda: _safely(notifications.order_cancelled, order.pk, actor.id))
        return order
