"""
HTTP Client Utilities for Firebase API Communication

Bu modül, Firebase API'leri ile güvenli ve performanslı iletişim için:
- Global singleton HTTP client (connection pooling)
- Retry mekanizması (exponential backoff)
- Circuit breaker pattern
- Kapsamlı hata yönetimi

Kullanım:
    from backend.app.core.http_client import get_http_client, retry_with_backoff
    
    @retry_with_backoff(max_retries=3)
    async def call_firebase():
        client = get_http_client()
        response = await client.post(url, json=payload)
        return response
"""
import asyncio
import logging
from typing import Optional, Callable, Any
from functools import wraps
import httpx
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Global HTTP client instance
_http_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

# Circuit breaker state - DEVRE DIŞI (threshold çok yüksek)
_circuit_breaker = {
    "failures": 0,
    "last_failure_time": None,
    "state": "closed",  # closed, open, half_open
    "failure_threshold": 9999,  # Circuit breaker devre dışı
    "timeout_seconds": 60,
}


def get_http_client() -> httpx.AsyncClient:
    """
    Global HTTP client instance döndürür.
    Connection pooling ve keep-alive otomatik aktif.
    
    Returns:
        httpx.AsyncClient: Global HTTP client instance
    """
    global _http_client
    
    if _http_client is None:
        # İlk kez çağrıldığında client oluştur
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,       # Bağlantı timeout (10 -> 5)
                read=10.0,         # Okuma timeout (15 -> 10)
                write=5.0,         # Yazma timeout (10 -> 5)
                pool=2.0,          # Pool timeout (5 -> 2)
            ),
            limits=httpx.Limits(
                max_connections=100,        # Maksimum bağlantı sayısı
                max_keepalive_connections=20,  # Keep-alive bağlantılar
                keepalive_expiry=30.0,     # Keep-alive süresi
            ),
            http2=False,  # HTTP/2 kapalı (daha stabil)
            follow_redirects=True,
        )
        logger.info("Global HTTP client initialized with connection pooling")
    
    return _http_client


async def close_http_client():
    """
    Global HTTP client'ı kapatır.
    Uygulama kapatılırken çağrılmalı.
    """
    global _http_client
    
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.info("Global HTTP client closed")


def _check_circuit_breaker() -> bool:
    """
    Circuit breaker durumunu kontrol eder.
    
    Returns:
        bool: True ise istek gönderilebilir, False ise circuit açık
    """
    global _circuit_breaker
    
    if _circuit_breaker["state"] == "closed":
        return True
    
    if _circuit_breaker["state"] == "open":
        # Timeout doldu mu kontrol et
        if _circuit_breaker["last_failure_time"]:
            elapsed = (datetime.now() - _circuit_breaker["last_failure_time"]).total_seconds()
            if elapsed > _circuit_breaker["timeout_seconds"]:
                # Half-open durumuna geç (bir deneme izni ver)
                _circuit_breaker["state"] = "half_open"
                logger.info("Circuit breaker: OPEN -> HALF_OPEN")
                return True
        return False
    
    # half_open durumunda
    return True


def _record_success():
    """Circuit breaker için başarılı istek kaydı"""
    global _circuit_breaker
    
    if _circuit_breaker["state"] == "half_open":
        # Half-open'dan closed'a geç
        _circuit_breaker["state"] = "closed"
        _circuit_breaker["failures"] = 0
        logger.info("Circuit breaker: HALF_OPEN -> CLOSED (service recovered)")


def _record_failure():
    """Circuit breaker için başarısız istek kaydı"""
    global _circuit_breaker
    
    _circuit_breaker["failures"] += 1
    _circuit_breaker["last_failure_time"] = datetime.now()
    
    if _circuit_breaker["failures"] >= _circuit_breaker["failure_threshold"]:
        if _circuit_breaker["state"] != "open":
            _circuit_breaker["state"] = "open"
            logger.error(
                f"Circuit breaker: OPEN (failures: {_circuit_breaker['failures']}, "
                f"timeout: {_circuit_breaker['timeout_seconds']}s)"
            )


def retry_with_backoff(
    max_retries: int = 2,  # Default 2 retry (önceden 3)
    backoff_factor: float = 0.3,  # Daha hızlı backoff (önceden 1.0)
    max_backoff: float = 2.0,  # Max 2 saniye (önceden 10.0)
    retry_on: tuple = (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.NetworkError,
    )
):
    """
    Exponential backoff ile retry decorator.
    
    Args:
        max_retries: Maksimum retry sayısı
        backoff_factor: Backoff çarpanı (1.0 = 1s, 2s, 4s, 8s...)
        max_backoff: Maksimum bekleme süresi
        retry_on: Hangi exception'larda retry yapılacak
        
    Usage:
        @retry_with_backoff(max_retries=3, backoff_factor=1.0)
        async def call_api():
            client = get_http_client()
            return await client.post(url, json=data)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    # Circuit breaker kontrolü
                    if not _check_circuit_breaker():
                        logger.warning("Circuit breaker is OPEN, request blocked")
                        raise httpx.ConnectError("Service temporarily unavailable (circuit breaker open)")
                    
                    # Asıl fonksiyonu çağır
                    result = await func(*args, **kwargs)
                    
                    # Başarılı - circuit breaker'ı sıfırla
                    _record_success()
                    
                    if attempt > 0:
                        logger.info(f"Request succeeded after {attempt} retries")
                    
                    return result
                    
                except retry_on as e:
                    last_exception = e
                    _record_failure()
                    
                    if attempt < max_retries:
                        # Exponential backoff hesapla
                        backoff_time = min(
                            backoff_factor * (2 ** attempt),
                            max_backoff
                        )
                        
                        logger.warning(
                            f"Request failed (attempt {attempt + 1}/{max_retries + 1}): {type(e).__name__}: {e}. "
                            f"Retrying in {backoff_time:.1f}s..."
                        )
                        
                        await asyncio.sleep(backoff_time)
                    else:
                        logger.error(
                            f"Request failed after {max_retries + 1} attempts: {type(e).__name__}: {e}"
                        )
                        
                except Exception as e:
                    # Retry edilmeyecek exception'lar direkt fırlat
                    logger.error(f"Non-retryable error: {type(e).__name__}: {e}")
                    _record_failure()
                    raise
            
            # Tüm retry'lar tükendi, son exception'ı fırlat
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


async def safe_firebase_request(
    method: str,
    url: str,
    **kwargs
) -> httpx.Response:
    """
    Firebase API'sine güvenli HTTP isteği yapar.
    Retry ve circuit breaker otomatik aktif.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: Request URL
        **kwargs: httpx request parametreleri (json, headers, etc.)
        
    Returns:
        httpx.Response: HTTP response
        
    Raises:
        httpx.TimeoutException: Timeout durumunda
        httpx.ConnectError: Bağlantı hatası
        httpx.HTTPStatusError: 4xx/5xx hataları
    """
    @retry_with_backoff(max_retries=3, backoff_factor=1.0)
    async def _make_request():
        client = get_http_client()
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response
    
    return await _make_request()
