"""
app/integrations/payment.py - Payment gateway (iyzico) integration.

Defines functions to initialize and execute payments via iyzico's API using the iyzipay SDK.
Handles creation of payment requests and interpreting responses.
"""
import iyzipay
import json
from app.config import settings

# Prepare iyzico API options from settings
iyzico_options = {
    'api_key': settings.iyzico_api_key,
    'secret_key': settings.iyzico_secret_key,
    'base_url': settings.iyzico_base_url
}


def create_payment(order_id: str, user: dict, cart_items: list, card_info: dict, shipping_address: dict,
                   billing_address: dict = None):
    """
    Create a payment request to iyzico for the given order.
    - order_id: an internal ID to send as conversationId.
    - user: user profile dict (should contain name, email, phone, etc.).
    - cart_items: list of items where each item has keys: id, name, price, qty, and maybe category info.
    - card_info: dict with credit card details (holder name, number, exp month/year, cvc).
    - shipping_address: dict with address info (contact name, address, city, country, zipCode).
    - billing_address: if separate billing info, otherwise can be same as shipping_address.

    Returns a tuple: (success: bool, result: dict).
    On success, result contains payment details (like transaction ID); on failure, result contains error info.
    """
    # If payment is disabled (no API keys configured), simulate a success for development.
    if not settings.iyzico_api_key or not settings.iyzico_secret_key:
        print("Iyzico API keys not set - skipping real payment. Order will be marked as paid (simulation).")
        return True, {"paymentId": "SIMULATED", "status": "success", "message": "Payment simulation: no real charge."}

    # Build payment request payload as per iyzico's requirements
    buyer = {
        "id": user.get('id', 'user_' + user.get('email', '')),  # unique buyer id (could use user UID)
        "name": user.get('name', ""),
        "surname": user.get('name', "").split(" ")[-1] if user.get('name') else "",  # crude split of last name
        "gsmNumber": user.get('phone', ""),
        "email": user.get('email', ""),
        "identityNumber": "11111111111",  # Dummy TCKN or identity number (should be provided by user if required)
        "lastLoginDate": "",  # could use user['last_login'] if tracked
        "registrationDate": "",  # could use user['created_at']
        "registrationAddress": shipping_address.get('address', ""),
        "ip": "0.0.0.0",  # IP is ideally the user's IP; could be passed from request context
        "city": shipping_address.get('city', ""),
        "zipCode": shipping_address.get('zipCode', "")
    }
    address = {
        "contactName": shipping_address.get('contactName', user.get('name', "")),
        "address": shipping_address.get('address', ""),
        "city": shipping_address.get('city', ""),
        "zipCode": shipping_address.get('zipCode', "")
    }
    if billing_address is None:
        billing_address = address  # use shipping as billing if not provided

    # Prepare basket items in the format iyzico expects
    basket_items = []
    for item in cart_items:
        # Each item requires: id, name, category1, category2, itemType, price
        product_name = item.get('name', 'Item')
        price_str = f"{item.get('price', 0):.2f}"
        # Determine categories for item (for simplicity, using broad category or a placeholder)
        category1 = "General"
        category2 = item.get('category', "Products")
        basket_items.append({
            "id": str(item.get('id', item.get('product_id', "")) or ""),
            "name": product_name,
            "category1": category1,
            "category2": category2,
            "itemType": "PHYSICAL",  # assuming physical product; could be VIRTUAL for services
            "price": price_str
        })

    total_price = sum(item.get('price', 0) * item.get('qty', 1) for item in cart_items)
    total_price_str = f"{total_price:.2f}"
    # In iyzico, paidPrice can include additional fees or differences; here we'll set equal to total for simplicity
    request = {
        "locale": "tr",
        "conversationId": order_id,
        "price": total_price_str,
        "paidPrice": total_price_str,
        "currency": "TRY",
        "installment": 1,
        "basketId": order_id,
        "paymentChannel": "WEB",  # or "MOBILE" depending on context
        "paymentGroup": "PRODUCT",  # could be PRODUCT or LISTING or SUBSCRIPTION
        "paymentCard": {
            "cardHolderName": card_info.get('cardHolderName'),
            "cardNumber": card_info.get('cardNumber'),
            "expireMonth": card_info.get('expireMonth'),
            "expireYear": card_info.get('expireYear'),
            "cvc": card_info.get('cvc'),
            "registerCard": "0"  # set "1" if we want to store the card with iyzico for future use
        },
        "buyer": buyer,
        "shippingAddress": address,
        "billingAddress": {
            "contactName": billing_address.get('contactName', ""),
            "address": billing_address.get('address', ""),
            "city": billing_address.get('city', ""),
            "zipCode": billing_address.get('zipCode', "")
        },
        "basketItems": basket_items
    }
    try:
        payment_response = iyzipay.Payment().create(request, iyzico_options)
    except Exception as e:
        # If the iyzico SDK call fails (network error, etc.)
        return False, {"error": str(e)}
    # The response might be a JSON string; parse it
    try:
        response_json = payment_response
        if not isinstance(payment_response, dict):
            # If payment_response is an object or bytes, convert to dict
            response_json = json.loads(
                payment_response.read().decode('utf-8') if hasattr(payment_response, 'read') else str(payment_response))
    except Exception as e:
        response_json = payment_response  # if it was already a dict
    # Check status in response
    status_val = response_json.get('status')
    if status_val == 'success':
        # Payment successful
        return True, response_json
    else:
        # Payment failed or returned an error
        error_message = response_json.get('errorMessage') or response_json.get('message') or "Payment failed"
        return False, {"status": status_val, "message": error_message}
