"""
app/integrations/shipping_provider.py - Shipping tracking integration (Track123/AfterShip).

This module provides functions to interact with an external shipment tracking API.
Currently, it's a placeholder that can simulate or log tracking updates until an API key is provided.
"""
import requests
from app.config import settings, db

def register_shipment(order_id: str, tracking_number: str, carrier_code: str):
    """
    Registers a new shipment with the tracking service (if integrated).
    For Track123/AfterShip, this might involve making an API call to add the tracking number so it can be monitored.
    Here we simply log/print, as integration is not active without an API key.
    """
    if not settings.tracking_api_key:
        # Tracking integration not enabled; skip actual API call.
        print(f"[Shipping] Skipping shipment registration for order {order_id}: No API key configured.")
        return False
    # Example for AfterShip API (if it were used):
    # url = "https://api.aftership.com/v4/trackings"
    # headers = {"aftership-api-key": settings.tracking_api_key, "Content-Type": "application/json"}
    # data = {"tracking": {"tracking_number": tracking_number, "slug": carrier_code, "order_id": order_id}}
    # resp = requests.post(url, json=data, headers=headers)
    # if resp.status_code == 201:
    #     return True
    # else:
    #     print(f"AfterShip API response: {resp.status_code} {resp.text}")
    #     return False
    # For now, just return True as if registered.
    print(f"[Shipping] Registered tracking {tracking_number} with carrier {carrier_code} for order {order_id}.")
    return True

def update_tracking_statuses():
    """
    Periodic job to poll tracking updates for shipments in transit.
    This function queries all orders with a tracking number and not delivered status,
    calls the external API to get the latest status, and updates the order document.
    """
    if not settings.tracking_api_key:
        # Skip if no integration key available
        return
    orders_ref = db.collection('orders')
    # Find orders that have a tracking number and are not delivered
    query = orders_ref.where('tracking_number', '!=', None).where('shipping_status', 'in', ['shipped', 'in_transit'])
    for order_doc in query.stream():
        order = order_doc.to_dict()
        order_id = order_doc.id
        tracking_no = order.get('tracking_number')
        carrier = order.get('carrier_code')
        if not tracking_no or not carrier:
            continue
        # Call external API for status (this is pseudo-code, as actual API integration depends on provider)
        # Example pseudo-call:
        # status = external_tracking_api.get_status(tracking_no, carrier)
        # Here we simulate by toggling status or leaving it as is.
        status = "in_transit"
        # If we had actual data:
        # status = api_response.get('current_status', 'in_transit')
        if status and status != order.get('shipping_status'):
            # Update the order status in DB
            orders_ref.document(order_id).update({"shipping_status": status})
            print(f"[Shipping] Order {order_id} status updated to {status}.")
