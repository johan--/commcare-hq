import json
from django.test import SimpleTestCase
import os
from corehq.apps.app_manager.models import Application
from corehq.apps.app_manager.tests.app_factory import AppFactory


class BuildErrorsTest(SimpleTestCase):
    def test_subcase_errors(self):
        with open(os.path.join(os.path.dirname(__file__), 'data', 'subcase-details.json')) as f:
            source = json.load(f)

        app = Application.wrap(source)
        errors = app.validate_app()
        update_path_error = {
            'type': 'path error',
            'path': '/data/parent_age',
            'form_type': 'module_form',
            'module': {'name': {'en': "Parent"}, 'id': 0},
            'form': {'id': 0, 'name': {'en': "Register"}},
        }
        subcase_path_error = {
            'type': 'path error',
            'path': '/data/child_age',
            'form_type': 'module_form',
            'module': {'name': {'en': "Parent"}, 'id': 0},
            'form': {'id': 0, 'name': {'en': "Register"}},
        }
        self.assertIn(update_path_error, errors)
        self.assertIn(subcase_path_error, errors)

        form = app.get_module(0).get_form(0)
        errors = form.validate_for_build()
        self.assertIn(update_path_error, errors)
        self.assertIn(subcase_path_error, errors)

    def test_parent_cycle_in_app(self):
        cycle_error = {
            'type': 'parent cycle',
        }

        with open(os.path.join(os.path.dirname(__file__), 'data', 'cyclical-app.json')) as f:
            source = json.load(f)

            app = Application.wrap(source)
            errors = app.validate_app()

            self.assertIn(cycle_error, errors)

    def test_case_tile_configuration_errors(self):
        case_tile_error = {
            'type': "invalid tile configuration",
            'module': {'id': 0, 'name': {u'en': u'View'}},
            'reason': 'A case property must be assigned to the "sex" tile field.'
        }
        with open(os.path.join(
            os.path.dirname(__file__), 'data', 'bad_case_tile_config.json'
        )) as f:
            source = json.load(f)
            app = Application.wrap(source)
            errors = app.validate_app()
            self.assertIn(case_tile_error, errors)

    def test_case_list_form_advanced_module_different_case_config(self):
        case_tile_error = {
            'type': "all forms in case list module must load the same cases",
            'module': {'id': 1, 'name': {u'en': u'update module'}},
            'form': {'id': 1, 'name': {u'en': u'update form 1'}},
        }

        factory = AppFactory(build_version='2.11')
        m0, m0f0 = factory.new_basic_module('register', 'person')
        factory.form_opens_case(m0f0)

        m1, m1f0 = factory.new_advanced_module('update', 'person', case_list_form=m0f0)
        factory.form_requires_case(m1f0, case_type='house')
        factory.form_requires_case(m1f0, parent_case_type='house')

        m1f1 = factory.new_form(m1)
        factory.form_requires_case(m1f1)  # only loads a person case and not a house case

        errors = factory.app.validate_app()
        self.assertIn(case_tile_error, errors)
