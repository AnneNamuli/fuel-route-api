"""Django settings for the Fuel Route API. Environment-specific values come from env vars / .env."""

import os
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env_bool(name: str, default: bool = False) -> bool:
    """Read an env var as a boolean ('1', 'true', 'yes' are true)."""
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes')


def env_float(name: str, default: float, minimum: float | None = None, above: float | None = None) -> float:
    """Read an env var as a number, failing at startup with a clear message if it's invalid."""
    raw = os.environ.get(name, '').strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        raise ImproperlyConfigured(f'{name} must be a number, got {raw!r}.') from None
    if minimum is not None and value < minimum:
        raise ImproperlyConfigured(f'{name} must be at least {minimum}, got {value:g}.')
    if above is not None and value <= above:
        raise ImproperlyConfigured(f'{name} must be greater than {above}, got {value:g}.')
    return value


def env_list(name: str, default: str = '') -> list[str]:
    """Read a comma-separated env var as a list of non-empty strings."""
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


DEBUG = env_bool('DJANGO_DEBUG')

# Only local debug runs may fall back to an insecure development key.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.')
    SECRET_KEY = 'django-insecure-local-development-only'

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'fuel_route',
]

# Trip planning calls OpenRouteService, whose free key allows a few hundred routes a day, so limit
# planning requests per client IP. Format: "<number>/<second|minute|hour|day>".
TRIP_THROTTLE_RATE = os.environ.get('TRIP_THROTTLE_RATE', '30/hour').strip()
if not re.fullmatch(r'\d+/(s|sec|second|m|min|minute|h|hour|d|day)', TRIP_THROTTLE_RATE):
    raise ImproperlyConfigured(f'TRIP_THROTTLE_RATE must look like "30/hour", got {TRIP_THROTTLE_RATE!r}.')

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # JSON errors for everything, including unexpected exceptions.
    'EXCEPTION_HANDLER': 'fuel_route.exceptions.api_exception_handler',
    # The API is public and stateless: no logins (so no CSRF), open to everyone. The Django admin
    # keeps its own session login.
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
    'DEFAULT_THROTTLE_RATES': {'trip': TRIP_THROTTLE_RATE},
    # Behind a reverse proxy, set this to the number of proxies so throttling sees the real client IP.
    'NUM_PROXIES': int(os.environ.get('NUM_PROXIES', 0)) or None,
}

# Fuel route planning
ORS_API_KEY = os.environ.get('ORS_API_KEY', '')
VEHICLE_RANGE_MILES = env_float('VEHICLE_RANGE_MILES', 500, above=0)
VEHICLE_MPG = env_float('VEHICLE_MPG', 10, above=0)
# Station coordinates are city-level, so allow some slack when matching them to the route.
STATION_MAX_DISTANCE_MILES = env_float('STATION_MAX_DISTANCE_MILES', 10, above=0)
# Dollar value of avoiding a stop: plans skip stops that save less than this on fuel. 0 = cheapest fuel only.
FUEL_STOP_COST = env_float('FUEL_STOP_COST', 5, minimum=0)

SPECTACULAR_SETTINGS = {
    'TITLE': 'Fuel Route API',
    'VERSION': '0.1.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SORT_OPERATION_PARAMETERS': False,  # show start before finish
    # Open operations ready to run, so start/finish dropdowns show without clicking "Try it out".
    'SWAGGER_UI_SETTINGS': {'deepLinking': True, 'tryItOutEnabled': True},
    'POSTPROCESSING_HOOKS': [
        'drf_spectacular.hooks.postprocess_schema_enums',
        'fuel_route.schema.trip_location_suggestions',
    ],
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',  # route geometry compresses ~4x
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'fuel_route'),
        'USER': os.environ.get('POSTGRES_USER', 'postgres'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'postgres'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}


# Cache (shared by all workers, survives restarts): routing API responses are cached here.
# Create the table with `python manage.py createcachetable`.

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}


# Logging: warnings from the routing client and unexpected errors go to the console (and so to
# `docker compose logs`). Set LOG_LEVEL=DEBUG or INFO for more detail.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
    },
    'loggers': {
        'fuel_route': {'handlers': ['console'], 'level': os.environ.get('LOG_LEVEL', 'WARNING').upper()},
    },
}
