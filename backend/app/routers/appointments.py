"""
app/routers/appointments.py - Routes for appointment booking (user) and management (admin).
"""
from fastapi import APIRouter, Depends, HTTPException , Form , Query
from typing import List , Literal , Optional
from datetime import timedelta, datetime
from app.core.security import get_current_user, get_current_admin
from app.config import db
from app.schemas.appointment import AppointmentRequest, AppointmentAdminCreate, AppointmentUpdate, AppointmentOut , AppointmentStatus , AppointmentAdminOut, UserBrief, ServiceBrief

router = APIRouter(prefix="/appointments", tags=["Appointments"])

@router.post("/", response_model=AppointmentOut)
def request_appointment(
    service_id: str = Form(...),
    start: datetime = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Randevu talep formu (form-data). Kullanıcıdan kutu kutu bilgi alır.
    """
    user_id = current_user['id']
    end_time = start + timedelta(hours=1)

    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or service_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    if service_doc.to_dict().get('is_upcoming'):
        raise HTTPException(status_code=400, detail="Service not yet available for booking")

    overlapping = db.collection("appointments").where("service_id", "==", service_id) \
                        .where("status", "in", ["pending", "approved"]).stream()
    for appt in overlapping:
        data = appt.to_dict()
        s, e = data.get('start'), data.get('end')
        if isinstance(s, str): s = datetime.fromisoformat(s)
        if isinstance(e, str): e = datetime.fromisoformat(e)
        if start < e and end_time > s:
            raise HTTPException(status_code=400, detail="Time slot is not available")

    ref = db.collection("appointments").document()
    appt_data = {
        "service_id": service_id,
        "user_id": user_id,
        "start": start,
        "end": end_time,
        "status": "pending"
    }
    ref.set(appt_data)
    appt_data["id"] = ref.id
    return appt_data

@router.get("/", response_model=List[AppointmentOut])
def list_my_appointments(current_user: dict = Depends(get_current_user)):
    """
    List all appointments (past and pending) for the current user.
    """
    user_id = current_user['id']
    docs = db.collection("appointments").where("user_id", "==", user_id).stream()
    appts = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        appts.append(data)
    appts.sort(key=lambda x: x.get('start') or datetime.min)
    return appts

# Admin sub-router
admin_router = APIRouter(prefix="/appointments", dependencies=[Depends(get_current_admin)])

@admin_router.get("/", response_model=List[AppointmentAdminOut])
def list_appointments(status: Optional[str] = Query(None, regex="^(pending|approved|cancelled)$")):
    """
    Admin endpoint – lists all appointments.
    Optional **status** filter: *pending*, *approved*, *cancelled*.
    Each item includes user (name / phone / email / addresses) and
    service details (title / price).
    """

    # 1‒ Randevuları çek
    query = db.collection("appointments")
    if status:
        query = query.where("status", "==", status)
    appt_docs = list(query.stream())

    if not appt_docs:
        return []

    # 2‒ Gerekli user_id ve service_id kümelerini topla
    user_ids    = {d.get("user_id")    for d in map(lambda x: x.to_dict(), appt_docs)}
    service_ids = {d.get("service_id") for d in map(lambda x: x.to_dict(), appt_docs)}

    # 3‒ Toplu get – tek seferde çek (Firestore get_all)
    user_snaps = db.get_all([db.collection("users").document(uid) for uid in user_ids])
    svc_snaps  = db.get_all([db.collection("services").document(sid) for sid in service_ids])

    user_map = {s.id: s.to_dict() for s in user_snaps if s.exists}
    svc_map  = {s.id: s.to_dict() for s in svc_snaps  if s.exists}

    # 4‒ Sonuç listesi inşa et
    results = []
    for doc in appt_docs:
        d = doc.to_dict()
        uid = d.get("user_id")
        sid = d.get("service_id")

        user_data = user_map.get(uid, {})
        svc_data  = svc_map.get(sid,  {})

        results.append({
            "id":     doc.id,
            "start":  d.get("start"),
            "end":    d.get("end"),
            "status": d.get("status", "pending"),

            "user": {
                "id":    uid,
                "name":  user_data.get("name"),
                "phone": user_data.get("phone"),
                "email": user_data.get("email"),
                # İstersen sadece ilk adresi göster → user_data.get("addresses", [None])[0]
                "addresses": user_data.get("addresses"),
            },

            "service": {
                "id":    sid,
                "title": svc_data.get("title"),
                "price": svc_data.get("price"),
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
    end = end or (start + timedelta(hours=1))

    overlapping = db.collection("appointments").where("service_id", "==", service_id) \
                        .where("status", "in", ["pending", "approved"]).stream()
    for appt in overlapping:
        data = appt.to_dict()
        s, e = data.get('start'), data.get('end')
        if isinstance(s, str): s = datetime.fromisoformat(s)
        if isinstance(e, str): e = datetime.fromisoformat(e)
        if start < e and end > s:
            raise HTTPException(status_code=400, detail="Overlapping appointment")

    ref = db.collection("appointments").document()
    appt_data = {
        "service_id": service_id,
        "user_id": user_id,
        "start": start,
        "end": end,
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
def delete_appointment(appointment_id: str,
                       status: AppointmentStatus = Form(...)):
    """
    Admin endpoint to fully delete an appointment (used for removing blocks or test entries).
    """
    appt_ref = db.collection("appointments").document(appointment_id)
    doc = appt_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Appointment not found")
    appt_ref.delete()
    return {"detail": "Appointment deleted"}


@router.get("/calendar")
def get_all_busy_slots():
    """
    Önümüzdeki 30 gün için TÜM servislerdeki dolu randevu saatlerini döndürür.
    Hiçbir parametre almaz.
    """
    now = datetime.utcnow()
    date_from = now
    date_to = now + timedelta(days=30)

    q = (db.collection("appointments")
           .where("status", "in", ["pending", "approved"])
           .where("start", ">=", date_from)
           .where("start", "<=", date_to))

    busy = []
    for doc in q.stream():
        data = doc.to_dict()
        busy.append({
            "service_id": data.get("service_id"),
            "start": data.get("start"),
            "end": data.get("end"),
            "status": data.get("status")
        })

    return {"busy": busy}