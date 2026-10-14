import urllib.parse
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from . import notifications, paynow, services
from .models import MenuItem, Notification, Order, PaymentIntent, Vendor, WalletTransaction

KEY = "test-integration-key"
FAST = ["django.contrib.auth.hashers.MD5PasswordHasher"]
PAY = dict(PAYMENTS_MODE="paynow", PAYNOW_ID="1201", PAYNOW_KEY=KEY, PAYNOW_AUTH_EMAIL="me@example.com",
           PASSWORD_HASHERS=FAST)


def make_user(name, balance="0", email="", phone=""):
    u = User.objects.create_user(name, email=email, password="pw-12345-xyz", first_name=name.title())
    u.profile.balance, u.profile.phone = Decimal(balance), phone
    u.profile.save()
    return u


def signed(pairs):
    return urllib.parse.urlencode(list(pairs) + [("hash", paynow.make_hash(pairs, KEY))])


class PaynowProtocolTests(TestCase):
    def test_matches_paynows_published_test_vector(self):
        pairs = [("id", "1201"), ("reference", "TEST REF"), ("amount", "99.99"),
                 ("additionalinfo", "A test ticket transaction"),
                 ("returnurl", "http://www.google.com/search?q=returnurl"),
                 ("resulturl", "http://www.google.com/search?q=resulturl"), ("status", "Message")]
        self.assertEqual(
            paynow.make_hash(pairs, "3e9fed89-60e1-4ce5-ab6e-6b1eb2d4f977"),
            "2A033FC38798D913D42ECB786B9B19645ADEDBDE788862032F1BD82CF3B92DEF84F316385D5B40DBB35F1A4FD7D5BFE73835174136463CDD48C9366B0749C689")

    @override_settings(**PAY)
    def test_verify_rejects_tampering_and_missing_hash(self):
        body = signed([("reference", "R1"), ("amount", "5.00"), ("status", "Paid")])
        self.assertTrue(paynow.verify(paynow.parse(body), KEY))
        self.assertFalse(paynow.verify(paynow.parse(body.replace("5.00", "0.01")), KEY))
        self.assertFalse(paynow.verify(paynow.parse("reference=R1&status=Paid"), KEY))

    @override_settings(**PAY)
    def test_initiate_success_sends_signed_request(self):
        reply = signed([("status", "Ok"), ("pollurl", "https://www.paynow.co.zw/interface/CheckPayment/?guid=abc"),
                        ("instructions", "Dial *151*2*4# to approve")])
        with mock.patch.object(paynow, "_post", return_value=reply) as post:
            out = paynow.initiate_mobile("GZUC-1", Decimal("5"), "0771234567", "ecocash", "me@example.com", "https://x/r", "https://x/b")
        sent = post.call_args[0][1]
        self.assertTrue(paynow.verify(sent, KEY))
        self.assertEqual(dict(sent)["amount"], "5.00")
        self.assertEqual(out["poll_url"], "https://www.paynow.co.zw/interface/CheckPayment/?guid=abc")

    @override_settings(**PAY)
    def test_initiate_error_and_forged_reply(self):
        with mock.patch.object(paynow, "_post", return_value="status=Error&error=Invalid+Integration+ID"):
            with self.assertRaisesMessage(paynow.PaynowError, "Invalid Integration ID"):
                paynow.initiate_mobile("R", Decimal("5"), "0771234567", "ecocash", "a@b.c", "u", "u")
        with mock.patch.object(paynow, "_post", return_value="status=Ok&pollurl=https%3A%2F%2Fevil.example%2F&hash=BAD"):
            with self.assertRaises(paynow.PaynowError):
                paynow.initiate_mobile("R", Decimal("5"), "0771234567", "ecocash", "a@b.c", "u", "u")

    @override_settings(**PAY)
    def test_poll_refuses_foreign_urls(self):
        with self.assertRaises(paynow.PaynowError):
            paynow.poll("https://evil.example/steal")


@override_settings(**PAY)
class TopUpFlowTests(TestCase):
    def setUp(self):
        self.user = make_user("student", "1.00", phone="0771234567")

    def start(self, amount="5.00", phone="0771234567"):
        reply = {"poll_url": "https://www.paynow.co.zw/interface/CheckPayment/?guid=z", "instructions": "Approve", "paynow_reference": "PN1"}
        with mock.patch.object(paynow, "initiate_mobile", return_value=reply):
            return services.start_topup(self.user, Decimal(amount), phone, "https://x/r", "https://x/b")[0]

    def balance(self):
        self.user.profile.refresh_from_db()
        return self.user.profile.balance

    def test_phone_must_be_ecocash_or_onemoney(self):
        self.assertEqual(services.method_for_phone("0771234567"), "ecocash")
        self.assertEqual(services.method_for_phone("0712345678"), "onemoney")
        with self.assertRaises(services.BusinessError):
            services.method_for_phone("0731234567")

    def test_pending_intent_credits_nothing_until_paid(self):
        intent = self.start()
        self.assertEqual((intent.status, intent.method, self.balance()), ("pending", "ecocash", Decimal("1.00")))

    def test_paid_credits_exactly_once(self):
        intent = self.start()
        for _ in range(3):  # webhook + polling + a retry can all arrive
            services.settle_intent(intent.pk, "Paid", "5.00", "PN1")
        self.assertEqual(self.balance(), Decimal("6.00"))
        self.assertEqual(WalletTransaction.objects.filter(kind="topup", reference=intent.reference).count(), 1)

    def test_amount_mismatch_is_not_credited(self):
        intent = self.start()
        services.settle_intent(intent.pk, "Paid", "0.50", "PN1")
        intent.refresh_from_db()
        self.assertEqual((intent.status, self.balance()), ("failed", Decimal("1.00")))

    def test_cancelled_never_credits_even_if_paid_arrives_later_is_blocked_by_state(self):
        intent = self.start()
        services.settle_intent(intent.pk, "Cancelled")
        intent.refresh_from_db()
        self.assertEqual((intent.status, self.balance()), ("failed", Decimal("1.00")))

    def test_gateway_failure_marks_intent_failed(self):
        with mock.patch.object(paynow, "initiate_mobile", side_effect=paynow.PaynowError("Down")):
            with self.assertRaises(services.BusinessError):
                services.start_topup(self.user, Decimal("5"), "0771234567", "u", "u")
        self.assertEqual(PaymentIntent.objects.get().status, "failed")

    def test_polling_settles_and_is_throttled(self):
        intent = self.start()
        reply = {"status": "Paid", "amount": "5.00", "paynowreference": "PN1"}
        with mock.patch.object(paynow, "poll", return_value=reply) as poll:
            services.refresh_intent(intent)
            services.refresh_intent(PaymentIntent.objects.get(pk=intent.pk))  # already credited
        self.assertEqual((self.balance(), poll.call_count), (Decimal("6.00"), 1))

    def test_webhook_requires_valid_signature(self):
        intent = self.start()
        url = reverse("paynow_result")
        bad = urllib.parse.urlencode({"reference": intent.reference, "amount": "5.00", "status": "Paid", "hash": "FORGED"})
        self.assertEqual(self.client.post(url, bad, content_type="application/x-www-form-urlencoded").status_code, 400)
        self.assertEqual(self.balance(), Decimal("1.00"))
        good = signed([("reference", intent.reference), ("paynowreference", "PN1"), ("amount", "5.00"),
                       ("status", "Paid"), ("pollurl", "https://www.paynow.co.zw/x")])
        self.assertEqual(self.client.post(url, good, content_type="application/x-www-form-urlencoded").status_code, 200)
        self.assertEqual(self.balance(), Decimal("6.00"))
        self.client.post(url, good, content_type="application/x-www-form-urlencoded")  # replay
        self.assertEqual(self.balance(), Decimal("6.00"))

    def test_wallet_page_starts_topup_and_status_is_private(self):
        self.client.login(username="student", password="pw-12345-xyz")
        reply = {"poll_url": "https://www.paynow.co.zw/interface/CheckPayment/?guid=z", "instructions": "Approve", "paynow_reference": "P"}
        with mock.patch.object(paynow, "initiate_mobile", return_value=reply):
            r = self.client.post(reverse("wallet"), {"amount": "5.00", "phone": "0771234567"})
        intent = PaymentIntent.objects.get()
        self.assertRedirects(r, reverse("topup_wait", args=[intent.reference]))
        self.assertContains(self.client.get(r["Location"]), "Approve")
        make_user("other")
        self.client.login(username="other", password="pw-12345-xyz")
        self.assertEqual(self.client.get(reverse("topup_status", args=[intent.reference])).status_code, 404)

    def test_paynow_phone_required_and_validated(self):
        self.client.login(username="student", password="pw-12345-xyz")
        self.assertContains(self.client.post(reverse("wallet"), {"amount": "5"}), "Enter the EcoCash")
        self.assertContains(self.client.post(reverse("wallet"), {"amount": "5", "phone": "12345"}), "Zimbabwe mobile number")
        self.assertFalse(PaymentIntent.objects.exists())


@override_settings(PASSWORD_HASHERS=FAST)
class NotificationTests(TestCase):
    def setUp(self):
        self.student = make_user("student", "20", email="s@example.com")
        self.owner = make_user("owner", email="owner@example.com", phone="0771234567")
        self.v = Vendor.objects.create(name="Kitchen", category=Vendor.FOOD, owner=self.owner)
        self.item = MenuItem.objects.create(vendor=self.v, name="Sadza", price=Decimal("2.50"))

    def paid_order(self):
        order = services.place_food_order(self.student, self.v, {self.item: 2}, "Hall 1")
        with self.captureOnCommitCallbacks(execute=True):
            services.pay_order(order.pk, self.student)
        return order

    def test_vendor_gets_alert_and_email_when_order_is_paid(self):
        order = self.paid_order()
        alert = Notification.objects.get(user=self.owner)
        self.assertIn(f"#{order.pk}", alert.text)
        self.assertEqual(alert.url, "/jobs/")
        self.assertEqual([m.to for m in mail.outbox], [["owner@example.com"]])  # customer has no email alert on payment
        self.assertTrue(Notification.objects.filter(user=self.student).exists())

    def test_customer_alerted_on_each_step_and_vendor_on_payout(self):
        order = self.paid_order()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            services.advance_order(order.pk, self.owner)
        self.assertIn("Preparing", Notification.objects.filter(user=self.student).first().text)
        self.assertEqual([m.to for m in mail.outbox], [["s@example.com"]])
        with self.captureOnCommitCallbacks(execute=True):
            services.advance_order(order.pk, self.owner)
            services.advance_order(order.pk, self.owner)
        self.assertTrue(Notification.objects.filter(user=self.owner, text__contains="added to your wallet").exists())

    def test_cancel_notifies_the_other_side(self):
        order = self.paid_order()
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel_order(order.pk, self.owner)
        self.assertTrue(Notification.objects.filter(user=self.student, text__contains="declined").exists())

    def test_sms_only_when_enabled_and_number_normalised(self):
        self.assertEqual(notifications.to_e164("0771234567"), "+263771234567")
        self.assertEqual(notifications.to_e164("+263 77 123 4567"), "+263771234567")
        self.assertEqual(notifications.to_e164("12345"), "")
        with mock.patch("core.notifications.urllib.request.urlopen") as op:
            self.paid_order()
            op.assert_not_called()
        with override_settings(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="t", TWILIO_FROM="GZUConnect"):
            with mock.patch("core.notifications.urllib.request.urlopen") as op:
                self.paid_order()
                self.assertEqual(op.call_count, 1)  # vendor only, never the customer
                req = op.call_args[0][0]
                self.assertIn("To=%2B263771234567", req.data.decode())

    def test_failing_channels_never_break_the_booking(self):
        with override_settings(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="t", TWILIO_FROM="X"):
            with mock.patch("core.notifications.urllib.request.urlopen", side_effect=OSError("down")), \
                 mock.patch("core.notifications.send_mail", side_effect=OSError("smtp down")):
                order = self.paid_order()
        order.refresh_from_db()
        self.assertEqual(order.stage, Order.CONFIRMED)
        self.assertTrue(Notification.objects.filter(user=self.owner).exists())

    def test_alerts_page_marks_read_and_is_private(self):
        self.paid_order()
        self.client.login(username="owner", password="pw-12345-xyz")
        self.assertEqual(self.owner.notifications.filter(read=False).count(), 1)
        self.assertContains(self.client.get(reverse("alerts")), "New paid order")
        self.assertEqual(self.owner.notifications.filter(read=False).count(), 0)
        self.client.login(username="student", password="pw-12345-xyz")
        self.assertNotContains(self.client.get(reverse("alerts")), "New paid order")
