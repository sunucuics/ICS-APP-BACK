# backend/app/integrations/aras.py
from __future__ import annotations

import os, re, requests
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from backend.app.config import settings

ARAS_URL = os.getenv("ARAS_SERVICE_URL") or "https://customerservices.araskargo.com.tr/ArasCargoCustomerIntegrationService/ArasCargoIntegrationService.svc"
ARAS_SOAPACTION = os.getenv("ARAS_SOAPACTION") or "http://tempuri.org/IArasCargoIntegrationService/SetDataXML"
ARAS_TIMEOUT = int(os.getenv("ARAS_TIMEOUT", str(settings.ARAS_TIMEOUT or 30)))

ARAS_USER = (settings.ARAS_USERNAME or "").strip()
ARAS_PASS = (settings.ARAS_PASSWORD or "").strip()
ARAS_CODE = (settings.ARAS_CUSTOMER_CODE or "").strip()

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

def _strip_illegal_xml(s: str) -> str:
    return _ILLEGAL_XML.sub("", s or "")

def _x(v: Any) -> str:
    # XML text escape
    s = _strip_illegal_xml(str(v or ""))
    return escape(s, {'"': "&quot;", "'": "&apos;"})

def _digits_only(s: Any) -> str:
    return re.sub(r"\D+", "", str(s or ""))

def _normalize_tr_phone(s: Any) -> str:
    d = _digits_only(s)
    # 90xxxxxxxxxx -> 0xxxxxxxxxx
    if d.startswith("90") and len(d) >= 12:
        d = "0" + d[2:]
    if len(d) == 10:
        d = "0" + d
    return d[:11]

def _login_str() -> str:
    # IntegrationService loginInfo "user|pass|customercode"
    return f"{ARAS_USER}|{ARAS_PASS}|{ARAS_CODE}"

def _build_queryinfo_xml(payload: Dict[str, Any]) -> str:
    """
    IntegrationService SetDataXML beklediği iç XML:
    <Main><Order>...</Order></Main>
    (Bunu string olarak geçiyoruz; o yüzden &lt; &gt; ile escape’leyeceğiz.)
    """
    phone = _normalize_tr_phone(payload.get("receiver_phone", ""))
    return (
        f"<Main>"
        f"  <Order>"
        f"    <TradingWaybillNumber>{_x(payload.get('trading_waybill_number'))}</TradingWaybillNumber>"
        f"    <ReceiverName>{_x(payload.get('receiver_name'))}</ReceiverName>"
        f"    <ReceiverAddress>{_x(payload.get('receiver_address'))}</ReceiverAddress>"
        f"    <ReceiverCity>{_x(payload.get('receiver_city'))}</ReceiverCity>"
        f"    <ReceiverTown>{_x(payload.get('receiver_town'))}</ReceiverTown>"
        f"    <ReceiverPhone>{_x(phone)}</ReceiverPhone>"
        f"    <Email>{_x(payload.get('email',''))}</Email>"
        f"    <Kg>{_x(payload.get('kg', 1))}</Kg>"
        f"    <Desi>{_x(payload.get('desi', 1))}</Desi>"
        f"    <PieceCount>{_x(payload.get('piece_count', 1))}</PieceCount>"
        f"    <OrderNote>{_x(payload.get('order_note',''))}</OrderNote>"
        f"    <Barcode>{_x(payload.get('barcode',''))}</Barcode>"
        f"  </Order>"
        f"</Main>"
    )

def _soap_envelope_setdataxml(inner_query_xml: str) -> str:
    # queryInfo bir STRING alanı; bu yüzden iç XML'i &lt; &gt; ile encode edip tekst olarak gönderiyoruz.
    qi_encoded = (
        inner_query_xml
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    body = (
        f'<SetDataXML xmlns="http://tempuri.org/">'
        f'  <loginInfo>{_x(_login_str())}</loginInfo>'
        f'  <queryInfo>{qi_encoded}</queryInfo>'
        f'</SetDataXML>'
    )
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        '               xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        '               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'  <soap:Body>{body}</soap:Body>'
        '</soap:Envelope>'
    )
    return envelope

def _post_soap(envelope: str) -> requests.Response:
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": ARAS_SOAPACTION,
    }
    resp = requests.post(ARAS_URL, data=envelope.encode("utf-8"), headers=headers, timeout=ARAS_TIMEOUT)
    resp.raise_for_status()
    return resp

def _parse_setdataxml_response(text: str) -> Dict[str, Any]:
    """
    <SetDataXMLResponse><SetDataXMLResult> ... </SetDataXMLResult>
    İçerik bazen düz yazı, bazen XML olabilir. XML ise tipik alanları ayıklamaya çalışıyoruz.
    """
    out = {"result_code": None, "result_message": "", "invoice_key": None, "cargo_barcode": None, "cargo_reference_code": None, "raw": text}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # SOAP sarfını parse etmeye çalış
        try:
            # Bazı servisler text/xml(1.1) yerine soap12 dönebiliyor; ikisini de dene
            ns11 = {"soap": "http://schemas.xmlsoap.org/soap/envelope/"}
            ns12 = {"soap": "http://www.w3.org/2003/05/soap-envelope"}
            for ns in (ns11, ns12):
                try:
                    r = ET.fromstring(text)
                    body = r.find("soap:Body", ns)
                    if body is None:
                        continue
                    res = body.find(".//SetDataXMLResponse") or body.find(".//{http://tempuri.org/}SetDataXMLResponse")
                    if res is not None:
                        result = res.find(".//SetDataXMLResult") or res.find(".//{http://tempuri.org/}SetDataXMLResult")
                        if result is not None and (result.text or "").strip():
                            txt = result.text.strip()
                            return _parse_inner_result_string(txt, out)
                except ET.ParseError:
                    continue
        except Exception:
            return out
        return out

    # SOAP 1.1 path
    ns = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "t":"http://tempuri.org/"}
    body = root.find("soap:Body", ns)
    res = body.find(".//t:SetDataXMLResponse", ns) if body is not None else None
    if res is None and body is not None:
        res = body.find(".//SetDataXMLResponse")
    if res is not None:
        el = res.find("t:SetDataXMLResult", ns) or res.find("SetDataXMLResult")
        if el is not None and (el.text or "").strip():
            return _parse_inner_result_string(el.text.strip(), out)
    return out

def _parse_inner_result_string(s: str, base: Dict[str, Any]) -> Dict[str, Any]:
    # s XML’e benziyorsa parse edip bilinen alanları ara
    out = dict(base)
    out["raw"] = s
    if "<" in s and ">" in s:
        try:
            inner = ET.fromstring(s)
            def pick(*names: str) -> Optional[str]:
                for node in inner.iter():
                    if any(node.tag.endswith(n) for n in names):
                        txt = (node.text or "").strip()
                        if txt:
                            return txt
                return None
            out["result_code"] = pick("ResultCode", "Code")
            out["result_message"] = pick("ResultMessage", "Message", "Description") or ""
            out["invoice_key"] = pick("InvoiceKey", "InvoiceNo")
            out["cargo_barcode"] = pick("CargoBarcode", "Barcode")
            out["cargo_reference_code"] = pick("CargoReferenceCode", "ReferenceCode")
            return out
        except ET.ParseError:
            # düz yazıysa akıt
            pass
    # düz string
    out["result_message"] = s[:500]
    # çok basit başarılı ipucu
    if "Başar" in s or "basar" in s.lower():
        out["result_code"] = "0"
    return out

def create_shipment_with_setorder(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    orders.py’nin beklediği imza:
      - trading_waybill_number, receiver_* , kg, desi, piece_count, order_note, barcode
    Dönüş: cargo_barcode / cargo_reference_code / invoice_key (olanları) + raw
    """
    if not (ARAS_USER and ARAS_PASS and ARAS_CODE):
        raise RuntimeError("ARAS kimlik bilgileri eksik (ARAS_USERNAME / ARAS_PASSWORD / ARAS_CUSTOMER_CODE).")

    qinfo_xml = _build_queryinfo_xml(payload)
    envelope = _soap_envelope_setdataxml(qinfo_xml)
    resp = _post_soap(envelope)
    parsed = _parse_setdataxml_response(resp.text)

    # basit başarı kontrolü
    if (parsed.get("result_code") or "") not in ("0", "", None):
        raise RuntimeError(f"Aras SetDataXML hata: {parsed.get('result_code')} — {parsed.get('result_message')}")

    return parsed
