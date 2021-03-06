from datetime import datetime
from logistics.models import StockRequest, ContactRole,\
    ProductStock
from logistics.util import config
from logistics_project.apps.malawi.handlers.abstract.orderresponse import OrderResponseBaseHandler
from rapidsms.models import Contact

class OrderStockoutHandler(OrderResponseBaseHandler):
    """
    When a supply has been ordered and can't be filled, it is marked 
    out of stock with this handler.
    """

    keyword = "os|so|out"

    def help(self):
        self.respond(config.Messages.STOCKOUT_HELP)
        
    def handle_custom(self, text):
        now = datetime.utcnow()
        
        # Currently we just mark these stock requests stocked out.
        # Note that this has a different meaning for emergency orders
        # in which case we only confirm the emergency products.
        # However in the interest of simplicity we won't worry about that (yet?).
        pending_reqs = StockRequest.pending_requests().filter(supply_point=self.hsa.supply_point)
        for req in pending_reqs:
            req.mark_stockout(self.msg.logistics_contact, now)
        
        # if there were any emergency orders, only report those as stockouts
        # this is pretty confusing/hacky
        emergencies = pending_reqs.filter(is_emergency=True)
        stockouts = pending_reqs.filter(balance=0)
        if stockouts.count() > 0:
            reqs = stockouts
        elif emergencies.count() > 0:
            reqs = emergencies
        else:
            reqs = pending_reqs

        def _message_supervisors(message):
            supplier = self.msg.logistics_contact.supply_point.supplied_by
            if supplier is not None:
                supervisors = Contact.objects.filter(is_active=True,
                                                     supply_point__location=supplier.location,
                                                     role__in=[ContactRole.objects.get(code=config.Roles.DISTRICT_PHARMACIST),
                                                               ContactRole.objects.get(code=config.Roles.IMCI_COORDINATOR)])
                # note that if there are no supervisors registered, this will silently
                # not send notifications
                for super in supervisors:
                    super.message(message,
                                  contact=self.msg.logistics_contact.name,
                                  supply_point=self.msg.logistics_contact.supply_point.name,
                                  products=", ".join(req.product.sms_code for req in reqs))

        if emergencies.count() > 0:
            # Unable to resupply stocked out/emergency products.
            self.respond(config.Messages.HF_UNABLE_RESTOCK_EO, products=", ".join(req.product.sms_code for req in reqs))
            self.hsa.message(config.Messages.HSA_UNABLE_RESTOCK_EO, hsa=self.hsa.name,products=", ".join(req.product.sms_code for req in reqs))
            if stockouts.count() > 0:
                _message_supervisors(config.Messages.DISTRICT_UNABLE_RESTOCK_STOCKOUT)
            else:
                _message_supervisors(config.Messages.DISTRICT_UNABLE_RESTOCK_EO)
        else:
            self.respond(config.Messages.HF_UNABLE_RESTOCK_EO,
                         products=", ".join(req.product.sms_code for req in reqs))
            self.hsa.message(config.Messages.HSA_UNABLE_RESTOCK_ANYTHING, hsa=self.hsa.name)
            _message_supervisors(config.Messages.DISTRICT_UNABLE_RESTOCK_NORMAL)

            
        # this is pretty hacky, but set the SoH to 0 for the stocked out products
        # so that they show properly in things like alerts
        for req in reqs:
            self.msg.logistics_contact.supply_point.update_stock(req.product, 0)

