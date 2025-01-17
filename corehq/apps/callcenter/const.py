WEEK0 = 'week0'
WEEK1 = 'week1'
MONTH0 = 'month0'
MONTH1 = 'month1'

DATE_RANGES = [WEEK0, WEEK1, MONTH0, MONTH1]

FORMS_SUBMITTED = 'forms_submitted'

CASES_TOTAL = 'cases_total'
CASES_ACTIVE = 'cases_active'
CASES_OPENED = 'cases_opened'
CASES_CLOSED = 'cases_closed'

LEGACY_TOTAL_CASES = 'totalCases'
LEGACY_CASES_UPDATED = 'casesUpdated'
LEGACY_FORMS_SUBMITTED = 'formsSubmitted'

CASE_SLUGS = [CASES_TOTAL, CASES_ACTIVE, CASES_OPENED, CASES_CLOSED]
STANDARD_SLUGS = [FORMS_SUBMITTED] + CASE_SLUGS
LEGACY_SLUGS = [LEGACY_TOTAL_CASES, LEGACY_FORMS_SUBMITTED, LEGACY_CASES_UPDATED]

PCI_CHILD_FORM = 'http://openrosa.org/formdesigner/85823851-3622-4E9E-9E86-401500A39354'
PCI_MOTHER_FORM = 'http://openrosa.org/formdesigner/366434ec56aba382966f77639a2414bbc3c56cbc'
AAROHI_CHILD_FORM = 'http://openrosa.org/formdesigner/09486EF6-04C8-480C-BA11-2F8887BBBADD'
AAROHI_MOTHER_FORM = 'http://openrosa.org/formdesigner/6C63E53D-2F6C-4730-AA5E-BAD36B50A170'
INFOMOVAL_FIND_PATIENT_FORM = 'http://openrosa.org/formdesigner/DA10DCC2-8240-4101-B964-6F5424BD2B86'
INFOMOVAL_REGISTER_CONTACT_FORM = 'http://openrosa.org/formdesigner/c0671536f2087bb80e460d57f60c98e5b785b955'
INFOMOVAL_HOME_VISIT_FORM = 'http://openrosa.org/formdesigner/74BD43B5-5253-4855-B195-F3F049B8F8CC'


TYPE_DURATION = 'duration'
TYPE_SUM = 'sum'

PER_DOMAIN_FORM_INDICATORS = {
    'aarohi': [
        {'slug': 'motherForms', 'type': TYPE_SUM, 'xmlns': AAROHI_MOTHER_FORM},
        {'slug': 'childForms', 'type': TYPE_SUM, 'xmlns': AAROHI_CHILD_FORM},
        {'slug': 'motherDuration', 'type': TYPE_DURATION, 'xmlns': AAROHI_MOTHER_FORM},
    ],
    'pci-india': [
        {'slug': 'motherForms', 'type': TYPE_SUM, 'xmlns': PCI_MOTHER_FORM},
        {'slug': 'childForms', 'type': TYPE_SUM, 'xmlns': PCI_CHILD_FORM},
    ],
    'infomovel': [
        {'slug': 'findPatientForms', 'type': TYPE_SUM, 'xmlns': INFOMOVAL_FIND_PATIENT_FORM},
        {'slug': 'registerContactForms', 'type': TYPE_SUM, 'xmlns': INFOMOVAL_REGISTER_CONTACT_FORM},
        {'slug': 'homeVisitForms', 'type': TYPE_SUM, 'xmlns': INFOMOVAL_HOME_VISIT_FORM},
    ]
}
