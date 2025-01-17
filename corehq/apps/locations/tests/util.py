from django.test import TestCase
from dimagi.utils.couch.database import iter_bulk_delete
from corehq.util.test_utils import unit_testing_only
from corehq.apps.commtrack.models import SupplyPointCase
from corehq.apps.commtrack.tests.util import bootstrap_domain
from corehq.apps.locations.models import Location, SQLLocation, LocationType

TEST_DOMAIN = 'locations-test'
TEST_LOCATION_TYPE = 'location'


def make_loc(code, name=None, domain=TEST_DOMAIN, type=TEST_LOCATION_TYPE, parent=None):
    name = name or code
    loc = Location(site_code=code, name=name, domain=domain, location_type=type, parent=parent)
    loc.save()
    return loc


@unit_testing_only
def delete_all_locations():
    ids = [
        doc['id'] for doc in
        SupplyPointCase.get_db().view('commtrack/supply_point_by_loc', reduce=False).all()
    ]
    iter_bulk_delete(SupplyPointCase.get_db(), ids)

    iter_bulk_delete(Location.get_db(), SQLLocation.objects.location_ids())

    SQLLocation.objects.all().delete()


def _setup_location_types(domain, location_types):
    location_types_dict = {}
    previous = None
    for name in location_types:
        location_type = LocationType.objects.create(
            domain=domain,
            name=name,
            parent_type=previous,
            administrative=False,
        )
        location_types_dict[name] = previous = location_type
    return location_types_dict


def _setup_locations(domain, locations, location_types):
    locations_dict = {}

    def create_locations(locations, types, parent):
        for name, children in locations:
            location = Location(domain=domain, name=name, parent=parent,
                                location_type=types[0])
            location.save()
            locations_dict[name] = location.sql_location
            create_locations(children, types[1:], location)

    create_locations(locations, location_types, None)
    return locations_dict


def setup_locations_and_types(domain, location_types, locations):
    """
    Create a hierarchy of locations.

    :param location_types: A flat list of location type names
    :param locations: A (recursive) list defining the locations to be
        created.  Each entry is a (name, [child1, child2..]) tuple.
    :return: (location_types, locations) where each is a dictionary mapping
        string to object created
    """
    return (
        _setup_location_types(domain, location_types),
        _setup_locations(domain, locations, location_types)
    )


class LocationHierarchyTestCase(TestCase):
    """
    Sets up and tears down a hierarchy for you based on the two class attrs
    """
    location_type_names = []
    location_structure = []
    domain = 'test-domain'

    @classmethod
    def setUpClass(cls):
        cls.domain_obj = bootstrap_domain(cls.domain)
        cls.location_types, cls.locations = setup_locations_and_types(
            cls.domain, cls.location_type_names, cls.location_structure
        )

    @classmethod
    def tearDownClass(cls):
        cls.domain_obj.delete()
        delete_all_locations()
