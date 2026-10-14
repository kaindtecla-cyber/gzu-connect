import logging
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import paynow, services
from .forms import FoodForm, LaundryForm, PrintForm, RatingForm, RegisterForm, TopUpForm, TutorBookForm
from .models import Notification, Order, PaymentIntent, Profile, TutorProfile, Vendor, WalletTransaction


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome to GZU Connect! Top up your wallet to start booking.")
        return redirect("home")
    return render(request, "registration/register.html", {"form": form})


@login_required
def home(request):
    Profile.objects.get_or_create(user=request.user)
    active = request.user.orders.filter(stage__gte=1, stage__lt=4)[:5]
    return render(request, "core/home.html", {"active": active})


# ---------- food ----------
@login_required
def food_list(request):
    vendors = Vendor.objects.filter(category=Vendor.FOOD, active=True)
    return render(request, "core/food_list.html", {"vendors": vendors})


@login_required
def food_detail(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk, category=Vendor.FOOD, active=True)
    form = FoodForm(vendor, request.POST or None, initial={"location": request.user.profile.hall})
    if request.method == "POST" and form.is_valid():
        try:
            order = services.place_food_order(request.user, vendor, form.quantities(), form.cleaned_data["location"])
            return redirect("order_pay", pk=order.pk)
        except services.BusinessError as e:
            messages.error(request, str(e))
    return render(request, "core/food_detail.html", {"vendor": vendor, "form": form})


# ---------- print & laundry ----------
@login_required
def print_order(request):
    form = PrintForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            order = services.place_print_order(request.user, d["vendor"], d["document"], d["pages"], d["copies"],
                                               d["colour"], form.slot())
            return redirect("order_pay", pk=order.pk)
        except services.BusinessError as e:
            messages.error(request, str(e))
    return render(request, "core/print.html", {"form": form})


@login_required
def laundry_order(request):
    form = LaundryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            order = services.place_laundry_order(request.user, d["vendor"], d["bag"], form.slot(), d["pickup_from"])
            return redirect("order_pay", pk=order.pk)
        except services.BusinessError as e:
            messages.error(request, str(e))
    return render(request, "core/laundry.html", {"form": form})


# ---------- tutoring ----------
@login_required
def tutor_list(request):
    q = request.GET.get("q", "").strip()
    tutors = TutorProfile.objects.filter(active=True).select_related("user")
    if q:
        tutors = tutors.filter(Q(module_code__icontains=q) | Q(module_name__icontains=q) | Q(user__first_name__icontains=q))
    return render(request, "core/tutors.html", {"tutors": tutors, "q": q})


@login_required
def tutor_book(request, pk):
    tutor = get_object_or_404(TutorProfile, pk=pk, active=True)
    form = TutorBookForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            order = services.place_tutor_order(request.user, tutor, form.cleaned_data["hours"], form.slot())
            return redirect("order_pay", pk=order.pk)
        except services.BusinessError as e:
            messages.error(request, str(e))
    return render(request, "core/tutor_book.html", {"tutor": tutor, "form": form})


# ---------- orders ----------
def _my_order(request, pk):
    return get_object_or_404(Order.objects.select_related("vendor", "tutor", "provider"), pk=pk, customer=request.user)


@login_required
def order_pay(request, pk):
    order = _my_order(request, pk)
    if order.stage != Order.AWAITING:
        return redirect("order_detail", pk=order.pk)
    if request.method == "POST":
        try:
            services.pay_order(order.pk, request.user)
            messages.success(request, "Payment received. Your booking is confirmed!")
            return redirect("order_detail", pk=order.pk)
        except services.BusinessError as e:
            messages.error(request, str(e))
    return render(request, "core/order_pay.html", {"order": order})


@login_required
def order_list(request):
    return render(request, "core/orders.html", {"orders": request.user.orders.exclude(stage=Order.AWAITING)})


@login_required
def order_detail(request, pk):
    order = _my_order(request, pk)
    rating_form = None
    if order.stage == Order.COMPLETED and not hasattr(order, "rating"):
        rating_form = RatingForm()
    return render(request, "core/order_detail.html", {"order": order, "rating_form": rating_form})


@login_required
@require_POST
def order_cancel(request, pk):
    try:
        order = services.cancel_order(_my_order(request, pk).pk, request.user)
        messages.success(request, f"Order cancelled. ${order.amount} was refunded to your wallet.")
    except services.BusinessError as e:
        messages.error(request, str(e))
    return redirect("order_detail", pk=pk)


@login_required
@require_POST
def order_rate(request, pk):
    order = _my_order(request, pk)
    if order.stage != Order.COMPLETED or hasattr(order, "rating"):
        messages.error(request, "You can only rate a completed order once.")
        return redirect("order_detail", pk=pk)
    form = RatingForm(request.POST)
    if form.is_valid():
        rating = form.save(commit=False)
        rating.order = order
        rating.save()
        messages.success(request, "Thanks for rating!")
    else:
        messages.error(request, "Choose a star rating from 1 to 5.")
    return redirect("order_detail", pk=pk)


@login_required
def order_document(request, pk):
    """Print files are private: only the customer and the print vendor can download them."""
    order = get_object_or_404(Order, pk=pk)
    if request.user.id not in (order.customer_id, order.provider_id) and not request.user.is_superuser:
        raise Http404
    if not order.document:
        raise Http404
    return FileResponse(order.document.open("rb"), as_attachment=True, filename=os.path.basename(order.document.name))


# ---------- wallet ----------
def _abs(request, name, **kwargs):
    path = reverse(name, kwargs=kwargs or None)
    return (settings.SITE_URL.rstrip("/") + path) if settings.SITE_URL else request.build_absolute_uri(path)


@login_required
def wallet_view(request):
    paynow_mode = settings.PAYMENTS_MODE == "paynow"
    form = TopUpForm(request.POST or None, initial={"phone": request.user.profile.phone})
    if request.method == "POST" and form.is_valid():
        try:
            intent, tx = services.start_topup(request.user, form.cleaned_data["amount"], form.cleaned_data["phone"],
                                              _abs(request, "paynow_result"), _abs(request, "wallet"))
            if intent:
                return redirect("topup_wait", reference=intent.reference)
            messages.success(request, f"Topped up ${tx.amount}. Reference {tx.reference} (simulated, no real money).")
            return redirect("wallet")
        except services.BusinessError as e:
            messages.error(request, str(e))
    return render(request, "core/wallet.html", {"form": form, "paynow_mode": paynow_mode,
                                                "transactions": request.user.transactions.all()[:30]})


@login_required
def topup_wait(request, reference):
    intent = get_object_or_404(PaymentIntent, reference=reference, user=request.user)
    return render(request, "core/topup_wait.html", {"intent": intent, "back_url": reverse("wallet")})


@login_required
def topup_status(request, reference):
    intent = get_object_or_404(PaymentIntent, reference=reference, user=request.user)
    intent = services.refresh_intent(intent)
    return JsonResponse({"status": intent.status, "error": intent.error})


@csrf_exempt
@require_POST
def paynow_result(request):
    """Paynow calls this when a payment changes status. The signature is checked before anything is trusted."""
    if settings.PAYMENTS_MODE != "paynow":
        raise Http404
    pairs = paynow.parse(request.body.decode("utf-8", "replace"))
    if not paynow.verify(pairs, settings.PAYNOW_KEY):
        logging.getLogger(__name__).warning("Paynow webhook with bad hash rejected")
        return HttpResponseBadRequest("bad hash")
    fields = {k.lower(): v for k, v in pairs}
    intent = PaymentIntent.objects.filter(reference=fields.get("reference", "")).first()
    if intent:
        services.settle_intent(intent.pk, fields.get("status"), fields.get("amount"), fields.get("paynowreference", ""))
    return HttpResponse("OK")


# ---------- notifications ----------
@login_required
def notifications_view(request):
    items = list(request.user.notifications.all()[:50])
    unread_ids = [n.pk for n in items if not n.read]
    response = render(request, "core/notifications.html", {"items": items, "unread_ids": set(unread_ids), "nav": "alerts"})
    if unread_ids:
        Notification.objects.filter(pk__in=unread_ids, user=request.user).update(read=True)
    return response


# ---------- vendor / tutor dashboard ----------
@login_required
def jobs(request):
    base = Order.objects.filter(provider=request.user).exclude(stage__in=[Order.AWAITING])
    return render(request, "core/jobs.html", {
        "open_jobs": base.filter(stage__gte=1, stage__lt=4).select_related("customer"),
        "done_jobs": base.filter(Q(stage=4) | Q(stage=-1)).select_related("customer")[:20],
    })


@login_required
@require_POST
def job_advance(request, pk):
    try:
        order = services.advance_order(pk, request.user)
        messages.success(request, f"Order #{order.pk} is now: {order.stage_label}.")
    except services.BusinessError as e:
        messages.error(request, str(e))
    except Order.DoesNotExist:
        raise Http404
    return redirect("jobs")
