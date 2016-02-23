import operator

from bson import ObjectId
from bson.errors import InvalidId
from django.core.exceptions import SuspiciousOperation, ImproperlyConfigured
from django.contrib.admin.views.main import ChangeList, ORDER_VAR
from django.contrib.admin.options import IncorrectLookupParameters
from django.core.paginator import InvalidPage
from mongoengine.queryset.visitor import Q


class DocumentChangeList(ChangeList):
    def get_queryset(self, request):
        # First, we collect all the declared list filters.
        (self.filter_specs, self.has_filters, remaining_lookup_params,
         filters_use_distinct) = self.get_filters(request)

        # Then, we let every list filter modify the queryset to its liking.
        qs = self.root_queryset
        for filter_spec in self.filter_specs:
            new_qs = filter_spec.queryset(request, qs)
            if new_qs is not None:
                qs = new_qs

        try:
            # Finally, we apply the remaining lookup parameters from the query
            # string (i.e. those that haven't already been processed by the
            # filters).
            qs = qs.filter(**remaining_lookup_params)
        except (SuspiciousOperation, ImproperlyConfigured):
            # Allow certain types of errors to be re-raised as-is so that the
            # caller can treat them in a special way.
            raise
        except Exception as e:
            # Every other error is caught with a naked except, because we don't
            # have any other way of validating lookup parameters. They might be
            # invalid if the keyword arguments are incorrect, or if the values
            # are not in the correct type, so we might get FieldError,
            # ValueError, ValidationError, or ?.
            raise IncorrectLookupParameters(e)

        qs = self.apply_select_related(qs)

        # Set ordering.
        ordering = self.get_ordering(request, qs)
        qs = qs.order_by(*ordering)

        # Apply search results
        qs, search_use_distinct = self.model_admin.get_search_results(
            request, qs, self.query)

        # Remove duplicates from results, if necessary
        if filters_use_distinct | search_use_distinct:
            return qs.distinct()
        else:
            return qs

    def get_ordering(self, request, queryset):
        """
        Returns the list of ordering fields for the change list.
        First we check the get_ordering() method in model admin, then we check
        the object's default ordering. Then, any manually-specified ordering
        from the query string overrides anything. Finally, a deterministic
        order is guaranteed by ensuring the primary key is used as the last
        ordering field.
        """
        params = self.params
        ordering = list(self.model_admin.get_ordering(request)
                        or self._get_default_ordering())
        if ORDER_VAR in params:
            # Clear ordering and used params
            ordering = []
            order_params = params[ORDER_VAR].split('.')
            for p in order_params:
                try:
                    none, pfx, idx = p.rpartition('-')
                    field_name = self.list_display[int(idx)]
                    order_field = self.get_ordering_field(field_name)
                    if not order_field:
                        continue  # No 'admin_order_field', skip it
                    ordering.append(pfx + order_field)
                except (IndexError, ValueError):
                    continue  # Invalid ordering specified, skip it.

        # Add the given query's ordering fields, if any.
        try:
            ordering.extend(queryset._ordering)
        except TypeError:
            pass

        # Ensure that the primary key is systematically present in the list of
        # ordering fields so we can guarantee a deterministic order across all
        # database backends.
        pk_name = self.lookup_opts.pk.name
        if not (set(ordering) & set(['pk', '-pk', pk_name, '-' + pk_name])):
            # The two sets do not intersect, meaning the pk isn't present. So
            # we add it.
            ordering.append('-pk')

        return ordering

    def get_results(self, request):
        paginator = self.model_admin.get_paginator(
            request, self.queryset, self.list_per_page)
        # Get the number of objects, with admin filters applied.
        result_count = paginator.count

        # Get the total number of objects, with no admin filters applied.
        # Perform a slight optimization:
        # full_result_count is equal to paginator.count if no filters
        # were applied
        if self.get_filters_params():
            full_result_count = self.root_queryset.count()
        else:
            full_result_count = result_count
        can_show_all = result_count <= self.list_max_show_all
        multi_page = result_count > self.list_per_page

        # Get the list of objects to display on this page.
        if (self.show_all and can_show_all) or not multi_page:
            result_list = self.queryset.clone()
        else:
            try:
                result_list = paginator.page(self.page_num + 1).object_list
            except InvalidPage:
                raise IncorrectLookupParameters

        self.result_count = result_count
        self.full_result_count = full_result_count
        self.result_list = result_list
        self.can_show_all = can_show_all
        self.multi_page = multi_page
        self.paginator = paginator

    def get_search_results(self, request, queryset, search_term):
        """Perform actual search by fields specified in modeladmin.search_fields.

        If query is a valid ObjectId, then search only in 'id' field,
        to make is super fast.
        Otherwise, remove 'id' from search fields, because it raises an exception.

        Args:
            request: current django request.
            queryset: queryset to search in.
            search_term: query to search for.

        Returns:
            tuple (queryset, use_distinct).
            queryset: queryset to implement the search.
            use_distinct: a boolean indicating if the results may contain duplicates.
                Currently it's always False.
        """
        # Apply keyword searches.
        def construct_search(field_name):
            if field_name.startswith('^'):
                return "%s__istartswith" % field_name[1:]
            elif field_name.startswith('='):
                return "%s__iexact" % field_name[1:]
            elif field_name.startswith('@'):
                return "%s__search" % field_name[1:]
            else:
                return "%s__icontains" % field_name

        use_distinct = False
        search_fields = self.get_search_fields(request)

        if search_fields and search_term:
            if 'id' in search_fields:
                try:
                    ObjectId(unicode(search_term))
                except InvalidId:
                    # remove id field from search fields, if a query can't be an id
                    search_fields = tuple([field for field in search_fields if field != 'id'])
                else:
                    # otherwise, if a query has an id format, remove all other fields,
                    # to make search super fast
                    search_fields = ('id',)

            orm_lookups = [construct_search(str(search_field))
                           for search_field in search_fields]
            for bit in search_term.split():
                or_queries = [Q(**{orm_lookup: bit})
                              for orm_lookup in orm_lookups]
                queryset = queryset.filter(reduce(operator.or_, or_queries))
        return queryset, use_distinct
