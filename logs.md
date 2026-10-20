DEBUG mode is set to: False
Running in PRODUCTION mode
/usr/local/lib/python3.13/site-packages/requests/__init__.py:113: RequestsDependencyWarning: urllib3 (2.6.3) or chardet (7.6.0)/charset_normalizer (3.4.4) doesn't match a supported version!
  warnings.warn(
/app/apps/core/views.py:782: SyntaxWarning: invalid escape sequence '\d'
  </html>"""
/app/apps/core/views.py:785: SyntaxWarning: invalid escape sequence '\/'

/usr/local/lib/python3.13/site-packages/dj_rest_auth/registration/serializers.py:225: UserWarning: app_settings.USERNAME_REQUIRED is deprecated, use: app_settings.SIGNUP_FIELDS['username']['required']
  required=allauth_account_settings.USERNAME_REQUIRED,
/usr/local/lib/python3.13/site-packages/dj_rest_auth/registration/serializers.py:227: UserWarning: app_settings.EMAIL_REQUIRED is deprecated, use: app_settings.SIGNUP_FIELDS['email']['required']
  email = serializers.EmailField(required=allauth_account_settings.EMAIL_REQUIRED)
/usr/local/lib/python3.13/site-packages/dj_rest_auth/registration/serializers.py:285: UserWarning: app_settings.EMAIL_REQUIRED is deprecated, use: app_settings.SIGNUP_FIELDS['email']['required']
  email = serializers.EmailField(required=allauth_account_settings.EMAIL_REQUIRED)
System check identified some issues:

WARNINGS:
?: (account.W001) ACCOUNT_LOGIN_METHODS conflicts with ACCOUNT_SIGNUP_FIELDS
Scanning events for duplicate removal (sport=basketball, dry_run=False)...
Loaded 14053 events to inspect.
Merged duplicate: removed Event #73993 (thesportsdb) into primary Event #73990 (thesportsdb) - Japan Basketball Women vs Italy Basketball Women (2026-09-04 09:30:00+00:00)
Merged duplicate: removed Event #73988 (thesportsdb) into primary Event #73990 (thesportsdb) - Japan Basketball Women vs Italy Basketball Women (2026-09-04 09:30:00+00:00)
Traceback (most recent call last):
  File "/app/manage.py", line 22, in <module>
    main()
    ~~~~^^
  File "/app/manage.py", line 18, in main
    execute_from_command_line(sys.argv)
    ~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/core/management/__init__.py", line 442, in execute_from_command_line
    utility.execute()
    ~~~~~~~~~~~~~~~^^
  File "/usr/local/lib/python3.13/site-packages/django/core/management/__init__.py", line 436, in execute
    self.fetch_command(subcommand).run_from_argv(self.argv)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/core/management/base.py", line 420, in run_from_argv
    self.execute(*args, **cmd_options)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/core/management/base.py", line 464, in execute
    output = self.handle(*args, **options)
  File "/app/apps/event/management/commands/cleanup_duplicate_events.py", line 131, in handle
    EventTimeline.objects.filter(event=secondary).update(event=primary)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/manager.py", line 87, in manager_method
    return getattr(self.get_queryset(), name)(*args, **kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/query.py", line 1495, in filter
    return self._filter_or_exclude(False, args, kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/query.py", line 1513, in _filter_or_exclude
    clone._filter_or_exclude_inplace(negate, args, kwargs)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/query.py", line 1523, in _filter_or_exclude_inplace
    self._query.add_q(Q(*args, **kwargs))
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/sql/query.py", line 1648, in add_q
    clause, _ = self._add_q(q_object, can_reuse)
                ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/sql/query.py", line 1680, in _add_q
    child_clause, needed_inner = self.build_filter(
                                 ~~~~~~~~~~~~~~~~~^
        child,
        ^^^^^^
    ...<7 lines>...
        update_join_types=update_join_types,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/sql/query.py", line 1590, in build_filter
    condition = self.build_lookup(lookups, col, value)
  File "/usr/local/lib/python3.13/site-packages/django/db/models/sql/query.py", line 1417, in build_lookup
    lookup = lookup_class(lhs, rhs)
  File "/usr/local/lib/python3.13/site-packages/django/db/models/lookups.py", line 38, in __init__
    self.rhs = self.get_prep_lookup()
               ~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/fields/related_lookups.py", line 103, in get_prep_lookup
    self.rhs = get_normalized_value(self.rhs, self.lhs)[0]
               ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/django/db/models/fields/related_lookups.py", line 20, in get_normalized_value
    raise ValueError("Model instances passed to related filters must be saved.")
ValueError: Model instances passed to related filters must be saved.