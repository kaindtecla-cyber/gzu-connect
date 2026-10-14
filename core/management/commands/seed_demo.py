import os
from decimal import Decimal

from django.contrib.auth.models import User
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import MenuItem, TutorProfile, Vendor

LOCAL_DEFAULT_PASSWORD = "gzuconnect-demo"


class Command(BaseCommand):
    help = "Create demo vendors, tutors and accounts (example data only)."

    def _user(self, username, first, **extra):
        user, created = User.objects.get_or_create(username=username, defaults={"first_name": first, **extra})
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user

    def handle(self, *args, **opts):
        global DEMO_PASSWORD
        DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD") or (LOCAL_DEFAULT_PASSWORD if settings.DEBUG else None)
        if not DEMO_PASSWORD:
            raise CommandError("Set DEMO_PASSWORD to a private password before seeding on a live site.")
        student = self._user("student", "Tecla")
        student.profile.balance = Decimal("10.00")
        student.profile.hall = "Hall 1"
        student.profile.save()

        food = [("Nyasha's Kitchen", "Near the main gate", [("Sadza & beef", "2.50"), ("Chicken & chips", "3.50")]),
                ("Campus Cafeteria", "Student centre", [("Rice & beef", "3.00"), ("Veg stew & sadza", "2.00")]),
                ("Mai Rudo's Takeaway", "Opposite the library", [("Pizza slice", "2.50"), ("Burger combo", "3.50")])]
        for i, (name, loc, items) in enumerate(food, 1):
            owner = self._user(f"vendor_food{i}", name.split()[0])
            v, _ = Vendor.objects.get_or_create(name=name, defaults={"category": Vendor.FOOD, "location": loc, "owner": owner})
            for n, p in items:
                MenuItem.objects.get_or_create(vendor=v, name=n, defaults={"price": Decimal(p)})

        owner = self._user("vendor_print", "Chomuhondo")
        Vendor.objects.get_or_create(name="Chomuhondo Prints", defaults={
            "category": Vendor.PRINT, "location": "Library gate", "owner": owner})
        owner = self._user("vendor_print2", "Masvingo")
        Vendor.objects.get_or_create(name="Masvingo Copy Centre", defaults={
            "category": Vendor.PRINT, "location": "Main road", "owner": owner,
            "bw_price": Decimal("0.06"), "colour_price": Decimal("0.25")})
        owner = self._user("vendor_laundry", "GT")
        Vendor.objects.get_or_create(name="GT Best Laundry", defaults={
            "category": Vendor.LAUNDRY, "location": "Near the halls", "owner": owner})

        tutors = [("tatenda", "Tatenda", "HCS223", "Technopreneurship", "1.50"),
                  ("rutendo", "Rutendo", "HCS221", "Machine Learning", "2.00"),
                  ("farai", "Farai", "HCS260", "Dissertation statistics", "2.50"),
                  ("chipo", "Chipo", "HCS201", "Databases", "1.50")]
        for uname, first, code, mod, rate in tutors:
            u = self._user(uname, first)
            TutorProfile.objects.get_or_create(user=u, module_code=code, defaults={
                "module_name": mod, "hourly_rate": Decimal(rate)})
        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready. Log in as 'student', 'vendor_print', 'vendor_laundry', 'vendor_food1' or 'tatenda' "
            f"(password: {'the DEMO_PASSWORD you set' if os.environ.get('DEMO_PASSWORD') else DEMO_PASSWORD}). Example data only."))
