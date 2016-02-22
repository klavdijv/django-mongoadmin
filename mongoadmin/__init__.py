from .options import *

from mongoadmin.sites import site

from django.conf import settings

if getattr(settings, 'MONGOADMIN_OVERRIDE_ADMIN', False):
    from django import get_version as get_django_version
    from distutils.version import StrictVersion
    import django.contrib.admin

    # copy already registered model admins
    # without that the already registered models
    # don't show up in the new admin
    if StrictVersion(get_django_version()) < StrictVersion('1.9'):
        site._registry = django.contrib.admin.site._registry
        django.contrib.admin.site = site
    else:
        site._registry = django.contrib.admin.sites.site._registry
        django.contrib.admin.sites.site = site
