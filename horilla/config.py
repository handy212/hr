"""
horilla/config.py

Horilla app configurations
"""

import importlib
import logging

from django.apps import apps
from django.conf import settings
from django.contrib.auth.context_processors import PermWrapper

logger = logging.getLogger(__name__)


def get_apps_in_base_dir():
    return settings.SIDEBARS


def import_method(accessibility):
    module_path, method_name = accessibility.rsplit(".", 1)
    module = __import__(module_path, fromlist=[method_name])
    accessibility_method = getattr(module, method_name)
    return accessibility_method


ALL_MENUS = {}


def sidebar(request):

    base_dir_apps = get_apps_in_base_dir()

    if not request.user.is_anonymous:
        request.MENUS = []
        MENUS = request.MENUS

        for app in base_dir_apps:
            from saas.features import APP_FEATURES,request_feature_allowed
            if not request_feature_allowed(request,APP_FEATURES.get(app)):
                continue
            if apps.is_installed(app):
                try:
                    sidebar = importlib.import_module(app + ".sidebar")

                except Exception as e:
                    logger.error(e)
                    continue

                if sidebar:
                    accessibility = None
                    if getattr(sidebar, "ACCESSIBILITY", None):
                        accessibility = import_method(sidebar.ACCESSIBILITY)

                    if hasattr(sidebar, "MENU") and (
                        not accessibility
                        or accessibility(
                            request,
                            sidebar.MENU,
                            PermWrapper(request.user),
                        )
                    ):
                        MENU = {}
                        MENU["menu"] = sidebar.MENU
                        MENU["app"] = app
                        MENU["img_src"] = sidebar.IMG_SRC
                        MENU["submenu"] = []
                        MENUS.append(MENU)
                        for submenu in sidebar.SUBMENUS:
                            from saas.features import url_feature_allowed
                            if not url_feature_allowed(request,submenu['redirect']):
                                continue
                            accessibility = None

                            if submenu.get("accessibility"):
                                accessibility = import_method(submenu["accessibility"])
                            redirect: str = submenu["redirect"]
                            redirect = redirect.split("?")
                            submenu["redirect"] = redirect[0]

                            if not accessibility or accessibility(
                                request,
                                submenu,
                                PermWrapper(request.user),
                            ):
                                MENU["submenu"].append(submenu)
        ALL_MENUS[request.session.session_key] = MENUS


def get_MENUS(request):
    # Rebuild at most once per request — accessibility checks hit the DB.
    cached = getattr(request, "_horilla_menus", None)
    if cached is not None:
        return {"sidebar": cached}
    ALL_MENUS[request.session.session_key] = []
    sidebar(request)
    menus = ALL_MENUS.get(request.session.session_key)
    request._horilla_menus = menus
    return {"sidebar": menus}


def load_ldap_settings():
    """
    Fetch LDAP settings dynamically from the database after Django is ready.

    Diagnostics here go through `logger`, and stay ASCII, on purpose. This runs
    from `horilla_ldap`'s AppConfig.ready(), so it executes during
    django.setup() for every management command and for runserver. Two
    consequences follow:

    * A bare `print()` of a non-ASCII character raises UnicodeEncodeError on a
      console that is not UTF-8 -- Windows defaults to cp1252 -- and that
      exception propagates out of django.setup(). A warning nobody needed to
      read took the whole process down. `logging` routes through a handler that
      absorbs encoding errors instead of raising them, so a diagnostic can no
      longer be fatal.
    * The likeliest caller is a database that predates the horilla_ldap app, so
      the table genuinely does not exist. That is an ordinary state, not an
      error, and it must not stop anything.
    """
    try:
        from django.db import connection

        from horilla_ldap.models import LDAPSettings

        # Ensure DB is ready before querying
        if not connection.introspection.table_names():
            logger.warning("Database is empty. Using default LDAP settings.")
            return settings.DEFAULT_LDAP_CONFIG

        ldap_config = LDAPSettings.objects.first()
        if ldap_config:
            return {
                "LDAP_SERVER": ldap_config.ldap_server,
                "BIND_DN": ldap_config.bind_dn,
                "BIND_PASSWORD": ldap_config.bind_password,
                "BASE_DN": ldap_config.base_dn,
            }
    except Exception as e:
        logger.warning("Could not load LDAP settings (%s)", e)
        return settings.DEFAULT_LDAP_CONFIG  # Return default on error

    return settings.DEFAULT_LDAP_CONFIG  # Fallback in case of an issue
