import os.path
from pathlib import Path
import environ
import logging.config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
env = environ.Env()

if os.path.exists(BASE_DIR / ".env"):
    # OS environment variables take precedence over variables from .env
    env.read_env(str(BASE_DIR / ".env"))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

DEBUG = env("DJANGO_DEBUG", default=True)
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-v9@!9+-)rsufs7qy6j4ki-ywhggph**_^8h+-*zabvj314a**y",
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "billing",
    "example.apps.ExampleConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "example.config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "example.config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "ATOMIC_REQUESTS": False,
    }
}

# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = "/static/"

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# My Additions
# ---------------------

# This is a good article for how to build custom users with the email as username
# inheriting from AbstractUser rather than AbstractUserBase:
# https://www.fomfus.com/articles/how-to-use-email-as-username-for-django-authentication-removing-the-username
AUTH_USER_MODEL = "example.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/minute",
        "user": "120/minute",
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

ATOMIC_REQUESTS = False
ENVIRONMENT = env.str("DJANGO_SETTINGS_MODULE").split(".")[-1]

# LOGGING
LOGLEVEL = env("LOGLEVEL", default="DEBUG")

# See https://www.caktusgroup.com/blog/2015/01/27/Django-Logging-Configuration-logging_config-default-settings-logger/
# Django's default logger is painful and there's no good reason to merge with it.
LOGGING_CONFIG = None

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "[%(name)s] at=%(levelname)s timestamp=%(asctime)s "
                + "pathname=%(pathname)s funcname=%(funcName)s lineno=%(lineno)s %(message)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
            "simple": {
                "format": "[%(name)s] at=%(levelname)s timestamp=%(asctime)s %(message)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
        },
        "handlers": {
            "console": {
                "level": "DEBUG",
                "class": "logging.StreamHandler",
                "formatter": "verbose",
            },
            "request_log_handler": {
                "level": "INFO",
                "class": "logging.StreamHandler",
                "formatter": "simple",
            },
        },
        "loggers": {
            "django": {
                "handlers": ["console"],
                "level": "ERROR",  # Without this, it logs as a WARNING all 4xx requests.
                "propagate": False,
            },
            "billing": {"handlers": ["console"], "level": LOGLEVEL},
        },
    }
)

# django-allauth
SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_LOGOUT_REDIRECT_URL = "/login/"
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE = False

## See https://django-allauth.readthedocs.io/en/latest/advanced.html#custom-user-models
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "email"

# Billing
# Stripe - Don't use the 'mock' key because we want to patch the stripe library in the tests
BILLING_STRIPE_API_KEY = "testing"
BILLING_STRIPE_WH_SECRET = None
BILLING_APPLICATION_NAME = "example"
BILLING_CHECKOUT_SUCCESS_URL = "/accounts/profile/"
BILLING_CHECKOUT_CANCEL_URL = "/accounts/profile/"

# Celery - Will only be used if you pip install celery
# https://docs.celeryproject.org/en/stable/getting-started/brokers/redis.html
CELERY_BROKER_URL = None
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_TIME_LIMIT = 60  # Raise exception after 60 seconds.
CELERY_WORKER_TASK_LOG_FORMAT = "[%(name)s] at=%(levelname)s timestamp=%(asctime)s processName=%(processName)s task_id=%(task_id)s task_name=%(task_name)s %(message)s"
CELERY_WORKER_LOG_FORMAT = "[%(name)s] at=%(levelname)s timestamp=%(asctime)s processName=%(processName)s %(message)s"
CELERY_WORKER_LOG_COLOR = False
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
