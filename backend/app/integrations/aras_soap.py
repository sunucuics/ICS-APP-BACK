# backend/app/integrations/aras_soap.py
from __future__ import annotations

import os
import re
import html
import requests
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

_ARAS_NS = "http://tempuri.org/"
_SOAP_ENV_12 = "http://www.w3.org/2003/05/soap-envelope"

_SOAP_ENV_11 = "http://schemas.xmlsoap.org/soap/envelope/"
_SOAP_VERSION = (os.getenv("ARAS_SOAP_VERSION", "1.1") or "1.1").strip()  # "1.1" | "1.2"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(name, default)

def _aras_base_url() -> str:
    env = (_env("ARAS_ENV", "test") or "test").lower()
    if env == "prod":
        return _env("ARAS_LIVE_URL") or "https://customerws.araskargo.com.tr/arascargoservice.asmx"
    return _env("ARAS_TEST_URL") or "https://customerservicestest.araskargo.com.tr/arascargoservice/arascargoservice.asmx"

def _creds() -> Tuple[str, str]:
    return (_env("ARAS_USERNAME") or "", _env("ARAS_PASSWORD") or "")

def _timeout() -> int:
    try:
        return int(_env("ARAS_TIMEOUT", "20"))
    except Exception:
        return 20

def _e(s: Any) -> str:
    # XML escape (None -> "")
    return html.escape("" if s is None else str(s), quote=True)

def _digits_only(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\D+", "", s)

def _normalize_phone_tr(s: Optional[str]) -> str:
    """
    Doküman 'sadece rakam' istiyor.
    - '+90' ya da '90' başlarsa yerelle (0xxx...) eşle.
    - 10-11 haneye toleranslı; olduğu gibi rakamları gönderiyoruz.
    """
    d = _digits_only(s)
    if d.startswith("90") and len(d) in (12, 13):  # 90 + 10/11
        d = "0" + d[2:]
    return d

def _upper_tr(s: Optional[str]) -> str:
    # Basit büyük harf; altyapın varsa python-icu ile daha iyi yapılabilir
    return (s or "").upper()

@dataclass
class PieceDetailIn:
    BarcodeNumber: str
    VolumetricWeight: Optional[str] = None
    Weight: Optional[str] = None
    ProductNumber: Optional[str] = None
    Description: Optional[str] = None

@dataclass
class SetOrderIn:
    IntegrationCode: str
    TradingWaybillNumber: str
    ReceiverName: str
    ReceiverAddress: str
    ReceiverPhone1: str
    ReceiverCityName: str
    ReceiverTownName: str
    PieceCount: Optional[int] = None
    InvoiceNumber: Optional[str] = None
    VolumetricWeight: Optional[str] = None
    Weight: Optional[str] = None
    Description: Optional[str] = None
    PayorTypeCode: int = 1            # 1=Gönderici, 2=Alıcı
    IsWorldWide: int = 0              # 0=Yurtiçi, 1=Yurtdışı
    # Opsiyoneller
    ReceiverPhone2: Optional[str] = None
    ReceiverPhone3: Optional[str] = None
    SpecialField1: Optional[str] = None
    SpecialField2: Optional[str] = None
    SpecialField3: Optional[str] = None
    IsCod: Optional[int] = None       # 0/1
    CodAmount: Optional[str] = None
    CodCollectionType: Optional[int] = None  # 0=Nakit,1=KK
    CodBillingType: Optional[int] = None     # sabit "0" öneriliyor
    CityCode: Optional[str] = None
    TownCode: Optional[str] = None
    ReceiverDistrictName: Optional[str] = None
    ReceiverQuarterName: Optional[str] = None
    ReceiverAvenueName: Optional[str] = None
    ReceiverStreetName: Optional[str] = None
    PrivilegeOrder: Optional[str] = None
    PieceDetails: Optional[List[PieceDetailIn]] = None

class ArasSOAPClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or _aras_base_url()
        self.username, self.password = _creds()
        self.timeout = _timeout()

    # ---------- SOAP envelope builders ----------

    def _envelope(self, body_xml: str, action: str, ver: str) -> Tuple[Dict[str, str], str]:
        if ver == "1.1":
            headers = {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f"\"{_ARAS_NS}{action}\"",
            }
            envelope = (
                f'<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                f'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
                f'xmlns:soap="{_SOAP_ENV_11}">'
                f"<soap:Body>{body_xml}</soap:Body></soap:Envelope>"
            )
            return headers, envelope

        # 1.2
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{_ARAS_NS}{action}"',
        }
        envelope = (
            f'<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            f'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            f'xmlns:soap12="{_SOAP_ENV_12}">'
            f"<soap12:Body>{body_xml}</soap12:Body></soap12:Envelope>"
        )
        return headers, envelope

    def _post(self, action: str, inner_xml: str) -> requests.Response:
        # Önce tercih edilen versiyon, sonra diğerleri
        order = [_SOAP_VERSION] + ([v for v in ("1.1", "1.2") if v != _SOAP_VERSION])
        last = None
        for ver in order:
            headers, xml = self._envelope(inner_xml, action, ver)
            resp = requests.post(self.base_url, data=xml.encode("utf-8"), headers=headers, timeout=self.timeout)
            last = resp
            # 415 → "Unsupported Media Type": versiyon uymadı, diğerine geç
            if resp.status_code == 415:
                continue
            return resp
        return last  # type: ignore

    # ---------- Public methods ----------

    def set_order(self, order: SetOrderIn) -> Dict[str, Any]:
        # Normalize/validate
        phone1 = _normalize_phone_tr(order.ReceiverPhone1)
        phone2 = _normalize_phone_tr(order.ReceiverPhone2)
        phone3 = _normalize_phone_tr(order.ReceiverPhone3)

        city = _upper_tr(order.ReceiverCityName)
        town = _upper_tr(order.ReceiverTownName)

        # PieceDetails xml
        pd_xml = ""
        if order.PieceDetails:
            parts = []
            for p in order.PieceDetails:
                parts.append(
                    "<PieceDetail>"
                    + (f"<VolumetricWeight>{_e(p.VolumetricWeight)}</VolumetricWeight>" if p.VolumetricWeight else "")
                    + (f"<Weight>{_e(p.Weight)}</Weight>" if p.Weight else "")
                    + f"<BarcodeNumber>{_e(p.BarcodeNumber)}</BarcodeNumber>"
                    + (f"<ProductNumber>{_e(p.ProductNumber)}</ProductNumber>" if p.ProductNumber else "<ProductNumber />")
                    + (f"<Description>{_e(p.Description)}</Description>" if p.Description else "<Description />")
                    + "</PieceDetail>"
                )
            pd_xml = f"<PieceDetails>{''.join(parts)}</PieceDetails>"

        # SetOrder body (dokümandaki yapıya sadık)
        body = (
            f'<SetOrder xmlns="{_ARAS_NS}">'
            f"<orderInfo><Order>"
            f"<UserName>{_e(self.username)}</UserName>"
            f"<Password>{_e(self.password)}</Password>"
            f"<TradingWaybillNumber>{_e(order.TradingWaybillNumber)}</TradingWaybillNumber>"
            + (f"<InvoiceNumber>{_e(order.InvoiceNumber)}</InvoiceNumber>" if order.InvoiceNumber else "")
            + f"<ReceiverName>{_e(order.ReceiverName)}</ReceiverName>"
            + f"<ReceiverAddress>{_e(order.ReceiverAddress)}</ReceiverAddress>"
            + f"<ReceiverPhone1>{_e(phone1)}</ReceiverPhone1>"
            + (f"<ReceiverPhone2>{_e(phone2)}</ReceiverPhone2>" if phone2 else "")
            + (f"<ReceiverPhone3>{_e(phone3)}</ReceiverPhone3>" if phone3 else "")
            + f"<ReceiverCityName>{_e(city)}</ReceiverCityName>"
            + f"<ReceiverTownName>{_e(town)}</ReceiverTownName>"
            + (f"<VolumetricWeight>{_e(order.VolumetricWeight)}</VolumetricWeight>" if order.VolumetricWeight else "")
            + (f"<Weight>{_e(order.Weight)}</Weight>" if order.Weight else "")
            + (f"<PieceCount>{_e(order.PieceCount)}</PieceCount>" if order.PieceCount is not None else "")
            + (f"<Description>{_e(order.Description)}</Description>" if order.Description else "")
            + f"<IntegrationCode>{_e(order.IntegrationCode)}</IntegrationCode>"
            + f"<PayorTypeCode>{_e(order.PayorTypeCode)}</PayorTypeCode>"
            + f"<IsWorldWide>{_e(order.IsWorldWide)}</IsWorldWide>"
            + (f"<SpecialField1>{_e(order.SpecialField1)}</SpecialField1>" if order.SpecialField1 else "")
            + (f"<SpecialField2>{_e(order.SpecialField2)}</SpecialField2>" if order.SpecialField2 else "")
            + (f"<SpecialField3>{_e(order.SpecialField3)}</SpecialField3>" if order.SpecialField3 else "")
            + (f"<IsCod>{_e(order.IsCod)}</IsCod>" if order.IsCod is not None else "")
            + (f"<CodAmount>{_e(order.CodAmount)}</CodAmount>" if order.CodAmount else "")
            + (f"<CodCollectionType>{_e(order.CodCollectionType)}</CodCollectionType>" if order.CodCollectionType is not None else "")
            + (f"<CodBillingType>{_e(order.CodBillingType)}</CodBillingType>" if order.CodBillingType is not None else "")
            + (f"<CityCode>{_e(order.CityCode)}</CityCode>" if order.CityCode else "")
            + (f"<TownCode>{_e(order.TownCode)}</TownCode>" if order.TownCode else "")
            + (f"<ReceiverDistrictName>{_e(order.ReceiverDistrictName)}</ReceiverDistrictName>" if order.ReceiverDistrictName else "")
            + (f"<ReceiverQuarterName>{_e(order.ReceiverQuarterName)}</ReceiverQuarterName>" if order.ReceiverQuarterName else "")
            + (f"<ReceiverAvenueName>{_e(order.ReceiverAvenueName)}</ReceiverAvenueName>" if order.ReceiverAvenueName else "")
            + (f"<ReceiverStreetName>{_e(order.ReceiverStreetName)}</ReceiverStreetName>" if order.ReceiverStreetName else "")
            + (f"<PrivilegeOrder>{_e(order.PrivilegeOrder)}</PrivilegeOrder>" if order.PrivilegeOrder else "")
            + pd_xml +
            "</Order></orderInfo>"
            f"<userName>{_e(self.username)}</userName>"
            f"<password>{_e(self.password)}</password>"
            f"</SetOrder>"
        )

        resp = self._post("SetOrder", body)
        ok, code, msg = self._interpret_response(resp)
        return {
            "ok": ok,
            "code": code,
            "message": msg,
            "http_status": resp.status_code,
            "raw": resp.text,
        }

    def get_order_with_integration_code(self, integration_code: str) -> Dict[str, Any]:
        body = (
            f'<GetOrderWithIntegrationCode xmlns="{_ARAS_NS}">'
            f"<integrationCode>{_e(integration_code)}</integrationCode>"
            f"<userName>{_e(self.username)}</userName>"
            f"<password>{_e(self.password)}</password>"
            f"</GetOrderWithIntegrationCode>"
        )
        resp = self._post("GetOrderWithIntegrationCode", body)
        ok, code, msg = self._interpret_response(resp)
        return {
            "ok": ok,
            "code": code,
            "message": msg,
            "http_status": resp.status_code,
            "raw": resp.text,
        }

    def cancel_dispatch(self, order_code: str) -> Dict[str, Any]:
        body = (
            f'<CancelDispatch xmlns="{_ARAS_NS}">'
            f"<orderCode>{_e(order_code)}</orderCode>"
            f"<userName>{_e(self.username)}</userName>"
            f"<password>{_e(self.password)}</password>"
            f"</CancelDispatch>"
        )
        resp = self._post("CancelDispatch", body)
        ok, code, msg = self._interpret_response(resp)
        return {
            "ok": ok,
            "code": code,
            "message": msg,
            "http_status": resp.status_code,
            "raw": resp.text,
        }

    # ---------- Helpers ----------

    _ERR_MAP = {
        "0": "Başarılı",
        "934": "Alıcı adı 100 karakteri aşamaz.",
        "935": "IntegrationCode alanı hatalı/boş/limit aşıldı/telefon formatı.",
        "936": "Zorunlu alan/format hatası (adres, ad, il/ilçe, ödeme tipi vb.).",
        "1000": "Kullanıcı adı veya şifre hatalı.",
        "1002": "Entegrasyon bilgileriniz güncellenirken hata.",
        "1003": "Aras şube bilginiz tanımlı değil.",
        "1006": "Sevk adresi bulunamadı/aktif değil.",
        "5000": "Genel sistem hatası.",
        "5002": "Genel sistem hatası.",
        "5003": "Genel sistem hatası.",
        "5004": "Genel sistem hatası.",
        "5005": "Genel sistem hatası.",
        "5006": "Genel sistem hatası.",
        "5007": "Genel sistem hatası.",
        "5008": "Kayıt yapılamadı.",
        "60020": "En az bir adet sipariş bilgisi göndermelisiniz.",
        "60022": "İrsaliyesi kesilmiş gönderi güncellenemez.",
        "70018": "ReceiverAddress en fazla 250 karakter.",
        "70019": "InvoiceNumber en fazla 20 karakter.",
        "70021": "Parça/kolİ sayısı uyumsuz veya 0.",
        "70022": "Parça barkod bilgisi eksik.",
        "70023": "Volume bilgisi eksik.",
        "70024": "Kg bilgisi eksik.",
        "70025": "Bir dosya gönderisi bir parçadan oluşmalıdır.",
        "70026": "Decimal/tekrar barkod/varış kapalı/COD tutar sınırı hataları.",
        "70027": "Kg decimal olmalı / irsaliyesi kesilmiş güncellenemez.",
        "70028": "Parça barkodları aynı olamaz / kargo işleme alındı.",
        "70121": "TradingWaybillNumber en fazla 16 karakter.",
        "999": "İrsaliyesi kesilmiş sipariş iptal edilemez.",
        "1": "Silme başarılı (CancelDispatch).",
        "-1": "Kayıt bulunamadı (CancelDispatch).",
        "-2": "Kullanıcı adı/şifre hatalı (CancelDispatch).",
    }

    def _interpret_response(self, resp: requests.Response) -> Tuple[bool, Optional[str], str]:
        """
        Basit yorumlayıcı:
        - 404 ve gövdede 'İşlem sonucu alındı' varsa → başarı kabul (Aras bazen böyle dönebiliyor)
        - Aksi halde gövdedeki sayı kodlarını yakalayıp mesajı üretir.
        """
        text = resp.text or ""
        if resp.status_code == 404 and "İşlem sonucu alındı" in text:
            return True, "0", "Başarılı (404/gövde onayı)"

        # Tipik durumlarda result içinde kod dönüyor; yoksa 'Başarılı' kelimesini arayalım
        m_code = re.search(r">\s*(-?\d{1,5})\s*<", text)
        if m_code:
            code = m_code.group(1)
            ok = (code == "0" or code == "1")
            msg = self._ERR_MAP.get(code, f"Kod {code}")
            return ok, code, msg

        if "Başarılı" in text or "basarili" in text.lower():
            return True, "0", "Başarılı"

        # HTTP 200 olup kod çıkmazsa ham metni döndür
        return (resp.ok, None, "Yanıt parse edilemedi")
