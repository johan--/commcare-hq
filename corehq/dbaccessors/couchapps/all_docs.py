from corehq.preindex import get_preindex_plugin
from corehq.util.couch_helpers import paginate_view
from dimagi.utils.chunked import chunked
from dimagi.utils.couch.database import get_db


def _get_all_docs_dbs():
    return get_preindex_plugin('domain').get_dbs('domain') + [get_db(None)]


def get_all_doc_ids_for_domain_grouped_by_db(domain):
    """
    This function has the limitation that it only gets docs from the main db
    and extra dbs that are listed for the 'domain' design doc
    in corehq/apps/domain/__init__.py

    """
    # todo: move view to all_docs/by_domain_doc_type as in this original commit:
    # todo: https://github.com/dimagi/commcare-hq/commit/400d3878afc5e9f5118ffb30d22b8cebe9afb4a6
    for db in _get_all_docs_dbs():
        results = db.view(
            'domain/related_to_domain',
            startkey=[domain],
            endkey=[domain, {}],
            include_docs=False,
            reduce=False,
        )
        yield (db, (result['id'] for result in results))


def get_doc_count_by_type(db, doc_type):
    key = [doc_type]
    result = db.view(
        'all_docs/by_doc_type', startkey=key, endkey=key + [{}], reduce=True,
        group_level=1).one()
    if result:
        return result['value']
    else:
        return 0


def get_all_docs_with_doc_types(db, doc_types):
    for doc_type in doc_types:
        results = paginate_view(
            db, 'all_docs/by_doc_type',
            chunk_size=100, startkey=[doc_type], endkey=[doc_type, {}],
            attachments=True, include_docs=True, reduce=False)
        for result in results:
            yield result['doc']


def delete_all_docs_by_doc_type(db, doc_types):
    for chunk in chunked(get_all_docs_with_doc_types(db, doc_types), 100):
        db.bulk_delete(chunk)
