# -*- coding: utf-8 -*-
import re
from django.test import SimpleTestCase
from corehq.apps.app_manager.const import APP_V2
from corehq.apps.app_manager.models import (
    AdvancedModule,
    Application,
    DetailColumn,
    FormActionCondition,
    FormSchedule,
    MappingItem,
    Module,
    OpenCaseAction,
    OpenSubCaseAction,
    PreloadAction,
    ReportAppConfig,
    ReportModule,
    ScheduleVisit,
    SortElement,
    UpdateCaseAction,
)
from corehq.apps.app_manager.tests.app_factory import AppFactory
from corehq.apps.app_manager.tests.util import SuiteMixin, TestXmlMixin, commtrack_enabled
from corehq.apps.app_manager.xpath import (
    session_var,
)
from corehq.toggles import NAMESPACE_DOMAIN
from corehq.feature_previews import MODULE_FILTER
from toggle.shortcuts import update_toggle_cache, clear_toggle_cache


class SuiteTest(SimpleTestCase, TestXmlMixin, SuiteMixin):
    file_path = ('data', 'suite')

    def setUp(self):
        update_toggle_cache(MODULE_FILTER.slug, 'domain', True, NAMESPACE_DOMAIN)

    def tearDown(self):
        clear_toggle_cache(MODULE_FILTER.slug, 'domain', NAMESPACE_DOMAIN)

    def test_normal_suite(self):
        self._test_generic_suite('app', 'normal-suite')

    def test_tiered_select(self):
        self._test_generic_suite('tiered-select', 'tiered-select')

    def test_3_tiered_select(self):
        self._test_generic_suite('tiered-select-3', 'tiered-select-3')

    def test_multisort_suite(self):
        self._test_generic_suite('multi-sort', 'multi-sort')

    def test_sort_only_value_suite(self):
        self._test_generic_suite('sort-only-value', 'sort-only-value')
        self._test_app_strings('sort-only-value')

    def test_sort_cache_suite(self):
        app = Application.wrap(self.get_json('suite-advanced'))
        detail = app.modules[0].case_details.short
        detail.sort_elements.append(
            SortElement(
                field=detail.columns[0].field,
                type='index',
                direction='descending',
            )
        )
        self.assertXmlPartialEqual(
            self.get_xml('sort-cache'),
            app.create_suite(),
            "./detail[@id='m0_case_short']"
        )

    def test_callcenter_suite(self):
        self._test_generic_suite('call-center')

    def test_careplan_suite(self):
        self._test_generic_suite('careplan')

    def test_careplan_suite_own_module(self):
        app = Application.wrap(self.get_json('careplan'))
        app.get_module(1).display_separately = True
        self.assertXmlEqual(self.get_xml('careplan-own-module'), app.create_suite())

    @commtrack_enabled(True)
    def test_product_list_custom_data(self):
        # product data shouldn't be interpreted as a case index relationship
        app = Application.wrap(self.get_json('suite-advanced'))
        custom_path = 'product_data/is_bedazzled'
        app.modules[1].product_details.short.columns[0].field = custom_path
        suite_xml = app.create_suite()
        for xpath in ['/template/text/xpath', '/sort/text/xpath']:
            self.assertXmlPartialEqual(
                '<partial><xpath function="{}"/></partial>'.format(custom_path),
                suite_xml,
                './detail[@id="m1_product_short"]/field[1]'+xpath,
            )

    @commtrack_enabled(True)
    def test_autoload_supplypoint(self):
        app = Application.wrap(self.get_json('app'))
        app.modules[0].forms[0].source = re.sub('/data/plain',
                                                session_var('supply_point_id'),
                                                app.modules[0].forms[0].source)
        app_xml = app.create_suite()
        self.assertXmlPartialEqual(
            self.get_xml('autoload_supplypoint'),
            app_xml,
            './entry[1]'
        )

    def test_case_assertions(self):
        self._test_generic_suite('app_case_sharing', 'suite-case-sharing')

    def test_no_case_assertions(self):
        self._test_generic_suite('app_no_case_sharing', 'suite-no-case-sharing')

    def test_attached_picture(self):
        self._test_generic_suite_partial('app_attached_image', "./detail", 'suite-attached-image')

    def test_copy_form(self):
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)
        module = app.add_module(AdvancedModule.new_module('module', None))
        original_form = app.new_form(module.id, "Untitled Form", None)
        original_form.source = '<source>'

        app._copy_form(module, original_form, module, rename=True)

        form_count = 0
        for f in app.get_forms():
            form_count += 1
            if f.unique_id != original_form.unique_id:
                self.assertEqual(f.name['en'], 'Copy of {}'.format(original_form.name['en']))
        self.assertEqual(form_count, 2, 'Copy form has copied multiple times!')

    def test_owner_name(self):
        self._test_generic_suite('owner-name')

    def test_form_filter(self):
        """
        Ensure form filter gets added correctly and appropriate instances get added to the entry.
        """
        app = Application.wrap(self.get_json('suite-advanced'))
        form = app.get_module(1).get_form(1)
        form.form_filter = "./edd = '123'"

        expected = """
        <partial>
          <menu id="m1">
            <text>
              <locale id="modules.m1"/>
            </text>
            <command id="m1-f0"/>
            <command id="m1-f1" relevant="instance('casedb')/casedb/case[@case_id=instance('commcaresession')/session/data/case_id_load_clinic0]/edd = '123'"/>
            <command id="m1-f2"/>
            <command id="m1-case-list"/>
          </menu>
        </partial>
        """
        self.assertXmlPartialEqual(expected, app.create_suite(), "./menu[@id='m1']")

    def test_module_filter(self):
        """
        Ensure module filter gets added correctly
        """
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)
        app.build_spec.version = '2.20.0'
        module = app.add_module(Module.new_module('m0', None))
        module.new_form('f0', None)

        module.module_filter = "/mod/filter = '123'"
        self.assertXmlPartialEqual(
            self.get_xml('module-filter'),
            app.create_suite(),
            "./menu[@id='m0']"
        )

    def test_module_filter_with_session(self):
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)
        app.build_spec.version = '2.20.0'
        module = app.add_module(Module.new_module('m0', None))
        form = module.new_form('f0', None)
        form.xmlns = 'f0-xmlns'

        module.module_filter = "#session/user/mod/filter = '123'"
        self.assertXmlPartialEqual(
            self.get_xml('module-filter-user'),
            app.create_suite(),
            "./menu[@id='m0']"
        )
        self.assertXmlPartialEqual(
            self.get_xml('module-filter-user-entry'),
            app.create_suite(),
            "./entry[1]"
        )

    def test_usercase_id_added_update(self):
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)

        child_module = app.add_module(Module.new_module("Untitled Module", None))
        child_module.case_type = 'child'

        child_form = app.new_form(0, "Untitled Form", None)
        child_form.xmlns = 'http://id_m1-f0'
        child_form.requires = 'case'
        child_form.actions.usercase_update = UpdateCaseAction(update={'name': '/data/question1'})
        child_form.actions.usercase_update.condition.type = 'always'

        self.assertXmlPartialEqual(self.get_xml('usercase_entry'), app.create_suite(), "./entry[1]")

    def test_usercase_id_added_preload(self):
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)

        child_module = app.add_module(Module.new_module("Untitled Module", None))
        child_module.case_type = 'child'

        child_form = app.new_form(0, "Untitled Form", None)
        child_form.xmlns = 'http://id_m1-f0'
        child_form.requires = 'case'
        child_form.actions.usercase_preload = PreloadAction(preload={'/data/question1': 'name'})
        child_form.actions.usercase_preload.condition.type = 'always'

        self.assertXmlPartialEqual(self.get_xml('usercase_entry'), app.create_suite(), "./entry[1]")

    def test_open_case_and_subcase(self):
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)

        module = app.add_module(Module.new_module('parent', None))
        module.case_type = 'phone'
        module.unique_id = 'm0'

        form = app.new_form(0, "Untitled Form", None)
        form.xmlns = 'http://m0-f0'
        form.actions.open_case = OpenCaseAction(name_path="/data/question1")
        form.actions.open_case.condition.type = 'always'
        form.actions.subcases.append(OpenSubCaseAction(
            case_type='tablet',
            case_name="/data/question1",
            condition=FormActionCondition(type='always')
        ))

        self.assertXmlPartialEqual(self.get_xml('open_case_and_subcase'), app.create_suite(), "./entry[1]")

    def test_update_and_subcase(self):
        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)

        module = app.add_module(Module.new_module('parent', None))
        module.case_type = 'phone'
        module.unique_id = 'm0'

        form = app.new_form(0, "Untitled Form", None)
        form.xmlns = 'http://m0-f0'
        form.requires = 'case'
        form.actions.update_case = UpdateCaseAction(update={'question1': '/data/question1'})
        form.actions.update_case.condition.type = 'always'
        form.actions.subcases.append(OpenSubCaseAction(
            case_type=module.case_type,
            case_name="/data/question1",
            condition=FormActionCondition(type='always')
        ))

        self.assertXmlPartialEqual(self.get_xml('update_case_and_subcase'), app.create_suite(), "./entry[1]")

    def test_graphing(self):
        self._test_generic_suite('app_graphing', 'suite-graphing')

    def test_fixtures_in_graph(self):
        self._test_generic_suite('app_fixture_graphing', 'suite-fixture-graphing')

    def test_fixture_to_case_selection(self):
        factory = AppFactory(build_version='2.9')

        module, form = factory.new_basic_module('my_module', 'cases')
        module.fixture_select.active = True
        module.fixture_select.fixture_type = 'days'
        module.fixture_select.display_column = 'my_display_column'
        module.fixture_select.variable_column = 'my_variable_column'
        module.fixture_select.xpath = 'date(scheduled_date) <= date(today() + $fixture_value)'

        factory.form_requires_case(form)

        self.assertXmlEqual(self.get_xml('fixture-to-case-selection'), factory.app.create_suite())

    def test_fixture_to_case_selection_with_form_filtering(self):
        factory = AppFactory(build_version='2.9')

        module, form = factory.new_basic_module('my_module', 'cases')
        module.fixture_select.active = True
        module.fixture_select.fixture_type = 'days'
        module.fixture_select.display_column = 'my_display_column'
        module.fixture_select.variable_column = 'my_variable_column'
        module.fixture_select.xpath = 'date(scheduled_date) <= date(today() + $fixture_value)'

        factory.form_requires_case(form)

        form.form_filter = "$fixture_value <= today()"

        self.assertXmlEqual(self.get_xml('fixture-to-case-selection-with-form-filtering'), factory.app.create_suite())

    def test_fixture_to_case_selection_localization(self):
        factory = AppFactory(build_version='2.9')

        module, form = factory.new_basic_module('my_module', 'cases')
        module.fixture_select.active = True
        module.fixture_select.fixture_type = 'days'
        module.fixture_select.display_column = 'my_display_column'
        module.fixture_select.localize = True
        module.fixture_select.variable_column = 'my_variable_column'
        module.fixture_select.xpath = 'date(scheduled_date) <= date(today() + $fixture_value)'

        factory.form_requires_case(form)

        self.assertXmlEqual(self.get_xml('fixture-to-case-selection-localization'), factory.app.create_suite())

    def test_fixture_to_case_selection_parent_child(self):
        factory = AppFactory(build_version='2.9')

        m0, m0f0 = factory.new_basic_module('parent', 'parent')
        m0.fixture_select.active = True
        m0.fixture_select.fixture_type = 'province'
        m0.fixture_select.display_column = 'display_name'
        m0.fixture_select.variable_column = 'var_name'
        m0.fixture_select.xpath = 'province = $fixture_value'

        factory.form_requires_case(m0f0)

        m1, m1f0 = factory.new_basic_module('child', 'child')
        m1.fixture_select.active = True
        m1.fixture_select.fixture_type = 'city'
        m1.fixture_select.display_column = 'display_name'
        m1.fixture_select.variable_column = 'var_name'
        m1.fixture_select.xpath = 'city = $fixture_value'

        factory.form_requires_case(m1f0, parent_case_type='parent')

        self.assertXmlEqual(self.get_xml('fixture-to-case-selection-parent-child'), factory.app.create_suite())

    def test_case_detail_tabs(self):
        self._test_generic_suite("app_case_detail_tabs", 'suite-case-detail-tabs')

    def test_case_tile_suite(self):
        self._test_generic_suite("app_case_tiles", "suite-case-tiles")

    def test_case_tile_pull_down(self):
        app = Application.new_app('domain', 'Untitled Application', application_version=APP_V2)

        module = app.add_module(Module.new_module('Untitled Module', None))
        module.case_type = 'patient'
        module.case_details.short.use_case_tiles = True
        module.case_details.short.persist_tile_on_forms = True
        module.case_details.short.pull_down_tile = True

        module.case_details.short.columns = [
            DetailColumn(
                header={'en': 'a'},
                model='case',
                field='a',
                format='plain',
                case_tile_field='header'
            ),
            DetailColumn(
                header={'en': 'b'},
                model='case',
                field='b',
                format='plain',
                case_tile_field='top_left'
            ),
            DetailColumn(
                header={'en': 'c'},
                model='case',
                field='c',
                format='enum',
                enum=[
                    MappingItem(key='male', value={'en': 'Male'}),
                    MappingItem(key='female', value={'en': 'Female'}),
                ],
                case_tile_field='sex'
            ),
            DetailColumn(
                header={'en': 'd'},
                model='case',
                field='d',
                format='plain',
                case_tile_field='bottom_left'
            ),
            DetailColumn(
                header={'en': 'e'},
                model='case',
                field='e',
                format='date',
                case_tile_field='date'
            ),
        ]

        form = app.new_form(0, "Untitled Form", None)
        form.xmlns = 'http://id_m0-f0'
        form.requires = 'case'

        self.assertXmlPartialEqual(
            self.get_xml('case_tile_pulldown_session'),
            app.create_suite(),
            "./entry/session"
        )

    def test_persistent_case_tiles_in_advanced_forms(self):
        """
        Test that the detail-persistent attributes is set correctly when persistent
        case tiles are used on advanced forms.
        """
        factory = AppFactory()
        module, form = factory.new_advanced_module("my_module", "person")
        factory.form_requires_case(form, "person")
        module.case_details.short.custom_xml = '<detail id="m0_case_short"></detail>'
        module.case_details.short.persist_tile_on_forms = True
        suite = factory.app.create_suite()

        # The relevant check is really that detail-persistent="m0_case_short"
        # but assertXmlPartialEqual xpath implementation doesn't currently
        # support selecting attributes
        self.assertXmlPartialEqual(
            """
            <partial>
                <datum
                    detail-confirm="m0_case_long"
                    detail-persistent="m0_case_short"
                    detail-select="m0_case_short"
                    id="case_id_load_person_0"
                    nodeset="instance('casedb')/casedb/case[@case_type='person'][@status='open']"
                    value="./@case_id"
                />
            </partial>
            """,
            suite,
            "entry/session/datum"
        )

    def test_persistent_case_tiles_in_advanced_module_case_lists(self):
        """
        Test that the detail-persistent attributes is set correctly when persistent
        case tiles are used on advanced module case lists
        """
        factory = AppFactory()
        module, form = factory.new_advanced_module("my_module", "person")
        factory.form_requires_case(form, "person")
        module.case_list.show = True
        module.case_details.short.custom_xml = '<detail id="m0_case_short"></detail>'
        module.case_details.short.persist_tile_on_forms = True
        suite = factory.app.create_suite()

        # The relevant check is really that detail-persistent="m0_case_short"
        # but assertXmlPartialEqual xpath implementation doesn't currently
        # support selecting attributes
        self.assertXmlPartialEqual(
            """
            <partial>
                <datum
                    detail-confirm="m0_case_long"
                    detail-persistent="m0_case_short"
                    detail-select="m0_case_short"
                    id="case_id_case_person"
                    nodeset="instance('casedb')/casedb/case[@case_type='person'][@status='open']"
                    value="./@case_id"
                />
            </partial>
            """,
            suite,
            'entry/command[@id="m0-case-list"]/../session/datum',
        )

    def test_subcase_repeat_mixed(self):
        app = Application.new_app(None, "Untitled Application", application_version=APP_V2)
        module_0 = app.add_module(Module.new_module('parent', None))
        module_0.unique_id = 'm0'
        module_0.case_type = 'parent'
        form = app.new_form(0, "Form", None)

        form.actions.open_case = OpenCaseAction(name_path="/data/question1")
        form.actions.open_case.condition.type = 'always'

        child_case_type = 'child'
        form.actions.subcases.append(OpenSubCaseAction(
            case_type=child_case_type,
            case_name="/data/question1",
            condition=FormActionCondition(type='always')
        ))
        # subcase in the middle that has a repeat context
        form.actions.subcases.append(OpenSubCaseAction(
            case_type=child_case_type,
            case_name="/data/repeat/question1",
            repeat_context='/data/repeat',
            condition=FormActionCondition(type='always')
        ))
        form.actions.subcases.append(OpenSubCaseAction(
            case_type=child_case_type,
            case_name="/data/question1",
            condition=FormActionCondition(type='always')
        ))

        expected = """
        <partial>
            <session>
              <datum id="case_id_new_parent_0" function="uuid()"/>
              <datum id="case_id_new_child_1" function="uuid()"/>
              <datum id="case_id_new_child_3" function="uuid()"/>
            </session>
        </partial>
        """
        self.assertXmlPartialEqual(expected,
                                   app.create_suite(),
                                   './entry[1]/session')

    def test_report_module(self):
        from corehq.apps.userreports.tests import get_sample_report_config

        app = Application.new_app('domain', "Untitled Application", application_version=APP_V2)

        report_module = app.add_module(ReportModule.new_module('Reports', None))
        report_module.unique_id = 'report_module'
        report = get_sample_report_config()
        report._id = 'd3ff18cd83adf4550b35db8d391f6008'

        report_app_config = ReportAppConfig(
            report_id=report._id,
            header={'en': 'CommBugz'},
            uuid='ip1bjs8xtaejnhfrbzj2r6v1fi6hia4i',
        )
        report_app_config._report = report
        report_module.report_configs = [report_app_config]
        report_module._loaded = True
        self.assertXmlPartialEqual(
            self.get_xml('reports_module_menu'),
            app.create_suite(),
            "./menu",
        )
        self.assertXmlPartialEqual(
            self.get_xml('reports_module_select_detail'),
            app.create_suite(),
            "./detail[@id='reports.ip1bjs8xtaejnhfrbzj2r6v1fi6hia4i.select']",
        )
        self.assertXmlPartialEqual(
            self.get_xml('reports_module_summary_detail'),
            app.create_suite(),
            "./detail[@id='reports.ip1bjs8xtaejnhfrbzj2r6v1fi6hia4i.summary']",
        )
        self.assertXmlPartialEqual(
            self.get_xml('reports_module_data_detail'),
            app.create_suite(),
            "./detail/detail[@id='reports.ip1bjs8xtaejnhfrbzj2r6v1fi6hia4i.data']",
        )
        self.assertXmlPartialEqual(
            self.get_xml('reports_module_data_entry'),
            app.create_suite(),
            "./entry",
        )
        self.assertIn(
            'reports.ip1bjs8xtaejnhfrbzj2r6v1fi6hia4i=CommBugz',
            app.create_app_strings('default'),
        )

    def test_shadow_module(self):
        self._test_generic_suite('shadow_module')

    def test_shadow_module_forms_only(self):
        self._test_generic_suite('shadow_module_forms_only')

    def test_shadow_module_cases(self):
        self._test_generic_suite('shadow_module_cases')
