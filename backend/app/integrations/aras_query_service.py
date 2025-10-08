# backend/app/integrations/aras_query_service.py
from __future__ import annotations
import os
import json
from typing import Any, Dict, List, Optional, Tuple
import requests
import xml.etree.ElementTree as ET

from backend.app.config import settings

QUERY_URL = (os.getenv("ARAS_QUERY_SERVICE_URL")
             or "https://customerservices.araskargo.com.tr/ArasCargoCustomerIntegrationService/ArasCargoIntegrationService.svc").strip()

USERNAME = settings.ARAS_USERNAME
PASSWORD = settings.ARAS_PASSWORD
CUSTOMER_CODE = (settings.ARAS_CUSTOMER_CODE or "").strip()

TIMEOUT = int(os.getenv("ARAS_TIMEOUT", str(getattr(settings, "ARAS_TIMEOUT", 30))))

# WCF BasicHttpBinding genelde SOAP 1.1 ister
_SOAP_ACTIONS = [
    # en yaygın
    "http://tempuri.org/IArasCargoIntegrationService/GetQueryJSON",
    "http://tempuri.org/ArasCargoIntegrationService/GetQueryJSON",
    "http://tempuri.org/GetQueryJSON",
    "GetQueryJSON",
]

def _soap_envelope_getqueryjson(login_info_xml: str, query_info_xml: str) -> str:
    """
    WCF GetQueryJSON iki string parametre bekliyor (loginInfo, queryInfo),
    bunlar da XML stringleri. SOAP 1.1 zarfıyla gönderiyoruz.
    """
    # Parametreleri CDATA ile sarmalayarak özel karakter riskini kaldırıyoruz.
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetQueryJSON xmlns="http://tempuri.org/">
      <loginInfo><![CDATA[{login_info_xml}]]></loginInfo>
      <queryInfo><![CDATA[{query_info_xml}]]></queryInfo>
    </GetQueryJSON>
  </soap:Body>
</soap:Envelope>""".strip()

def _post_soap_getquery(xml: str) -> Tuple[requests.Response, List[Tuple[str, int]]]:
    """
    SOAPAction varyasyonlarını sırayla dener; ilk 200/500 (fault) yanıtı döndürür.
    404/415 gelirse sıradaki action denenir.
    """
    tried: List[Tuple[str, int]] = []
    url = QUERY_URL.rstrip("/")
    for action in _SOAP_ACTIONS:
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"\"{action}\"",
        }
        try:
            resp = requests.post(url, data=xml.encode("utf-8"), headers=headers, timeout=TIMEOUT)
            tried.append((action, resp.status_code))
            # 200 → tamam. 500 (SoapFault) → çoğu zaman iş hatası ama SOAP doğru; yine döndür.
            if resp.status_code not in (404, 415):
                return resp, tried
        except requests.RequestException:
            tried.append((action, -1))
            continue

    # hepsi 404/415 olduysa son yanıtı sentetikle
    class Dummy:
        status_code = 599
        text = "Network/Action mismatch"
        ok = False
    return Dummy(), tried  # type: ignore

def _login_info_xml(username: str, password: str, customer_code: str) -> str:
    return f"""<LoginInfo>
<UserName>{username}</UserName>
<Password>{password}</Password>
<CustomerCode>{customer_code}</CustomerCode>
</LoginInfo>""".strip()

def _query_info_xml(query_type: int, **kwargs: Any) -> str:
    """
    Esnek QueryInfo üreticisi.
    Örnekler:
      - Tarihe göre liste: query_type=4, Date="01.10.2025" veya DateStart/DateEnd
      - Barkod/tracking:  (dökümanınıza göre alan adı 'Barcode' ya da 'RefNo' olabilir)
    """
    # Bilinen alan isimleri; gelen kwargs içindeyse basılır
    fields_order = [
        "QueryType", "Date", "DateStart", "DateEnd", "Barcode",
        "RefNo", "ReceiverName", "City", "Page", "PageSize"
    ]
    parts = [f"<QueryType>{int(query_type)}</QueryType>"]
    for k in fields_order[1:]:
        v = kwargs.get(k) or kwargs.get(k.lower())
        if v is None:
            continue
        parts.append(f"<{k}>{v}</{k}>")
    return f"<QueryInfo>\n" + "\n".join(parts) + "\n</QueryInfo>"

def get_query_json(query_type: int, **kwargs: Any) -> Dict[str, Any]:
    """
    WCF GetQueryJSON çağrısı. JSON string döner; burada parse edilip dict olarak verilir.
    kwargs: Date / DateStart / DateEnd / Barcode / RefNo / ReceiverName / City / Page / PageSize
    """
    if not (USERNAME and PASSWORD and CUSTOMER_CODE):
        raise ValueError("Aras sorgu için ARAS_USERNAME / ARAS_PASSWORD / ARAS_CUSTOMER_CODE gerekli")

    login_xml = _login_info_xml(USERNAME, PASSWORD, CUSTOMER_CODE)
    query_xml = _query_info_xml(query_type, **kwargs)
    envelope = _soap_envelope_getqueryjson(login_xml, query_xml)

    resp, tried = _post_soap_getquery(envelope)
    text = resp.text or ""

    if resp.status_code == 200:
        # SOAP gövdesinin içinde JSON metin olur → çıkartalım
        try:
            root = ET.fromstring(text)
            for node in root.iter():
                if node.tag.endswith("GetQueryJSONResult") and (node.text or "").strip():
                    return json.loads(node.text)
        except Exception:
            pass
        # Bulamazsak ham döndür
        return {"raw": text, "_actions": tried}

    if resp.status_code == 500:
        # SOAP Fault: genelde iş kuralı/parametre hatası. Ham döndür.
        return {"error": "soap_fault", "status": 500, "raw": text[:4000], "_actions": tried}

    return {"error": "transport", "status": resp.status_code, "raw": text[:1000], "_actions": tried}
