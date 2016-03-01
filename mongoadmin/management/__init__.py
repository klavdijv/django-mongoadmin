"""Creates django permissions for all installed Mongo models."""
from __future__ import unicode_literals

from django.apps import apps
from django.contrib.auth import get_permission_codename
from django.contrib.contenttypes.models import ContentType
from django.core import exceptions
from django.db import DEFAULT_DB_ALIAS, router
from django.db.models.signals import post_migrate
from mongoengine.base import TopLevelDocumentMetaclass
from mongoadmin.sites import site


def get_mongo_models():
    """Get list of all mongo models used in site."""
    return [model for model in site._registry.keys()
            if isinstance(model, TopLevelDocumentMetaclass)]


def get_default_permissions(opts):
    """Get default add/change/delete permissions given model._meta.

    Args:
        opts: model ._meta attribute.

    Returns:
        tuple of tuples (codename, verbose_name)
            for each default permission.
    """
    return ((get_permission_codename(action, opts),
             'Can %s %s' % (action, opts.verbose_name_raw))
            for action in ('add', 'change', 'delete'))


def create_permissions(app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs):
    """Create default permissions for all mongo models.

    This is basically a function copied from django.contrib.auth.management,
    with two replacements: get_mongo_models() and get_default_permissions()
    function calls.
    """
    if not app_config.models_module:
        return

    try:
        Permission = apps.get_model('auth', 'Permission')
    except LookupError:
        return

    if not router.allow_migrate_model(using, Permission):
        return

    from django.contrib.contenttypes.models import ContentType

    permission_name_max_length = Permission._meta.get_field('name').max_length
    verbose_name_max_length = permission_name_max_length - 11  # len('Can change ') prefix

    # This will hold the permissions we're looking for as
    # (content_type, (codename, name))
    searched_perms = list()
    # The codenames and ctypes that should exist.
    ctypes = set()
    for klass in get_mongo_models():
        # Force looking up the content types in the current database
        # before creating foreign keys to them.
        ctype = ContentType.objects.db_manager(using).get_for_model(klass)

        if len(klass._meta.verbose_name) > verbose_name_max_length:
            raise exceptions.ValidationError(
                "The verbose_name of %s.%s is longer than %s characters" % (
                    ctype.app_label,
                    ctype.model,
                    verbose_name_max_length,
                )
            )

        ctypes.add(ctype)
        for perm in get_default_permissions(klass._meta):
            searched_perms.append((ctype, perm))

    # Find all the Permissions that have a content_type for a model we're
    # looking for.  We don't need to check for codenames since we already have
    # a list of the ones we're going to create.
    all_perms = set(Permission.objects.using(using).filter(
        content_type__in=ctypes,
    ).values_list(
        "content_type", "codename"
    ))

    perms = [
        Permission(codename=codename, name=name, content_type=ct)
        for ct, (codename, name) in searched_perms
        if (ct.pk, codename) not in all_perms
    ]
    # Validate the permissions before bulk_creation to avoid cryptic database
    # error when the name is longer than 255 characters
    for perm in perms:
        if len(perm.name) > permission_name_max_length:
            raise exceptions.ValidationError(
                "The permission name %s of %s.%s is longer than %s characters" % (
                    perm.name,
                    perm.content_type.app_label,
                    perm.content_type.model,
                    permission_name_max_length,
                )
            )
    Permission.objects.using(using).bulk_create(perms)
    if verbosity >= 2:
        for perm in perms:
            print("Adding permission '%s'" % perm)


post_migrate.connect(create_permissions,
                     dispatch_uid="mongoadmin.management.create_permissions")
