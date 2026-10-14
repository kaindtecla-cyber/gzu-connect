"""Django settings for GZU Connect."""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
if not DEBUG and SECRET_KEY == "dev-insecure-key-change-me":
    raise RuntimeError("Set the SECRET_KEY environment variable when DEBUG is off.")

ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.context_processors.wallet",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=600)}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Harare"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Business rules
COMMISSION_RATE = "0.12"          # GZU Connect keeps 12% of every booking
MAX_UPLOAD_MB = 10
MAX_TOPUP = 100                    # per top-up, USD
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600

# ---- Payments (Paynow: EcoCash / OneMoney) ----
# "simulated" credits the wallet instantly with no real money (demo). "paynow" uses real mobile money.
PAYMENTS_MODE = os.environ.get("PAYMENTS_MODE", "simulated")
PAYNOW_ID = os.environ.get("PAYNOW_ID", "")
PAYNOW_KEY = os.environ.get("PAYNOW_KEY", "")
PAYNOW_AUTH_EMAIL = os.environ.get("PAYNOW_AUTH_EMAIL", "")   # your Paynow merchant login email (required in test mode)
SITE_URL = os.environ.get("SITE_URL", "")                     # e.g. https://gzu-connect.onrender.com
if PAYMENTS_MODE == "paynow" and not (PAYNOW_ID and PAYNOW_KEY and PAYNOW_AUTH_EMAIL):
    raise RuntimeError("PAYMENTS_MODE=paynow needs PAYNOW_ID, PAYNOW_KEY and PAYNOW_AUTH_EMAIL.")

# ---- Notifications ----
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_BACKEND = ("django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST
                 else "django.core.mail.backends.console.EmailBackend")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "GZU Connect <no-reply@gzuconnect.local>")
SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "")             # "" (off) or "twilio"
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_FROM", "")               # alphanumeric sender ID or number
