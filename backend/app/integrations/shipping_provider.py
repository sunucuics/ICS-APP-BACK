# backend/app/integrations/shipping_provider.py
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
import xml.etree.ElementTree as ET

from backend.app.config import settings

logger = logging.getLogger("shipping.aras")

# ---- ENV / Settings ----------------------------------------------------------

_BASE = (settings.ARAS_BASE_URL or "").strip()
_HINT = (settings.ARAS_SOAPACTION_HINT or os.getenv("ARAS_SOAPACTION_HINT", "")).strip()
_TIMEOUT = float(os.getenv("ARAS_TIMEOUT", str(settings.ARAS_TIMEOUT or 60)))
_DEBUG = bool(getattr(settings, "ARAS_DEBUG", False)) or os.getenv("ARAS_DEBUG", "0") == "1"
_VER_CONF = (os.getenv("ARAS_SOAP_VERSION", "auto") or "auto").strip().lower()  # "1.1" | "1.2" | "auto"

ARAS_PAYOR_TYPE_CODE = (os.getenv("ARAS_PAYOR_TYPE_CODE", "1") or "1").strip()  # 1: Gönderici öder
ARAS_IS_WORLDWIDE = (os.getenv("ARAS_IS_WORLDWIDE", "0") or "0").strip()
ARAS_IS_COD = (os.getenv("ARAS_IS_COD", "0") or "0").strip()
ARAS_UNIT_ID = (os.getenv("ARAS_UNIT_ID", "1") or "1").strip()  # genelde 1
ARAS_SENDER_ACCOUNT_ADDRESS_ID = (os.getenv("ARAS_SENDER_ACCOUNT_ADDRESS_ID", "") or "").strip()
ARAS_DEFAULT_COUNTRY = (os.getenv("ARAS_DEFAULT_COUNTRY", "Türkiye") or "Türkiye").strip()

ARAS_USERNAME = settings.ARAS_USERNAME
ARAS_PASSWORD = settings.ARAS_PASSWORD
ARAS_CUSTOMER_CODE = (settings.ARAS_CUSTOMER_CODE or "").strip()


# ---- Error type --------------------------------------------------------------

@dataclass
class ShippingProviderError(Exception):
    http_status: int
    message: str
    raw: Optional[str] = None
    def __str__(self) -> str:
        return f"{self.http_status} {self.message}"


def make_cargokey(order_id: str) -> str:
    key = "".join(ch for ch in order_id if ch.isalnum())[:16].upper()
    return key or "ORD000000000000"


# ---- Utilities ---------------------------------------------------------------

def _normalize_base(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "?op=" in u:
        u = u.split("?op=", 1)[0]
    if u.endswith("?wsdl"):
        u = u[:-5]
    return u.rstrip("/")


def _build_endpoint(base_url: str) -> str:
    return _normalize_base(base_url)


def _versions_to_try() -> List[str]:
    if _VER_CONF in ("1.1", "1.2"):
        return [_VER_CONF]
    # auto → ÖNCE 1.2, sonra 1.1
    return ["1.2", "1.1"]


def _headers(soap_action: str, ver: str) -> Dict[str, str]:
    if ver == "1.2":
        return {"Content-Type": f'application/soap+xml; charset=utf-8; action="{soap_action}"'}
    return {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f"\"{soap_action}\""}


def _post_soap(url: str, soap_action: str, xml: str, ver: str) -> requests.Response:
    headers = _headers(soap_action, ver)
    resp = requests.post(url, data=xml.encode("utf-8"), headers=headers, timeout=int(_TIMEOUT))
    if _DEBUG:
        logger.info(
            "ARAS POST url=%s status=%s ver=%s ctype=%s soapaction=%s",
            getattr(resp.request, "url", url),
            resp.status_code, ver,
            resp.request.headers.get("Content-Type"),
            resp.request.headers.get("SOAPAction"),
        )
    return resp


def _post_soap_multitry(action_name: str, xml: str) -> Tuple[requests.Response, List[Tuple[str, str, int]]]:
    """
    Deneme stratejisi:
      - Birden çok SOAPAction varyasyonu (ipucu + tempuri...) dener.
      - Birden çok URL varyasyonu (case farkları, ?op=Action, eski env anahtarları) dener.
      - SOAP 1.1 → 1.2 (veya yalnızca istenen) versiyon(lar)ını dener.
    Başarı kriteri:
      - İlk 200 dönen yanıt ya da 404 + "İşlem sonucu alındı" metni görüldüğünde döner.
    Devam (kombinasyon değiştirme) kriterleri:
      - 404 (genel)
      - 500 ve SOAPAction/Action tanınmadı veya trailing slash format hatası
      - 415 (Unsupported Media Type) → muhtemelen yanlış SOAP versiyonu
    """
    trials: List[Tuple[str, str, int]] = []

    # 1) SOAPAction adayları (önce ipucu)
    actions: List[str] = []
    if _HINT:
        actions.append(_HINT)
    for a in (
        f"http://tempuri.org/{action_name}",
        f"http://tempuri.org/Service/{action_name}",
        f"http://tempuri.org/ArasCargoService/{action_name}",
        action_name,  # çıplak
    ):
        if a not in actions:
            actions.append(a)

    # 2) URL adayları (önce canonical base, sonra legacy env anahtarları)
    bases: List[str] = []
    if _BASE:
        bases.append(_BASE)
    for envkey in ("ARAS_SERVICE_URL", "ARAS_ENDPOINT"):
        alt = (os.getenv(envkey, "") or "").strip()
        if alt and alt not in bases:
            bases.append(alt)

    urls: List[str] = []
    for b in bases:
        b = _normalize_base(b)
        if not b:
            continue
        # temel .asmx
        urls.append(b)
        # case varyantları (ArasCargoService)
        urls.append(b.replace("/arascargoservice/", "/ArasCargoService/"))
        # bazı kurulumlar '?op=Action' seviyor
        if b.lower().endswith(".asmx"):
            urls.append(f"{b}?op={action_name}")
        # tek dosya adı → dizin/alt-dosya varyasyonları
        if b.lower().endswith("/arascargoservice.asmx"):
            urls.append(b.replace("/arascargoservice.asmx", "/arascargoservice/arascargoservice.asmx"))
            urls.append(b.replace("/arascargoservice.asmx", "/ArasCargoService/ArasCargoService.asmx"))

    # tekrarları kaldır (sıra korunur)
    urls = list(dict.fromkeys(urls).keys())
    actions = list(dict.fromkeys(actions).keys())

    last_resp: Optional[requests.Response] = None

    for url in urls:
        for action in actions:
            for ver in _versions_to_try():
                try:
                    resp = _post_soap(url, action, xml, ver)
                    trials.append((url, f"{action} (v{ver})", resp.status_code))

                    text = resp.text or ""
                    low = text.lower()

                    # --- Özel başarı: 404 + "İşlem sonucu alındı." (bazı eski Aras kurulumları)
                    if resp.status_code == 404 and ("işlem sonucu alındı" in low or "islem sonucu alindi" in low):
                        return resp, trials

                    # --- Devam edilmesi gereken tipik hatalar
                    if resp.status_code in (404, 415 , 400):
                        last_resp = resp
                        continue

                    if resp.status_code == 500:
                        # SOAPAction header değeri/Action ismi tanınmadı
                        if "did not recognize the value of http header soapaction" in low:
                            last_resp = resp
                            continue
                        if "the action" in low and "was not recognized" in low:
                            last_resp = resp
                            continue
                        # Trailing slash / format hatası
                        if "request format is unrecognized for url unexpectedly ending in '/'" in low:
                            last_resp = resp
                            continue

                    # --- Diğer tüm durumlarda (200 veya 500'de farklı fault vb.) bu yanıtı döndür
                    return resp, trials

                except requests.RequestException:
                    trials.append((url, f"{action} (v{ver})", -1))
                    last_resp = None
                    continue

    # Tüm kombinasyonlar tükendi → elde kalan en son yanıta dön
    if last_resp is not None:
        return last_resp, trials

    # Ağa hiç ulaşılamadıysa sentetik yanıt
    class Dummy:
        status_code = 599
        text = "Network error"
        ok = False
    return Dummy(), trials  # type: ignore



# ---- SOAP ENVELOPES ----------------------------------------------------------

def _soap_envelope_setorder(order: Dict[str, Any]) -> str:
    """
    SetOrder body (tek sipariş). Resmi örnekteki alan adlarıyla bire bir uyumlu.
    Minimum: ReceiverName, ReceiverAddress, ReceiverPhone1, ReceiverCityName, ReceiverTownName,
             PieceCount, IntegrationCode (+ UserName/Password).
    Sahada çoğu hesap PayorTypeCode, IsCod, IsWorldWide, UnitID, (bazı hesaplar SenderAccountAddressId) bekliyor.
    """
    # Opsiyonelleri güvenli varsayılanlarla doldur
    payor = ARAS_PAYOR_TYPE_CODE or "1"
    is_cod = ARAS_IS_COD or "0"
    is_world = ARAS_IS_WORLDWIDE or "0"
    unit_id = ARAS_UNIT_ID or "1"
    sender_addr_id = ARAS_SENDER_ACCOUNT_ADDRESS_ID  # boş ise hiç basmayacağız

    # İsteğe bağlı alanları boş string geçiyoruz (IIS/SOAP 1.1 sorun çıkarmasın diye)
    trading = order.get("tradingWaybillNumber", "") or ""
    invoice_no = order.get("invoiceNumber", "") or ""
    special1 = order.get("orgReceiverCustId", "") or ""  # siz zaten bunu yolluyordunuz

    # Ülke adı boşsa TR ver
    country = order.get("country") or ARAS_DEFAULT_COUNTRY

    optional_sender = f"<SenderAccountAddressId>{sender_addr_id}</SenderAccountAddressId>" if sender_addr_id else ""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <SetOrder xmlns="http://tempuri.org/">
      <orderInfo>
        <Order>
          <UserName>{ARAS_USERNAME}</UserName>
          <Password>{ARAS_PASSWORD}</Password>

          <TradingWaybillNumber>{trading}</TradingWaybillNumber>
          <InvoiceNumber>{invoice_no}</InvoiceNumber>

          <ReceiverName>{order['receiverCustName']}</ReceiverName>
          <ReceiverAddress>{order['receiverAddress']}</ReceiverAddress>
          <ReceiverPhone1>{order['receiverPhone1']}</ReceiverPhone1>
          <ReceiverPhone2></ReceiverPhone2>
          <ReceiverPhone3></ReceiverPhone3>
          <ReceiverCityName>{order['cityName']}</ReceiverCityName>
          <ReceiverTownName>{order['townName']}</ReceiverTownName>

          <VolumetricWeight></VolumetricWeight>
          <Weight></Weight>
          <PieceCount>{int(order.get('cargoCount', 1))}</PieceCount>

          <SpecialField1>{special1}</SpecialField1>
          <SpecialField2></SpecialField2>
          <SpecialField3></SpecialField3>

          <CodAmount></CodAmount>
          <CodCollectionType></CodCollectionType>
          <CodBillingType></CodBillingType>

          <IntegrationCode>{order['CargoKey']}</IntegrationCode>
          <Description>{order.get('description','')}</Description>

          <TaxNumber></TaxNumber>
          <TtDocumentId></TtDocumentId>
          <TaxOffice></TaxOffice>
          <PrivilegeOrder></PrivilegeOrder>

          <Country>{country}</Country>
          <CountryCode></CountryCode>
          <CityCode></CityCode>
          <TownCode></TownCode>
          <ReceiverDistrictName></ReceiverDistrictName>
          <ReceiverQuarterName></ReceiverQuarterName>
          <ReceiverAvenueName></ReceiverAvenueName>
          <ReceiverStreetName></ReceiverStreetName>

          <PayorTypeCode>{payor}</PayorTypeCode>
          <IsWorldWide>{is_world}</IsWorldWide>
          <IsCod>{is_cod}</IsCod>
          <UnitID>{unit_id}</UnitID>

          <PieceDetails></PieceDetails>
          {optional_sender}
        </Order>
      </orderInfo>
      <userName>{ARAS_USERNAME}</userName>
      <password>{ARAS_PASSWORD}</password>
    </SetOrder>
  </soap:Body>
</soap:Envelope>""".strip()




def _soap_envelope_getcargoinfo(integration_code: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetCargoInfo xmlns="http://tempuri.org/">
      <username>{ARAS_USERNAME}</username>
      <password>{ARAS_PASSWORD}</password>
      <customerCode>{ARAS_CUSTOMER_CODE}</customerCode>
      <integrationCode>{integration_code}</integrationCode>
    </GetCargoInfo>
  </soap:Body>
</soap:Envelope>""".strip()


# ---- XML parse helpers -------------------------------------------------------

def _parse_setorder_response(xml_text: str) -> Dict[str, Any]:
    """SetOrderResponse → SetOrderResult → OrderResultInfo[...]"""
    out = {"message": "", "result_code": None, "invoice_key": None, "org_id": None}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        out["message"] = (xml_text or "")[:200]
        return out

    def find_text(*suffixes: str) -> Optional[str]:
        for node in root.iter():
            if any(node.tag.endswith(s) for s in suffixes):
                if node.text and node.text.strip():
                    return node.text.strip()
        return None

    out["result_code"] = find_text("ResultCode", "ErrorCode", "Code")
    out["message"] = find_text("ResultMessage", "Message", "Description") or ""
    out["invoice_key"] = find_text("InvoiceKey", "InvoiceNo")
    out["org_id"] = find_text("OrgReceiverCustId")
    return out


def _parse_getcargoinfo(xml_text: str) -> Optional[Dict[str, Any]]:
    """
    GetCargoInfoResult/IrsDataDataTable döner; kolon isimleri versiyona göre değişebilir.
    Etiket uçlarında tanıdık anahtarları arıyoruz.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    out: Dict[str, Any] = {}
    for node in root.iter():
        tag = node.tag
        txt = (node.text or "").strip()
        if not txt:
            continue
        if tag.endswith(("TrackingNumber", "TrackingNo", "Barcode")):
            out["TrackingNumber"] = txt
        elif tag.endswith(("CargoKey", "IntegrationCode")):
            out["CargoKey"] = txt
        elif tag.endswith(("InvoiceKey", "InvoiceNo")):
            out["InvoiceKey"] = txt
        elif tag.endswith(("WaybillNo", "WayBill")):
            out["WaybillNo"] = txt
        elif tag.endswith(("Status", "LastStatus", "SonDurum")):
            out["Status"] = txt
    return out or None


# ---- Public API --------------------------------------------------------------

async def create_shipment_with_setdispatch(order: Dict[str, Any]) -> Dict[str, Any]:
    """NOTE: Artık SetOrder çağırıyor; fonksiyon adı geriye dönük uyumluluk için aynı."""
    xml = _soap_envelope_setorder(order)
    resp, trials = _post_soap_multitry("SetOrder", xml)
    text = resp.text or ""
    low = text.lower()

    if resp.status_code == 599:
        raise ShippingProviderError(599, "Aras ağına bağlanılamadı", raw=str(trials))

    parsed = _parse_setorder_response(text)
    code = (parsed.get("result_code") or "").strip()
    msg = (parsed.get("message") or "").lower()

    # Başarı kabulü
    if code == "0" or "başar" in msg or "basar" in msg:
        tracking = None

        # (Opsiyonel) tracking no çek: GetCargoInfo
        if ARAS_CUSTOMER_CODE and order.get("CargoKey"):
            try:
                soap = _soap_envelope_getcargoinfo(order["CargoKey"])
                r2, _ = _post_soap_multitry("GetCargoInfo", soap)
                if getattr(r2, "ok", False):
                    gi = _parse_getcargoinfo(r2.text)
                    tracking = (gi or {}).get("TrackingNumber")
            except Exception:
                pass

        return {
            "tracking_number": tracking or f"AR{order['CargoKey']}",
            "cargo_key": order["CargoKey"],
            "invoice_key": parsed.get("invoice_key"),
            "waybill_no": None,
            "message": parsed.get("message") or "Aras: Başarılı",
            "raw_xml": text[:4000],
            "trials": trials[-10:],
        }

    # WSDL 'action not recognized' vs. diğer hatalar
    if resp.status_code in (500, 400):
        raise ShippingProviderError(
            resp.status_code,
            f"Aras hata: {parsed.get('message') or (text[:200] or 'Boş gövde')} | last={trials[-1] if trials else None}",
            text[:4000],
        )
    if resp.status_code == 404:
        raise ShippingProviderError(404, "Aras hata: Not Found (muhtemelen yanlış endpoint/action)", str(trials))

    raise ShippingProviderError(resp.status_code, f"Aras hata: {parsed.get('message') or text[:200]}", text[:4000])


async def get_status_with_integration_code(integration_code: str) -> Optional[Dict[str, Any]]:
    if not integration_code:
        return None
    xml = _soap_envelope_getcargoinfo(integration_code)
    resp, _ = _post_soap_multitry("GetCargoInfo", xml)
    if not getattr(resp, "ok", False):
        return None
    found = _parse_getcargoinfo(resp.text)
    if not found:
        return None
    found["_matched_by"] = "IntegrationCode"
    return found
