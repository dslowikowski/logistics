from rapidsms.models import Contact
from logistics.apps.logistics.models import SupplyPoint
from logistics.apps.logistics.util import config
from logistics.apps.malawi.exceptions import MultipleHSAException
from rapidsms.contrib.locations.models import Location
from django.db.models.query_utils import Q


def get_hsa(hsa_id):
    """
    Attempt to get an HSA by code, return None if unable to find them.
    """
    # in the future we should do some massaging of this code as well
    # to catch things like o's -> 0's and such.
    try:
        sp = SupplyPoint.objects.get(code=hsa_id, type=config.hsa_supply_point_type())
        return Contact.objects.get(supply_point=sp)
    except (SupplyPoint.DoesNotExist, Contact.DoesNotExist):
        return None
    except Contact.MultipleObjectsReturned:
        # this is weird, shouldn't be possible, but who knows.
        raise MultipleHSAException("More than one HSA found with id %s" % hsa_id)

def hsas_below(location):
    """
    Given an optional location, return all HSAs below that location.
    """
    hsas = Contact.objects.filter(role__code="hsa")
    if location:
        # support up to 3 levels of parentage. this covers
        # hsa->facility-> district, which is all we allow you to select
        
        hsas = hsas.filter(Q(supply_point__location=location) | \
                           Q(supply_point__supplied_by__location=location) | \
                           Q(supply_point__supplied_by__supplied_by__location=location))
    return hsas
    
    
def get_supervisors(supply_point):
    """
    Get all supervisors at a particular facility
    """
    return supply_point.active_contact_set.filter\
                (role__code__in=config.Roles.SUPERVISOR_ROLES)

def get_districts():
    return Location.objects.filter(type__slug="district")

def get_facilities():
    return Location.objects.filter(type__slug="facility")

def get_facility_supply_points():
    return SupplyPoint.objects.filter(type__code="hf")