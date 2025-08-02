"""
app/routers/carts.py - Routes for shopping cart operations (for logged-in users).
Allows adding, removing, and viewing cart items.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.config import db
from app.schemas.cart import Cart, CartItem

router = APIRouter(prefix="/cart", tags=["Cart"])

@router.get("/", response_model=Cart)
def get_cart(current_user: dict = Depends(get_current_user)):
    """
    Retrieve the current cart for the logged-in user.
    """
    user_id = current_user['id']
    cart_doc = db.collection("carts").document(user_id).get()
    if not cart_doc.exists:
        # If no cart doc, return empty cart
        return {"user_id": user_id, "items": []}
    cart_data = cart_doc.to_dict()
    cart_data['user_id'] = user_id
    return cart_data

@router.post("/items", response_model=Cart)
def add_to_cart(product_id: str, quantity: int = 1, current_user: dict = Depends(get_current_user)):
    """
    Add a product to the user's cart (or update quantity if already in cart).
    """
    user_id = current_user['id']
    # Get product info to store name/price snapshot
    product_doc = db.collection("products").document(product_id).get()
    if not product_doc.exists or product_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Product not found")
    product = product_doc.to_dict()
    if product.get('is_upcoming'):
        raise HTTPException(status_code=400, detail="Product is not yet available for purchase")
    if product.get('stock', 0) <= 0:
        raise HTTPException(status_code=400, detail="Product is out of stock")
    cart_ref = db.collection("carts").document(user_id)
    cart_doc = cart_ref.get()
    if cart_doc.exists:
        cart = cart_doc.to_dict()
    else:
        cart = {"items": []}
    items = cart.get('items', [])
    found = False
    for item in items:
        if item['product_id'] == product_id:
            # Update existing item quantity
            new_qty = item['qty'] + quantity
            if new_qty <= 0:
                # remove item if qty goes to 0 or below
                items.remove(item)
            else:
                item['qty'] = new_qty
            found = True
            break
    if not found and quantity > 0:
        # Add new item
        item = {
            "product_id": product_id,
            "name": product.get('title', ''),
            "price": float(product.get('price', 0)),  # store current price
            "qty": quantity
        }
        items.append(item)
    cart['items'] = items
    cart_ref.set(cart)  # overwrite or create
    cart['user_id'] = user_id
    return cart

@router.put("/items/{product_id}", response_model=Cart)
def update_cart_item(product_id: str, quantity: int, current_user: dict = Depends(get_current_user)):
    """
    Update the quantity of a specific product in the cart. If quantity is 0, removes the item.
    """
    user_id = current_user['id']
    cart_ref = db.collection("carts").document(user_id)
    cart_doc = cart_ref.get()
    if not cart_doc.exists:
        raise HTTPException(status_code=404, detail="Cart is empty")
    cart = cart_doc.to_dict()
    items = cart.get('items', [])
    new_items = []
    for item in items:
        if item['product_id'] == product_id:
            if quantity > 0:
                item['qty'] = quantity
                new_items.append(item)
            # if quantity == 0, we skip adding it effectively removing it
        else:
            new_items.append(item)
    cart['items'] = new_items
    cart_ref.set(cart)
    cart['user_id'] = user_id
    return cart

@router.delete("/items/{product_id}", response_model=Cart)
def remove_cart_item(product_id: str, current_user: dict = Depends(get_current_user)):
    """
    Remove an item completely from the cart.
    """
    user_id = current_user['id']
    cart_ref = db.collection("carts").document(user_id)
    cart_doc = cart_ref.get()
    if not cart_doc.exists:
        raise HTTPException(status_code=404, detail="Cart is empty")
    cart = cart_doc.to_dict()
    items = cart.get('items', [])
    new_items = [item for item in items if item['product_id'] != product_id]
    cart['items'] = new_items
    cart_ref.set(cart)
    cart['user_id'] = user_id
    return cart
