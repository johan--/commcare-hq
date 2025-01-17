import datetime, pytz

from celery.task import periodic_task
from celery.schedules import crontab
from django.conf import settings
from xml.etree import ElementTree

from casexml.apps.case.models import CommCareCase
from casexml.apps.case.mock import CaseBlock
from casexml.apps.case.xml import V2
from corehq.apps.domain.models import Domain
from corehq.apps.groups.models import Group
from corehq.apps.hqcase.dbaccessors import get_cases_in_domain
from corehq.apps.hqcase.utils import submit_case_blocks
from dimagi.utils.decorators.memoized import memoized

DOMAINS = ["hsph-dev", "hsph-betterbirth", "hsph-learning-sites", "hsph-test"]
PAST_N_DAYS = 21
GROUPS_TO_CHECK = ["cati", "cati-tl"]
GROUP_SHOULD_BE = "fida"
BIRTH_TYPE = "birth"
CATI_FIDA_CHECK_TYPE = "cati_fida_check"
OWNER_FIELD_MAPPINGS = {
        "cati": "cati_assignment",
        "fida": "field_follow_up_assignment"
    }
INDEXED_GROUPS = dict((domain, {}) for domain in DOMAINS)
GROUPS_BY_ID = dict((domain, {}) for domain in DOMAINS)

@memoized
def indexed_facilities():
    facility_index = {}
    for domain in DOMAINS:
        current_domain_index = {}
        facilities = get_cases_in_domain(domain, type="facility")
        for facility in facilities:
            case_sharing_group = GROUPS_BY_ID[domain].get(facility.owner_id, None)
            if case_sharing_group is None:
                continue
            cati_user = case_sharing_group.metadata.get('cati_user', None)
            fida_user = case_sharing_group.metadata.get('fida_user', None)
            current_domain_index[facility.facility_id] = {
                "cati": cati_user, 
                "fida": fida_user
            }
        facility_index[domain] = current_domain_index
    return facility_index


def update_groups_index(domain):
    groups = Group.by_domain(domain)
    for group in groups:
        GROUPS_BY_ID[domain][group._id] = group
        if group.case_sharing and group.metadata.get("main_user", None):
            INDEXED_GROUPS[domain][group.metadata["main_user"]] = group


def setup_indices():
    for domain in DOMAINS:
        update_groups_index(domain)
    facility_index = indexed_facilities()


def get_owner_username(domain, owner_type, facility_id):
    if not owner_type:
        return ''
    facility_index_by_domain = indexed_facilities()
    try:
        return facility_index_by_domain[domain][facility_id][owner_type]
    except KeyError:
        return None


def get_group_id(domain, owner_type, facility_id):
    owner_username = get_owner_username(domain, owner_type, facility_id)
    try:
        return INDEXED_GROUPS[domain][owner_username]._id
    except KeyError:
        return None

past_x_date = lambda time_zone, past_x_days: (datetime.datetime.now(time_zone) - datetime.timedelta(past_x_days)).date()
get_none_or_value = lambda _object, _attribute: getattr(_object, _attribute) if (hasattr(_object, _attribute)) else ''


@periodic_task(
    run_every=crontab(minute=1, hour="*/6"),
    queue=getattr(settings, 'CELERY_PERIODIC_QUEUE', 'celery')
)
def new_update_case_properties():
    _domain = Domain.get_by_name(DOMAINS[0])
    if _domain is None:
        return
    time_zone = _domain.get_default_timezone()
    past_21_date = past_x_date(time_zone, 21)
    past_42_date = past_x_date(time_zone, 42)
    setup_indices()
    for domain in DOMAINS:
        case_list = list(get_cases_in_domain(domain, type=BIRTH_TYPE))
        case_list = case_list + list(get_cases_in_domain(domain, type=CATI_FIDA_CHECK_TYPE))
        cases_to_modify = []
        for case in case_list:
            if case.closed:
                continue
            if not get_none_or_value(case, "owner_id") or not get_none_or_value(case, "date_admission") or not get_none_or_value(case, "facility_id"):
                continue
            curr_assignment = get_none_or_value(case, "current_assignment")
            next_assignment = get_none_or_value(case, "next_assignment")
            facility_id = get_none_or_value(case, "facility_id")
            fida_group = get_group_id(domain, "fida", facility_id)

            # get cati_owner_username from current owner-group
            assigned_owner_group = get_none_or_value(case, "owner_id")
            if assigned_owner_group not in GROUPS_BY_ID[domain]:
                continue
            cati_owner_username = GROUPS_BY_ID[domain][assigned_owner_group].metadata.get('main_user', None)

            # Assignment Directly from Registration ##
            # Assign Cases to Call Center
            if case.date_admission >= past_21_date and (not curr_assignment) and (not next_assignment):
                owner_id = get_group_id(domain, "cati", facility_id)
                if not owner_id:
                    continue
                owner_group = GROUPS_BY_ID[domain].get(owner_id, None)
                cati_name = owner_group.metadata.get('name', None) if owner_group else None
                update = {
                    "current_assignment": "cati",
                    "cati_name": cati_name
                }
                cases_to_modify.append({
                    "case_id": case._id,
                    "update": update,
                    "close": False,
                    "owner_id": owner_id,
                })
            # Assign Cases Directly To Field
            elif (case.date_admission >= past_42_date) and (case.date_admission < past_21_date) and (not curr_assignment) and (not next_assignment):
                if not fida_group:
                    continue
                update = {
                    "current_assignment": "fida",
                    "cati_status": 'skipped',
                }
                cases_to_modify.append(
                    {
                        "case_id": case._id,
                        "update": update,
                        "close": False,
                        "owner_id": fida_group,
                    }
                )
            # Assign Cases Directly to Lost to Follow Up
            elif case.date_admission < past_42_date and (not curr_assignment) and (not next_assignment):
                update = {
                    "cati_status": 'skipped',
                    "last_assignment": '',
                    "closed_status": "timed_out_lost_to_follow_up",
                }
                cases_to_modify.append(
                    {
                        "case_id": case._id,
                        "update": update,
                        "close": True,
                    }
                )

            ## Assignment from Call Center ##
            # Assign Cases to Field (manually by call center)
            elif (case.date_admission >= past_42_date) and next_assignment == "fida":
                if not cati_owner_username or not fida_group:
                    continue
                update = {
                    "last_cati_user": cati_owner_username,
                    "current_assignment": "fida",
                    "next_assignment": '',
                    "cati_status": 'manually_assigned_to_field'
                }
                cases_to_modify.append(
                    {
                        "case_id": case._id,
                        "update": update,
                        "close": False,
                        "owner_id": fida_group,
                    }
                )
            # Assign cases to field (automatically)
            elif (case.date_admission >= past_42_date) and (case.date_admission < past_21_date) and (curr_assignment == "cati" or curr_assignment == "cati_tl"):
                if not cati_owner_username or not fida_group:
                    continue
                update = {
                    "last_cati_assignment": curr_assignment,
                    "last_cati_user": cati_owner_username,
                    "cati_status": 'timed_out',
                    "current_assignment": "fida",
                    "next_assignment": '',
                }
                cases_to_modify.append(
                    {
                        "case_id": case._id,
                        "update": update,
                        "close": False,
                        "owner_id": fida_group,
                    }
                )
            # Assign Cases to Lost to Follow Up
            elif case.date_admission < past_42_date and (curr_assignment == "cati" or curr_assignment == "cati_tl"):
                if not get_owner_username(domain, curr_assignment, facility_id) or not cati_owner_username:
                    continue
                update = {
                    "last_cati_assignment": curr_assignment,
                    "last_cati_user": cati_owner_username,
                    "last_user": get_owner_username(domain, curr_assignment, facility_id),
                    "cati_status": 'timed_out',
                    "last_assignment": curr_assignment,
                    "current_assignment": '',
                    "closed_status": "timed_out_lost_to_follow_up",
                    "next_assignment": ''
                }
                cases_to_modify.append(
                    {
                        "case_id": case._id,
                        "update": update,
                        "close": True,
                    }
                )

            ## Assignment from Field ##
            # Assign Cases to Lost to Follow Up
            elif case.date_admission < past_42_date and (curr_assignment == "fida" or curr_assignment == "fida_tl"):
                if not get_owner_username(domain, curr_assignment, facility_id):
                    continue
                update = {
                    "last_user": get_owner_username(domain, curr_assignment, facility_id),
                    "last_assignment": curr_assignment,
                    "current_assignment": '',
                    "closed_status": "timed_out_lost_to_follow_up",
                    "next_assignment": '',
                }
                cases_to_modify.append(
                    {
                        "case_id": case._id,
                        "update": update,
                        "close": True,
                    }
                )
        case_blocks = []
        for case in cases_to_modify:
            kwargs = {
                "create": False,
                "case_id": case["case_id"],
                "update": case["update"],
                "close": case["close"],
            }
            if case.get("owner_id", None):
                kwargs["owner_id"] = case["owner_id"]
            case_blocks.append(ElementTree.tostring(CaseBlock(**kwargs).as_xml()))
        submit_case_blocks(case_blocks, domain)
