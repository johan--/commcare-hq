from os.path import abspath, dirname, join
from datetime import datetime

from django.test import TestCase
from django.test import Client
from corehq.util.spreadsheets.excel import WorkbookJSONReader

from couchforms.models import XFormInstance
from django_prbac.models import UserRole, Role, Grant
from corehq.apps.users.models import WebUser
from corehq.apps.data_interfaces.utils import archive_forms_old
from corehq import privileges, toggles
from corehq.apps.accounting import generator

THISDIR = dirname(abspath(__file__))
BASE_PATH = join(THISDIR, 'files')
BASIC_XLSX = 'basic_forms_bulk.xlsx'
MISSING_XLSX = 'missing_forms_bulk.xlsx'
MALFORM_XLSX = 'malformatted_forms_bulk.xlsx'
WRONG_FILETYPE = 'wrong_file.xyz'


class BulkArchiveForms(TestCase):
    def setUp(self):
        self.domain_name = 'test'
        self.password = "password"

        username = "ben"
        email = "ben@domain.com"

        self.client = Client()
        self.user = WebUser.create(self.domain_name, username, self.password, email, is_admin=True)
        self.url = '/a/{}/data/edit/archive_forms/'.format(self.domain_name)

        django_user = self.user.get_django_user()
        try:
            self.user_role = UserRole.objects.get(user=django_user)
        except UserRole.DoesNotExist:
            user_privs = Role.objects.get_or_create(
                name="Privileges for %s" % django_user.username,
                slug="%s_privileges" % django_user.username,
            )[0]
            self.user_role = UserRole.objects.create(
                user=django_user,
                role=user_privs,
            )

        # Setup default roles and plans
        generator.instantiate_accounting_for_tests()

        self.bulk_role = Role.objects.filter(slug=privileges.BULK_CASE_MANAGEMENT)[0]
        Grant.objects.create(from_role=self.user_role.role, to_role=self.bulk_role)

        self.client.login(username=self.user.username, password=self.password)

        toggles.BULK_ARCHIVE_FORMS.set(self.user.username, True)

    def tearDown(self):
        self.user.delete()

    def test_bulk_archive_get_form(self):

        # Logged in
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['bulk_upload']['download_url'],
                         '/static/data_interfaces/files/forms_bulk_example.xlsx')

        grant = Grant.objects.get(
            from_role=self.user_role.role,
            to_role=self.bulk_role
        )
        grant.delete()

        # Revoked privileges should not render form
        response = self.client.get(self.url)
        self.assertFalse('bulk_upload' in response.context)

    def test_bulk_archive_missing_file(self):
        response = self.client.post(self.url, follow=True)

        # Huge hack for determining what has been sent in django messages object.
        # Need to find out how to inspect messages after redirect
        self.assertIn('No files uploaded', response.content)

    def test_bulk_archive_wrong_filetype(self):
        with open(join(BASE_PATH, WRONG_FILETYPE)) as fp:
            response = self.client.post(self.url, {'bulk_upload_file': fp}, follow=True)
            self.assertIn('CommCare HQ does not support that file type.', response.content)

    def test_bulk_archive_basic(self):
        with open(join(BASE_PATH, BASIC_XLSX)) as fp:
            response = self.client.post(self.url, {'bulk_upload_file': fp}, follow=True)
            self.assertIn('We received your file and are processing it.', response.content)


class BulkArchiveFormsUnit(TestCase):

    XFORMS = {
        'PRESENT': 'present_id',
        'PRESENT_2': 'present_2_id'
    }

    def setUp(self):
        self.domain_name = 'test'
        self.password = "password"
        username = "ben"
        email = "ben@domain.com"
        self.user = WebUser.create(self.domain_name, username, self.password, email)
        self.xforms = {}

        for key, _id, in self.XFORMS.iteritems():
            self.xforms[_id] = XFormInstance(xmlns='fake-xmlns',
                domain=self.domain_name,
                received_on=datetime.utcnow(),
                form={
                    '#type': 'fake-type',
                    '@xmlns': 'fake-xmlns'
                })
            self.xforms[_id]['_id'] = _id
            self.xforms[_id].save()

    def tearDown(self):
        self.user.delete()
        for key, xform, in self.xforms.iteritems():
            xform.delete()

    def test_archive_forms_basic(self):
        uploaded_file = WorkbookJSONReader(join(BASE_PATH, BASIC_XLSX))

        response = archive_forms_old(self.domain_name, self.user, list(uploaded_file.get_worksheet()))

        # Need to re-get instance from DB to get updated attributes
        for key, _id in self.XFORMS.iteritems():
            self.assertEqual(XFormInstance.get(_id).doc_type, 'XFormArchived')

        self.assertEqual(len(response['success']), len(self.xforms))

    def test_archive_forms_missing(self):
        uploaded_file = WorkbookJSONReader(join(BASE_PATH, MISSING_XLSX))

        response = archive_forms_old(self.domain_name, self.user, list(uploaded_file.get_worksheet()))

        for key, _id in self.XFORMS.iteritems():
            self.assertEqual(XFormInstance.get(_id).doc_type, 'XFormArchived')

        self.assertEqual(len(response['success']), len(self.xforms))
        self.assertEqual(len(response['errors']), 1,
                         "One error for trying to archive a missing form")

    def test_archive_forms_wrong_domain(self):
        uploaded_file = WorkbookJSONReader(join(BASE_PATH, BASIC_XLSX))

        response = archive_forms_old('wrong_domain', self.user, list(uploaded_file.get_worksheet()))

        self.assertEqual(len(response['errors']), len(self.xforms), "Error when wrong domain")
