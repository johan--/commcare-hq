from decimal import Decimal
from django.test.utils import override_settings
from lxml import etree
import os
import random
import uuid
from datetime import datetime, timedelta
from casexml.apps.case.mock import CaseBlock
from casexml.apps.case.xml import V2
from casexml.apps.phone.restore import RestoreConfig, RestoreParams
from casexml.apps.phone.tests import run_with_all_restore_configs
from casexml.apps.phone.tests.utils import synclog_id_from_restore_payload
from corehq.apps.commtrack.models import ConsumptionConfig, StockRestoreConfig, StockState
from corehq.apps.domain.models import Domain
from corehq.apps.consumption.shortcuts import set_default_monthly_consumption_for_domain
from corehq.apps.hqcase.utils import submit_case_blocks
from couchforms.models import XFormInstance
from dimagi.utils.parsing import json_format_datetime, json_format_date
from casexml.apps.stock import const as stockconst
from casexml.apps.stock.models import StockReport, StockTransaction
from corehq.apps.commtrack import const
from corehq.apps.commtrack.tests.util import CommTrackTest, get_ota_balance_xml, FIXED_USER, extract_balance_xml, \
    get_single_balance_block, get_single_transfer_block
from casexml.apps.case.tests.util import check_xml_line_by_line, check_user_has_case
from corehq.apps.receiverwrapper import submit_form_locally
from corehq.apps.commtrack.tests.util import make_loc, make_supply_point
from corehq.apps.commtrack.const import DAYS_IN_MONTH
from corehq.apps.commtrack.tests.data.balances import (
    balance_ota_block,
    submission_wrap,
    balance_submission,
    transfer_dest_only,
    transfer_source_only,
    transfer_both,
    balance_first,
    transfer_first,
    receipts_enumerated,
    balance_enumerated,
    products_xml)


class CommTrackOTATest(CommTrackTest):
    user_definitions = [FIXED_USER]

    def setUp(self):
        super(CommTrackOTATest, self).setUp()
        self.user = self.users[0]

    @run_with_all_restore_configs
    def test_ota_blank_balances(self):
        user = self.user
        self.assertFalse(get_ota_balance_xml(self.domain, user))

    @run_with_all_restore_configs
    def test_ota_basic(self):
        user = self.user
        amounts = [(p._id, i*10) for i, p in enumerate(self.products)]
        report = _report_soh(amounts, self.sp._id, 'stock')
        check_xml_line_by_line(
            self,
            balance_ota_block(
                self.sp,
                'stock',
                amounts,
                datestring=json_format_datetime(report.date),
            ),
            get_ota_balance_xml(self.domain, user)[0],
        )

    @run_with_all_restore_configs
    def test_ota_multiple_stocks(self):
        user = self.user
        date = datetime.utcnow()
        report = StockReport.objects.create(form_id=uuid.uuid4().hex, date=date,
                                            type=stockconst.REPORT_TYPE_BALANCE)
        amounts = [(p._id, i*10) for i, p in enumerate(self.products)]

        section_ids = sorted(('stock', 'losses', 'consumption'))
        for section_id in section_ids:
            _report_soh(amounts, self.sp._id, section_id, report=report)

        balance_blocks = get_ota_balance_xml(self.domain, user)
        self.assertEqual(3, len(balance_blocks))
        for i, section_id in enumerate(section_ids):
            check_xml_line_by_line(
                self,
                balance_ota_block(
                    self.sp,
                    section_id,
                    amounts,
                    datestring=json_format_datetime(date),
                ),
                balance_blocks[i],
            )

    @run_with_all_restore_configs
    def test_ota_consumption(self):
        self.ct_settings.consumption_config = ConsumptionConfig(
            min_transactions=0,
            min_window=0,
            optimal_window=60,
        )
        self.ct_settings.ota_restore_config = StockRestoreConfig(
            section_to_consumption_types={'stock': 'consumption'}
        )
        set_default_monthly_consumption_for_domain(self.domain.name, 5 * DAYS_IN_MONTH)
        self._save_settings_and_clear_cache()

        amounts = [(p._id, i*10) for i, p in enumerate(self.products)]
        report = _report_soh(amounts, self.sp._id, 'stock')
        balance_blocks = _get_ota_balance_blocks(self.domain, self.user)
        self.assertEqual(2, len(balance_blocks))
        stock_block, consumption_block = balance_blocks
        check_xml_line_by_line(
            self,
            balance_ota_block(
                self.sp,
                'stock',
                amounts,
                datestring=json_format_datetime(report.date),
            ),
            stock_block,
        )
        check_xml_line_by_line(
            self,
            balance_ota_block(
                self.sp,
                'consumption',
                [(p._id, 150) for p in self.products],
                datestring=json_format_datetime(report.date),
            ),
             consumption_block,
        )

    @run_with_all_restore_configs
    def test_force_consumption(self):
        self.ct_settings.consumption_config = ConsumptionConfig(
            min_transactions=0,
            min_window=0,
            optimal_window=60,
        )
        self.ct_settings.ota_restore_config = StockRestoreConfig(
            section_to_consumption_types={'stock': 'consumption'},
        )
        set_default_monthly_consumption_for_domain(self.domain.name, 5)
        self._save_settings_and_clear_cache()

        balance_blocks = _get_ota_balance_blocks(self.domain, self.user)
        self.assertEqual(0, len(balance_blocks))

        self.ct_settings.ota_restore_config.force_consumption_case_types = [const.SUPPLY_POINT_CASE_TYPE]
        self._save_settings_and_clear_cache()

        balance_blocks = _get_ota_balance_blocks(self.domain, self.user)
        # with no data, there should be no consumption block
        self.assertEqual(0, len(balance_blocks))

        self.ct_settings.ota_restore_config.use_dynamic_product_list = True
        self._save_settings_and_clear_cache()

        balance_blocks = _get_ota_balance_blocks(self.domain, self.user)
        self.assertEqual(1, len(balance_blocks))
        [balance_block] = balance_blocks
        element = etree.fromstring(balance_block)
        self.assertEqual(3, len([child for child in element]))

    def _save_settings_and_clear_cache(self):
        # since the commtrack settings object is stored as a memoized property on the domain
        # we need to refresh that as well
        self.ct_settings.save()
        self.domain = Domain.get(self.domain._id)


class CommTrackSubmissionTest(CommTrackTest):
    user_definitions = [FIXED_USER]

    def setUp(self):
        super(CommTrackSubmissionTest, self).setUp()
        self.user = self.users[0]
        loc2 = make_loc('loc2')
        self.sp2 = make_supply_point(self.domain.name, loc2)

    @override_settings(CASEXML_FORCE_DOMAIN_CHECK=False)
    def submit_xml_form(self, xml_method, timestamp=None, date_formatter=json_format_datetime, **submit_extras):
        instance_id = uuid.uuid4().hex
        instance = submission_wrap(
            instance_id,
            self.products,
            self.user,
            self.sp._id,
            self.sp2._id,
            xml_method,
            timestamp=timestamp,
            date_formatter=date_formatter,
        )
        submit_form_locally(
            instance=instance,
            domain=self.domain.name,
            **submit_extras
        )
        return instance_id

    def check_stock_models(self, case, product_id, expected_soh, expected_qty, section_id):
        if not isinstance(expected_qty, Decimal):
            expected_qty = Decimal(str(expected_qty))
        if not isinstance(expected_soh, Decimal):
            expected_soh = Decimal(str(expected_soh))

        latest_trans = StockTransaction.latest(case._id, section_id, product_id)
        self.assertIsNotNone(latest_trans)
        self.assertEqual(section_id, latest_trans.section_id)
        self.assertEqual(expected_soh, latest_trans.stock_on_hand)
        self.assertEqual(expected_qty, latest_trans.quantity)

    def check_product_stock(self, supply_point, product_id, expected_soh, expected_qty, section_id='stock'):
        self.check_stock_models(supply_point, product_id, expected_soh, expected_qty, section_id)


class CommTrackBalanceTransferTest(CommTrackSubmissionTest):

    def test_balance_submit(self):
        amounts = [(p._id, float(i*10)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_submission(amounts))
        for product, amt in amounts:
            self.check_product_stock(self.sp, product, amt, 0)

    def test_balance_submit_date(self):
        amounts = [(p._id, float(i*10)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_submission(amounts), date_formatter=json_format_date)
        for product, amt in amounts:
            self.check_product_stock(self.sp, product, amt, 0)

    def test_balance_enumerated(self):
        amounts = [(p._id, float(i*10)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_enumerated(amounts))
        for product, amt in amounts:
            self.check_product_stock(self.sp, product, amt, 0)

    def test_balance_consumption(self):
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        final_amounts = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_submission(final_amounts))
        for product, amt in final_amounts:
            self.check_product_stock(self.sp, product, amt, 0)
            inferred = amt - initial
            inferred_txn = StockTransaction.objects.get(case_id=self.sp._id, product_id=product,
                                              subtype=stockconst.TRANSACTION_SUBTYPE_INFERRED)
            self.assertEqual(Decimal(str(inferred)), inferred_txn.quantity)
            self.assertEqual(Decimal(str(amt)), inferred_txn.stock_on_hand)
            self.assertEqual(stockconst.TRANSACTION_TYPE_CONSUMPTION, inferred_txn.type)

    def test_balance_consumption_with_date(self):
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts), date_formatter=json_format_date)

        final_amounts = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_submission(final_amounts), date_formatter=json_format_date)
        for product, amt in final_amounts:
            self.check_product_stock(self.sp, product, amt, 0)

    def test_archived_product_submissions(self):
        """
        This is basically the same as above, but separated to be
        verbose about what we are checking (and to make it easy
        to change the expected behavior if the requirements change
        soon.
        """
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        final_amounts = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]

        self.submit_xml_form(balance_submission(initial_amounts))
        self.products[1].archive()
        self.submit_xml_form(balance_submission(final_amounts))

        for product, amt in final_amounts:
            self.check_product_stock(self.sp, product, amt, 0)

    def test_balance_submit_multiple_stocks(self):
        def _random_amounts():
            return [(p._id, float(random.randint(0, 100))) for i, p in enumerate(self.products)]

        section_ids = ('stock', 'losses', 'consumption')
        stock_amounts = [(id, _random_amounts()) for id in section_ids]
        for section_id, amounts in stock_amounts:
            self.submit_xml_form(balance_submission(amounts, section_id=section_id))

        for section_id, amounts in stock_amounts:
            for product, amt in amounts:
                self.check_product_stock(self.sp, product, amt, 0, section_id)

    def test_transfer_dest_only(self):
        amounts = [(p._id, float(i*10)) for i, p in enumerate(self.products)]
        self.submit_xml_form(transfer_dest_only(amounts))
        for product, amt in amounts:
            self.check_product_stock(self.sp, product, amt, amt)

    def test_transfer_source_only(self):
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        deductions = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        self.submit_xml_form(transfer_source_only(deductions))
        for product, amt in deductions:
            self.check_product_stock(self.sp, product, initial-amt, -amt)

    def test_transfer_both(self):
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        transfers = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        self.submit_xml_form(transfer_both(transfers))
        for product, amt in transfers:
            self.check_product_stock(self.sp, product, initial-amt, -amt)
            self.check_product_stock(self.sp2, product, amt, amt)

    def test_transfer_with_date(self):
        amounts = [(p._id, float(i*10)) for i, p in enumerate(self.products)]
        self.submit_xml_form(transfer_dest_only(amounts), date_formatter=json_format_date)
        for product, amt in amounts:
            self.check_product_stock(self.sp, product, amt, amt)

    def test_transfer_enumerated(self):
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        receipts = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        self.submit_xml_form(receipts_enumerated(receipts))
        for product, amt in receipts:
            self.check_product_stock(self.sp, product, initial + amt, amt)

    def test_balance_first_doc_order(self):
        initial = float(100)
        balance_amounts = [(p._id, initial) for p in self.products]
        transfers = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_first(balance_amounts, transfers))
        for product, amt in transfers:
            self.check_product_stock(self.sp, product, initial + amt, amt)

    def test_transfer_first_doc_order(self):
        # first set to 100
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        # then mark some receipts
        transfers = [(p._id, float(50 - 10*i)) for i, p in enumerate(self.products)]
        # then set to 50
        final = float(50)
        balance_amounts = [(p._id, final) for p in self.products]
        self.submit_xml_form(transfer_first(transfers, balance_amounts))
        for product, amt in transfers:
            self.check_product_stock(self.sp, product, final, 0)

    def test_blank_quantities(self):
        # submitting a bunch of blank data shouldn't submit transactions
        # so lets submit some initial data and make sure we don't modify it
        # or have new transactions
        initial = float(100)
        initial_amounts = [(p._id, initial) for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        trans_count = StockTransaction.objects.all().count()

        initial_amounts = [(p._id, '') for p in self.products]
        self.submit_xml_form(balance_submission(initial_amounts))

        self.assertEqual(trans_count, StockTransaction.objects.all().count())
        for product in self.products:
            self.check_product_stock(self.sp, product._id, 100, 0)

    def test_blank_product_id(self):
        initial = float(100)
        balances = [('', initial)]
        instance_id = self.submit_xml_form(balance_submission(balances))
        instance = XFormInstance.get(instance_id)
        self.assertEqual('XFormError', instance.doc_type)
        self.assertTrue('MissingProductId' in instance.problem)

    def test_blank_case_id_in_balance(self):
        instance_id = submit_case_blocks(
            case_blocks=get_single_balance_block(case_id='', product_id=self.products[0]._id, quantity=100),
            domain=self.domain.name,
        )
        instance = XFormInstance.get(instance_id)
        self.assertEqual('XFormError', instance.doc_type)
        self.assertTrue('IllegalCaseId' in instance.problem)

    def test_blank_case_id_in_transfer(self):
        instance_id = submit_case_blocks(
            case_blocks=get_single_transfer_block(
                src_id='', dest_id='', product_id=self.products[0]._id, quantity=100,
            ),
            domain=self.domain.name,
        )
        instance = XFormInstance.get(instance_id)
        self.assertEqual('XFormError', instance.doc_type)
        self.assertTrue('IllegalCaseId' in instance.problem)


class BugSubmissionsTest(CommTrackSubmissionTest):

    def test_device_report_submissions_ignored(self):
        """
        submit a device report with a stock block and make sure it doesn't
        get processed
        """
        self.assertEqual(0, StockTransaction.objects.count())

        fpath = os.path.join(os.path.dirname(__file__), 'data', 'xml', 'device_log.xml')
        with open(fpath) as f:
            form = f.read()
        amounts = [(p._id, 10) for p in self.products]
        product_block = products_xml(amounts)
        form = form.format(
            form_id=uuid.uuid4().hex,
            user_id=self.user._id,
            date=json_format_datetime(datetime.utcnow()),
            sp_id=self.sp._id,
            product_block=product_block
        )
        submit_form_locally(
            instance=form,
            domain=self.domain.name,
        )
        self.assertEqual(0, StockTransaction.objects.count())


class CommTrackSyncTest(CommTrackSubmissionTest):

    def setUp(self):
        super(CommTrackSyncTest, self).setUp()
        # reused stuff
        self.casexml_user = self.user.to_casexml_user()
        self.sp_block = CaseBlock(
            case_id=self.sp._id,
        ).as_xml()

        # bootstrap ota stuff
        self.ct_settings.consumption_config = ConsumptionConfig(
            min_transactions=0,
            min_window=0,
            optimal_window=60,
        )
        self.ct_settings.ota_restore_config = StockRestoreConfig(
            section_to_consumption_types={'stock': 'consumption'}
        )
        set_default_monthly_consumption_for_domain(self.domain.name, 5)
        self.ota_settings = self.ct_settings.get_ota_restore_settings()

        # get initial restore token
        restore_config = RestoreConfig(
            project=self.domain,
            user=self.casexml_user,
            params=RestoreParams(version=V2),
        )
        self.sync_log_id = synclog_id_from_restore_payload(restore_config.get_payload().as_string())

    def testStockSyncToken(self):
        # first restore should not have the updated case
        check_user_has_case(self, self.casexml_user, self.sp_block, should_have=False,
                            restore_id=self.sync_log_id, version=V2)

        # submit with token
        amounts = [(p._id, float(i*10)) for i, p in enumerate(self.products)]
        self.submit_xml_form(balance_submission(amounts), last_sync_token=self.sync_log_id)
        # now restore should have the case
        check_user_has_case(self, self.casexml_user, self.sp_block, should_have=True,
                            restore_id=self.sync_log_id, version=V2, line_by_line=False)


class CommTrackArchiveSubmissionTest(CommTrackSubmissionTest):

    def testArchiveLastForm(self):
        initial_amounts = [(p._id, float(100)) for p in self.products]
        self.submit_xml_form(
            balance_submission(initial_amounts),
            timestamp=datetime.utcnow() + timedelta(-30)
        )

        final_amounts = [(p._id, float(50)) for i, p in enumerate(self.products)]
        second_form_id = self.submit_xml_form(balance_submission(final_amounts))

        def _assert_initial_state():
            self.assertEqual(1, StockReport.objects.filter(form_id=second_form_id).count())
            # 6 = 3 stockonhand and 3 inferred consumption txns
            self.assertEqual(6, StockTransaction.objects.filter(report__form_id=second_form_id).count())
            self.assertEqual(3, StockState.objects.filter(case_id=self.sp._id).count())
            for state in StockState.objects.filter(case_id=self.sp._id):
                self.assertEqual(Decimal(50), state.stock_on_hand)
                self.assertEqual(
                    round(float(state.daily_consumption), 2),
                    1.67
                )

        # check initial setup
        _assert_initial_state()

        # archive and confirm commtrack data is deleted
        form = XFormInstance.get(second_form_id)
        form.archive()
        self.assertEqual(0, StockReport.objects.filter(form_id=second_form_id).count())
        self.assertEqual(0, StockTransaction.objects.filter(report__form_id=second_form_id).count())
        self.assertEqual(3, StockState.objects.filter(case_id=self.sp._id).count())
        for state in StockState.objects.filter(case_id=self.sp._id):
            # balance should be reverted to 100 in the StockState
            self.assertEqual(Decimal(100), state.stock_on_hand)
            # consumption should be none since there will only be 1 data point
            self.assertIsNone(state.daily_consumption)

        # unarchive and confirm commtrack data is restored
        form.unarchive()
        _assert_initial_state()

    def testArchiveOnlyForm(self):
        # check no data in stock states
        self.assertEqual(0, StockState.objects.filter(case_id=self.sp._id).count())

        initial_amounts = [(p._id, float(100)) for p in self.products]
        form_id = self.submit_xml_form(balance_submission(initial_amounts))

        # check that we made stuff
        def _assert_initial_state():
            self.assertEqual(1, StockReport.objects.filter(form_id=form_id).count())
            self.assertEqual(3, StockTransaction.objects.filter(report__form_id=form_id).count())
            self.assertEqual(3, StockState.objects.filter(case_id=self.sp._id).count())
            for state in StockState.objects.filter(case_id=self.sp._id):
                self.assertEqual(Decimal(100), state.stock_on_hand)
        _assert_initial_state()

        # archive and confirm commtrack data is cleared
        form = XFormInstance.get(form_id)
        form.archive()
        self.assertEqual(0, StockReport.objects.filter(form_id=form_id).count())
        self.assertEqual(0, StockTransaction.objects.filter(report__form_id=form_id).count())
        self.assertEqual(0, StockState.objects.filter(case_id=self.sp._id).count())

        # unarchive and confirm commtrack data is restored
        form.unarchive()
        _assert_initial_state()


def _report_soh(amounts, case_id, section_id='stock', report=None):
    if report is None:
        report = StockReport.objects.create(
            form_id=uuid.uuid4().hex,
            date=datetime.utcnow(),
            type=stockconst.REPORT_TYPE_BALANCE,
        )
    for product_id, amount in amounts:
        StockTransaction.objects.create(
            report=report,
            section_id=section_id,
            case_id=case_id,
            product_id=product_id,
            stock_on_hand=amount,
            quantity=0,
            type=stockconst.TRANSACTION_TYPE_STOCKONHAND,
        )
    return report


def _get_ota_balance_blocks(project, user):
    restore_config = RestoreConfig(
        project=project,
        user=user.to_casexml_user(),
        params=RestoreParams(version=V2),
    )
    return extract_balance_xml(restore_config.get_payload().as_string())
