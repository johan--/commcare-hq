from corehq.apps.locations.models import Location, SQLLocation
from casexml.apps.case.tests.util import check_user_has_case
from casexml.apps.case.xml import V2
from casexml.apps.case.mock import CaseBlock
from mock import patch
from corehq.apps.commtrack.helpers import make_supply_point
from corehq.apps.commtrack.tests.util import CommTrackTest, make_loc, FIXED_USER
from corehq.apps.commtrack.models import SupplyPointCase
from corehq.toggles import MULTIPLE_LOCATIONS_PER_USER, NAMESPACE_DOMAIN


class LocationsTest(CommTrackTest):
    user_definitions = [FIXED_USER]

    def setUp(self):
        super(LocationsTest, self).setUp()
        self.user = self.users[0]

    def test_sync(self):
        test_state = make_loc(
            'teststate',
            type='state',
            parent=self.user.location
        )
        test_village = make_loc(
            'testvillage',
            type='village',
            parent=test_state
        )

        try:
            sql_village = SQLLocation.objects.get(
                name='testvillage',
                domain=self.domain.name,
            )

            self.assertEqual(sql_village.name, test_village.name)
            self.assertEqual(sql_village.domain, test_village.domain)
        except SQLLocation.DoesNotExist:
            self.fail("Synced SQL object does not exist")

    def test_archive(self):
        test_state = make_loc(
            'teststate',
            type='state',
            parent=self.user.location
        )
        test_state.save()

        original_count = len(list(Location.by_domain(self.domain.name)))

        loc = self.user.location
        loc.archive()

        # it should also archive children
        self.assertEqual(
            len(list(Location.by_domain(self.domain.name))),
            original_count - 2
        )
        self.assertEqual(
            len(Location.root_locations(self.domain.name)),
            0
        )

        loc.unarchive()

        # and unarchive children
        self.assertEqual(
            len(list(Location.by_domain(self.domain.name))),
            original_count
        )
        self.assertEqual(
            len(Location.root_locations(self.domain.name)),
            1
        )

    def test_archive_flips_sp_cases(self):
        loc = make_loc('someloc')
        sp = make_supply_point(self.domain.name, loc)

        self.assertFalse(sp.closed)
        loc.archive()
        sp = SupplyPointCase.get(sp._id)
        self.assertTrue(sp.closed)

        loc.unarchive()
        sp = SupplyPointCase.get(sp._id)
        self.assertFalse(sp.closed)


class MultiLocationsTest(CommTrackTest):
    """
    LEGACY TESTS FOR MULTI LOCATION FUNCTIONALITY

    These tests cover special functionality, generic location
    test additions should not be added to this class.
    """
    user_definitions = [FIXED_USER]

    def setUp(self):
        super(MultiLocationsTest, self).setUp()
        self.user = self.users[0]
        MULTIPLE_LOCATIONS_PER_USER.set(self.user.domain, True, NAMESPACE_DOMAIN)
        # add the users location for delgate access as well
        self.user.add_location_delegate(self.user.location)

    def check_supply_point(self, user, sp, should_have=True):
        caseblock = CaseBlock(
            create=False,
            case_id=sp,
        ).as_xml()
        check_user_has_case(
            self,
            user.to_casexml_user(),
            caseblock,
            line_by_line=False,
            should_have=should_have,
        )

    def test_default_location_settings(self):
        user = self.user

        self.assertEqual(len(user.locations), 1)
        self.assertEqual(user.locations[0].name, 'loc1')
        self.check_supply_point(user, self.sp._id)

    def test_commtrack_user_has_multiple_locations(self):
        user = self.user

        loc = make_loc('secondloc')
        sp = make_supply_point(self.domain.name, loc)
        user.add_location_delegate(loc)

        self.check_supply_point(user, sp._id)
        self.assertTrue(len(user.locations), 2)
        self.assertEqual(user.locations[1].name, 'secondloc')

    def test_locations_can_be_removed(self):
        user = self.user

        # can't test with the original since the user already owns it
        loc = make_loc('secondloc')
        sp = make_supply_point(self.domain.name, loc)
        user.add_location_delegate(loc)

        self.check_supply_point(user, sp._id)

        user.remove_location_delegate(loc)

        self.check_supply_point(user, sp._id, False)
        self.assertEqual(len(user.locations), 1)

    def test_location_removal_only_submits_if_it_existed(self):
        user = self.user

        # can't test with the original since the user already owns it
        loc = make_loc('secondloc')
        make_supply_point(self.domain.name, loc)

        with patch('corehq.apps.users.models.CommCareUser.submit_location_block') as submit_blocks:
            user.remove_location_delegate(loc)
            self.assertEqual(submit_blocks.call_count, 0)


    def test_can_clear_locations(self):
        user = self.user
        user.clear_location_delegates()

        self.assertEqual(len(user.locations), 0)

    def test_can_set_locations(self):
        user = self.user

        loc1 = make_loc('secondloc')
        sp1 = make_supply_point(self.domain.name, loc1)

        loc2 = make_loc('thirdloc')
        sp2 = make_supply_point(self.domain.name, loc2)

        user.create_location_delegates([loc1, loc2])

        # should only have the two new cases
        self.assertEqual(len(user.locations), 2)

        # and will have access to these two
        self.check_supply_point(user, sp1._id)
        self.check_supply_point(user, sp2._id)

    def test_setting_new_list_causes_submit(self):
        """
        this test mostly exists to make sure the one
        testing no submits doesn't silently stop actually working
        """
        user = self.user

        loc1 = make_loc('secondloc')
        make_supply_point(self.domain.name, loc1)

        with patch('corehq.apps.users.models.CommCareUser.submit_location_block') as submit_blocks:
            user.create_location_delegates([loc1])
            self.assertEqual(submit_blocks.call_count, 1)

    def test_setting_existing_list_does_not_submit(self):
        user = self.user

        user.clear_location_delegates()

        loc1 = make_loc('secondloc')
        make_supply_point(self.domain.name, loc1)

        loc2 = make_loc('thirdloc')
        make_supply_point(self.domain.name, loc2)

        user.add_location_delegate(loc1)
        user.add_location_delegate(loc2)

        with patch('corehq.apps.users.models.CommCareUser.submit_location_block') as submit_blocks:
            user.create_location_delegates([loc1, loc2])
            self.assertEqual(submit_blocks.call_count, 0)
