# app/integrations/shipping_provider.py
"""
Aras Kargo SOAP entegrasyonu (minimum).
- create_shipment_with_setorder(receiver, integration_code): SetOrder çağrısı yapar.
  Başarıda takip numarasını (InvoiceKey/Barcode) döndürür.
"""

from __future__ import annotations
from datetime import date
import re
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional, Tuple

import requests

from backend.app.config import settings

_NS = {"t": "http://tempuri.org/"}  # Aras SOAP namespace


def _only_digits(s: Optional[str]) -> str:
    """Telefon gibi alanları sadece rakama indirger (maks 11 hane)."""
    if not s:
        return ""
    return re.sub(r"\D+", "", s)[:11]


def _compose_address(addr: Dict[str, Any]) -> str:
    """get_current_address çıktısını okunur tek satıra çevirir."""
    parts = [
        addr.get("neighborhood"),
        addr.get("street"),
        f"No:{addr.get('buildingNo')}" if addr.get("buildingNo") else None,
        f"Kat:{addr.get('floor')}" if addr.get("floor") else None,
        f"Daire:{addr.get('apartment')}" if addr.get("apartment") else None,
        addr.get("district"),
        addr.get("city"),
        addr.get("zipCode"),
    ]
    return ", ".join([p for p in parts if p])


def create_shipment_with_setorder(
    *,
    receiver: Dict[str, Any],
    integration_code: Optional[str] = None,
) -> Tuple[bool, Optional[str], str]:
    """
    Aras 'SetOrder' ile gönderi oluşturur.

    Parametreler
    ----------
    receiver:
      {
        "name": "Alıcı Ad Soyad",
        "phone": "5xx...",
        "address": {  # get_current_address çıktısı
          "label": "...",
          "name": "...",
          "city": "...",
          "zipCode": "...",
          "district": "...",
          "neighborhood": "...",
          "street": "...",
          "buildingNo": "...",
          "floor": "...",
          "apartment": "...",
          "note": "...",
          "id": "..."
        }
      }
    integration_code:
      Bizim sistemdeki sipariş benzersiz kodu (yoksa otomatik UUID üretir).
      Aras tarafında sonradan sorgulamak için çok kullanışlıdır.

    Dönüş
    -----
    (success: bool, tracking_number: Optional[str], message: str)
    """
    if not settings.ARAS_USERNAME or not settings.ARAS_PASSWORD:
        return (False, None, "ARAS kimlik bilgileri (.env) eksik.")

    url = settings.ARAS_BASE_URL

    addr = receiver.get("address") or {}
    receiver_name = receiver.get("name") or addr.get("name") or "Müşteri"
    phone = _only_digits(receiver.get("phone")) or _only_digits(addr.get("phone"))
    # telefon hiç yoksa boş string gönderiyoruz; mümkünse kullanıcı profilinden telefon yazdırın.

    city = addr.get("city") or ""
    town = addr.get("district") or ""
    neighborhood = addr.get("neighborhood") or ""
    street = addr.get("street") or ""

    receiver_address = _compose_address(addr)
    integ_code = integration_code or str(uuid.uuid4())

    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <SetOrder xmlns="http://tempuri.org/">
      <orderInfo>
        <Order>
          <UserName>{settings.ARAS_USERNAME}</UserName>
          <Password>{settings.ARAS_PASSWORD}</Password>

          <ReceiverName>{receiver_name}</ReceiverName>
          <ReceiverAddress>{receiver_address}</ReceiverAddress>
          <ReceiverPhone1>{phone}</ReceiverPhone1>
          <ReceiverPhone2></ReceiverPhone2>
          <ReceiverPhone3></ReceiverPhone3>

          <ReceiverCityName>{city}</ReceiverCityName>
          <ReceiverTownName>{town}</ReceiverTownName>
          <ReceiverDistrictName>{neighborhood}</ReceiverDistrictName>
          <ReceiverStreetName>{street}</ReceiverStreetName>

          <VolumetricWeight>1</VolumetricWeight>
          <Weight>1</Weight>
          <PieceCount>1</PieceCount>

          <CodAmount>0</CodAmount>
          <IntegrationCode>{integ_code}</IntegrationCode>
          <Description>Sipariş</Description>

          <Country>Turkey</Country>
          <CountryCode>TR</CountryCode>

          <PayorTypeCode>1</PayorTypeCode>  <!-- 1: Ücreti Gönderici -->
          <IsWorldWide>0</IsWorldWide>
          <IsCod>false</IsCod>

          <PieceDetails>
            <PieceDetail xsi:nil="true" />
          </PieceDetails>
        </Order>
      </orderInfo>
      <userName>{settings.ARAS_USERNAME}</userName>
      <password>{settings.ARAS_PASSWORD}</password>
    </SetOrder>
  </soap:Body>
</soap:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "\"http://tempuri.org/SetOrder\"",
    }

    try:
        resp = requests.post(url, data=soap_body.encode("utf-8"), headers=headers, timeout=settings.ARAS_TIMEOUT)
    except Exception as e:
        return (False, None, f"Aras bağlantı hatası: {e}")

    if resp.status_code != 200:
        # Servis bazen SOAP Fault döndürebilir; metni kısaltarak log/mesaj veriyoruz.
        return (False, None, f"Aras HTTP {resp.status_code}: {resp.text[:200]}")

    # SOAP yanıtını çöz
    try:
        root = ET.fromstring(resp.content)
        result_code = root.find(".//t:ResultCode", _NS)
        result_msg = root.find(".//t:ResultMessage", _NS)

        if result_code is None:
            return (False, None, "Aras yanıtı beklenen formatta değil.")

        if result_code.text != "0":
            msg = result_msg.text if result_msg is not None else "Bilinmeyen hata"
            return (False, None, f"Aras SetOrder hata: {msg}")

        # Takip bilgisi: çoğunlukla InvoiceKey, bazen BarcodeNumber alanında gelebilir
        invoice_key = root.find(".//t:InvoiceKey", _NS)
        barcode = root.find(".//t:BarcodeNumber", _NS)
        tracking = (invoice_key.text if invoice_key is not None else None) or \
                   (barcode.text if barcode is not None else None)

        # Test ortamında bazen anında barkod dönmeyebilir → integration_code'u geçici takip olarak döndür
        if not tracking:
            tracking = integ_code

        return (True, tracking, "Gönderi oluşturuldu.")

    except Exception as e:
        return (False, None, f"Aras yanıt parse hatası: {e}")

# app/integrations/shipping_provider.py  (DOSYANIN SONUNA EKLE)

def get_status_with_integration_code(integration_code: str) -> Tuple[bool, Optional[str], bool, Optional[str]]:
    """
    Aras 'GetOrderWithIntegrationCode' ile durum sorgular.
    Dönüş: (ok, status_text, delivered_bool, tracking_no_maybe)

    delivered_bool: True ise 'Teslim Edildi' sayabilirsin.
    tracking_no_maybe: Barkod/InvoiceKey bulursa döndürür (bizde yoksa güncelleyebilirsin).
    """
    if not settings.ARAS_USERNAME or not settings.ARAS_PASSWORD:
        return (False, None, False, None)

    url = settings.ARAS_BASE_URL
    soap_q = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetOrderWithIntegrationCode xmlns="http://tempuri.org/">
      <userName>{settings.ARAS_USERNAME}</userName>
      <password>{settings.ARAS_PASSWORD}</password>
      <integrationCode>{integration_code}</integrationCode>
    </GetOrderWithIntegrationCode>
  </soap:Body>
</soap:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "\"http://tempuri.org/GetOrderWithIntegrationCode\"",
    }

    try:
        resp = requests.post(url, data=soap_q.encode("utf-8"), headers=headers, timeout=settings.ARAS_TIMEOUT)
    except Exception as e:
        return (False, f"Bağlantı hatası: {e}", False, None)

    if resp.status_code != 200:
        return (False, f"HTTP {resp.status_code}", False, None)

    try:
        root = ET.fromstring(resp.content)

        # Olası alan isimleri: LastStatusName / StatusName / OperationStatus / DeliveryStatus / Status ...
        cand_tags = ["LastStatusName", "StatusName", "OperationStatus", "DeliveryStatus", "Status"]
        status_text = None
        for tag in cand_tags:
            el = root.find(f".//t:{tag}", _NS)
            if el is not None and (el.text or "").strip():
                status_text = el.text.strip()
                break

        # Teslim bilgisini sezgisel tespit (bazı hesaplarda açıkça IsDelivered/DeliveryDate gelebilir)
        delivered = False
        is_delivered_el = root.find(".//t:IsDelivered", _NS)
        if is_delivered_el is not None and (is_delivered_el.text or "").strip() in ("1", "true", "True"):
            delivered = True
        # Metin bazlı sezgi
        if (status_text or "").lower().find("teslim") != -1:
            delivered = True

        # Takip numarası yakalama (varsa)
        tracking = None
        for tag in ("BarcodeNumber", "InvoiceKey"):
            el = root.find(f".//t:{tag}", _NS)
            if el is not None and (el.text or "").strip():
                tracking = el.text.strip()
                break

        return (True, status_text, delivered, tracking)

    except Exception as e:
        return (False, f"Parse hatası: {e}", False, None)

# --- Takip Job'u: Teslim durumlarını güncelle --------------------------------
def update_tracking_statuses(limit: int = 50) -> None:
    """
    Firestore'da 'Kargoya Verildi' durumundaki siparişleri tarar,
    Aras 'GetOrderWithIntegrationCode' ile sorgular ve 'Teslim Edildi' olduysa günceller.
    Ayrıca yeni barkod/InvoiceKey dönerse tracking_number'ı da günceller.

    limit: Bu çağrıda en fazla kaç sipariş bakılacak (APScheduler periyodik çağırır).
    """
    try:
        # Dairesel import kaçınmak için fonksiyon içinde import ediyoruz
        from backend.app.config import db, settings
        from firebase_admin import firestore
        from google.cloud.firestore_v1 import SERVER_TIMESTAMP

        # Güvenlik: TEST’teyken Aras TEST endpointi kullanılır.
        # Canlıya geçince .env'de ARAS_ENV=PROD yapacaksın; istersen burada da ek kilit tut.
        # if settings.ARAS_ENV.upper() not in ("TEST", "PROD"):
        #     print("[ARAS] ARAS_ENV uygun değil, job atlandı.")
        #     return

        # Sadece "Kargoya Verildi" siparişleri senkronize edelim
        q = (
            db.collection("orders")
            .where("status", "==", "Kargoya Verildi")
            .order_by("updated_at", direction=firestore.Query.ASCENDING)
            .limit(limit)
            .stream()
        )

        checked = 0
        updated = 0
        for snap in q:
            checked += 1
            data = snap.to_dict() or {}
            integ = data.get("integration_code")
            if not integ:
                continue

            ok, status_text, delivered, new_track = get_status_with_integration_code(integ)
            if not ok:
                # Aras geçici hata verebilir; loglayıp devam
                print(f"[ARAS] Sorgu hatası (order={snap.id}): {status_text}")
                continue

            patch = {"updated_at": SERVER_TIMESTAMP}
            touch = False

            if delivered and data.get("status") != "Teslim Edildi":
                patch["status"] = "Teslim Edildi"
                touch = True

            if new_track and new_track != data.get("tracking_number"):
                patch["tracking_number"] = new_track
                touch = True

            if status_text:
                patch["_last_aras_status"] = status_text
                # tek başına status_text değişse de updated_at güncellenmesi faydalı

            if touch:
                snap.reference.update(patch)
                updated += 1

        print(f"[ARAS] Tracking job: kontrol={checked}, guncellenen={updated}")

    except Exception as e:
        # Job hiçbir zaman uygulamayı çökertmesin; hatayı logla
        print(f"[ARAS] Tracking job exception: {e}")


def get_label_pdf(integration_code: str) -> Tuple[bool, Optional[str], Optional[bytes], str]:
    """
    Aras Label/Print API: integration_code (veya takip no) ile PDF döner.
    Dönüş: (ok, filename, pdf_bytes, msg)
    """
    # TODO: Aras endpoint çağırın, bytes alın.
    raise NotImplementedError

def request_pickup(integration_code: str, pickup_date: date, time_window: str) -> Tuple[bool, Optional[str]]:
    """
    Aras Pickup API: belirtilen tarihte ve zaman aralığında kurye talebi.
    Dönüş: (ok, pickup_ref) — pickup_ref: Aras tarafındaki referans.
    """
    # TODO: Aras endpoint çağırın.
    raise NotImplementedError