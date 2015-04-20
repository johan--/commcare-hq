Getting Started
---------------

The first thing to do is install CommCareHQ as described in the
README.md file in the source code root.

When you are ready, start the following services:

The Django development server: ::

    $ ./manage.py runserver

Flip Elasticsearch aliases. (See
:ref:`Elasticsearch Indexes <elasticsearch_indexes>` for more information) ::

    $ ./manage.py ptop_es_manage --flip_all_aliases

Start pillowtop. pillowtop listens for CouchDB changes, reads new
documents, and passes them on to Elasticsearch or PosgreSQL. ::

    $ ./manage.py run_ptop --all

For CloudCare, start TouchForms. CloudCare lets you test forms within a
browser instead of requiring a phone. ::

    $ jython submodules/touchforms-src/touchforms/backend/xformserver.py

Then point your favourite browser at http://127.0.0.1:8000/
