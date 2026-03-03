"""
# `app/routers/appointments.py` — Randevu Yönetimi Dokümantasyonu

Bu modül, kullanıcıların randevu talebi oluşturabilmesini, kendi randevularını listeleyebilmesini ve admin paneli üzerinden randevuların yönetilebilmesini sağlayan API uç noktalarını içerir.
Hem **kullanıcı** hem de **admin** işlemleri için ayrı router’lar tanımlanmıştır.
"""
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from typing import List, Optional, Any, Dict
from datetime import timedelta, datetime
import logging
import threading
from pydantic import BaseModel

from backend.app.core.security import get_current_user, get_current_admin
from backend.app.config import db
from backend.app.schemas.appointment import (
    AppointmentOut, AppointmentStatus, AppointmentAdminOut,
    ServiceAvailability, ServiceBrief, AppointmentWithDetails
)
from firebase_admin import messaging, firestore

logger = logging.getLogger("ics.appointments")

router = APIRouter(prefix="/appointments", tags=["Appointments"])

# --- Güvenli tarih dönüştürücü ------------------------------------------------
def _coerce_dt(v: Any) -> Optional[datetime]:
    """
    Firestore Timestamp | str (ISO/ISOZ) | datetime(aware/naive) | None -> naive UTC datetime
    Saklama için kullanılır - UTC olarak saklar.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        # aware ise UTC'ye çevirip tzinfo'yu at
        if v.tzinfo:
            from datetime import timezone
            utc_dt = v.astimezone(timezone.utc)
            return utc_dt.replace(tzinfo=None)
        return v  # already naive, assume UTC
    # Firestore Timestamp nesneleri to_datetime() destekler
    if hasattr(v, "to_datetime"):
        dt = v.to_datetime()  # Firestore genellikle UTC-aware döner
        if dt.tzinfo:
            from datetime import timezone
            utc_dt = dt.astimezone(timezone.utc)
            return utc_dt.replace(tzinfo=None)
        return dt
    if isinstance(v, str):
        s = v.strip()
        try:
            # 'Z' (UTC) son ekini destekle
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo:
                from datetime import timezone
                utc_dt = dt.astimezone(timezone.utc)
                return utc_dt.replace(tzinfo=None)
            return dt  # already naive, assume UTC
        except Exception as exc:
            logger.debug("ISO parse failed for %r: %s", v, exc)
            return None
    return None


def _to_local_dt(v: Any) -> Optional[datetime]:
    """
    Firestore'dan okunan UTC datetime'ı Türkiye saatine (UTC+3) çevirir.
    API response'ları için kullanılır.
    Server timezone'undan bağımsız çalışır (Cloud Run UTC kullanır).
    """
    utc_dt = _coerce_dt(v)
    if utc_dt is None:
        return None
    from datetime import timezone, timedelta
    _TURKEY_TZ = timezone(timedelta(hours=3))
    aware_utc = utc_dt.replace(tzinfo=timezone.utc)
    local_dt = aware_utc.astimezone(_TURKEY_TZ)
    return local_dt.replace(tzinfo=None)  # Naive Türkiye datetime döndür


def _from_local_dt(v: Any) -> Optional[datetime]:
    """
    Frontend'den gelen Türkiye saatindeki (UTC+3) naive datetime'ı UTC'ye çevirir.
    Yazma (kaydetme) işlemleri için kullanılır.
    Frontend timezone bilgisi göndermez — saat her zaman kullanıcının yerel saatidir (Türkiye).
    """
    dt = _coerce_dt(v)
    if dt is None:
        return None
    from datetime import timezone, timedelta
    _TURKEY_TZ = timezone(timedelta(hours=3))
    # Naive datetime'ı Türkiye saati olarak işaretle, sonra UTC'ye çevir
    aware_local = dt.replace(tzinfo=_TURKEY_TZ)
    utc_dt = aware_local.astimezone(timezone.utc)
    return utc_dt.replace(tzinfo=None)  # Naive UTC döndür


# --- Admin Push Notification Helper -------------------------------------------
def _send_admin_push_notification(title: str, body: str, appointment_id: str):
    """
    Admin kullanıcılara (role="admin") FCM push notification gönderir.
    Uygulama kapalı olsa bile bildirim alınır.
    """
    try:
        # Admin kullanıcıların FCM token'larını al (role alanı kullanılıyor)
        admin_users = db.collection("users").where("role", "==", "admin").stream()
        
        fcm_tokens = []
        for user_doc in admin_users:
            user_data = user_doc.to_dict() or {}
            fcm_token = user_data.get("fcm_token")
            if fcm_token:
                fcm_tokens.append(fcm_token)
        
        # Duplicate token'ları kaldır (aynı cihaza çift bildirim gitmesin)
        fcm_tokens = list(set(fcm_tokens))

        if not fcm_tokens:
            logger.info("No admin FCM tokens found, skipping push notification")
            return

        logger.info("Sending push notification to %d admin devices", len(fcm_tokens))
        
        # FCM multicast mesajı oluştur
        multicast_message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={
                'click_action': 'FLUTTER_NOTIFICATION_CLICK',
                'type': 'admin_appointment',
                'appointment_id': appointment_id,
                'title': title,
                'body': body,
            },
            tokens=fcm_tokens,
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    sound='default',
                    priority='high',
                    channel_id='high_importance_channel',  # Flutter'daki channel ile eşleşmeli
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound='default',
                        badge=1,
                        content_available=True,
                    ),
                ),
            ),
        )
        
        # Bildirimi gönder
        response = messaging.send_each_for_multicast(multicast_message)
        logger.info(
            "FCM push sent to admins: success=%d, failure=%d",
            response.success_count,
            response.failure_count
        )
        
    except Exception as e:
        logger.error("Failed to send admin push notification: %s", e)


# === Kullanıcı: Randevu Talebi ===============================================
@router.post("/", response_model=AppointmentOut)
def request_appointment(
    service_id: str = Form(...),
    start: datetime = Form(...),
    notes: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    """
    Randevu talep formu (form-data).
    """
    # Giriş tarihini normalize et — frontend Türkiye saati gönderir, UTC'ye çevir
    start_norm = _from_local_dt(start)
    if not start_norm:
        raise HTTPException(status_code=400, detail="Invalid start datetime format")
    end_time = start_norm + timedelta(hours=1)

    # Servis kontrolü
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or (service_doc.to_dict() or {}).get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    if (service_doc.to_dict() or {}).get('is_upcoming'):
        raise HTTPException(status_code=400, detail="Service not yet available for booking")

    # Çakışma kontrolü (timestamp/str güvenli)
    overlapping = db.collection("appointments").where("service_id", "==", service_id) \
                    .where("status", "in", ["pending", "approved"]).stream()
    for appt in overlapping:
        data = appt.to_dict() or {}
        s = _coerce_dt(data.get('start'))
        e = _coerce_dt(data.get('end')) or (s + timedelta(hours=1) if s else None)
        if not s or not e:
            logger.debug("Skip overlap check due to missing times for doc %s", appt.id)
            continue
        # [start_norm, end_time) ile [s, e) kesişim kontrolü
        if (start_norm < e) and (end_time > s):
            raise HTTPException(status_code=400, detail="Time slot is not available")

    # Kayıt
    user_id = current_user['id']
    ref = db.collection("appointments").document()
    appt_data = {
        "service_id": service_id,
        "user_id": user_id,
        "start": start_norm,
        "end": end_time,
        "status": "pending",
        "notes": notes
    }
    ref.set(appt_data)
    appt_data["id"] = ref.id

    # Admin bildirimi ve FCM push'ı arka plan thread'inde çalıştır
    # Kullanıcı yanıtı beklemeden hızlıca döner
    service_data = service_doc.to_dict() or {}
    service_title = service_data.get("title") or "Hizmet"

    def _background_notify():
        try:
            user_doc_bg = db.collection("users").document(user_id).get()
            user_data = user_doc_bg.to_dict() if user_doc_bg.exists else {}

            user_name = user_data.get("name") or user_data.get("email") or "Bir müşteri"

            start_local = _to_local_dt(start_norm)
            date_str = start_local.strftime("%d/%m/%Y")
            time_str = start_local.strftime("%H:%M")

            notification_title = "🗓️ Yeni Randevu Talebi"
            notification_body = (
                f"{user_name} - {service_title} için "
                f"{date_str} tarihinde saat {time_str}'de randevu talebi oluşturdu."
            )

            # Firestore bildirim + FCM push aynı thread'de sıralı çalışır
            notification_ref = db.collection("admin_notifications").document()
            notification_ref.set({
                "title": notification_title,
                "body": notification_body,
                "type": "appointment",
                "is_read": False,
                "created_at": firestore.SERVER_TIMESTAMP,
                "data": {
                    "appointment_id": ref.id,
                    "user_id": user_id,
                    "user_name": user_data.get("name"),
                    "user_email": user_data.get("email"),
                    "user_phone": user_data.get("phone"),
                    "service_id": service_id,
                    "service_title": service_title,
                    "start": start_norm.isoformat(),
                    "end": end_time.isoformat(),
                },
            })
            logger.info("Admin notification created for appointment %s", ref.id)

            _send_admin_push_notification(notification_title, notification_body, ref.id)

            # Kullanıcıya da kalıcı bildirim kaydet (randevu talebi alındı)
            user_notification_ref = db.collection("user_notifications").document()
            user_notification_ref.set({
                "user_id": user_id,
                "title": "Randevu Talebiniz Alındı 🗓️",
                "body": f"{service_title} için {date_str} tarihinde saat {time_str}'de randevu talebiniz alındı. Onay bekleniyor.",
                "type": "appointment_status",
                "is_read": False,
                "created_at": firestore.SERVER_TIMESTAMP,
                "data": {
                    "appointment_id": ref.id,
                    "status": "pending",
                    "service_id": service_id,
                    "service_title": service_title,
                    "start": start_norm.isoformat(),
                },
            })
            logger.info("User notification saved for appointment request %s user=%s", ref.id, user_id)

        except Exception as e:
            logger.error("Background notification failed for appointment %s: %s", ref.id, e)

    threading.Thread(target=_background_notify, daemon=True).start()

    return appt_data


# === Kullanıcı: Kendi Randevularım ===========================================
@router.get("/", response_model=List[AppointmentOut])
def list_my_appointments(current_user: dict = Depends(get_current_user)):
    """
    List all appointments (past and pending) for the current user.
    """
    user_id = current_user['id']
    docs = db.collection("appointments").where("user_id", "==", user_id).stream()
    appts: List[dict] = []
    for doc in docs:
        d = doc.to_dict() or {}
        d['id'] = doc.id
        # normalize for safe clients/sorting
        d['start'] = _coerce_dt(d.get('start'))
        d['end'] = _coerce_dt(d.get('end'))
        appts.append(d)
    appts.sort(key=lambda x: x.get('start') or datetime.min)
    return appts


# === Admin Router =============================================================
admin_router = APIRouter(prefix="/appointments", dependencies=[Depends(get_current_admin)])


def _fetch_appointments_admin(status: Optional[str] = None) -> List[dict]:
    """Admin randevu listesini döndüren ortak helper."""
    query = db.collection("appointments")
    if status:
        query = query.where("status", "==", status)
    appt_docs = list(query.stream())
    if not appt_docs:
        return []

    # Batch fetch: user + service dokümanlarını tek seferde çek
    user_ids = {(d.to_dict() or {}).get("user_id") for d in appt_docs}
    service_ids = {(d.to_dict() or {}).get("service_id") for d in appt_docs}
    user_ids.discard(None)
    service_ids.discard(None)

    user_snaps = db.get_all([db.collection("users").document(uid) for uid in user_ids]) if user_ids else []
    svc_snaps = db.get_all([db.collection("services").document(sid) for sid in service_ids]) if service_ids else []

    user_map = {s.id: s.to_dict() for s in user_snaps if s.exists}
    svc_map = {s.id: s.to_dict() for s in svc_snaps if s.exists}

    results = []
    for doc in appt_docs:
        d = doc.to_dict() or {}
        uid = d.get("user_id")
        sid = d.get("service_id")

        user_data = None
        if uid:
            user_data = {
                "id": uid,
                "name": (user_map.get(uid) or {}).get("name"),
                "phone": (user_map.get(uid) or {}).get("phone"),
                "email": (user_map.get(uid) or {}).get("email"),
                "addresses": (user_map.get(uid) or {}).get("addresses"),
            }

        results.append({
            "id": doc.id,
            "start": _to_local_dt(d.get("start")),
            "end": _to_local_dt(d.get("end")),
            "status": d.get("status", "pending"),
            "notes": d.get("notes"),
            "user": user_data,
            "service": {
                "id": sid,
                "title": (svc_map.get(sid) or {}).get("title"),
                "price": (svc_map.get(sid) or {}).get("price"),
            },
        })

    results.sort(key=lambda x: x["start"] or datetime.min)
    return results


@admin_router.get("", response_model=List[AppointmentAdminOut])
def list_appointments_no_slash(status: Optional[str] = Query(None, pattern="^(pending|approved|completed|cancelled)$")):
    """Admin endpoint – lists all appointments. Optional **status** filter."""
    return _fetch_appointments_admin(status)


@admin_router.get("/", response_model=List[AppointmentAdminOut])
def list_appointments_with_slash(status: Optional[str] = Query(None, pattern="^(pending|approved|completed|cancelled)$")):
    """Admin endpoint – lists all appointments. Optional **status** filter."""
    return _fetch_appointments_admin(status)


@admin_router.post("/", response_model=AppointmentOut)
def create_appointment(
    service_id: str = Form(...),
    user_id: str = Form(None),
    start: datetime = Form(...),
    end: datetime = Form(None)
):
    """
    Admin paneli – elle randevu oluştur (form-data ile).
    """
    start_norm = _from_local_dt(start)
    if not start_norm:
        raise HTTPException(status_code=400, detail="Invalid start datetime format")
    end_norm = _from_local_dt(end) if end else (start_norm + timedelta(hours=1))

    overlapping = db.collection("appointments").where("service_id", "==", service_id) \
                    .where("status", "in", ["pending", "approved"]).stream()
    for appt in overlapping:
        data = appt.to_dict() or {}
        s = _coerce_dt(data.get('start'))
        e = _coerce_dt(data.get('end')) or (s + timedelta(hours=1) if s else None)
        if not s or not e:
            logger.debug("Skip overlap check due to missing times for doc %s", appt.id)
            continue
        if (start_norm < e) and (end_norm > s):
            raise HTTPException(status_code=400, detail="Overlapping appointment")

    ref = db.collection("appointments").document()
    appt_data = {
        "service_id": service_id,
        "user_id": user_id,
        "start": start_norm,
        "end": end_norm,
        "status": "approved"
    }
    ref.set(appt_data)
    appt_data["id"] = ref.id
    return appt_data


class BlockDayRequest(BaseModel):
    service_id: str
    date: str  # YYYY-MM-DD
    notes: Optional[str] = None


@admin_router.post("/block-day", response_model=Dict[str, Any])
def block_entire_day(req: BlockDayRequest):
    """
    Admin paneli – seçilen tarihteki tüm çalışma saatlerini (09:00-17:00) tek seferde bloklar.
    Zaten dolu olan saatler atlanır.
    JSON body kabul eder (FormData değil) — token refresh sonrası retry desteği için.
    """
    service_id = req.service_id
    date = req.date
    notes = req.notes

    try:
        year, month, day = map(int, date.split("-"))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD")

    # Çalışma saatleri: 09:00 - 17:00 (9 slot)
    working_hours = list(range(9, 18))

    # Mevcut randevuları çek (bu servis + bu tarih için)
    existing = db.collection("appointments") \
        .where("service_id", "==", service_id) \
        .where("status", "in", ["pending", "approved"]) \
        .stream()

    busy_hours = set()
    for appt in existing:
        data = appt.to_dict() or {}
        s = _coerce_dt(data.get("start"))
        if not s:
            continue
        # UTC'den Turkey saatine çevir (karşılaştırma için)
        local_s = _to_local_dt(s)
        if local_s and local_s.year == year and local_s.month == month and local_s.day == day:
            busy_hours.add(local_s.hour)

    blocked = 0
    skipped = 0
    batch = db.batch()

    for hour in working_hours:
        if hour in busy_hours:
            skipped += 1
            continue

        # Naive local time -> UTC via _from_local_dt
        start_local = datetime(year, month, day, hour, 0)
        start_utc = _from_local_dt(start_local)
        end_utc = start_utc + timedelta(hours=1)

        ref = db.collection("appointments").document()
        appt_data = {
            "service_id": service_id,
            "user_id": None,
            "start": start_utc,
            "end": end_utc,
            "status": "approved",
        }
        if notes:
            appt_data["notes"] = notes

        batch.set(ref, appt_data)
        blocked += 1

    if blocked > 0:
        batch.commit()

    message = f"{blocked} saat bloklandı"
    if skipped > 0:
        message += f", {skipped} saat zaten doluydu"

    return {"blocked": blocked, "skipped": skipped, "message": message}


def _notify_user_status_change(appointment_id: str, new_status: str, appt_data: dict):
    """
    Randevu durumu değiştiğinde kullanıcıya:
    1. user_notifications koleksiyonuna kalıcı bildirim kaydeder
    2. FCM push bildirim gönderir (device token ile)
    Background thread'de çalıştırılmak üzere tasarlanmıştır.
    """
    try:
        user_id = appt_data.get("user_id")
        if not user_id:
            return

        status_labels = {
            "approved": "onaylandı ✅",
            "cancelled": "iptal edildi ❌",
            "completed": "tamamlandı 🎉",
        }
        status_text = status_labels.get(new_status, f"güncellendi ({new_status})")

        start_dt = _to_local_dt(appt_data.get("start"))
        date_str = start_dt.strftime("%d/%m/%Y %H:%M") if start_dt else ""

        title = "Randevu Durumu Güncellendi"
        body = f"{date_str} tarihli randevunuz {status_text}."

        # 1) Firestore'a kalıcı bildirim kaydet (user_notifications koleksiyonu)
        try:
            notification_ref = db.collection("user_notifications").document()
            notification_ref.set({
                "user_id": user_id,
                "title": title,
                "body": body,
                "type": "appointment_status",
                "is_read": False,
                "created_at": firestore.SERVER_TIMESTAMP,
                "data": {
                    "appointment_id": appointment_id,
                    "status": new_status,
                    "service_id": appt_data.get("service_id"),
                    "start": start_dt.isoformat() if start_dt else None,
                },
            })
            logger.info("User notification saved to Firestore for appointment %s status=%s user=%s",
                        appointment_id, new_status, user_id)
        except Exception as e:
            logger.error("Failed to save user notification to Firestore: %s", e)

        # 2) FCM push bildirim gönder (device token ile)
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return
        user_data = user_doc.to_dict() or {}
        fcm_token = user_data.get("fcm_token")
        if not fcm_token:
            logger.info("No FCM token for user %s, skipping push notification", user_id)
            return

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={
                "click_action": "FLUTTER_NOTIFICATION_CLICK",
                "type": "appointment_status",
                "appointment_id": appointment_id,
            },
            token=fcm_token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default", priority="high", channel_id="high_importance_channel"
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1, content_available=True)
                )
            ),
        )
        messaging.send(message)
        logger.info("FCM push sent for appointment %s status=%s token=%s...", appointment_id, new_status, fcm_token[:20])
    except Exception as e:
        logger.error("Failed to send user status notification: %s", e)


@admin_router.put("/{appointment_id}")
def update_appointment_status_form(
    appointment_id: str,
    status: AppointmentStatus = Form(...)
):
    """
    Admin – Randevu durumunu güncelle (dropdown).
    """
    ref = db.collection("appointments").document(appointment_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Appointment not found")
    ref.update({"status": status})

    # Kullanıcıya bildirim arka planda
    appt_data = doc.to_dict() or {}
    threading.Thread(
        target=_notify_user_status_change,
        args=(appointment_id, status, appt_data),
        daemon=True,
    ).start()

    return {"detail": f"Appointment {appointment_id} updated to {status}"}


@admin_router.put("/{appointment_id}/status")
def update_appointment_status_json(
    appointment_id: str,
    status_data: dict
):
    """
    Admin – Randevu durumunu güncelle (JSON).
    """
    ref = db.collection("appointments").document(appointment_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Appointment not found")

    status = status_data.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status field is required")

    ref.update({"status": status})

    # Kullanıcıya bildirim arka planda
    appt_data = doc.to_dict() or {}
    threading.Thread(
        target=_notify_user_status_change,
        args=(appointment_id, status, appt_data),
        daemon=True,
    ).start()

    return {"detail": f"Appointment {appointment_id} updated to {status}"}


@admin_router.delete("/{appointment_id}")
def delete_appointment(appointment_id: str, status: AppointmentStatus = Form(...)):
    """
    Admin endpoint to fully delete an appointment (used for removing blocks or test entries).
    """
    appt_ref = db.collection("appointments").document(appointment_id)
    doc = appt_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Appointment not found")
    appt_ref.delete()
    return {"detail": "Appointment deleted"}


# === Admin: Servis Müsaitlik Yönetimi ========================================
@admin_router.get("/service-availability/{service_id}", response_model=ServiceAvailability)
def get_service_availability(service_id: str):
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or (service_doc.to_dict() or {}).get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    availability_doc = db.collection("service_availability").document(service_id).get()
    if availability_doc.exists:
        data = availability_doc.to_dict() or {}
        return ServiceAvailability(
            service_id=service_id,
            working_hours=data.get('working_hours', {}),
            break_times=data.get('break_times', []),
            is_available=data.get('is_available', True)
        )
    # varsayılan
    return ServiceAvailability(
        service_id=service_id,
        working_hours={
            'monday': ['09:00', '18:00'],
            'tuesday': ['09:00', '18:00'],
            'wednesday': ['09:00', '18:00'],
            'thursday': ['09:00', '18:00'],
            'friday': ['09:00', '18:00'],
            'saturday': ['10:00', '16:00'],
            'sunday': []
        },
        break_times=[{'start': '12:00', 'end': '13:00'}],
        is_available=True
    )


@admin_router.put("/service-availability/{service_id}")
def update_service_availability(service_id: str, availability: ServiceAvailability):
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or (service_doc.to_dict() or {}).get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    db.collection("service_availability").document(service_id).set({
        'working_hours': availability.working_hours,
        'break_times': availability.break_times,
        'is_available': availability.is_available,
        'updated_at': datetime.utcnow()
    })
    return {"detail": "Service availability updated successfully"}


# === Takvim/Busy Slotları =====================================================
@router.get("/calendar")
def get_all_busy_slots(
    service_id: Optional[str] = Query(None, description="İsteğe bağlı servis filtresi"),
    days: int = Query(30, ge=1, le=90, description="Bugünden itibaren kaç gün ileri bakılacağı")
):
    """
    Önümüzdeki `days` gün için dolu slotları döndürür.
    """
    now = datetime.utcnow()
    date_from = now
    date_to = now + timedelta(days=days)

    def fetch_by_status(st: str):
        q = db.collection("appointments").where("status", "==", st)
        if service_id:
            q = q.where("service_id", "==", service_id)
        return list(q.stream())

    docs = fetch_by_status("pending") + fetch_by_status("approved")

    busy = []
    for doc in docs:
        d = doc.to_dict() or {}
        # Filtreleme için UTC kullan
        s_utc = _coerce_dt(d.get("start"))
        e_utc = _coerce_dt(d.get("end")) or (s_utc + timedelta(hours=1) if s_utc else None)
        if not s_utc or not e_utc:
            continue
        if not (date_from <= s_utc <= date_to):
            continue
        # Görüntüleme için local time kullan
        s_local = _to_local_dt(d.get("start"))
        e_local = _to_local_dt(d.get("end")) or (s_local + timedelta(hours=1) if s_local else None)
        busy.append({
            "service_id": d.get("service_id"),
            "date": s_local.date().isoformat(),
            "start": s_local.strftime("%H:%M"),
            "end": e_local.strftime("%H:%M"),
            "status": d.get("status", "pending"),
            "appointment_id": doc.id,
        })

    busy.sort(key=lambda x: (x["date"], x["start"], x.get("service_id") or ""))
    return {"busy": busy}


# === Kullanıcı: Randevular + Servis Detayı ===================================
@router.get("/my-appointments", response_model=List[AppointmentWithDetails])
def get_my_appointments(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    docs = list(db.collection("appointments").where("user_id", "==", user_id).stream())
    if not docs:
        return []

    appointments: list[dict] = []
    service_ids: set[str] = set()

    for doc in docs:
        d = doc.to_dict() or {}
        s = _to_local_dt(d.get("start"))
        e = _to_local_dt(d.get("end"))
        svc_id = d.get("service_id")
        if svc_id:
            service_ids.add(svc_id)
        appointments.append({
            "id": doc.id,
            "service_id": svc_id,
            "user_id": d.get("user_id"),
            "start": s,
            "end": e,
            "status": d.get("status", "pending"),
            "notes": d.get("notes"),
        })

    service_map: dict[str, dict] = {}
    if service_ids:
        svc_refs = [db.collection("services").document(sid) for sid in service_ids]
        for snap in db.get_all(svc_refs):
            if snap.exists:
                service_map[snap.id] = snap.to_dict() or {}

    results: list[AppointmentWithDetails] = []
    for ap in appointments:
        svc = service_map.get(ap["service_id"], {})
        # Service bilgisi varsa ServiceBrief oluştur, yoksa None
        service_brief = None
        if svc:
            service_brief = ServiceBrief(
                id=ap["service_id"],
                title=svc.get("title"),
                price=svc.get("price"),
            )
        
        results.append(
            AppointmentWithDetails(
                id=ap["id"],
                service_id=ap["service_id"],
                user_id=ap["user_id"],
                start=ap["start"],
                end=ap["end"],
                status=ap["status"],
                notes=ap["notes"],
                service=service_brief,
            )
        )
    results.sort(key=lambda x: x.start or datetime.min)
    return results
