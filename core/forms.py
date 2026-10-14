import datetime

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import MenuItem, Profile, Rating, Vendor

TIME_SLOTS = [("9:00am", "9:00am"), ("11:00am", "11:00am"), ("2:00pm", "2:00pm"), ("4:00pm", "4:00pm")]
ALLOWED_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".jpg", ".jpeg", ".png"}
MAGIC = {".pdf": [b"%PDF"], ".doc": [b"\xd0\xcf\x11\xe0"], ".ppt": [b"\xd0\xcf\x11\xe0"],
         ".docx": [b"PK"], ".pptx": [b"PK"], ".png": [b"\x89PNG"], ".jpg": [b"\xff\xd8"], ".jpeg": [b"\xff\xd8"]}


PHONE_VALIDATOR = RegexValidator(r"^07[1378]\d{7}$", "Enter a Zimbabwe mobile number like 0771234567.")


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(max_length=30, label="First name")
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, label="Phone (EcoCash number)", required=False, validators=[PHONE_VALIDATOR])
    reg_number = forms.CharField(max_length=30, label="GZU registration number", required=False)
    hall = forms.CharField(max_length=60, label="Hall / where you stay", required=False)

    class Meta:
        model = User
        fields = ("username", "first_name", "email")

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            Profile.objects.update_or_create(user=user, defaults={
                "phone": self.cleaned_data["phone"], "reg_number": self.cleaned_data["reg_number"],
                "hall": self.cleaned_data["hall"]})
        return user


class TopUpForm(forms.Form):
    amount = forms.DecimalField(min_value=0.01, max_value=settings.MAX_TOPUP, decimal_places=2, max_digits=6)
    phone = forms.CharField(max_length=20, label="EcoCash / OneMoney number", required=False, validators=[PHONE_VALIDATOR])

    def clean(self):
        data = super().clean()
        if settings.PAYMENTS_MODE == "paynow" and not data.get("phone"):
            self.add_error("phone", "Enter the EcoCash or OneMoney number to charge.")
        return data


def _future_date_field():
    return forms.DateField(label="Date", widget=forms.DateInput(attrs={"type": "date"}))


class _SlotMixin:
    def clean_date(self):
        d = self.cleaned_data["date"]
        today = timezone.localdate()
        if d < today or d > today + datetime.timedelta(days=14):
            raise forms.ValidationError("Choose a date within the next 14 days.")
        return d

    def slot(self):
        return f"{self.cleaned_data['date']:%a %d %b} {self.cleaned_data['time']}"


class FoodForm(forms.Form):
    location = forms.CharField(max_length=100, label="Deliver to", help_text="Hall, room or landmark")

    def __init__(self, vendor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu_items = list(MenuItem.objects.filter(vendor=vendor, available=True))
        for item in self.menu_items:
            self.fields[f"q{item.pk}"] = forms.IntegerField(min_value=0, max_value=20, initial=0, required=False)

    def quantities(self):
        return {item: (self.cleaned_data.get(f"q{item.pk}") or 0) for item in self.menu_items}


class PrintForm(_SlotMixin, forms.Form):
    vendor = forms.ModelChoiceField(queryset=Vendor.objects.none(), label="Print point", empty_label=None)
    document = forms.FileField()
    pages = forms.IntegerField(min_value=1, max_value=500)
    copies = forms.IntegerField(min_value=1, max_value=50, initial=1)
    colour = forms.BooleanField(required=False, label="Colour (otherwise black & white)")
    date = _future_date_field()
    time = forms.ChoiceField(choices=TIME_SLOTS, label="Collection time")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Vendor.objects.filter(category=Vendor.PRINT, active=True)

    def clean_document(self):
        f = self.cleaned_data["document"]
        ext = "." + f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ALLOWED_EXT:
            raise forms.ValidationError("Upload a PDF, Word, PowerPoint, JPG or PNG file.")
        if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"File is too large (max {settings.MAX_UPLOAD_MB} MB).")
        head = f.read(8)
        f.seek(0)
        if not any(head.startswith(m) for m in MAGIC[ext]):
            raise forms.ValidationError("That file does not look like a valid " + ext.upper().lstrip(".") + ".")
        return f


class LaundryForm(_SlotMixin, forms.Form):
    vendor = forms.ModelChoiceField(queryset=Vendor.objects.none(), label="Laundry", empty_label=None)
    bag = forms.ChoiceField(choices=[("small", "Small bag"), ("large", "Large bag")])
    pickup_from = forms.CharField(max_length=100, label="Pick up from", help_text="Hall, room or address")
    date = _future_date_field()
    time = forms.ChoiceField(choices=TIME_SLOTS, label="Pickup time")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Vendor.objects.filter(category=Vendor.LAUNDRY, active=True)


class TutorBookForm(_SlotMixin, forms.Form):
    hours = forms.IntegerField(min_value=1, max_value=4, initial=1)
    date = _future_date_field()
    time = forms.ChoiceField(choices=TIME_SLOTS, label="Start time")


class RatingForm(forms.ModelForm):
    class Meta:
        model = Rating
        fields = ("stars", "comment")
        widgets = {"stars": forms.RadioSelect(choices=[(i, "★" * i) for i in range(5, 0, -1)])}
