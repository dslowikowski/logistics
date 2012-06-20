from django.conf import settings
from django.http import HttpResponseRedirect, HttpResponse
from django.db.models.query_utils import Q
from django.template.loader import get_template
from django.template.context import RequestContext
from django.shortcuts import render_to_response
from django.core.urlresolvers import reverse

from rapidsms.contrib.messagelog.models import Message

from logistics.reports import DynamicProductAvailabilitySummaryByFacilitySP
from logistics.decorators import place_in_request
from logistics.models import Product
from logistics.views import MonthPager

from logistics_project.apps.tanzania.reports import SupplyPointStatusBreakdown, national_aggregate, location_aggregates
from logistics_project.apps.tanzania.tables import SupervisionTable, RandRReportingHistoryTable, NotesTable, StockOnHandTable, ProductStockColumn, ProductMonthsOfStockColumn, RandRStatusTable, DeliveryStatusTable, AggregateRandRTable, AggregateRandRTable2, AggregateSOHTable, AggregateStockoutPercentColumn, AggregateSupervisionTable, AggregateDeliveryTable, UnrecognizedMessagesTable
from logistics_project.apps.tanzania.utils import chunks, get_user_location, soh_on_time_reporting, latest_status, randr_on_time_reporting, submitted_to_msd, facilities_below, supply_points_below

from logistics_project.apps.tanzania.reporting.models import *
from logistics_project.apps.tanzania.views import *

from models import DeliveryGroups
from views import get_facilities_and_location, _generate_soh_tables, _is_district, _is_region, _is_national


class TanzaniaReport(object):
    """
    Framework object for an ILSGateway report.
    """
    slug = "foo"
    name = "bar"
    def __init__(self, request, base_context={}):
        self.request = request
        self.base_context = base_context

    def reset_context(self):
        # Stuff common to all the reports.
        self.context = self.base_context.copy()
        self.mp = MonthPager(self.request)

        # self.facs, self.location = get_facilities_and_location(self.request)
        # self.dg = DeliveryGroups(self.mp.month, facs=self.facs)
        # self.bd = SupplyPointStatusBreakdown(self.facs, self.mp.year, self.mp.month)
        request = self.request
        mp = self.mp

        org = request.GET.get('place')
        if not org:
            if request.user.get_profile() is not None:
                if request.user.get_profile().location is not None:
                    org = request.user.get_profile().location.code
                elif request.user.get_profile().supply_point is not None:
                    org = request.user.get_profile().supply_point.code
        if not org:
            # TODO: Get this from config
            org = 'MOHSW-MOHSW'

        self.location = Location.objects.get(code=org)

        org_summary = OrganizationSummary.objects.get(date__range=(mp.begin_date,mp.end_date),organization__code=org)

        soh_data = GroupData.objects.filter(group_summary__title='soh_submit',group_summary__org_summary=org_summary)
        rr_data = GroupData.objects.filter(group_summary__title='rr_submit',group_summary__org_summary=org_summary)
        delivery_data = GroupData.objects.filter(group_summary__title='deliver',group_summary__org_summary=org_summary)
        supervision_data = GroupData.objects.filter(group_summary__title='supervision',group_summary__org_summary=org_summary)

        soh_json, soh_total, soh_complete = convert_soh_data_to_pie_chart(soh_data, mp.begin_date)
        rr_json, submit_total, submit_complete, submitting_group = convert_rr_data_to_pie_chart(rr_data, mp.begin_date)
        delivery_json, delivery_total, delivery_complete, delivery_group = convert_delivery_data_to_pie_chart(delivery_data, mp.begin_date)
        supervision_json, supervision_total, supervision_complete = convert_supervision_data_to_pie_chart(supervision_data, mp.begin_date)

        total = org_summary.total_orgs
        avg_lead_time = org_summary.average_lead_time_in_days

        product_availability = ProductAvailabilityData.objects.filter(date__range=(mp.begin_date,mp.end_date), organization__code=org).order_by('product__name')
        product_dashboard = ProductAvailabilityDashboardChart.objects.filter(date__range=(mp.begin_date,mp.end_date), organization__code=org).order_by('id')

        product_json = convert_product_data_to_stack_chart(product_availability, product_dashboard)

        # TODO: fix this so it makes more sense.  chart info probably shouldnt be in db
        chart_info = product_dashboard[0]

        # TODO: don't use location like this (district summary)

        report_list = [{"name": r.name, "slug": r.slug} for r in REPORT_LIST] # hack to get around 1.3 brokenness

        self.rr_data = (submit_complete / submit_total) * 100

        self.context.update({
            "month_pager": self.mp,
            "location": self.location,
            "level": self.level,

            # "facs": self.facs,

            "rr_json": rr_json,
            "graph_width": 300, # used in pie_reporting_generic
            "graph_height": 300,

            # "dg": self.dg,
            # "bd": self.bd,

            "report_list": report_list,
            "slug": self.slug,
            "name": self.name,
            "destination_url": reverse('new_reports2', args=(self.slug,)),
            "nav_mode": "direct-param",
        })

    def common_report(self):
        # Overridden in new templates.
        pass

    def national_report(self):
        raise NotImplementedError

    def regional_report(self):
        raise NotImplementedError

    def district_report(self):
        raise NotImplementedError
    
    def view_name(self):
        pass

    @property
    def level(self):
        return self.location.type.name.lower()

    def template(self):
        if self.slug == 'randr':
            return "%s/%s-%s2.html" % (getattr(settings, 'REPORT_FOLDER'), self.slug, self.level)
        return "%s/%s-%s.html" % (getattr(settings, 'REPORT_FOLDER'), self.slug, self.level)        

    def as_view(self):
        self.reset_context()
        self.common_report()
        try:
            if self.level == 'mohsw':
                self.national_report()
            elif self.level == 'region':
                self.regional_report()
            elif self.level == 'district':
                self.district_report()
            elif self.level == 'facility':
                raise NotImplementedError
        except NotImplementedError:
            return render_to_response("%s/unimplemented.html" % getattr(settings, 'REPORT_FOLDER'), self.context, context_instance=RequestContext(self.request))
        return render_to_response(self.template(), self.context, context_instance=RequestContext(self.request))


class RandRReport(TanzaniaReport):
    name = "R&R"
    slug = "randr"
    
    def national_report(self):
        # self.context['randr_table'] = AggregateRandRTable(object_list=national_aggregate(month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)
        self.context['randr_table'] = AggregateRandRTable2(object_list=national_aggregate(month=self.mp.month, year=self.mp.year, data=self.rr_data), request=self.request, month=self.mp.month, year=self.mp.year)

    def regional_report(self):
        self.context['randr_table'] = AggregateRandRTable(object_list=location_aggregates(self.location, month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)

    def district_report(self):
        self.context["on_time"] = randr_on_time_reporting(self.dg.submitting(), self.mp.year, self.mp.month)
        self.context["randr_history_table"] = RandRReportingHistoryTable(object_list=self.dg.submitting().select_related(), request=self.request,
                                                        month=self.mp.month, year=self.mp.year, prefix="randr_history")


class SOHReport(TanzaniaReport):
    name = "Stock On Hand"
    slug = "soh"

    def common_report(self):
        self.context['max_products'] = 6
        self.context['summary'] = DynamicProductAvailabilitySummaryByFacilitySP(facilities_below(self.location).filter(contact__is_active=True).distinct(), year=self.mp.year, month=self.mp.month)
    
    def national_report(self):
        table = AggregateSOHTable(object_list=national_aggregate(month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)
        products = Product.objects.all().order_by('sms_code')
        for product in products:
            pc = AggregateStockoutPercentColumn(product, self.mp.month, self.mp.year)
            table.add_column(pc, "pc_"+product.sms_code)
        self.context['soh_table'] = table

    def regional_report(self):
        table = AggregateSOHTable(object_list=location_aggregates(self.location, month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)
        products = Product.objects.all().order_by('sms_code')
        for product in products:
            pc = AggregateStockoutPercentColumn(product, self.mp.month, self.mp.year)
            table.add_column(pc, "pc_"+product.sms_code)
        self.context['soh_table'] = table

    def district_report(self):
        tables, products, product_set, show = _generate_soh_tables(self.request, self.facs, self.mp)
        self.context.update({
            'tables': tables,
            'products': products,
            'product_set': product_set,
            'show': show,
        })


class SupervisionReport(TanzaniaReport):
    name = "Supervision"
    slug = "supervision"

    def national_report(self):
        self.context['supervision_table'] = AggregateSupervisionTable(object_list=national_aggregate(month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)

    def regional_report(self):
        self.context['supervision_table'] = AggregateSupervisionTable(object_list=location_aggregates(self.location, month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)

    def district_report(self):
        self.context["supervision_table"] = SupervisionTable(object_list=self.dg.submitting().select_related(), request=self.request,
                                            month=self.mp.month, year=self.mp.year, prefix="supervision")

class DeliveryReport(TanzaniaReport):
    name = "Delivery"
    slug = "delivery"

    def national_report(self):
        self.context['delivery_table'] = AggregateDeliveryTable(object_list=national_aggregate(month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)

    def regional_report(self):
        self.context['delivery_table'] = AggregateDeliveryTable(object_list=location_aggregates(self.location, month=self.mp.month, year=self.mp.year), request=self.request, month=self.mp.month, year=self.mp.year)

    def district_report(self):
        self.context["delivery_table"] = DeliveryStatusTable(object_list=self.dg.delivering().select_related(), request=self.request, month=self.mp.month, year=self.mp.year)


class UnrecognizedMessagesReport(TanzaniaReport):
    name = "Unrecognized SMS"
    slug = "unrecognized"

    def national_report(self):
        self.context['table'] = UnrecognizedMessagesTable(request=self.request, object_list=Message.objects.exclude(contact=None).filter(date__month=self.mp.month,
                                                                                                                   date__year=self.mp.year).filter(Q(tags__name__in=["Handler_DefaultHandler"]) | Q(tags__name__in=["Error"])))
    def regional_report(self):
        self.context['table'] = UnrecognizedMessagesTable(request=self.request, object_list=Message.objects.exclude(contact=None).filter(contact__supply_point__in=supply_points_below(self.location), date__month=self.mp.month,
                                                                                                                   date__year=self.mp.year).filter(Q(tags__name__in=["Handler_DefaultHandler"]) | Q(tags__name__in=["Error"])))
    def district_report(self):
        self.context['table'] = UnrecognizedMessagesTable(request=self.request, object_list=Message.objects.exclude(contact=None).filter(contact__supply_point__in=supply_points_below(self.location), date__month=self.mp.month,
                                                                                                                   date__year=self.mp.year).filter(Q(tags__name__in=["Handler_DefaultHandler"]) | Q(tags__name__in=["Error"])))

REPORT_LIST = [
    SOHReport,
    RandRReport,
    SupervisionReport,
    DeliveryReport,
    UnrecognizedMessagesReport
]

@place_in_request()
def new_reports(request, slug=None):
    for r in REPORT_LIST:
        if r.slug == slug:
            ri = r(request)
            return ri.as_view()
    ri = REPORT_LIST[0](request)
    return ri.as_view()
