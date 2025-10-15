# backend/app/integrations/shipping_provider.py
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from .aras_soap import ArasSOAPClient, SetOrderIn, PieceDetailIn
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
    return ["1.1", "1.2"]


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
    Birden çok SOAPAction / URL / versiyon kombinasyonunu dener.
    404 ve 415 → kombinasyon aramaya devam.
    400 → eğer yanıt 'gerçek' SOAP fault/XML ise döndür; değilse (boş/HTML) kombinasyon aramaya devam.
    500 → header/action tanınmadı vb. ise devam; diğer 500'lerde yanıtı döndür.
    """
    trials: List[Tuple[str, str, int]] = []

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
        urls.append(b)
        urls.append(b.replace("/arascargoservice/", "/ArasCargoService/"))
        if b.lower().endswith(".asmx"):
            urls.append(f"{b}?op={action_name}")
        if b.lower().endswith("/arascargoservice.asmx"):
            urls.append(b.replace("/arascargoservice.asmx", "/arascargoservice/arascargoservice.asmx"))
            urls.append(b.replace("/arascargoservice.asmx", "/ArasCargoService/ArasCargoService.asmx"))

    urls = list(dict.fromkeys(urls).keys())
    actions = list(dict.fromkeys(actions).keys())

    last_resp: Optional[requests.Response] = None

    def _looks_like_xml_fault(resp: requests.Response) -> bool:
        ct = (resp.headers.get("Content-Type") or "").lower()
        text = (resp.text or "").strip()
        # XML olduğuna dair hızlı ipuçları
        if "xml" in ct:
            return True
        if text.startswith("<"):
            return True
        # Çok kısa/boşsa fault sayma
        if len(text) < 20:
            return False
        return False

    for url in urls:
        for action in actions:
            for ver in _versions_to_try():
                try:
                    resp = _post_soap(url, action, xml, ver)
                    trials.append((url, f"{action} (v{ver})", resp.status_code))

                    text = resp.text or ""
                    low = text.lower()

                    # 404 + "İşlem sonucu alındı" → bazı eski kurulumlarda başarı kabul
                    if resp.status_code == 404 and ("işlem sonucu alındı" in low or "islem sonucu alindi" in low):
                        return resp, trials

                    # 404/415 → kombinasyon aramaya devam
                    if resp.status_code in (404, 415):
                        last_resp = resp
                        continue

                    # 400 → yalnızca 'gerçek' SOAP/XML gibi görünüyorsa döndür; değilse denemeye devam
                    if resp.status_code == 400:
                        if _looks_like_xml_fault(resp):
                            return resp, trials
                        else:
                            last_resp = resp
                            continue

                    if resp.status_code == 500:
                        # Header/action tanınmadı vb. ise devam et
                        if "did not recognize the value of http header soapaction" in low:
                            last_resp = resp
                            continue
                        if "the action" in low and "was not recognized" in low:
                            last_resp = resp
                            continue
                        if "request format is unrecognized for url unexpectedly ending in '/'" in low:
                            last_resp = resp
                            continue

                    # 200 veya farklı 500/fault → bu yanıtı döndür
                    return resp, trials

                except requests.RequestException:
                    trials.append((url, f"{action} (v{ver})", -1))
                    last_resp = None
                    continue

    if last_resp is not None:
        return last_resp, trials

    class Dummy:
        status_code = 599
        text = "Network error"
        ok = False
    return Dummy(), trials  # type: ignore





# ---- SOAP ENVELOPES ----------------------------------------------------------

def _soap_envelope_setorder(order: Dict[str, Any]) -> str:
    """
    SetOrder body (tek sipariş). Minimum alanlar doldurulur, kritik alanlar normalize edilir:
    - Telefon: sadece rakam, 90xxxxxxxxxx → 0xxxxxxxxxx
    - City/Town: UPPER
    - İsim/adres: max uzunluk (100/250)
    - PieceCount: en az 1
    """
    import re

    def _digits_only(s: Any) -> str:
        return re.sub(r"\D+", "", str(s or ""))

    def _normalize_phone_tr(s: Any) -> str:
        d = _digits_only(s)
        if d.startswith("90") and len(d) >= 12:
            d = "0" + d[2:]
        return d

    def _upper(s: Any) -> str:
        return (str(s or "")).upper()

    def _trunc(s: Any, n: int) -> str:
        t = str(s or "")
        return t[:n] if len(t) > n else t

    payor = ARAS_PAYOR_TYPE_CODE or "1"
    is_cod = ARAS_IS_COD or "0"
    is_world = ARAS_IS_WORLDWIDE or "0"
    unit_id = ARAS_UNIT_ID or "1"
    sender_addr_id = ARAS_SENDER_ACCOUNT_ADDRESS_ID

    trading = (order.get("tradingWaybillNumber") or "")[:16]
    invoice_no = (order.get("invoiceNumber") or "")[:20]
    special1 = (order.get("orgReceiverCustId") or "")
    country = order.get("country") or ARAS_DEFAULT_COUNTRY

    recv_name = _trunc(order["receiverCustName"], 100)
    recv_addr = _trunc(order["receiverAddress"], 250)
    recv_phone1 = _normalize_phone_tr(order["receiverPhone1"])
    city = _upper(order["cityName"])
    town = _upper(order["townName"])
    piece_count = max(1, int(order.get("cargoCount", 1) or 1))

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

          <ReceiverName>{recv_name}</ReceiverName>
          <ReceiverAddress>{recv_addr}</ReceiverAddress>
          <ReceiverPhone1>{recv_phone1}</ReceiverPhone1>
          <ReceiverPhone2></ReceiverPhone2>
          <ReceiverPhone3></ReceiverPhone3>
          <ReceiverCityName>{city}</ReceiverCityName>
          <ReceiverTownName>{town}</ReceiverTownName>

          <VolumetricWeight></VolumetricWeight>
          <Weight></Weight>
          <PieceCount>{piece_count}</PieceCount>

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
    """
    Aras SetOrder çağrısı. Önce XML-zarf + multi-try yolunu dener.
    - Eğer 400 "boş gövde"/parse edilemez ise veya ARAS_USE_CLIENT=1 ise
      ArasSOAPClient tabanlı fallback'a geçer.
    """
    USE_CLIENT = os.getenv("ARAS_USE_CLIENT", "0") in ("1", "true", "True")

    def _as_client_call() -> Dict[str, Any]:
        """ArasSOAPClient üzerinden SetOrder; başarısızsa ShippingProviderError fırlatır."""
        res = create_shipment_with_setorder(
            integration_code=order["CargoKey"],
            trading_waybill_number=order.get("tradingWaybillNumber", ""),
            receiver_name=order["receiverCustName"],
            receiver_address=order["receiverAddress"],
            receiver_phone1=order["receiverPhone1"],
            receiver_city=order["cityName"],
            receiver_town=order["townName"],
            piece_count=order.get("cargoCount", 1),
            invoice_number=order.get("invoiceNumber"),
            volumetric_weight=None,
            weight=None,
            description=order.get("description", ""),
            payor_type_code=int(ARAS_PAYOR_TYPE_CODE or "1"),
            is_worldwide=int(ARAS_IS_WORLDWIDE or "0"),
            piece_details=None,
            is_cod=None,
            cod_amount=None,
            cod_collection_type=None,
            cod_billing_type=None,
        )
        if not res.get("ok"):
            raise ShippingProviderError(
                int(res.get("http_status") or 500),
                f"Aras hata: {res.get('message') or 'Bilinmeyen hata (client)'}",
                (res.get("raw") or "")[:4000],
            )
        # Client başarı → standardize edilmiş çıktı
        return {
            "tracking_number": f"AR{order['CargoKey']}",
            "cargo_key": order["CargoKey"],
            "invoice_key": None,
            "waybill_no": None,
            "message": res.get("message", "Aras: Başarılı"),
            "raw_xml": (res.get("raw") or "")[:4000],
            "trials": [],
        }

    # 1) İstenirse doğrudan client yolu
    if USE_CLIENT:
        return _as_client_call()

    # 2) XML-zarf + multi-try
    xml = _soap_envelope_setorder(order)
    resp, trials = _post_soap_multitry("SetOrder", xml)
    text = resp.text or ""
    low = text.lower()

    if resp.status_code == 599:
        raise ShippingProviderError(599, "Aras ağına bağlanılamadı", raw=str(trials))

    parsed = _parse_setorder_response(text)
    code = (parsed.get("result_code") or "").strip()
    msg = (parsed.get("message") or "").lower()

    # Başarı kabulü (kod=0 ya da "başar" içeren mesaj)
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

    # 3) 400 → Eğer gövde boş/parse edilemezse otomatik client fallback
    if resp.status_code == 400:
        looks_empty = not (parsed.get("result_code") or parsed.get("message")) and len(text.strip()) < 40
        if looks_empty:
            # otomatik fallback
            return _as_client_call()
        # boş değil → anlamlı iş hatasıdır
        raise ShippingProviderError(
            400,
            f"Aras hata: {parsed.get('message') or (text[:200] or 'İş hatası')}"
            f" | last={trials[-1] if trials else None}",
            text[:4000],
        )

    # 4) 500 → SOAPAction tanınmadı vb. denendiyse; yoksa iş fault'u
    if resp.status_code == 500:
        raise ShippingProviderError(
            500,
            f"Aras hata: {parsed.get('message') or (text[:200] or 'Sunucu hatası')}"
            f" | last={trials[-1] if trials else None}",
            text[:4000],
        )

    if resp.status_code == 404:
        raise ShippingProviderError(404, "Aras hata: Not Found (muhtemelen yanlış endpoint/action)", str(trials))

    # Diğer haller
    raise ShippingProviderError(resp.status_code, f"Aras hata: {parsed.get('message') or text[:200]}", text[:4000])




_client = ArasSOAPClient()

def create_shipment_with_setorder(
    *,
    integration_code: str,
    trading_waybill_number: str,
    receiver_name: str,
    receiver_address: str,
    receiver_phone1: str,
    receiver_city: str,
    receiver_town: str,
    piece_count: Optional[int] = None,
    invoice_number: Optional[str] = None,
    volumetric_weight: Optional[str] = None,
    weight: Optional[str] = None,
    description: Optional[str] = None,
    payor_type_code: int = 1,
    is_worldwide: int = 0,
    piece_details: Optional[List[Dict[str, str]]] = None,
    is_cod: Optional[int] = None,
    cod_amount: Optional[str] = None,
    cod_collection_type: Optional[int] = None,
    cod_billing_type: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Aras SetOrder çağrısı. Dönen sözlükte ok/code/message/http_status/raw bulunur.
    """
    pds = None
    if piece_details:
        pds = [PieceDetailIn(**pd) for pd in piece_details]

    order = SetOrderIn(
        IntegrationCode=integration_code,
        TradingWaybillNumber=trading_waybill_number,
        ReceiverName=receiver_name,
        ReceiverAddress=receiver_address,
        ReceiverPhone1=receiver_phone1,
        ReceiverCityName=receiver_city,
        ReceiverTownName=receiver_town,
        PieceCount=piece_count,
        InvoiceNumber=invoice_number,
        VolumetricWeight=volumetric_weight,
        Weight=weight,
        Description=description,
        PayorTypeCode=payor_type_code,
        IsWorldWide=is_worldwide,
        PieceDetails=pds,
        IsCod=is_cod,
        CodAmount=cod_amount,
        CodCollectionType=cod_collection_type,
        CodBillingType=cod_billing_type,
    )
    return _client.set_order(order)

def get_status_with_integration_code(integration_code: str) -> Dict[str, Any]:
    """
    Aras GetOrderWithIntegrationCode. raw içinde tam SOAP yanıtı gelir.
    """
    return _client.get_order_with_integration_code(integration_code)

def cancel_dispatch(order_code: str) -> Dict[str, Any]:
    """
    Aras CancelDispatch (order_code genelde IntegrationCode ile aynıdır).
    """
    return _client.cancel_dispatch(order_code)