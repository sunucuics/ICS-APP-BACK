# backend/app/utils/ip.py
from __future__ import annotations
import os
import ipaddress
from typing import Optional, Iterable
from fastapi import Request

# Opsiyonel: güvenilir proxy IP listesi (env ile verilebilir, virgülle ayrılmış)
# Örnek: TRUSTED_PROXIES="127.0.0.1,172.18.0.1,203.0.113.5"
TRUSTED_PROXIES = {
    ip.strip()
    for ip in (os.getenv("TRUSTED_PROXIES", "") or "").split(",")
    if ip.strip()
}


def _is_private_or_local(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except Exception:
        return True
    return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local


def _first_candidate_from_xff(xff: str) -> Optional[str]:
    # X-Forwarded-For: client, proxy1, proxy2,... -> ilk element client
    if not xff:
        return None
    first = xff.split(",")[0].strip()
    return first or None


def _iter_candidate_ips(request: Request, override: Optional[str]) -> Iterable[str]:
    """
    Aday IP'leri öncelik sırasına göre döndürür:
    1) override parametresi (test amaçlı)
    2) CF-Connecting-IP
    3) X-Real-IP
    4) X-Forwarded-For (ilk)
    5) request.client.host (son çare)
    """
    if override:
        yield override.split(",")[0].strip()

    headers = request.headers
    yield headers.get("cf-connecting-ip", "") or ""
    yield headers.get("x-real-ip", "") or ""
    xff = headers.get("x-forwarded-for", "") or ""
    yield _first_candidate_from_xff(xff) or ""
    # son çare: socket IP (proxy iç IP olabilir)
    yield (request.client.host or "").split(",")[0].strip()


def get_client_public_ip(request: Request, override: Optional[str] = None) -> Optional[str]:
    """
    Request'den güvenli şekilde public client IP'yi döndürür.
    - Private/loopback gibi IP'leri eler.
    - Eğer proxy arkasındaysanız ve X-Forwarded-For header'larına güveniyorsanız,
      proxy'lerinizin IP'lerini TRUSTED_PROXIES ile belirtin.
    - override parametresi test için kullanılabilir (örn. gerçek public IP ver).
    """
    # Eğer proxy'ler arkasındaysan, yalnızca TRUSTED_PROXIES içinden gelen proxylere
    # header'lara güvenmeliyiz. Eğer TRUSTED_PROXIES boşsa header'lara yine de bakarız,
    # çünkü küçük kurulumlarda bu rahatlık isteğe bağlıdır.
    socket_ip = (request.client.host or "").split(",")[0].strip()

    # Eğer TRUSTED_PROXIES setli ve socket IP trusted değilse,
    # header'lara itibar etmeyebiliriz; yine de en son socket IP'yi kontrol ederiz.
    trust_headers = True
    if TRUSTED_PROXIES:
        trust_headers = socket_ip in TRUSTED_PROXIES

    candidates = []
    for cand in _iter_candidate_ips(request, override):
        if not cand:
            continue
        # eğer cand socket_ip ise her zaman değerlendirilir
        if cand == socket_ip:
            candidates.append(cand)
            continue
        # header kaynaklıysa, eğer trust_headers False ise atla
        if cand != socket_ip and not trust_headers:
            continue
        candidates.append(cand)

    # İlk geçerli (public) IP'yi döndür
    for ip in candidates:
        try:
            # normalize
            ip_str = ip.split(",")[0].strip()
            if not ip_str:
                continue
            ip_obj = ipaddress.ip_address(ip_str)
        except Exception:
            continue
        if not (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local):
            return ip_str
    return None
