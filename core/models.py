from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg

MONEY = dict(max_digits=10, decimal_places=2)


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True)
    reg_number = models.CharField("GZU registration number", max_length=30, blank=True)
    hall = models.CharField("Hall / residence", max_length=60, blank=True)
    balance = models.DecimalField(default=Decimal("0.00"), **MONEY)

    def __str__(self):
        return f"{self.user.get_username()} ({self.balance})"


class Vendor(models.Model):
    FOOD, PRINT, LAUNDRY = "food", "print", "laundry"
    CATEGORIES = [(FOOD, "Food"), (PRINT, "Print & copy"), (LAUNDRY, "Laundry")]

    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="vendors",
                              help_text="The user who manages orders and receives payouts.")
    name = models.CharField(max_length=80)
    category = models.CharField(max_length=10, choices=CATEGORIES)
    location = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    # print pricing (per page)
    bw_price = models.DecimalField("B&W price per page", default=Decimal("0.05"), **MONEY)
    colour_price = models.DecimalField("Colour price per page", default=Decimal("0.20"), **MONEY)
    # laundry pricing (per bag)
    small_bag_price = models.DecimalField(default=Decimal("2.00"), **MONEY)
    large_bag_price = models.DecimalField(default=Decimal("3.50"), **MONEY)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def avg_rating(self):
        return Rating.objects.filter(order__vendor=self).aggregate(a=Avg("stars"))["a"]


class MenuItem(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=80)
    price = models.DecimalField(validators=[MinValueValidator(Decimal("0.01"))], **MONEY)
    available = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.vendor})"


class TutorProfile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tutor_profiles")
    module_code = models.CharField(max_length=12)
    module_name = models.CharField(max_length=80)
    hourly_rate = models.DecimalField(validators=[MinValueValidator(Decimal("0.50"))], **MONEY)
    bio = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["module_code", "user__first_name"]

    def __str__(self):
        return f"{self.module_code} - {self.user.get_short_name() or self.user.get_username()}"

    @property
    def avg_rating(self):
        return Rating.objects.filter(order__tutor=self).aggregate(a=Avg("stars"))["a"]


class Order(models.Model):
    FOOD, PRINT, LAUNDRY, TUTOR = "food", "print", "laundry", "tutor"
    KINDS = [(FOOD, "Food"), (PRINT, "Print"), (LAUNDRY, "Laundry"), (TUTOR, "Tutoring")]

    # stage 0 = awaiting payment, 1..4 = progress, -1 = cancelled
    AWAITING, CONFIRMED, IN_PROGRESS, READY, COMPLETED, CANCELLED = 0, 1, 2, 3, 4, -1
    STAGE_LABELS = {
        FOOD: {0: "Awaiting payment", 1: "Confirmed", 2: "Preparing", 3: "On the way", 4: "Delivered", -1: "Cancelled"},
        PRINT: {0: "Awaiting payment", 1: "Confirmed", 2: "Printing", 3: "Ready to collect", 4: "Collected", -1: "Cancelled"},
        LAUNDRY: {0: "Awaiting payment", 1: "Confirmed", 2: "Picked up", 3: "Washing", 4: "Delivered", -1: "Cancelled"},
        TUTOR: {0: "Awaiting payment", 1: "Confirmed", 2: "Tutor ready", 3: "In session", 4: "Completed", -1: "Cancelled"},
    }

    customer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders")
    kind = models.CharField(max_length=10, choices=KINDS)
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    tutor = models.ForeignKey(TutorProfile, null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    provider = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="jobs",
                                 help_text="Who manages this order and gets paid.")
    title = models.CharField(max_length=140)
    detail = models.TextField(blank=True)
    amount = models.DecimalField(validators=[MinValueValidator(Decimal("0.01"))], **MONEY)
    commission = models.DecimalField(default=Decimal("0.00"), **MONEY)
    stage = models.SmallIntegerField(default=0)
    document = models.FileField(upload_to="print_files/%Y/%m/", blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"#{self.pk} {self.title}"

    @property
    def vendor_amount(self):
        return self.amount - self.commission

    @property
    def stage_label(self):
        return self.STAGE_LABELS[self.kind][self.stage]

    @property
    def next_label(self):
        return self.STAGE_LABELS[self.kind].get(self.stage + 1) if 1 <= self.stage < 4 else None

    @property
    def stage_steps(self):
        labels = self.STAGE_LABELS[self.kind]
        return [(labels[i], i <= self.stage) for i in range(1, 5)]

    @property
    def is_open(self):
        return 1 <= self.stage < 4

    @property
    def can_cancel(self):
        return self.stage == self.CONFIRMED


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    name = models.CharField(max_length=80)
    unit_price = models.DecimalField(**MONEY)
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(20)])

    @property
    def total(self):
        return self.unit_price * self.quantity


class Rating(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="rating")
    stars = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True)


class WalletTransaction(models.Model):
    TOPUP, PAYMENT, REFUND, PAYOUT = "topup", "payment", "refund", "payout"
    KINDS = [(TOPUP, "Top-up"), (PAYMENT, "Payment"), (REFUND, "Refund"), (PAYOUT, "Payout")]

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=KINDS)
    amount = models.DecimalField(help_text="Signed: positive credits the wallet, negative debits it.", **MONEY)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    reference = models.CharField(max_length=40, blank=True)
    balance_after = models.DecimalField(**MONEY)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]


class PaymentIntent(models.Model):
    """A wallet top-up through a payment gateway. Credited at most once."""
    CREATED, PENDING, PAID, FAILED = "created", "pending", "paid", "failed"

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payment_intents")
    amount = models.DecimalField(**MONEY)
    phone = models.CharField(max_length=20)
    method = models.CharField(max_length=10)
    reference = models.CharField(max_length=40, unique=True)
    poll_url = models.URLField(max_length=300, blank=True)
    instructions = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, default=CREATED)
    paynow_reference = models.CharField(max_length=40, blank=True)
    error = models.CharField(max_length=200, blank=True)
    credited = models.BooleanField(default=False)
    last_polled = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    text = models.CharField(max_length=300)
    url = models.CharField(max_length=200, blank=True)
    read = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]


from django.db.models.signals import post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    """Every account (including ones made in the admin) gets a wallet profile."""
    if created:
        Profile.objects.get_or_create(user=instance)
