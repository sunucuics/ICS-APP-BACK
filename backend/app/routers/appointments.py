"""
app/routers/appointments.py - Routes for appointment booking (user) and management (admin).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from datetime import timedelta, datetime
from app.core.security import get_current_user, get_current_admin
from app.config import db
from app.schemas.appointment import AppointmentRequest, AppointmentAdminCreate, AppointmentUpdate, AppointmentOut

router = APIRouter(prefix="/appointments", tags=["Appointments"])

@router.post("/", response_model=AppointmentOut)
def request_appointment(req: AppointmentRequest, current_user: dict = Depends(get_current_user)):
    """
    User endpoint to request a new appointment for a service.
    The appointment will be created with status 'pending' and needs admin approval.
    """
    user_id = current_user['id']
    service_id = req.service_id
    start_time = req.start
    # Default duration (e.g., 1 hour) if end time not specified by user
    end_time = start_time + timedelta(hours=1)
    # Check that service exists and is not deleted or upcoming
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or service_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    if service_doc.to_dict().get('is_upcoming'):
        raise HTTPException(status_code=400, detail="Service not yet available for booking")
    # Check for overlapping appointments for this service at the requested time
    appt_ref = db.collection("appointments")
    overlapping = appt_ref.where("service_id", "==", service_id) \
                          .where("status", "in", ["pending", "approved"]) \
                          .stream()
    for appt in overlapping:
        appt_data = appt.to_dict()
        existing_start = appt_data.get('start')
        existing_end = appt_data.get('end')
        if existing_start and existing_end:
            # Convert to datetime if needed
            if isinstance(existing_start, str):
                existing_start = datetime.fromisoformat(existing_start)
            if isinstance(existing_end, str):
                existing_end = datetime.fromisoformat(existing_end)
            # Check overlap: start < existing_end and end > existing_start
            if start_time < existing_end and end_time > existing_start:
                raise HTTPException(status_code=400, detail="Selected time slot is not available")
    # Create pending appointment
    appt_ref = db.collection("appointments").document()
    appt_data = {
        "service_id": service_id,
        "user_id": user_id,
        "start": start_time,
        "end": end_time,
        "status": "pending"
    }
    appt_ref.set(appt_data)
    appt_data['id'] = appt_ref.id
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

@admin_router.get("/", response_model=List[AppointmentOut])
def list_appointments(status: str = None):
    """
    Admin endpoint to list appointments. Can filter by status (pending/approved/cancelled).
    """
    query = db.collection("appointments")
    if status:
        query = query.where("status", "==", status)
    docs = query.stream()
    appts = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        appts.append(data)
    appts.sort(key=lambda x: x.get('start') or datetime.min)
    return appts

@admin_router.post("/", response_model=AppointmentOut)
def create_appointment(admin_req: AppointmentAdminCreate):
    """
    Admin endpoint to manually create an appointment (for blocking or scheduling on behalf of user).
    """
    service_id = admin_req.service_id
    user_id = admin_req.user_id
    start_time = admin_req.start
    end_time = admin_req.end or (admin_req.start + timedelta(hours=1))
    # Overlap check similar to above
    overlapping = db.collection("appointments").where("service_id", "==", service_id) \
                          .where("status", "in", ["pending", "approved"]).stream()
    for appt in overlapping:
        appt_data = appt.to_dict()
        existing_start = appt_data.get('start'); existing_end = appt_data.get('end')
        if existing_start and existing_end:
            if isinstance(existing_start, str):
                existing_start = datetime.fromisoformat(existing_start)
            if isinstance(existing_end, str):
                existing_end = datetime.fromisoformat(existing_end)
            if start_time < existing_end and end_time > existing_start:
                raise HTTPException(status_code=400, detail="Time slot overlaps with an existing appointment")
    appt_ref = db.collection("appointments").document()
    appt_data = {
        "service_id": service_id,
        "user_id": user_id,  # can be None
        "start": start_time,
        "end": end_time,
        "status": "approved"  # since admin is creating, assume it's an immediate block
    }
    appt_ref.set(appt_data)
    appt_data['id'] = appt_ref.id
    return appt_data

@admin_router.put("/{appointment_id}")
def update_appointment_status(appointment_id: str, update: AppointmentUpdate):
    """
    Admin endpoint to approve or cancel an appointment request.
    """
    appt_ref = db.collection("appointments").document(appointment_id)
    doc = appt_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Appointment not found")
    new_status = update.status
    appt_ref.update({"status": new_status})
    return {"detail": f"Appointment {appointment_id} status updated to {new_status}"}

@admin_router.delete("/{appointment_id}")
def delete_appointment(appointment_id: str):
    """
    Admin endpoint to fully delete an appointment (used for removing blocks or test entries).
    """
    appt_ref = db.collection("appointments").document(appointment_id)
    doc = appt_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Appointment not found")
    appt_ref.delete()
    return {"detail": "Appointment deleted"}


@router.get("/calendar/{service_id}")
def get_service_calendar(
    service_id: str,
    date_from: datetime,
    date_to: datetime
):
    """
    Dolu randevu aralıklarını döndürür.
    - date_from / date_to: ISO8601 (2025-08-05T00:00:00Z) veya ‘YYYY-MM-DD’.
    - Yanıt: [{start:<ISO>, end:<ISO>, status:'pending'|'approved'}]
    """
    # Firestore’da ilgili service_id ve zaman aralığında çakışan pending/approved kayıtları çek
    q = (db.collection("appointments")
            .where("service_id", "==", service_id)
            .where("status", "in", ["pending", "approved"])
            .where("start", ">=", date_from)
            .where("start", "<=", date_to))
    slots = []
    for doc in q.stream():
        appt = doc.to_dict()
        slots.append({
            "start": appt["start"],
            "end": appt["end"],
            "status": appt["status"]
        })
    return {"service_id": service_id, "busy": slots}
