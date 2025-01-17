from __future__ import absolute_import
from collections import namedtuple
from datetime import datetime, timedelta
import pytz
from casexml.apps.case.dbaccessors import get_open_case_docs_in_domain
from casexml.apps.case.mock import CaseBlock
from casexml.apps.case.xml import V2
import uuid
from xml.etree import ElementTree
from corehq.apps.app_manager.const import USERCASE_TYPE
from corehq.apps.domain.models import Domain
from corehq.apps.es.domains import DomainES
from corehq.apps.es import filters
from corehq.apps.hqcase.utils import submit_case_blocks, get_case_by_domain_hq_user_id
from corehq.feature_previews import CALLCENTER
from corehq.util.quickcache import quickcache
from corehq.util.timezones.conversions import UserTime, ServerTime
from dimagi.utils.couch import CriticalSection


class DomainLite(namedtuple('DomainLite', 'name default_timezone cc_case_type use_fixtures')):
    def midnights(self, utcnow=None):
        """Returns a list containing two datetimes in UTC that corresponds to midnight
        in the domains timezone on either side of the current UTC datetime.
        i.e. [<previous midnight in TZ>, <next midnight in TZ>]

        >>> d = DomainLite('', 'Asia/Kolkata', '', True)
        >>> d.midnights(datetime(2015, 8, 27, 18, 30, 0  ))
        [datetime.datetime(2015, 8, 26, 18, 30), datetime.datetime(2015, 8, 27, 18, 30)]
        >>> d.midnights(datetime(2015, 8, 27, 18, 31, 0  ))
        [datetime.datetime(2015, 8, 27, 18, 30), datetime.datetime(2015, 8, 28, 18, 30)]
        """
        utcnow = utcnow or datetime.utcnow()
        tz = pytz.timezone(self.default_timezone)
        current_time_tz = ServerTime(utcnow).user_time(tz).done()
        midnight_tz1 = current_time_tz.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_tz_utc1 = UserTime(midnight_tz1).server_time().done()
        midnight_tz_utc2 = midnight_tz_utc1 + timedelta(days=(1 if midnight_tz_utc1 < utcnow else -1))
        return sorted([midnight_tz_utc1, midnight_tz_utc2])


CallCenterCase = namedtuple('CallCenterCase', 'case_id hq_user_id')


def sync_user_case(commcare_user, case_type, owner_id):
    """
    Each time a CommCareUser is saved this method gets called and creates or updates
    a case associated with the user with the user's details.

    This is also called to create user cases when the usercase is used for the
    first time.
    """
    with CriticalSection(['user_case_%s_for_%s' % (case_type, commcare_user._id)]):
        domain = commcare_user.project

        def valid_element_name(name):
            try:
                ElementTree.fromstring('<{}/>'.format(name))
                return True
            except ElementTree.ParseError:
                return False

        # remove any keys that aren't valid XML element names
        fields = {k: v for k, v in commcare_user.user_data.items() if valid_element_name(k)}

        # language or phone_number can be null and will break
        # case submission
        fields.update({
            'name': commcare_user.name or commcare_user.raw_username,
            'username': commcare_user.raw_username,
            'email': commcare_user.email,
            'language': commcare_user.language or '',
            'phone_number': commcare_user.phone_number or ''
        })

        case = get_case_by_domain_hq_user_id(domain.name, commcare_user._id, case_type)
        close = commcare_user.to_be_deleted() or not commcare_user.is_active
        caseblock = None
        if case:
            props = dict(case.dynamic_case_properties())

            changed = close != case.closed
            changed = changed or case.type != case_type
            changed = changed or case.name != fields['name']
            changed = changed or case.owner_id != owner_id

            if not changed:
                for field, value in fields.items():
                    if field != 'name' and props.get(field) != value:
                        changed = True
                        break

            if changed:
                caseblock = CaseBlock(
                    create=False,
                    case_id=case._id,
                    owner_id=owner_id,
                    case_type=case_type,
                    close=close,
                    update=fields
                )
        else:
            fields['hq_user_id'] = commcare_user._id
            caseblock = CaseBlock(
                create=True,
                case_id=uuid.uuid4().hex,
                owner_id=owner_id,
                user_id=owner_id,
                case_type=case_type,
                update=fields
            )

        if caseblock:
            casexml = ElementTree.tostring(caseblock.as_xml())
            submit_case_blocks(casexml, domain.name)


def sync_call_center_user_case(user):
    domain = user.project
    if domain and domain.call_center_config.enabled:
        owner_id = domain.call_center_config.case_owner_id
        if domain.call_center_config.use_user_location_as_owner:
            owner_id = user.location_id

        sync_user_case(
            user,
            domain.call_center_config.case_type,
            owner_id
        )


def sync_usercase(user):
    domain = user.project
    if domain and domain.usercase_enabled:
        sync_user_case(
            user,
            USERCASE_TYPE,
            user.get_id
        )


def is_midnight_for_domain(midnight_form_domain, error_margin=15, current_time=None):
    current_time = current_time or datetime.utcnow()
    diff = current_time - midnight_form_domain
    return diff.days >= 0 and diff < timedelta(minutes=error_margin)


def get_call_center_domains():
    result = (
        DomainES()
            .is_active()
            .is_snapshot(False)
            .filter(filters.term('call_center_config.enabled', True))
            .fields(['name', 'default_timezone', 'call_center_config.case_type', 'call_center_config.use_fixtures'])
            .run()
    )

    def to_domain_lite(hit):
        return DomainLite(
            name=hit['name'],
            default_timezone=hit['default_timezone'],
            cc_case_type=hit.get('call_center_config.case_type', ''),
            use_fixtures=hit.get('call_center_config.use_fixtures', True)
        )
    return [to_domain_lite(hit) for hit in result.hits]


def get_call_center_cases(domain_name, case_type, user=None):
    all_cases = []

    if user:
        docs = (doc for owner_id in user.get_owner_ids()
                for doc in get_open_case_docs_in_domain(domain_name, case_type,
                                                        owner_id=owner_id))
    else:
        docs = get_open_case_docs_in_domain(domain_name, case_type)

    for case_doc in docs:
        hq_user_id = case_doc.get('hq_user_id', None)
        if hq_user_id:
            all_cases.append(CallCenterCase(
                case_id=case_doc['_id'],
                hq_user_id=hq_user_id
            ))
    return all_cases


@quickcache(['domain'])
def get_call_center_case_type_if_enabled(domain):
    if CALLCENTER.enabled(domain):
        return Domain.get_by_name(domain).call_center_config.case_type
