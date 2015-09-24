import json
from django.conf import settings
from kafka import KafkaClient, KeyedProducer
from casexml.apps.case.models import CommCareCase
from corehq.apps.change_feed import data_sources
from corehq.apps.change_feed.models import ChangeMeta
from corehq.apps.change_feed.topics import get_topic
from couchforms.models import all_known_formlike_doc_types
from pillowtop.couchdb import CachedCouchDB
from pillowtop.listener import PythonPillow


class ChangeFeedPillow(PythonPillow):

    def __init__(self, couch_db=None):
        # todo: this will need to not be hard-coded if we ever split out forms and cases into their own domains
        couch_db = couch_db or CachedCouchDB(CommCareCase.get_db().uri, readonly=False)
        super(ChangeFeedPillow, self).__init__(couch_db=couch_db)
        self._kafka = KafkaClient(settings.KAFKA_URL)
        self._producer = KeyedProducer(self._kafka)

    def get_db_name(self):
        return self.couch_db.dbname

    def process_change(self, change, is_retry_attempt=False):
        document_type = _get_document_type(change.document)
        if document_type:
            assert change.document is not None
            change_meta = ChangeMeta(
                document_id=change.id,
                data_source_type=data_sources.COUCH,
                data_source_name=self.get_db_name(),
                document_type=document_type,
                document_subtype=_get_document_subtype(change.document),
                domain=change.document.get('domain', None),
                is_deletion=change.deleted,
            )
            self._producer.send_messages(
                bytes(get_topic(document_type)),
                bytes(change_meta.domain),
                bytes(json.dumps(change_meta.to_json())),
            )


def _get_document_type(document_or_none):
    return document_or_none.get('doc_type', None) if document_or_none else None


def _get_document_subtype(document_or_none):
    type = _get_document_type(document_or_none)
    if type == 'CommCareCase':
        return document_or_none.get('type', None)
    elif type in all_known_formlike_doc_types():
        return document_or_none.get('xmlns', None)
    return None
