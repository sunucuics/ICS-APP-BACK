# backend/app/integrations/aras_wsdl.py

import os
import requests
import xml.etree.ElementTree as ET

from backend.app.config import settings

ARAS_SVC_URL = "https://customerservices.araskargo.com.tr/ArasCargoCustomerIntegrationService/ArasCargoIntegrationService.svc"

def _post_soap(method: str, body: str) -> requests.Response:
    timeout = settings.ARAS_TIMEOUT
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
    <soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                     xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
      <soap12:Body>{body}</soap12:Body>
    </soap12:Envelope>"""
    return requests.post(ARAS_SVC_URL, data=envelope.encode("utf-8"), headers=headers, timeout=timeout)

def call_setdataxml(login_info: str, query_info: str) -> str:
    xml = f"""
    <SetDataXML xmlns="http://tempuri.org/">
      <loginInfo>{login_info}</loginInfo>
      <queryInfo>{query_info}</queryInfo>
    </SetDataXML>
    """.strip()
    resp = _post_soap("SetDataXML", xml)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    el = root.find(".//SetDataXMLResult") or root.find(".//t:SetDataXMLResult", {"t":"http://tempuri.org/"})
    return el.text if el is not None else ""

def call_getqueryjson(login_info: str, query_info: str) -> str:
    xml = f"""
    <GetQueryJSON xmlns="http://tempuri.org/">
      <loginInfo>{login_info}</loginInfo>
      <queryInfo>{query_info}</queryInfo>
    </GetQueryJSON>
    """.strip()
    resp = _post_soap("GetQueryJSON", xml)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    el = root.find(".//GetQueryJSONResult") or root.find(".//t:GetQueryJSONResult", {"t":"http://tempuri.org/"})
    return el.text if el is not None else ""
