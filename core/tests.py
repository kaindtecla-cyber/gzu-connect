import datetime
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import MenuItem, Order, Rating, TutorProfile, Vendor, WalletTransaction

TMP_MEDIA = tempfile.mkdtemp()


def make_user(name, balance="0"):
    u = User.objects.create_user(name, password="pw-12345-xyz", first_name=name.title())
    u.profile.balance = Decimal(balance)
    u.profile.save()
    return u


@override_settings(MEDIA_ROOT=TMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class GZUConnectTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.student = make_user("student", "10.00")
        self.other = make_user("other", "10.00")
        self.owner = make_user("owner")
        self.kitchen = Vendor.objects.create(name="Kitchen", category=Vendor.FOOD, owner=self.owner)
        self.sadza = MenuItem.objects.create(vendor=self.kitchen, name="Sadza", price=Decimal("2.50"))
        self.printer = Vendor.objects.create(name="Prints", category=Vendor.PRINT, owner=self.owner)
        self.tutor_user = make_user("tutor")
        self.tutor = TutorProfile.objects.create(user=self.tutor_user, module_code="HCS223",
                                                 module_name="Technopreneurship", hourly_rate=Decimal("1.50"))

    def food_order(self, qty=2):
        return services.place_food_order(self.student, self.kitchen, {self.sadza: qty}, "Hall 1")

    # ---- money rules ----
    def test_commission_rounds_to_cents(self):
        self.assertEqual(services.commission_for(Decimal("3.50")), Decimal("0.42"))
        self.assertEqual(services.commission_for(Decimal("2.50")), Decimal("0.30"))
        self.assertEqual(services.commission_for(Decimal("0.05")), Decimal("0.01"))

    def test_top_up_limits(self):
        for bad in ("0", "-5", "100.01"):
            with self.assertRaises(services.BusinessError):
                services.top_up(self.student, Decimal(bad))
        services.top_up(self.student, Decimal("5"))
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.balance, Decimal("15.00"))

    def test_food_total_comes_from_database_prices(self):
        order = self.food_order(3)
        self.assertEqual(order.amount, Decimal("7.50"))
        with self.assertRaises(services.BusinessError):
            services.place_food_order(self.student, self.kitchen, {self.sadza: 0}, "Hall 1")

    def test_pay_deducts_wallet_and_sets_commission(self):
        order = self.food_order(2)  # $5.00
        services.pay_order(order.pk, self.student)
        order.refresh_from_db()
        self.student.profile.refresh_from_db()
        self.assertEqual(order.stage, Order.CONFIRMED)
        self.assertEqual(order.commission, Decimal("0.60"))
        self.assertEqual(self.student.profile.balance, Decimal("5.00"))

    def test_cannot_pay_twice_or_overspend(self):
        order = self.food_order(2)
        services.pay_order(order.pk, self.student)
        with self.assertRaises(services.BusinessError):
            services.pay_order(order.pk, self.student)
        big = self.food_order(20)  # $50 > balance
        with self.assertRaises(services.BusinessError):
            services.pay_order(big.pk, self.student)
        big.refresh_from_db()
        self.student.profile.refresh_from_db()
        self.assertEqual(big.stage, Order.AWAITING)
        self.assertEqual(self.student.profile.balance, Decimal("5.00"))

    def test_other_users_cannot_pay_someone_elses_order(self):
        order = self.food_order()
        with self.assertRaises(services.BusinessError):
            services.pay_order(order.pk, self.other)

    # ---- lifecycle ----
    def test_provider_advances_and_is_paid_on_completion(self):
        order = self.food_order(2)
        services.pay_order(order.pk, self.student)
        for _ in range(2):
            services.advance_order(order.pk, self.owner)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.balance, Decimal("0.00"))  # not paid until the last step
        services.advance_order(order.pk, self.owner)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.balance, Decimal("4.40"))  # 5.00 - 0.60 commission
        with self.assertRaises(services.BusinessError):
            services.advance_order(order.pk, self.owner)  # already complete: no double payout
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.balance, Decimal("4.40"))

    def test_only_provider_can_advance(self):
        order = self.food_order()
        services.pay_order(order.pk, self.student)
        for who in (self.student, self.other):
            with self.assertRaises(services.BusinessError):
                services.advance_order(order.pk, who)

    def test_cancel_refunds_only_before_work_starts(self):
        order = self.food_order(2)
        services.pay_order(order.pk, self.student)
        services.cancel_order(order.pk, self.student)
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.balance, Decimal("10.00"))
        order.refresh_from_db()
        self.assertEqual((order.stage, order.commission), (Order.CANCELLED, Decimal("0.00")))
        second = self.food_order(1)
        services.pay_order(second.pk, self.student)
        services.advance_order(second.pk, self.owner)
        with self.assertRaises(services.BusinessError):
            services.cancel_order(second.pk, self.student)

    def test_wallet_ledger_matches_balance(self):
        order = self.food_order(2)
        services.pay_order(order.pk, self.student)
        services.cancel_order(order.pk, self.student)
        services.top_up(self.student, Decimal("3"))
        total = sum(t.amount for t in WalletTransaction.objects.filter(user=self.student))
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.balance, Decimal("10.00") + total)

    def test_tutor_cannot_book_self(self):
        with self.assertRaises(services.BusinessError):
            services.place_tutor_order(self.tutor_user, self.tutor, 1, "Mon 9:00am")
        order = services.place_tutor_order(self.student, self.tutor, 2, "Mon 9:00am")
        self.assertEqual((order.amount, order.provider), (Decimal("3.00"), self.tutor_user))

    # ---- web layer ----
    def login(self, user):
        self.client.login(username=user.username, password="pw-12345-xyz")

    def test_pages_require_login(self):
        for name in ("home", "orders", "wallet", "print", "laundry", "food_list", "tutors", "jobs"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/accounts/login/", r["Location"])

    def test_signup_creates_wallet(self):
        r = self.client.post(reverse("register"), {
            "username": "newbie", "first_name": "New", "email": "", "phone": "0771234567",
            "reg_number": "R123", "hall": "Hall 2", "password1": "Str0ng-pass-99x", "password2": "Str0ng-pass-99x"})
        self.assertRedirects(r, reverse("home"))
        u = User.objects.get(username="newbie")
        self.assertEqual((u.profile.balance, u.profile.hall), (Decimal("0.00"), "Hall 2"))

    def test_cannot_view_other_users_order(self):
        order = self.food_order()
        self.login(self.other)
        self.assertEqual(self.client.get(reverse("order_detail", args=[order.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("order_pay", args=[order.pk])).status_code, 404)

    def test_full_food_flow_in_browser_layer(self):
        self.login(self.student)
        r = self.client.post(reverse("food_detail", args=[self.kitchen.pk]), {f"q{self.sadza.pk}": 2, "location": "Hall 1"})
        order = Order.objects.get(customer=self.student)
        self.assertRedirects(r, reverse("order_pay", args=[order.pk]))
        self.client.post(reverse("order_pay", args=[order.pk]))
        self.login(self.owner)
        for _ in range(2):
            self.client.post(reverse("job_advance", args=[order.pk]))
        self.assertIn("Mark: Delivered", self.client.get(reverse("jobs")).content.decode())
        self.client.post(reverse("job_advance", args=[order.pk]))
        self.login(self.student)
        self.client.post(reverse("order_rate", args=[order.pk]), {"stars": 5, "comment": "Hot and fast"})
        self.assertEqual(Rating.objects.get(order=order).stars, 5)
        self.client.post(reverse("order_rate", args=[order.pk]), {"stars": 1})  # second rating ignored
        self.assertEqual(Rating.objects.filter(order=order).count(), 1)
        self.assertEqual(self.kitchen.avg_rating, 5)

    def test_rating_blocked_before_completion(self):
        order = self.food_order()
        services.pay_order(order.pk, self.student)
        self.login(self.student)
        self.client.post(reverse("order_rate", args=[order.pk]), {"stars": 5})
        self.assertFalse(Rating.objects.exists())

    def test_non_provider_cannot_use_job_endpoint(self):
        order = self.food_order()
        services.pay_order(order.pk, self.student)
        self.login(self.other)
        self.client.post(reverse("job_advance", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.stage, Order.CONFIRMED)

    def _print_post(self, upload, **over):
        data = {"vendor": self.printer.pk, "document": upload, "pages": 10, "copies": 2,
                "date": (timezone.localdate() + datetime.timedelta(days=1)).isoformat(), "time": "2:00pm"}
        data.update(over)
        return self.client.post(reverse("print"), data)

    def test_print_upload_rules_and_private_download(self):
        self.login(self.student)
        r = self._print_post(SimpleUploadedFile("evil.exe", b"MZ..."))
        self.assertContains(r, "Upload a PDF")
        r = self._print_post(SimpleUploadedFile("fake.pdf", b"not really a pdf"))
        self.assertContains(r, "does not look like")
        r = self._print_post(SimpleUploadedFile("plan.pdf", b"%PDF-1.4 hello"))
        order = Order.objects.get(kind=Order.PRINT)
        self.assertRedirects(r, reverse("order_pay", args=[order.pk]))
        self.assertEqual(order.amount, Decimal("1.00"))  # 10 pages x 2 copies x $0.05
        url = reverse("order_document", args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 200)       # customer
        self.login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)       # print vendor
        self.login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)       # anyone else

    def test_print_date_must_be_near_future(self):
        self.login(self.student)
        r = self._print_post(SimpleUploadedFile("plan.pdf", b"%PDF-1.4 x"), date="2020-01-01")
        self.assertContains(r, "within the next 14 days")
        self.assertFalse(Order.objects.exists())

    def test_wallet_page_topup(self):
        self.login(self.student)
        self.client.post(reverse("wallet"), {"amount": "5.00"})
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.balance, Decimal("15.00"))
        r = self.client.post(reverse("wallet"), {"amount": "5000"})
        self.assertEqual(self.student.profile.balance, Decimal("15.00"))
