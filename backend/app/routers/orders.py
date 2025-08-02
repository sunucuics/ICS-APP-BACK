"""
app/routers/orders.py - Routes for order checkout (user) and order management (admin).
Handles payment process, order creation, and providing tracking info.
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from typing import List
from datetime import datetime
from app.core.security import get_current_user, get_current_admin
from app.config import db
from app.integrations import payment as payment_integration, shipping_provider
from app.schemas.order import OrderCreate, OrderOut

router = APIRouter(prefix="/orders", tags=["Orders"])

@router.post("/", response_model=OrderOut)
def checkout_order(order_data: OrderCreate, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    """
    Checkout the current cart and create an order.
    Processes payment via iyzico and creates an order record upon success.
    """
    user_id = current_user['id']
    # Retrieve cart
    cart_doc = db.collection("carts").document(user_id).get()
    if not cart_doc.exists or not cart_doc.to_dict().get('items'):
        raise HTTPException(status_code=400, detail="Cart is empty")
    cart = cart_doc.to_dict()
    cart_items = cart.get('items', [])
    # Validate stock and calculate total
    total = 0.0
    # Also prepare items list for order record
    order_items = []
    for item in cart_items:
        product_id = item['product_id']
        qty = item['qty']
        # Check product availability
        product_ref = db.collection("products").document(product_id)
        product_doc = product_ref.get()
        if not product_doc.exists or product_doc.to_dict().get('is_deleted'):
            raise HTTPException(status_code=400, detail=f"Product {product_id} no longer available")
        product = product_doc.to_dict()
        if product.get('is_upcoming'):
            raise HTTPException(status_code=400, detail=f"Product {product['title']} is not yet available for purchase")
        if product.get('stock', 0) < qty:
            raise HTTPException(status_code=400, detail=f"Not enough stock for product {product['title']}")
        price = float(product['price'])
        # Apply discount if any (similar logic as in listing)
        final_price = price
        best_percent = 0
        import datetime
        now = datetime.datetime.utcnow()
        disc_q = db.collection("discounts").where("active", "==", True).where("target_id", "in", [product_id, product.get('category_id')]).stream()
        for d in disc_q:
            disc = d.to_dict()
            start_at = disc.get('start_at'); end_at = disc.get('end_at')
            if start_at and now < start_at: continue
            if end_at and now > end_at: continue
            if disc['target_type'] == 'product' and disc['target_id'] == product_id:
                best_percent = max(best_percent, disc['percent'])
                break
            elif disc['target_type'] == 'category' and disc['target_id'] == product.get('category_id'):
                best_percent = max(best_percent, disc['percent'])
        if best_percent > 0:
            final_price = round(price * (100 - best_percent) / 100, 2)
        total += final_price * qty
        order_items.append({
            "product_id": product_id,
            "name": product.get('title', ''),
            "qty": qty,
            "price": final_price
        })
    # Process payment through iyzico
    # Prepare card info
    card_info = {
        "cardHolderName": order_data.card_holder_name,
        "cardNumber": order_data.card_number,
        "expireMonth": order_data.expire_month,
        "expireYear": order_data.expire_year,
        "cvc": order_data.cvc
    }
    # Get user's chosen shipping address
    shipping_address = None
    if order_data.address_id:
        # find the address in user's profile
        user_doc = db.collection("users").document(user_id).get()
        if user_doc.exists:
            user_profile = user_doc.to_dict()
            for addr in user_profile.get('addresses', []):
                if addr.get('id') == order_data.address_id:
                    shipping_address = addr
                    break
    if not shipping_address:
        # If not provided or not found, use first address or throw error
        user_profile = current_user  # current_user has addresses loaded (in security dependency)
        addresses = user_profile.get('addresses', [])
        if addresses:
            shipping_address = addresses[0]
        else:
            raise HTTPException(status_code=400, detail="Shipping address not provided")
    # Use shipping_address to build contact info
    address_info = {
        "contactName": shipping_address.get('name') or current_user.get('name', ""),
        "address": shipping_address.get('address'),
        "city": shipping_address.get('city'),
        "country": shipping_address.get('country'),
        "zipCode": shipping_address.get('zipCode')
    }
    # Execute payment
    order_id = db.collection("orders").document().id  # generate an ID for conversation reference
    success, payment_result = payment_integration.create_payment(
        order_id=order_id,
        user=current_user,
        cart_items=order_items,
        card_info=card_info,
        shipping_address=address_info,
        billing_address=None
    )
    if not success:
        raise HTTPException(status_code=402, detail=f"Payment failed: {payment_result.get('message')}")
    # Payment success: create order record
    order_data_doc = {
        "user_id": user_id,
        "items": order_items,
        "total": round(total, 2),
        "payment_status": "paid" if success else "failed",
        "shipping_status": "pending",  # not shipped yet
        "tracking_number": None,
        "carrier_code": None,
        "created_at": datetime.utcnow()
    }
    db.collection("orders").document(order_id).set(order_data_doc)
    # Update stock for each product (deduct quantities sold)
    for item in order_items:
        product_ref = db.collection("products").document(item['product_id'])
        # We perform an atomic update (could also use transaction for stronger consistency)
        product_ref.update({"stock": firestore.Increment(-item['qty']) if hasattr(__import__('firebase_admin').firestore, 'Increment') else product_ref.get().to_dict().get('stock', 0) - item['qty']})
    # Clear user's cart
    db.collection("carts").document(user_id).delete()
    # Optionally, start a background task to register shipment with tracking provider (if available)
    background_tasks.add_task(shipping_provider.register_shipment, order_id, "", "")
    # Prepare return data
    order_data_doc["id"] = order_id
    return order_data_doc

@router.get("/", response_model=List[OrderOut])
def list_my_orders(current_user: dict = Depends(get_current_user)):
    """
    List all orders of the current logged-in user.
    """
    user_id = current_user['id']
    orders_ref = db.collection("orders").where("user_id", "==", user_id)
    docs = orders_ref.stream()
    orders = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        orders.append(data)
    # Sort by created_at descending perhaps
    orders.sort(key=lambda o: o.get('created_at', datetime.min), reverse=True)
    return orders

@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """
    Retrieve a specific order by ID (if it belongs to the current user).
    """
    doc = db.collection("orders").document(order_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order = doc.to_dict()
    # Only allow owner or admin to fetch
    if order.get('user_id') != current_user['id'] and current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Not allowed to view this order")
    order['id'] = order_id
    return order

@router.get("/{order_id}/tracking")
def get_tracking_info(order_id: str, current_user: dict = Depends(get_current_user)):
    """
    Get the latest tracking status for a given order.
    Accessible to the order owner or admin.
    """
    doc = db.collection("orders").document(order_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order = doc.to_dict()
    if order.get('user_id') != current_user['id'] and current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Not authorized to view tracking for this order")
    tracking_number = order.get('tracking_number')
    carrier_code = order.get('carrier_code')
    status = order.get('shipping_status')
    if not tracking_number or not carrier_code:
        return {"detail": "No tracking information available for this order."}
    # If integration was active, we might call an API to refresh status here.
    # For now, just return what we have in the order record.
    return {
        "tracking_number": tracking_number,
        "carrier": carrier_code,
        "status": status
    }

# Admin sub-router for orders management
admin_router = APIRouter(prefix="/orders", dependencies=[Depends(get_current_admin)])

@admin_router.get("/", response_model=List[OrderOut])
def list_all_orders():
    """
    Admin endpoint to list all orders.
    Could be filtered by status or date in future.
    """
    docs = db.collection("orders").stream()
    orders = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        orders.append(data)
    orders.sort(key=lambda o: o.get('created_at', datetime.min), reverse=True)
    return orders

@admin_router.put("/{order_id}/shipping")
def update_order_shipping(order_id: str, tracking_number: str = None, carrier_code: str = None, status: str = None):
    """
    Admin endpoint to update shipping info of an order.
    This can add a tracking number and carrier, or update the shipping status.
    """
    order_ref = db.collection("orders").document(order_id)
    doc = order_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    updates = {}
    if tracking_number is not None:
        updates['tracking_number'] = tracking_number
    if carrier_code is not None:
        updates['carrier_code'] = carrier_code
    if status is not None:
        updates['shipping_status'] = status
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    order_ref.update(updates)
    # If tracking number and carrier were set, optionally register with tracking service
    if tracking_number and carrier_code:
        shipping_provider.register_shipment(order_id, tracking_number, carrier_code)
    return {"detail": "Order shipping info updated."}
