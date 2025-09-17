"""
# `app/routers/appointments.py` — Randevu Yönetimi Dokümantasyonu

Bu modül, kullanıcıların randevu talebi oluşturabilmesini, kendi randevularını listeleyebilmesini ve admin paneli üzerinden randevuların yönetilebilmesini sağlayan API uç noktalarını içerir.
Hem **kullanıcı** hem de **admin** işlemleri için ayrı router’lar tanımlanmıştır.
"""
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from typing import List, Optional, Any
from datetime import timedelta, datetime
import logging

from app.core.security import get_current_user, get_current_admin
from app.config import db
from app.schemas.appointment import (
    AppointmentOut, AppointmentStatus, AppointmentAdminOut,
    ServiceAvailability, ServiceBrief, AppointmentWithDetails
)

logger = logging.getLogger("ics.appointments")

router = APIRouter(prefix="/appointments", tags=["Appointments"])

# --- Güvenli tarih dönüştürücü ------------------------------------------------
def _coerce_dt(v: Any) -> Optional[datetime]:
    """
    Firestore Timestamp | str (ISO/ISOZ) | datetime(aware/naive) | None -> naive datetime (server local time)
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        # aware ise tz'yi at, lokale çevirip naive yap
        return v.astimezone().replace(tzinfo=None) if v.tzinfo else v
    # Firestore Timestamp nesneleri to_datetime() destekler
    if hasattr(v, "to_datetime"):
        dt = v.to_datetime()
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    if isinstance(v, str):
        s = v.strip()
        try:
            # 'Z' (UTC) son ekini destekle
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
        except Exception as exc:
            logger.debug("ISO parse failed for %r: %s", v, exc)
            return None
    return None


# === Kullanıcı: Randevu Talebi ===============================================
@router.post("/", response_model=AppointmentOut)
def request_appointment(
    service_id: str = Form(...),
    start: datetime = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Randevu talep formu (form-data).
    """
    # Giriş tarihini normalize et
    start_norm = _coerce_dt(start)
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
        "status": "pending"
    }
    ref.set(appt_data)
    appt_data["id"] = ref.id
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


@admin_router.get("/", response_model=List[AppointmentAdminOut])
def list_appointments(status: Optional[str] = Query(None, pattern="^(pending|approved|cancelled)$")):
    """
    Admin endpoint – lists all appointments.
    Optional **status** filter.
    """
    query = db.collection("appointments")
    if status:
        query = query.where("status", "==", status)
    appt_docs = list(query.stream())
    if not appt_docs:
        return []

    # user_id / service_id kümeleri
    user_ids = { (d.to_dict() or {}).get("user_id") for d in appt_docs }
    service_ids = { (d.to_dict() or {}).get("service_id") for d in appt_docs }
    user_ids.discard(None)
    service_ids.discard(None)

    user_snaps = db.get_all([db.collection("users").document(uid) for uid in user_ids]) if user_ids else []
    svc_snaps  = db.get_all([db.collection("services").document(sid) for sid in service_ids]) if service_ids else []

    user_map = {s.id: s.to_dict() for s in user_snaps if s.exists}
    svc_map  = {s.id: s.to_dict() for s in svc_snaps  if s.exists}

    results = []
    for doc in appt_docs:
        d = doc.to_dict() or {}
        uid = d.get("user_id")
        sid = d.get("service_id")

        results.append({
            "id":     doc.id,
            "start":  _coerce_dt(d.get("start")),
            "end":    _coerce_dt(d.get("end")),
            "status": d.get("status", "pending"),
            "user": {
                "id":    uid,
                "name":  (user_map.get(uid) or {}).get("name"),
                "phone": (user_map.get(uid) or {}).get("phone"),
                "email": (user_map.get(uid) or {}).get("email"),
                "addresses": (user_map.get(uid) or {}).get("addresses"),
            },
            "service": {
                "id":    sid,
                "title": (svc_map.get(sid) or {}).get("title"),
                "price": (svc_map.get(sid) or {}).get("price"),
            }
        })

    results.sort(key=lambda x: x["start"] or datetime.min)
    return results


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
    start_norm = _coerce_dt(start)
    if not start_norm:
        raise HTTPException(status_code=400, detail="Invalid start datetime format")
    end_norm = _coerce_dt(end) if end else (start_norm + timedelta(hours=1))

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
        s = _coerce_dt(d.get("start"))
        e = _coerce_dt(d.get("end")) or (s + timedelta(hours=1) if s else None)
        if not s or not e:
            continue
        if not (date_from <= s <= date_to):
            continue
        busy.append({
            "service_id": d.get("service_id"),
            "date": s.date().isoformat(),
            "start": s.strftime("%H:%M"),
            "end": e.strftime("%H:%M"),
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
        s = _coerce_dt(d.get("start"))
        e = _coerce_dt(d.get("end"))
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
        results.append(
            AppointmentWithDetails(
                id=ap["id"],
                service_id=ap["service_id"],
                user_id=ap["user_id"],
                start=ap["start"],
                end=ap["end"],
                status=ap["status"],
                notes=ap["notes"],
                service=ServiceBrief(
                    id=ap["service_id"],
                    title=svc.get("title"),
                    price=svc.get("price"),
                ),
            )
        )
    results.sort(key=lambda x: x.start or datetime.min)
    return results
