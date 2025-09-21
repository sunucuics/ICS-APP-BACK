# app/services/orders_helpers.py
from __future__ import annotations

from typing import Any, Dict, List, Optional , Tuple
from firebase_admin import firestore
from fastapi.responses import JSONResponse
import inspect
from google.cloud.firestore_v1.base_query import FieldFilter
from decimal import Decimal
from backend.app.config import db , settings
from backend.app.routers import users as users_router
from backend.app.schemas.order import OrderItem, OrderOut
from backend.app.integrations.shipping_provider import create_shipment_with_setorder  # sizdeki yol farklıysa düzeltin
from google.cloud.firestore_v1 import SERVER_TIMESTAMP


# Bu modül dışına açık edilenler
__all__ = [
    "coerce_item",
    "calc_totals",
    "order_doc_to_out",
    "aras_single_package",
    "extract_uid",
    "extract_name",
    "extract_phone",
    "resolve_active_address",
    "fetch_cart_items",
    "clear_cart",
    "auto_after_create",
    "ensure_aras_env_or_raise",
    "build_order_doc",
]


# ──────────────────────────────────────────────────────────────────────────────
# Düşük seviye yardımcılar
# ──────────────────────────────────────────────────────────────────────────────

def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    # Pydantic/BaseModel
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        try:
            return obj.dict()
        except Exception:
            pass
    # Generic object
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return {}

def coerce_item(raw: Any) -> Dict[str, Any]:
    """
    Min alanlar: product_id/title/quantity/unit_price
    Alias'lar:  name=title, price=unit_price, total=line_total  (OrderOut uyumluluğu)
    """
    d = raw if isinstance(raw, dict) else raw.dict()
    qty = max(1, int(d.get("quantity", 1)))
    price_dec = Decimal(str(d.get("unit_price", d.get("price", 0))))
    line_total = (price_dec * qty).quantize(Decimal("0.01"))
    currency = (d.get("currency") or "TRY").upper()
    title = d.get("title") or d.get("name") or d.get("product_name") or "Ürün"
    unit_price = float(price_dec)

    return {
        "product_id": d.get("product_id") or d.get("id"),
        "title": title,
        "name": title,                 # ← alias (response şeması bekliyor)
        "sku": d.get("sku"),
        "variant_id": d.get("variant_id"),
        "quantity": qty,
        "unit_price": unit_price,
        "price": unit_price,           # ← alias (response şeması bekliyor)
        "currency": currency,
        "image_url": d.get("image_url"),
        "options": d.get("options") or {},
        "line_total": float(line_total),
        "total": float(line_total),    # ← alias
    }


def _normalize_items(raw_items):
    """
    Her bir satırı dict'e çevirir ve OrderItemOut ile uyumlu alias'ları tamamlar.
    OrderItem / BaseModel gelse bile dict'e zorlar.
    """
    items_out = []
    for it in (raw_items or []):
        # 1) dict'e çevir
        if isinstance(it, dict):
            item = dict(it)
        else:
            try:
                item = it.dict()
            except Exception:
                try:
                    item = dict(it)
                except Exception:
                    # okunamayanı atla (hata fırlatma)
                    continue

        # 2) Zorunlu/alias alanları
        title = item.get("title") or item.get("name") or item.get("product_name") or "Ürün"
        item.setdefault("title", title)
        item.setdefault("name", title)

        # price / unit_price
        price = item.get("unit_price", item.get("price", 0.0))
        try:
            price = float(price)
        except Exception:
            price = 0.0
        item["unit_price"] = price
        item.setdefault("price", price)

        # quantity
        try:
            qty = int(item.get("quantity", 1))
        except Exception:
            qty = 1
        item["quantity"] = max(1, qty)

        # line_total / total
        lt = item.get("line_total")
        if lt is None:
            lt = price * item["quantity"]
        try:
            lt = float(lt)
        except Exception:
            lt = 0.0
        item["line_total"] = lt
        item.setdefault("total", lt)

        # product_id garanti et
        if not item.get("product_id"):
            item["product_id"] = item.get("id") or item.get("productId")

        # options dict olsun
        if not isinstance(item.get("options"), dict):
            item["options"] = {}

        items_out.append(item)
    return items_out


def calc_totals(items: List[Dict[str, Any]], currency: str = "TRY") -> Dict[str, Any]:
    """
    Sipariş tutar özetini hesaplar. Vergi/indirim kuralınız varsa burada uygulayın.
    """
    subtotal = sum(Decimal(str(it["line_total"])) for it in items)
    discount = Decimal("0.00")
    shipping = Decimal("0.00")
    tax = Decimal("0.00")
    grand_total = (subtotal - discount + shipping + tax).quantize(Decimal("0.01"))

    return {
        "item_count": int(sum(int(it["quantity"]) for it in items)),
        "subtotal": float(subtotal),
        "discount": float(discount),
        "shipping": float(shipping),
        "tax": float(tax),
        "grand_total": float(grand_total),
        "currency": currency.upper(),
    }


def order_doc_to_out(doc):
    return _order_doc_to_out(doc)


def aras_single_package(
    receiver: Dict[str, Any],
    integration_code: str,
    items: List[Dict[str, Any]],
) -> Tuple[bool, Optional[str], str]:
    """
    Aras'a TEK paket/tek takip numarası açar.
    create_shipment_with_setorder fonksiyonunuz içte item başına paket üretiyorsa
    oradaki döngüyü kaldırın ve 'contents' parametresiyle tek çağrı yapın.
    """
    contents = [
        {"description": it["title"], "quantity": it["quantity"], "sku": it.get("sku")}
        for it in items
    ]
    return create_shipment_with_setorder(
        receiver=receiver,
        integration_code=integration_code,
        contents=contents,  # fonksiyonunuz **kwargs kabul ediyorsa bu bilgiyi alır
    )


def enrich_items_from_products(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    product_id ile 'products/{slug}/items' alt koleksiyonundan özet bilgileri çekip satıra snapshot olarak ekler.
    Yoksa sessizce geçer. (UYGULAMA: sipariş oluştururken çağır)
    """
    try:
        ids = {it.get("product_id") for it in items if it.get("product_id")}
        cache: Dict[str, Dict[str, Any]] = {}
        
        # Collection group query ile tüm products/{slug}/items alt koleksiyonlarını ara
        colg = db.collection_group("items")
        for pid in ids:
            if not pid:
                continue
            try:
                # Collection group query ile product_id'ye göre ara
                docs = list(colg.where(filter=FieldFilter("id", "==", str(pid))).limit(1).stream())
                if docs:
                    cache[str(pid)] = docs[0].to_dict() or {}
            except Exception:
                continue

        for it in items:
            pid = str(it.get("product_id"))
            pdata = cache.get(pid)
            if not pdata:
                continue

            # Görsel ve başlıkları eksikse üret
            it.setdefault("image_url", pdata.get("image_url") or (pdata.get("images") or [None])[0])
            it.setdefault("title", pdata.get("title") or pdata.get("name"))
            it["name"] = it.get("name") or it.get("title") or pdata.get("title") or pdata.get("name") or "Ürün"
            it.setdefault("sku", it.get("sku") or pdata.get("sku"))

            # Ürün snapshot (admin panelde detay için)
            it["product"] = {
                "title": pdata.get("title") or pdata.get("name"),
                "slug": pdata.get("slug"),
                "brand": pdata.get("brand"),
                "category": pdata.get("category"),
                "attributes": pdata.get("attributes") or pdata.get("specs"),
                "images": pdata.get("images"),
            }
    except Exception:
        # Bu enrichment hiçbir zaman siparişi düşürmesin
        pass

    return items

def extract_uid(principal) -> Optional[str]:
    """
    principal → uid/id/user_id/sub nerede ise onu döndürür.
    Hem attribute hem dict key kontrol eder.
    """
    if principal is None:
        return None
    # 1) Attribute yoluyla hızlı kontrol
    for attr in ("uid", "id", "user_id", "userId", "sub"):
        val = getattr(principal, attr, None)
        if val:
            return str(val)

    # 2) Dict/Pydantic yoluyla
    d = _as_dict(principal)
    for key in ("uid", "id", "user_id", "userId", "sub"):
        if d.get(key):
            return str(d[key])

    # 3) Olası gömülü claim alanları
    for blob_key in ("claims", "decoded", "token", "firebase", "auth", "context"):
        blob = d.get(blob_key) or _as_dict(getattr(principal, blob_key, None))
        if isinstance(blob, dict):
            for key in ("uid", "sub", "user_id"):
                if blob.get(key):
                    return str(blob[key])
    return None


def extract_name(principal) -> Optional[str]:
    """
    display name farklı alan adlarıyla gelebilir.
    """
    if principal is None:
        return None
    for attr in ("name", "display_name", "displayName", "full_name", "fullName"):
        val = getattr(principal, attr, None)
        if val:
            return str(val)
    d = _as_dict(principal)
    for key in ("name", "display_name", "displayName", "full_name", "fullName"):
        if d.get(key):
            return str(d[key])
    return None


def extract_phone(principal) -> Optional[str]:
    """
    phone / phone_number / phoneNumber destekler.
    """
    if principal is None:
        return None
    for attr in ("phone", "phone_number", "phoneNumber"):
        val = getattr(principal, attr, None)
        if val:
            return str(val)
    d = _as_dict(principal)
    for key in ("phone", "phone_number", "phoneNumber"):
        if d.get(key):
            return str(d[key])
    return None


def resolve_active_address(principal) -> Optional[Dict[str, Any]]:
    """
    Aktif adresi toleranslı biçimde çözer.
    Arama sırası:
      1) principal.active_address / principal.address / principal.shipping_address
      2) principal.*_address_id veya users/{uid}.*_address_id → id ile adresi getir
      3) users/{uid}.active_address
      4) users/{uid}.addresses (array) → is_active/is_default/selected → yoksa ilk eleman
      5) users/{uid}/addresses alt koleksiyonu:
         - is_active=True → is_default=True → selected=True → yoksa ilk doküman
      6) (ops) addresses kök koleksiyonu user_id=uid → ilk doküman
    """
    uid = extract_uid(principal)  # ← doğrudan kullan, import yok
    if not uid:
        return None

    # 1) Principal içi doğrudan adres objesi
    p_d = _as_dict(principal)
    for k in ("active_address", "address", "shipping_address"):
        if p_d.get(k):
            return p_d[k]

    # 2) ID ile referans verilen adres (principal veya user doc)
    addr_id = None
    for k in ("active_address_id", "selected_address_id", "default_address_id", "address_id"):
        if p_d.get(k):
            addr_id = p_d[k]
            break

    user_doc = db.collection("users").document(uid).get()
    user_data = user_doc.to_dict() or {} if user_doc.exists else {}

    if not addr_id:
        for k in ("active_address_id", "selected_address_id", "default_address_id", "address_id"):
            if user_data.get(k):
                addr_id = user_data[k]
                break

    # 3) users/{uid}.active_address (gömülü obje)
    if user_data.get("active_address"):
        return user_data["active_address"]

    # 2.a) Subcollection’dan id ile çek
    if addr_id:
        sub_ref = db.collection("users").document(uid).collection("addresses").document(str(addr_id)).get()
        if sub_ref.exists:
            return sub_ref.to_dict()
        # 2.b) Kök 'addresses' koleksiyonu (opsiyonel)
        try:
            root_ref = db.collection("addresses").document(str(addr_id)).get()
            if root_ref.exists:
                return root_ref.to_dict()
        except Exception:
            pass

    # 4) users/{uid}.addresses ARRAY alanı
    arr = user_data.get("addresses")
    if isinstance(arr, list) and arr:
        for flag in ("is_active", "isDefault", "is_default", "selected"):
            chosen = next((a for a in arr if isinstance(a, dict) and a.get(flag)), None)
            if chosen:
                return chosen
        return arr[0]  # flag yoksa ilkini dön

    # 5) users/{uid}/addresses alt koleksiyonu
    try:
        docs = list(
            db.collection("users").document(uid).collection("addresses")
              .where(filter=FieldFilter("is_active", "==", True)).limit(1).stream()
        )
        if docs:
            return docs[0].to_dict()

        docs = list(
            db.collection("users").document(uid).collection("addresses")
              .where(filter=FieldFilter("is_default", "==", True)).limit(1).stream()
        )
        if docs:
            return docs[0].to_dict()

        docs = list(
            db.collection("users").document(uid).collection("addresses")
              .where(filter=FieldFilter("selected", "==", True)).limit(1).stream()
        )
        if docs:
            return docs[0].to_dict()

        docs = list(
            db.collection("users").document(uid).collection("addresses")
              .limit(1).stream()
        )
        if docs:
            return docs[0].to_dict()
    except Exception:
        pass

    # 6) (ops) addresses kök koleksiyonu (user_id=uid)
    try:
        docs = list(
            db.collection("addresses")
              .where(filter=FieldFilter("user_id", "==", uid))
              .limit(1).stream()
        )
        if docs:
            return docs[0].to_dict()
    except Exception:
        pass

    return None




def fetch_cart_items(uid: str) -> List[Dict[str, Any]]:
    """
    Sepet item'larını döndürür. Aşağıdaki olası yapılara bakar:
      1) carts/{uid} dokümanında 'items' (array) alanı
      2) carts/{uid}/items alt koleksiyonu (dokümanlar → dict)
      3) users/{uid}.cart.items
    """
    # 1) carts/{uid}.items
    cart_doc = db.collection("carts").document(uid).get()
    if cart_doc.exists:
        data = cart_doc.to_dict() or {}
        items = data.get("items")
        if isinstance(items, list) and items:
            return items

    # 2) carts/{uid}/items alt koleksiyonu
    try:
        coll = (
            db.collection("carts")
              .document(uid)
              .collection("items")
              .stream()
        )
        items = [d.to_dict() for d in coll]
        if items:
            return items
    except Exception:
        pass

    # 3) users/{uid}.cart.items
    user_doc = db.collection("users").document(uid).get()
    if user_doc.exists:
        data = user_doc.to_dict() or {}
        cart = data.get("cart") or {}
        items = cart.get("items")
        if isinstance(items, list) and items:
            return items

    return []


def clear_cart(uid: str) -> None:
    """
    Sepeti temizler. Hem doküman alanını hem de alt koleksiyonu siler.
    """
    # carts/{uid}.items → []
    db.collection("carts").document(uid).set({"items": []}, merge=True)

    # carts/{uid}/items alt koleksiyonunu boşalt
    try:
        coll = (
            db.collection("carts")
              .document(uid)
              .collection("items")
              .stream()
        )
        batch = db.batch()
        count = 0
        for d in coll:
            batch.delete(d.reference)
            count += 1
            if count % 400 == 0:  # Firestore batch limit güvenliği
                batch.commit()
                batch = db.batch()
        if count % 400 != 0:
            batch.commit()
    except Exception:
        pass


def auto_after_create(order_id: str, integration_code: str) -> None:
    """
    Opsiyonel otomasyon kancası. Varsa kendi otomasyon fonksiyonunuzu burada çağırın.
    Bu stub, var/yok durumunda hataya düşmemesi için güvenli bırakılmıştır.
    """
    try:
        # from backend.app.services.automation import after_order_create
        # after_order_create(order_id=order_id, integration_code=integration_code)
        return
    except Exception:
        return


def ensure_aras_env_or_raise() -> str:
    """
    ARAS_ENV doğrulaması. Geçersizse hata fırlatır.
    """
    allowed = {"TEST", "SANDBOX", "PROD", "PRODUCTION", "LIVE"}
    env = (settings.ARAS_ENV or "").upper()
    if env not in allowed:
        raise ValueError(
            f"Geçersiz ARAS_ENV: {settings.ARAS_ENV}. İzin verilenler: {', '.join(sorted(allowed))}"
        )
    return env


def build_order_doc(
    *,
    uid: str,
    order_id: str,
    addr: Dict[str, Any],
    items: List[Dict[str, Any]],
    totals: Dict[str, Any],
    tracking_no: str,
    note: Optional[str],
    simulated: bool,
    checkout_id: Optional[str],
    log_msg: str,
    principal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Firestore'a yazılacak sipariş dokümanını derler.
    """
    # Müşteri bilgilerini ekle
    customer_info = {}
    if principal:
        customer_info = {
            "customer_name": extract_name(principal) or "Müşteri",
            "customer_phone": extract_phone(principal) or "",
            "customer_email": principal.get("email") or "",
        }
    
    doc = {
        "user_id": uid,
        "status": "Hazırlanıyor",
        "integration_code": order_id,
        "address": addr,
        "items": items,
        "totals": totals,
        **customer_info,  # Müşteri bilgilerini ekle
        "shipment": {
            "provider": "Aras Kargo",
            "tracking_number": tracking_no,
            "status": "label_created",
            "simulated": bool(simulated),
            "log": log_msg,
        },
        "note": note,
        "created_at": SERVER_TIMESTAMP,
        "updated_at": SERVER_TIMESTAMP,
        "_log": log_msg,
    }
    if checkout_id:
        doc["_checkout_id"] = checkout_id
    return doc


def _fetch_active_address(uid: str) -> Dict[str, Any] | None:
    user_doc = db.collection("users").document(uid).get()
    if user_doc.exists:
        u = user_doc.to_dict() or {}
        cur_id = u.get("current_address_id") or u.get("active_address_id")
        if cur_id:
            d = (
                db.collection("users")
                .document(uid)
                .collection("addresses")
                .document(cur_id)
                .get()
            )
            if d.exists:
                return d.to_dict() | {"id": d.id}

    q = (
        db.collection("users")
        .document(uid)
        .collection("addresses")
        .where("is_current", "==", True)
        .limit(1)
        .get()
    )
    if q:
        d = q[0]
        return d.to_dict() | {"id": d.id}

    q2 = (
        db.collection("users")
        .document(uid)
        .collection("addresses")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(1)
        .get()
    )
    if q2:
        d = q2[0]
        return d.to_dict() | {"id": d.id}
    return None


def _call_users_current_address(principal):
    fn = getattr(users_router, "get_current_address", None)
    if not callable(fn):
        raise RuntimeError("users.get_current_address bulunamadı.")

    try:
        sig = inspect.signature(fn)
        if len(sig.parameters) == 0:
            resp = fn()
        else:
            first_param_name = next(iter(sig.parameters))
            resp = fn(**{first_param_name: principal})
    except Exception as e:
        raise RuntimeError(f"users.get_current_address çağrısı başarısız: {e}") from e

    if isinstance(resp, dict):
        return resp
    try:
        if hasattr(resp, "model_dump"):
            return resp.model_dump()
        if hasattr(resp, "dict"):
            return resp.dict()
    except Exception:
        pass
    if isinstance(resp, JSONResponse):
        try:
            return resp.body and JSONResponse.render(resp, None)
        except Exception:
            pass
    raise RuntimeError("users.get_current_address beklenen formatta veri döndürmedi.")


def _resolve_active_address(principal) -> Dict[str, Any] | None:
    uid = _extract_uid(principal)
    if not uid:
        return None

    addr = _fetch_active_address(uid)
    if addr:
        return addr

    try:
        resp = _call_users_current_address(principal)
        if isinstance(resp, dict):
            if resp.get("city"):
                return resp
            inner = resp.get("address") if isinstance(resp.get("address"), dict) else None
            if inner and inner.get("city"):
                return inner
    except Exception:
        raise

    try:
        udoc = db.collection("users").document(uid).get()
        if udoc.exists:
            udata = udoc.to_dict() or {}
            for key in ("address", "shipping_address", "current_address"):
                ad = udata.get(key)
                if isinstance(ad, dict) and ad.get("city"):
                    return ad
    except Exception:
        pass

    return None


def _extract_uid(principal) -> Optional[str]:
    if principal is None:
        return None
    if isinstance(principal, dict):
        for k in ("id", "uid", "user_id", "sub"):
            v = principal.get(k)
            if v:
                return str(v)
        user = principal.get("user")
        if isinstance(user, dict):
            return str(
                user.get("id") or user.get("uid") or user.get("user_id") or ""
            )
        return None
    for k in ("id", "uid", "user_id", "sub"):
        v = getattr(principal, k, None)
        if v:
            return str(v)
    user = getattr(principal, "user", None)
    if user:
        if isinstance(user, dict):
            return str(
                user.get("id") or user.get("uid") or user.get("user_id") or ""
            )
        return str(
            getattr(user, "id", None)
            or getattr(user, "uid", None)
            or getattr(user, "user_id", None)
            or ""
        )
    return None


def _extract_name(principal) -> Optional[str]:
    if principal is None:
        return None
    if isinstance(principal, dict):
        return principal.get("name") or principal.get("full_name") or (
            (principal.get("user") or {}).get("name")
        )
    return (
        getattr(principal, "name", None)
        or getattr(principal, "full_name", None)
        or getattr(getattr(principal, "user", None), "name", None)
    )


def _extract_phone(principal) -> Optional[str]:
    if principal is None:
        return None
    if isinstance(principal, dict):
        return principal.get("phone") or (principal.get("user") or {}).get("phone")
    return getattr(principal, "phone", None) or getattr(
        getattr(principal, "user", None), "phone", None
    )


def _to_order_item(d: Dict[str, Any]) -> OrderItem:
    return OrderItem(
        product_id=str(d.get("product_id") or d.get("id") or d.get("sku") or ""),
        name=d.get("name") or d.get("title") or "Ürün",
        quantity=int(d.get("quantity") or d.get("qty") or 1),
        price=float(d.get("price") or d.get("unit_price") or 0.0),
    )


def _fetch_cart_items(uid: str) -> List[OrderItem]:
    cart_doc = db.collection("carts").document(uid).get()
    if cart_doc.exists:
        data = cart_doc.to_dict() or {}
        items = data.get("items") or []
        return [_to_order_item(x) for x in items if isinstance(x, dict)]

    try:
        it_q = (
            db.collection("users")
            .document(uid)
            .collection("cart_items")
            .stream()
        )
        items = [_to_order_item(doc.to_dict() or {}) for doc in it_q]
        if items:
            return items
    except Exception:
        pass

    try:
        q = db.collection("carts").where("user_id", "==", uid).limit(1).get()
        if q:
            data = q[0].to_dict() or {}
            items = data.get("items") or []
            return [_to_order_item(x) for x in items if isinstance(x, dict)]
    except Exception:
        pass

    return []


def _clear_cart(uid: str) -> None:
    ref = db.collection("carts").document(uid)
    snap = ref.get()
    if snap.exists:
        ref.update({"items": []})
        return

    try:
        batch = db.batch()
        items = (
            db.collection("users")
            .document(uid)
            .collection("cart_items")
            .stream()
        )
        did = False
        for it in items:
            batch.delete(it.reference)
            did = True
        if did:
            batch.commit()
            return
    except Exception:
        pass

    try:
        q = db.collection("carts").where("user_id", "==", uid).limit(1).get()
        if q:
            q[0].reference.update({"items": []})
    except Exception:
        pass


def _order_doc_to_out(doc):
    """
    Firestore doc → temiz dict (OrderOut ile uyumlu). HİÇBİR Pydantic nesnesi dönmez.
    """
    data = doc.to_dict() if hasattr(doc, "to_dict") else doc
    if not data:
        raise ValueError("Boş sipariş dokümanı.")

    shipment = data.get("shipment") or {}
    shipping_provider = data.get("shipping_provider") or shipment.get("provider")
    tracking_number = data.get("tracking_number") or shipment.get("tracking_number")

    # ITEM'LERİ normalize et (OrderItem/ eski kayıtlar da düzgün döner)
    items_out = _normalize_items(data.get("items", []))

    return {
        "id": data.get("integration_code") or getattr(doc, "id", None),
        "user_id": data.get("user_id"),
        "status": data.get("status") or "Hazırlanıyor",

        "shipping_provider": shipping_provider,
        "tracking_number": tracking_number,
        "integration_code": data.get("integration_code"),

        "address": data.get("address") or {},
        "items": items_out,

        "totals": data.get("totals"),
        "shipment": shipment,

        "note": data.get("note"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "_checkout_id": data.get("_checkout_id"),
        "_log": data.get("_log"),
    }


def _doc_to_out(doc):
    return _order_doc_to_out(doc)

def _map_aras_status(status_text: str) -> str:
    """
    Aras'tan gelen metin durumunu uygulama statülerimize eşler.
    'Hazırlanıyor' → ilk sipariş anı,
    İlk kabul/scan/transfer/şubeye giriş → 'Kargoya Verildi'
    Dağıtım → 'Dağıtımda'
    Teslim → 'Teslim Edildi'
    """
    t = (status_text or "").lower()
    if "teslim" in t:
        return "Teslim Edildi"
    if "dağıtım" in t:
        return "Dağıtımda"
    if "kabul" in t or "transfer" in t or "yolda" in t or "şube" in t or "hub" in t:
        return "Kargoya Verildi"
    return "Hazırlanıyor"
