# backend/app/core/mailer.py
from __future__ import annotations
import asyncio, smtplib, ssl, logging, re, textwrap , socket
from email.message import EmailMessage
from typing import Optional
from backend.app.config import settings

log = logging.getLogger("mailer")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

def _as_bool(v) -> bool:
    if isinstance(v, bool): return v
    if v is None: return False
    return str(v).strip().lower() in {"1", "true", "yes", "on"}

def _plain_from_html(html: str) -> str:
    txt = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    return textwrap.dedent(txt).strip()

async def mailer_send(
    *,
    to: str,
    subject: str,
    html: str,
    sender_name: Optional[str] = None,
    reply_to: Optional[str] = None
) -> None:
    """
    Güvenli ve dayanıklı SMTP gönderici.
    - STARTTLS (587) ve SSL (465) akıllı seçim + otomatik 465 fallback.
    - DNS/port preflight ile ağ sorunlarını hızlı teşhis eder.
    - SMTP yanıt kodlarını yüzeye çıkarır; prod'da kolay debug için EMAIL_DEBUG ile ayrıntılı loglar verir.
    """
    host = getattr(settings, "smtp_host", None)
    port = int(getattr(settings, "smtp_port", 0) or 0)
    user = getattr(settings, "smtp_user", None)
    pwd  = getattr(settings, "smtp_password", None)
    from_addr = getattr(settings, "smtp_from", None) or user
    use_starttls = bool(str(getattr(settings, "smtp_use_starttls", True)).strip().lower() in {"1","true","yes","on"})
    email_debug  = bool(str(getattr(settings, "email_debug", False)).strip().lower() in {"1","true","yes","on"})

    # Konfigürasyon doğrulama
    if not (host and port and user and pwd and from_addr):
        raise RuntimeError(
            f"SMTP config eksik: host={host} port={port} user={bool(user)} "
            f"pwd={bool(pwd)} from={from_addr}"
        )

    # Mesaj kurulumu
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = f"{sender_name} <{from_addr}>" if sender_name else from_addr
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    # HTML + düz metin
    def _plain_from_html(h: str) -> str:
        # Basit, bağımsız bir düz metin dönüştürücü (mevcut helper yoksa)
        import re, textwrap
        txt = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
        txt = re.sub(r"<[^>]+>", "", txt)
        return textwrap.dedent(txt).strip()

    msg.set_content(_plain_from_html(html))
    msg.add_alternative(html, subtype="html")

    # Tek yerde TLS bağlamı
    ctx = ssl.create_default_context()

    def _send_starttls(_port: int):
        with smtplib.SMTP(host, _port, timeout=40) as s:
            if email_debug:
                s.set_debuglevel(1)
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)

    def _send_ssl(_port: int):
        with smtplib.SMTP_SSL(host, _port, context=ctx, timeout=40) as s:
            if email_debug:
                s.set_debuglevel(1)
            s.login(user, pwd)
            s.send_message(msg)

    # Preflight: verilen porta çıkış var mı?
    if email_debug:
        log.info("[MAIL] host=%s port=%s starttls=%s from=%s to=%s subject=%s",
                 host, port, use_starttls, from_addr, to, subject)
        log.info("[MAIL] preflight: %s:%s erişim denemesi…", host, port)
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        primary_reachable = True
    except Exception as e:
        primary_reachable = False
        if email_debug:
            log.warning("Preflight FAILED %s:%s → %s", host, port, e)

    # Ana akış + fallback
    try:
        if primary_reachable:
            # Port erişilebilir; doğru protokolle dene
            if use_starttls and port != 465:
                _send_starttls(port)
            else:
                _send_ssl(port or 465)
        else:
            # Verilen port kapalıysa önce 465 SSL deneriz
            if email_debug:
                log.info("Primary port unreachable. Trying SSL:465 fallback…")
            sock = socket.create_connection((host, 465), timeout=5)
            sock.close()
            _send_ssl(465)
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        # Ağ tabanlı hatalarda doğrudan 465'e düş
        if email_debug:
            log.warning("SMTP %s:%s unreachable (%s). Forcing SSL:465…", host, port, e)
        _send_ssl(465)
    except smtplib.SMTPResponseException as e:
        # Sunucu yanıt kodu/hatasını yüzeye çıkar
        code = getattr(e, "smtp_code", None)
        err  = getattr(e, "smtp_error", None)
        log.error("SMTP response error: code=%s msg=%s", code, err)
        raise
    except Exception:
        log.exception("SMTP unknown error")
        raise



# -------- HTML ŞABLONLARI --------
def _brand() -> str:
    return getattr(settings, "brand_name", None) or "ICS"

def _order_link(order_id: str) -> str:
    base = getattr(settings, "frontend_base_url", "") or ""
    return f"{base}/orders/{order_id}" if base else "#"

# --- yardımcılar (şablonlar için) ---
def _currency_symbol() -> str:
    cur = (getattr(settings, "currency", None) or "TRY").upper()
    return "₺" if cur in {"TRY", "TL"} else cur

def _fmt_money(v: float) -> str:
    try:
        num = float(v or 0)
    except Exception:
        num = 0.0
    # 12.345,67 formatı
    s = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {_currency_symbol()}"

def _to_float(v) -> float:
    try: return float(v)
    except Exception: return 0.0

def _unit_price(item: dict) -> float:
    # final_price > 0 ise onu kullan, değilse price'a düş
    fp = item.get("final_price", None)
    if fp is not None and _to_float(fp) > 0:
        return _to_float(fp)
    return _to_float(item.get("price"))

def _items_block(items: Optional[list], totals: Optional[dict]) -> str:
    items = items or []
    if not items:
        return ""
    rows = []
    computed_subtotal = 0.0
    for it in items:
        name = (it.get("name") or it.get("title") or it.get("product_id") or "").strip()
        qty = int(it.get("qty", 0) or 0)
        unit = _unit_price(it)
        sub = qty * unit
        computed_subtotal += sub
        rows.append(
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eef2f7'>{name}</td>"
            f"<td style='padding:8px 12px;text-align:center;border-bottom:1px solid #eef2f7'>{qty}</td>"
            f"<td style='padding:8px 12px;text-align:right;border-bottom:1px solid #eef2f7'>{_fmt_money(unit)}</td>"
            f"<td style='padding:8px 12px;text-align:right;border-bottom:1px solid #eef2f7'>{_fmt_money(sub)}</td>"
            f"</tr>"
        )
    grand = totals.get("grand_total") if isinstance(totals, dict) else None
    if grand is None:
        grand = computed_subtotal
    tfoot = (
        "<tr>"
        "<td colspan='3' style='padding:10px 12px;text-align:right;font-weight:600'>Toplam</td>"
        f"<td style='padding:10px 12px;text-align:right;font-weight:700'>{_fmt_money(grand)}</td>"
        "</tr>"
    )
    table = (
        "<table role='presentation' width='100%' "
        "style='border-collapse:collapse;width:100%;margin-top:12px'>"
        "<thead>"
        "<tr style='background:#f8fafc;color:#0f172a'>"
        "<th style='text-align:left;padding:10px 12px'>Ürün</th>"
        "<th style='text-align:center;padding:10px 12px;width:60px'>Adet</th>"
        "<th style='text-align:right;padding:10px 12px;width:120px'>Birim</th>"
        "<th style='text-align:right;padding:10px 12px;width:140px'>Ara Toplam</th>"
        "</tr>"
        "</thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"<tfoot>{tfoot}</tfoot>"
        "</table>"
    )
    return table

def _address_block(address: Optional[dict]) -> str:
    if not isinstance(address, dict) or not address:
        return ""
    keys_in_order = [
        "title", "full_name", "name",
        "line1", "line2", "neighborhood", "district", "street",
        "building", "buildingNo", "apartment", "no",
        "postcode", "postal_code", "zipCode",  # <- eklendi
        "city", "state", "country",
        "full_address", "address"
    ]
    parts = []
    for k in keys_in_order:
        v = address.get(k)
        if v:
            parts.append(str(v))
    # benzersiz + boş olmayanları satır satır yaz
    seen, lines = set(), []
    for p in parts:
        if p not in seen:
            lines.append(p); seen.add(p)
    shown = "<br/>".join(lines)
    return (
        "<div style='margin-top:16px;padding:12px;border:1px solid #eef2f7;border-radius:10px'>"
        "<div style='font-weight:600;margin-bottom:6px'>Teslimat adresi</div>"
        f"<div style='color:#334155'>{shown}</div>"
        "</div>"
    )

# -------- HTML ŞABLONLARI (DETAYLI) --------
def tpl_shipped_html(
    customer_name: str,
    order_id: str,
    items: Optional[list] = None,
    totals: Optional[dict] = None,
    address: Optional[dict] = None,
    tracking_number: str = "",
    tracking_url: Optional[str] = None,
) -> str:
    order_link = _order_link(order_id)
    tlink = f'<p style="margin:8px 0 0"><a href="{tracking_url}">Kargoyu takip et</a></p>' if tracking_url else ""
    return f"""<!doctype html><html><body style="font-family:system-ui;-webkit-font-smoothing:antialiased;background:#f6f9fc;padding:24px">
<table role="presentation" width="100%" style="max-width:680px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
<tr><td style="padding:24px 24px 0;font-size:20px;font-weight:700">{_brand()}</td></tr>
<tr><td style="padding:16px 24px 0">
  <h2 style="margin:0 0 8px">Kargonuz yola çıktı 🚚</h2>
  <p style="margin:0 0 4px">Merhaba {customer_name or ''}, siparişiniz kargoya verildi.</p>
  <p style="margin:0 0 8px">Takip numaranız: <b>{tracking_number}</b></p>
  {tlink}
  <p style="margin:12px 0">Sipariş detayları: <a href="{order_link}">{order_link}</a></p>
  {_items_block(items, totals)}
  {_address_block(address)}
</td></tr>
<tr><td style="padding:16px 24px 24px;color:#6b7280;font-size:12px">Bu e-posta otomatik gönderildi; yine de yanıtlayarak bize ulaşabilirsiniz.</td></tr>
</table></body></html>"""

def tpl_delivered_html(
    customer_name: str,
    order_id: str,
    items: Optional[list] = None,
    totals: Optional[dict] = None,
    address: Optional[dict] = None,
) -> str:
    order_link = _order_link(order_id)
    return f"""<!doctype html><html><body style="font-family:system-ui;-webkit-font-smoothing:antialiased;background:#f6f9fc;padding:24px">
<table role="presentation" width="100%" style="max-width:680px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
<tr><td style="padding:24px 24px 0;font-size:20px;font-weight:700">{_brand()}</td></tr>
<tr><td style="padding:16px 24px 0">
  <h2 style="margin:0 0 8px">Teslim edildi ✅</h2>
  <p style="margin:0 8px 8px 0">Merhaba {customer_name or ''}, siparişiniz başarıyla teslim edilmiştir.</p>
  <p style="margin:12px 0">Sipariş detayları: <a href="{order_link}">{order_link}</a></p>
  {_items_block(items, totals)}
  {_address_block(address)}
</td></tr>
<tr><td style="padding:16px 24px 24px;color:#6b7280;font-size:12px">Bizi tercih ettiğiniz için teşekkür ederiz.</td></tr>
</table></body></html>"""

def tpl_canceled_html(
    customer_name: str,
    order_id: str,
    reason: Optional[str] = None,
    items: Optional[list] = None,
    totals: Optional[dict] = None,
    address: Optional[dict] = None,
) -> str:
    order_link = _order_link(order_id)
    reason_html = f"<p style='margin:8px 0'>İptal nedeni: {reason}</p>" if reason else ""
    return f"""<!doctype html><html><body style="font-family:system-ui;-webkit-font-smoothing:antialiased;background:#f6f9fc;padding:24px">
<table role="presentation" width="100%" style="max-width:680px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
<tr><td style="padding:24px 24px 0;font-size:20px;font-weight:700">{_brand()}</td></tr>
<tr><td style="padding:16px 24px 0">
  <h2 style="margin:0 0 8px">Siparişiniz iptal edildi ❗</h2>
  <p style="margin:0 0 8px">Merhaba {customer_name or ''}, siparişiniz iptal edilmiştir.</p>
  {reason_html}
  <p style="margin:12px 0">Detaylar: <a href="{order_link}">{order_link}</a></p>
  {_items_block(items, totals)}
  {_address_block(address)}
</td></tr>
<tr><td style="padding:16px 24px 24px;color:#6b7280;font-size:12px">Sorularınız için bu e-postaya yanıt verebilirsiniz.</td></tr>
</table></body></html>"""

