import logging
import re
import urllib
import uuid
import datetime
from couchdbkit.resource import ResourceNotFound
from dimagi.utils.couch.database import get_db
from corehq.apps.users.models import CouchUser, CommCareUser
from django.template.loader import render_to_string
from django.conf import settings
from corehq.apps.hqcase.utils import submit_case_blocks
from corehq.apps.sms.mixin import MobileBackend
from corehq.util.quickcache import quickcache
from django.core.exceptions import ValidationError
from xml.etree.ElementTree import XML, tostring
from dimagi.utils.parsing import json_format_datetime
from dimagi.utils.modules import to_function
from django.utils.translation import ugettext as _

phone_number_plus_re = re.compile("^\+{0,1}\d+$")

def strip_plus(phone_number):
    if (isinstance(phone_number, basestring) and len(phone_number) > 0
        and phone_number[0] == "+"):
        return phone_number[1:]
    else:
        return phone_number

def clean_phone_number(text):
    """
    strip non-numeric characters and add '%2B' at the front
    """
    non_decimal = re.compile(r'[^\d.]+')
    plus = '+'
    cleaned_text = "%s%s" % (plus, non_decimal.sub('', text))
    return cleaned_text

def clean_outgoing_sms_text(text):
    try:
        return urllib.quote(text)
    except KeyError:
        return urllib.quote(text.encode('utf-8'))

def validate_phone_number(phone_number):
    if (not isinstance(phone_number, basestring) or
        not phone_number_plus_re.match(phone_number)):
        raise ValidationError(_("Invalid phone number format."))


def format_message_list(message_list):
    """
    question = message_list[-1]
    if len(question) > 160:
        return question[0:157] + "..."
    else:
        extra_space = 160 - len(question)
        message_start = ""
        if extra_space > 3:
            for msg in message_list[0:-1]:
                message_start += msg + ". "
            if len(message_start) > extra_space:
                message_start = message_start[0:extra_space-3] + "..."
        return message_start + question
    """
    # Some gateways (yo) allow a longer message to be sent and handle splitting it up on their end, so for now just join all messages together
    return " ".join(message_list)

def submit_xml(domain, template, context):
    case_block = render_to_string(template, context)
    case_block = tostring(XML(case_block)) # Ensure the XML is formatted properly, an exception is raised if not
    submit_case_blocks(case_block, domain)

# Creates a case by submitting system-generated casexml
def register_sms_contact(domain, case_type, case_name, user_id, contact_phone_number, contact_phone_number_is_verified="1", contact_backend_id=None, language_code=None, time_zone=None, owner_id=None):
    utcnow = str(datetime.datetime.utcnow())
    case_id = str(uuid.uuid3(uuid.NAMESPACE_URL, utcnow))
    if owner_id is None:
        owner_id = user_id
    context = {
        "case_id" : case_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "case_type" : case_type,
        "case_name" : case_name,
        "owner_id" : owner_id,
        "user_id" : user_id,
        "contact_phone_number" : contact_phone_number,
        "contact_phone_number_is_verified" : contact_phone_number_is_verified,
        "contact_backend_id" : contact_backend_id,
        "language_code" : language_code,
        "time_zone" : time_zone
    }
    submit_xml(domain, "sms/xml/register_contact.xml", context)
    return case_id

def update_contact(domain, case_id, user_id, contact_phone_number=None, contact_phone_number_is_verified=None, contact_backend_id=None, language_code=None, time_zone=None):
    context = {
        "case_id" : case_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "user_id" : user_id,
        "contact_phone_number" : contact_phone_number,
        "contact_phone_number_is_verified" : contact_phone_number_is_verified,
        "contact_backend_id" : contact_backend_id,
        "language_code" : language_code,
        "time_zone" : time_zone
    }
    submit_xml(domain, "sms/xml/update_contact.xml", context)

def create_task(parent_case, submitting_user_id, task_owner_id, form_unique_id, task_activation_datetime, task_deactivation_datetime, incentive):
    utcnow = str(datetime.datetime.utcnow())
    subcase_guid = str(uuid.uuid3(uuid.NAMESPACE_URL, utcnow))
    context = {
        "subcase_guid" : subcase_guid,
        "user_id" : submitting_user_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "task_owner_id" : task_owner_id,
        "form_unique_id" : form_unique_id,
        "task_activation_date" : json_format_datetime(task_activation_datetime),
        "task_deactivation_date" : json_format_datetime(task_deactivation_datetime),
        "parent" : parent_case,
        "incentive" : incentive,
    }
    submit_xml(parent_case.domain, "sms/xml/create_task.xml", context)
    return subcase_guid

def update_task(domain, subcase_guid, submitting_user_id, task_owner_id, form_unique_id, task_activation_datetime, task_deactivation_datetime, incentive):
    context = {
        "subcase_guid" : subcase_guid,
        "user_id" : submitting_user_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "task_owner_id" : task_owner_id,
        "form_unique_id" : form_unique_id,
        "task_activation_date" : json_format_datetime(task_activation_datetime),
        "task_deactivation_date" : json_format_datetime(task_deactivation_datetime),
        "incentive" : incentive,
    }
    submit_xml(domain, "sms/xml/update_task.xml", context)

def close_task(domain, subcase_guid, submitting_user_id):
    context = {
        "subcase_guid" : subcase_guid,
        "user_id" : submitting_user_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
    }
    submit_xml(domain, "sms/xml/close_task.xml", context)


def get_available_backends(index_by_api_id=False):
    result = {}
    for backend_class in settings.SMS_LOADED_BACKENDS:
        klass = to_function(backend_class)
        if index_by_api_id:
            api_id = klass.get_api_id()
            result[api_id] = klass
        else:
            result[klass.__name__] = klass
    return result

CLEAN_TEXT_REPLACEMENTS = (
    # Common emoticon replacements
    (":o", ": o"),
    (":O", ": O"),
    (":x", ": x"),
    (":X", ": X"),
    (":D", ": D"),
    (":p", ": p"),
    (":P", ": P"),
    # Special punctuation ascii conversion
    (u"\u2013", "-"), # Dash
    (u"\u201c", '"'), # Open double quote
    (u"\u201d", '"'), # Close double quote
    (u"\u2018", "'"), # Open single quote
    (u"\u2019", "'"), # Close single quote
    (u"\u2026", "..."), # Ellipsis
)

def clean_text(text):
    """
    Performs the replacements in CLEAN_TEXT_REPLACEMENTS on text.
    """
    for a, b in CLEAN_TEXT_REPLACEMENTS:
        text = text.replace(a, b)
    return text


def get_contact(contact_id):
    from corehq.apps.sms.models import CommConnectCase
    contact = None
    try:
        contact = CommConnectCase.get(contact_id)
    except ResourceNotFound:
        pass

    if contact and contact.doc_type == 'CommCareCase':
        return contact

    contact = None
    try:
        contact = CouchUser.get_by_user_id(contact_id)
    except CouchUser.AccountTypeError:
        pass

    if not contact:
        raise Exception("Contact not found")

    return contact


def get_backend_by_class_name(class_name):
    backends = dict([(d.split('.')[-1], d) for d in settings.SMS_LOADED_BACKENDS])
    backend_path = backends.get(class_name)
    if backend_path is not None:
        return to_function(backend_path)
    return None


def touchforms_error_is_config_error(touchforms_error):
    """
    Returns True if the given TouchformsError is the result of a
    form configuration error.
    """
    error_type = touchforms_error.response_data.get('error_type', '')
    return any([s in error_type for s in (
        'XPathTypeMismatchException',
        'XPathUnhandledException',
        'XFormParseException',
    )])


@quickcache(['backend_id'], timeout=5 * 60)
def get_backend_name(backend_id):
    """
    Returns None if the backend is not found, otherwise
    returns the backend's name.
    """
    if not backend_id:
        return None

    try:
        doc = MobileBackend.get_db().get(backend_id)
    except ResourceNotFound:
        return None

    return doc.get('name', None)
