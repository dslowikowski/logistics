from django import template
from django.template.loader import render_to_string
from django.conf import settings
from datetime import datetime, timedelta
from django.db.models.query_utils import Q
from logistics.apps.logistics.models import SupplyPoint, StockRequest,\
    StockRequestStatus, Product, ProductStock
from django.db.models.aggregates import Count
from collections import defaultdict
from django.db.models.expressions import F
from logistics.context_processors import custom_settings
from logistics.apps.logistics.views import get_location_children
from rapidsms.contrib.messagelog.models import Message
from logistics.apps.logistics.tables import ShortMessageTable
from django.core.urlresolvers import reverse
from logistics.apps.logistics.reports import ReportingBreakdown,\
    ProductAvailabilitySummary
from logistics.apps.malawi.util import hsas_below
register = template.Library()

def _r_2_s_helper(template, dict):
    extras = {"MEDIA_URL": settings.MEDIA_URL}
    dict.update(extras)
    dict.update(custom_settings(None))
    return render_to_string(template, dict)
    
@register.simple_tag
def aggregate_table(location, commodity_filter=None, commoditytype_filter=None):
    context = { "location": location, 
                "commodity_filter": commodity_filter,
                "commoditytype_filter": commoditytype_filter }
    context["rows"] = get_location_children(location, commodity_filter, commoditytype_filter)
    return _r_2_s_helper("logistics/partials/aggregate_table.html", context)

@register.simple_tag
def flat_table(location, commodity_filter=None, commoditytype_filter=None):
    
    class SupplyPointRow():
        
        def __init__(self, supply_point, commodity_filter, commoditytype_filter):
            self.supply_point = supply_point
            self.commodity_filter = commodity_filter
            self.commoditytype_filter = commoditytype_filter
            self._cached_stock = {}
            
        @property
        def is_active(self):
            return self.supply_point.location.is_active
        
        @property
        def name(self):
            return self.supply_point.name
        
        @property 
        def code(self):
            return self.supply_point.code
        
        @property
        def url(self):
            return reverse("malawi_hsa", args=[self.supply_point.code])

        def _call_stock_count(self, name):
            if name in self._cached_stock:
                return self._cached_stock[name]
            val = getattr(self.supply_point, name)(self.commodity_filter, self.commoditytype_filter)
            self._cached_stock[name] = val
            return val
        
        def stockout_count(self): return self._call_stock_count("stockout_count")
        def emergency_stock_count(self): return self._call_stock_count("emergency_stock_count")
        def adequate_supply_count(self): return self._call_stock_count("adequate_supply_count")
        def overstocked_count(self): return self._call_stock_count("overstocked_count")
        
        @property
        def consumption(self): 
            if self.commodity_filter is not None:
                return self.supply_point.consumption(product=self.commodity_filter,
                                                     producttype=self.commoditytype_filter)
            
        
    rows = [SupplyPointRow(SupplyPoint.objects.get(location=child), commodity_filter, commoditytype_filter)\
            for child in location.children()]
    return _r_2_s_helper("logistics/partials/aggregate_table.html", {"rows": rows})


@register.simple_tag
def reporting_rates(locations, type=None, days=30):
    # with a list of locations - display reporting
    # rates associated with those locations
    if locations:
        since = datetime.utcnow() - timedelta(days=days)
        base_points = SupplyPoint.objects.filter(location__in=locations)
        if type is not None:
            base_points = base_points.filter(type__code=type)
        if base_points.count() > 0:
            late_facilities = base_points.filter(Q(last_reported__lt=since) | Q(last_reported=None)).order_by('-last_reported','name')
            on_time_facilities = base_points.filter(last_reported__gte=since).order_by('-last_reported','name')
            return _r_2_s_helper("logistics/partials/reporting_rates.html", 
                                    {"late_facilities": late_facilities,
                                     "on_time_facilities": on_time_facilities,
                                     "graph_width": 200,
                                     "graph_height": 200,
                                     "days": days,
                                     "table_class": "minor_table" })
                                     
    return "" # no data, no report


@register.simple_tag
def reporting_breakdown(locations, type=None, days=30):
    # with a list of locations - display reporting
    # rates associated with those locations
    if locations:
        since = datetime.utcnow() - timedelta(days=days)
        base_points = SupplyPoint.objects.filter(location__in=locations)
        if type is not None:
            base_points = base_points.filter(type__code=type)
        if base_points.count() > 0:
            report = ReportingBreakdown(base_points, days)
            return _r_2_s_helper("logistics/partials/reporting_breakdown.html", 
                                 {"report": report,
                                  "graph_width": 200,
                                  "graph_height": 200,
                                  "days": days,
                                  "table_class": "minor_table" })
                                     
    return "" # no data, no report



@register.simple_tag
def order_response_stats(locations, type=None, days=30):
    """
    With a list of locations - display reporting
    rates associated with those locations.
    This method only looks at closed orders
    """
    if locations:
        since = datetime.utcnow() - timedelta(days=days)
        base_points = SupplyPoint.objects.filter(location__in=locations)
        if type is not None:
            base_points = base_points.filter(type__code=type)
        if base_points.count() > 0:
            data = []
            for sp in base_points:
                this_sp_data = defaultdict(lambda x: 0)
                this_sp_data["supply_point"] = sp
                base_reqs = StockRequest.objects.filter(supply_point=sp, 
                                                        requested_on__gte=since)
                this_sp_data["total"] = base_reqs.aggregate(Count("pk"))["pk__count"]
                # by status
                by_status = base_reqs.values('response_status').annotate(total=Count('pk'))
                for row in by_status:
                    this_sp_data[row["response_status"] if row["response_status"] else "requested"] = row["total"]
                data.append(this_sp_data)
            return _r_2_s_helper("logistics/partials/order_response_stats.html", 
                                    {"data": data })
                                     
            
    return "" # no data, no report
            
@register.simple_tag
def order_fill_stats(locations, type=None, days=30):
    """
    With a list of locations - display reporting
    rates associated with those locations.
    This method only looks at closed orders
    """
    if locations:
        since = datetime.utcnow() - timedelta(days=days)
        base_points = SupplyPoint.objects.filter(location__in=locations)
        if type is not None:
            base_points = base_points.filter(type__code=type)
        if base_points.count() > 0:
            base_reqs = StockRequest.objects.filter(supply_point__in=base_points, 
                                                    requested_on__gte=since, 
                                                    status__in=StockRequestStatus.CHOICES_CLOSED)
            totals = base_reqs.values('product').annotate(total=Count('pk'))
            stocked_out = base_reqs.filter(amount_received=0).values('product').annotate(total=Count('pk'))
            not_stocked_out = base_reqs.filter(amount_received__gt=0).exclude(response_status=StockRequestStatus.STOCKED_OUT)
            under_supplied = not_stocked_out.filter(amount_requested__gt=F('amount_received')).values('product').annotate(total=Count('pk'))
            well_supplied = not_stocked_out.filter(amount_requested=F('amount_received')).values('product').annotate(total=Count('pk'))
            over_supplied = not_stocked_out.filter(amount_requested__lt=F('amount_received')).values('product').annotate(total=Count('pk'))
            main_data = {}
            for row in totals:
                main_data[row["product"]]=defaultdict(lambda x: 0)
                main_data[row["product"]]["product"] = Product.objects.get(pk=row["product"])
                main_data[row["product"]]["total"] = row["total"]
            
            def _update_main_data(main, to_update, tag):
                for row in to_update:
                    main[row["product"]][tag] = row["total"]
            _update_main_data(main_data, stocked_out, "stocked_out")
            _update_main_data(main_data, under_supplied, "under_supplied")
            _update_main_data(main_data, well_supplied, "well_supplied")
            _update_main_data(main_data, over_supplied, "over_supplied")
            return _r_2_s_helper("logistics/partials/order_fill_stats.html", 
                                    {"data": main_data})
                                     
    return "" # no data, no report

@register.simple_tag
def stockonhand_table(supply_point):
    return _r_2_s_helper("logistics/partials/stockonhand_table_full.html", 
                         {"stockonhands": supply_point.productstock_set.all()})
    
@register.simple_tag
def recent_messages(contact, limit=5):
    return ShortMessageTable(Message.objects.filter(contact=contact, direction="I")[:limit]).as_html()

@register.simple_tag
def product_availability_summary(location):
    if not location:
        pass # TODO: probably want to disable this if things get slow
        #return '<p class="notice">To view the product availability summary, first select a district.</p>'
    
    hsas = hsas_below(location)
    summary = ProductAvailabilitySummary(hsas)
    return _r_2_s_helper("logistics/partials/product_availability_summary.html", 
                         {"summary": summary})

    