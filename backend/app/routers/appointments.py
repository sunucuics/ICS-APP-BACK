"""
# `app/routers/appointments.py` — Randevu Yönetimi Dokümantasyonu

Bu modül, kullanıcıların randevu talebi oluşturabilmesini, kendi randevularını listeleyebilmesini ve admin paneli üzerinden randevuların yönetilebilmesini sağlayan API uç noktalarını içerir.
Hem **kullanıcı** hem de **admin** işlemleri için ayrı router’lar tanımlanmıştır.

---

## Genel Yapı
- `router`: Kullanıcı tarafı işlemleri (`/appointments` prefix’i ile)
- `admin_router`: Admin tarafı işlemleri (`/appointments` prefix’i ile, `get_current_admin` bağımlılığıyla korunur)
- Firestore veri tabanı (`app.config.db`) kullanılır.
- Randevuların çakışmaması için tarih-saat aralıkları kontrol edilir.
- Servis bilgileri `services` koleksiyonundan, kullanıcı bilgileri `users` koleksiyonundan çekilir.

---

## Kullanıcı Tarafı Fonksiyonlar

### 1) `request_appointment(service_id: str, start: datetime, current_user: dict) -> AppointmentOut`
**Amaç:** Kullanıcıların form-data üzerinden randevu talebi oluşturması.

**Adımlar:**
1. `service_id` ve `start` bilgileri form-data olarak alınır.
2. `current_user` bilgisi `get_current_user` ile elde edilir.
3. Randevu **1 saatlik** olacak şekilde `end_time` hesaplanır.
4. İlgili servis Firestore’dan çekilir:
   - Servis yoksa veya silinmişse `404` döner.
   - Servis henüz aktif değilse (`is_upcoming=True`) `400` döner.
5. Aynı servis ve saat diliminde başka `pending` veya `approved` randevu varsa `400` döner.
6. Randevu Firestore’a `"pending"` statüsüyle kaydedilir.
7. Oluşan randevu bilgisi `id` eklenerek döndürülür.

---

### 2) `list_my_appointments(current_user: dict) -> List[AppointmentOut]`
**Amaç:** Giriş yapmış kullanıcının tüm randevularını listelemek.

**Adımlar:**
1. `current_user` ile `user_id` elde edilir.
2. Firestore’da `appointments` koleksiyonundan ilgili `user_id` eşleşmeleri çekilir.
3. Her belgeye `id` eklenir.
4. Başlangıç saatine göre sıralanır.
5. Liste döndürülür.

---

### 3) `get_all_busy_slots() -> dict`
**Amaç:** Önümüzdeki **30 gün** için tüm servislerdeki dolu randevu saatlerini döndürmek.

**Adımlar:**
1. Şimdiki tarih (`now`) ve 30 gün sonrası (`date_to`) hesaplanır.
2. Firestore’da `status` alanı `"pending"` veya `"approved"` olan, başlangıcı bu tarih aralığında olan randevular çekilir.
3. `service_id`, `start`, `end`, `status` alanları ile liste oluşturulur.
4. `{ "busy": [...] }` formatında döndürülür.

---

## Admin Tarafı Fonksiyonlar

### 4) `list_appointments(status: Optional[str]) -> List[AppointmentAdminOut]`
**Amaç:** Tüm randevuları listelemek, isteğe bağlı olarak `status` filtresi uygulamak.

**Adımlar:**
1. Firestore’dan tüm randevular çekilir; `status` parametresi verilmişse filtre uygulanır.
2. Tüm `user_id` ve `service_id` değerleri toplanır.
3. Firestore `get_all` ile kullanıcılar (`users`) ve servisler (`services`) tek sorguda çekilir.
4. Kullanıcı ve servis bilgileri haritalanarak (`user_map`, `svc_map`) her randevuya eklenir.
5. Başlangıç tarihine göre sıralanır ve döndürülür.

---

### 5) `create_appointment(service_id: str, user_id: str, start: datetime, end: datetime) -> AppointmentOut`
**Amaç:** Admin panelinden manuel randevu oluşturmak.

**Adımlar:**
1. `end` boşsa `start + 1 saat` olarak belirlenir.
2. Çakışma kontrolü yapılır; çakışma varsa `400` döner.
3. Randevu `"approved"` statüsüyle Firestore’a eklenir.
4. `id` eklenerek döndürülür.

---

### 6) `update_appointment_status_form(appointment_id: str, status: AppointmentStatus)`
**Amaç:** Mevcut randevunun durumunu güncellemek.

**Adımlar:**
1. `appointment_id` ile belge çekilir, yoksa `404` döner.
2. `status` alanı güncellenir.
3. Güncelleme bilgisi döndürülür.

---

### 7) `delete_appointment(appointment_id: str, status: AppointmentStatus)`
**Amaç:** Randevuyu tamamen silmek (blok veya test kaydı temizlemek için).

**Adımlar:**
1. `appointment_id` ile belge çekilir, yoksa `404` döner.
2. Belge Firestore’dan silinir.
3. Silindi bilgisi döndürülür.

---

## Kullanım Notları
- **Tarih formatı:** `start` ve `end` alanları ISO 8601 formatında gönderilmelidir.
- **Yetkilendirme:** Kullanıcı fonksiyonları `get_current_user`, admin fonksiyonları `get_current_admin` bağımlılığı ile korunur.
- **Çakışma kontrolü:** Randevu başlangıcı ve bitişi mevcut randevularla kesişmemelidir.

"""
from fastapi import APIRouter, Depends, HTTPException , Form , Query
from typing import List , Literal , Optional
from datetime import timedelta, datetime, date, time
import calendar
from app.core.security import get_current_user, get_current_admin
from app.config import db
from app.schemas.appointment import (
    AppointmentRequest, AppointmentAdminCreate, AppointmentUpdate, AppointmentOut, 
    AppointmentStatus, AppointmentAdminOut, UserBrief, ServiceBrief,
    ServiceAvailability, MonthlyAvailability, DayAvailability, TimeSlot,
    AppointmentBookingRequest, AppointmentWithDetails
)

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


# Admin - Servis müsaitlik yönetimi

@admin_router.get("/service-availability/{service_id}", response_model=ServiceAvailability)
def get_service_availability(service_id: str):
    """
    Servis müsaitlik ayarlarını getirir.
    """
    # Servis kontrolü
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or service_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Müsaitlik ayarlarını al
    availability_doc = db.collection("service_availability").document(service_id).get()
    if availability_doc.exists:
        data = availability_doc.to_dict()
        return ServiceAvailability(
            service_id=service_id,
            working_hours=data.get('working_hours', {}),
            break_times=data.get('break_times', []),
            is_available=data.get('is_available', True)
        )
    else:
        # Varsayılan ayarlar
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
def update_service_availability(
    service_id: str,
    availability: ServiceAvailability
):
    """
    Servis müsaitlik ayarlarını günceller.
    """
    # Servis kontrolü
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or service_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Müsaitlik ayarlarını kaydet
    db.collection("service_availability").document(service_id).set({
        'working_hours': availability.working_hours,
        'break_times': availability.break_times,
        'is_available': availability.is_available,
        'updated_at': datetime.utcnow()
    })
    
    return {"detail": "Service availability updated successfully"}


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


# Yeni endpoint'ler - Aylık takvim sistemi için

@router.get("/availability/{service_id}/{year}/{month}", response_model=MonthlyAvailability)
def get_monthly_availability(
    service_id: str,
    year: int,
    month: int
):
    """
    Belirli bir hizmet için aylık müsaitlik bilgilerini döndürür.
    """
    # Geçerli tarih kontrolü
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Invalid month")
    
    if year < datetime.now().year:
        raise HTTPException(status_code=400, detail="Cannot get availability for past years")
    
    # Servis kontrolü
    service_doc = db.collection("services").document(service_id).get()
    if not service_doc.exists or service_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Servis müsaitlik ayarlarını al
    availability_doc = db.collection("service_availability").document(service_id).get()
    availability_data = availability_doc.to_dict() if availability_doc.exists else {}
    
    # Varsayılan çalışma saatleri (Pazartesi-Cuma 09:00-18:00)
    default_working_hours = {
        'monday': ['09:00', '18:00'],
        'tuesday': ['09:00', '18:00'],
        'wednesday': ['09:00', '18:00'],
        'thursday': ['09:00', '18:00'],
        'friday': ['09:00', '18:00'],
        'saturday': ['10:00', '16:00'],
        'sunday': []
    }
    
    working_hours = availability_data.get('working_hours', default_working_hours)
    break_times = availability_data.get('break_times', [])
    
    # Ayın günlerini oluştur
    days_in_month = calendar.monthrange(year, month)[1]
    days = []
    
    # Mevcut randevuları al
    month_start = datetime(year, month, 1)
    month_end = datetime(year, month, days_in_month, 23, 59, 59)
    
    existing_appointments = db.collection("appointments").where("service_id", "==", service_id) \
        .where("status", "in", ["pending", "approved"]) \
        .where("start", ">=", month_start) \
        .where("start", "<=", month_end).stream()
    
    # Randevuları tarihe göre grupla
    appointments_by_date = {}
    for appt in existing_appointments:
        data = appt.to_dict()
        start_time = data.get('start')
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)
        appt_date = start_time.date()
        if appt_date not in appointments_by_date:
            appointments_by_date[appt_date] = []
        appointments_by_date[appt_date].append({
            'id': appt.id,
            'start': start_time,
            'end': data.get('end')
        })
    
    # Her gün için müsaitlik bilgilerini oluştur
    for day in range(1, days_in_month + 1):
        current_date = date(year, month, day)
        day_name = current_date.strftime('%A').lower()
        
        # Çalışma günü mü kontrol et
        is_working_day = day_name in working_hours and working_hours[day_name]
        
        time_slots = []
        if is_working_day:
            # Çalışma saatleri
            work_start, work_end = working_hours[day_name]
            work_start_time = datetime.strptime(work_start, '%H:%M').time()
            work_end_time = datetime.strptime(work_end, '%H:%M').time()
            
            # 1 saatlik dilimler oluştur
            current_time = work_start_time
            while current_time < work_end_time:
                slot_start = datetime.combine(current_date, current_time)
                slot_end = slot_start + timedelta(hours=1)
                
                # Mola saatleri kontrolü
                is_break_time = False
                for break_time in break_times:
                    break_start = datetime.strptime(break_time['start'], '%H:%M').time()
                    break_end = datetime.strptime(break_time['end'], '%H:%M').time()
                    if break_start <= current_time < break_end:
                        is_break_time = True
                        break
                
                # Randevu kontrolü
                appointment_id = None
                is_available = not is_break_time
                
                if current_date in appointments_by_date:
                    for appt in appointments_by_date[current_date]:
                        appt_start = appt['start']
                        appt_end = appt['end']
                        if isinstance(appt_end, str):
                            appt_end = datetime.fromisoformat(appt_end)
                        
                        if slot_start < appt_end and slot_end > appt_start:
                            is_available = False
                            appointment_id = appt['id']
                            break
                
                time_slots.append(TimeSlot(
                    start_time=current_time.strftime('%H:%M'),
                    end_time=(current_time.replace(hour=current_time.hour + 1)).strftime('%H:%M'),
                    is_available=is_available,
                    appointment_id=appointment_id
                ))
                
                # Sonraki saat
                current_time = (datetime.combine(current_date, current_time) + timedelta(hours=1)).time()
        
        days.append(DayAvailability(
            date=current_date,
            is_working_day=is_working_day,
            time_slots=time_slots
        ))
    
    return MonthlyAvailability(
        service_id=service_id,
        year=year,
        month=month,
        days=days
    )


@router.post("/book", response_model=AppointmentOut)
def book_appointment(
    request: AppointmentBookingRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Yeni randevu talebi oluşturur.
    """
    user_id = current_user['id']
    
    # Servis kontrolü
    service_doc = db.collection("services").document(request.service_id).get()
    if not service_doc.exists or service_doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Service not found")
    
    if service_doc.to_dict().get('is_upcoming'):
        raise HTTPException(status_code=400, detail="Service not yet available for booking")
    
    # Tarih ve saat kontrolü
    if request.date < date.today():
        raise HTTPException(status_code=400, detail="Cannot book appointments in the past")
    
    # Randevu başlangıç ve bitiş zamanlarını oluştur
    start_datetime = datetime.combine(request.date, datetime.strptime(request.start_time, '%H:%M').time())
    end_datetime = start_datetime + timedelta(hours=1)
    
    # Çakışma kontrolü
    overlapping = db.collection("appointments").where("service_id", "==", request.service_id) \
        .where("status", "in", ["pending", "approved"]).stream()
    
    for appt in overlapping:
        data = appt.to_dict()
        s, e = data.get('start'), data.get('end')
        if isinstance(s, str): s = datetime.fromisoformat(s)
        if isinstance(e, str): e = datetime.fromisoformat(e)
        if start_datetime < e and end_datetime > s:
            raise HTTPException(status_code=400, detail="Time slot is not available")
    
    # Randevu oluştur
    ref = db.collection("appointments").document()
    appt_data = {
        "service_id": request.service_id,
        "user_id": user_id,
        "start": start_datetime,
        "end": end_datetime,
        "status": "pending",
        "notes": request.notes
    }
    ref.set(appt_data)
    appt_data["id"] = ref.id
    return appt_data


@router.get("/my-appointments", response_model=List[AppointmentWithDetails])
def get_my_appointments(current_user: dict = Depends(get_current_user)):
    """
    Kullanıcının randevularını detaylı bilgilerle birlikte döndürür.
    """
    user_id = current_user['id']
    
    # Randevuları al
    docs = db.collection("appointments").where("user_id", "==", user_id).stream()
    appointments = []
    
    for doc in docs:
        data = doc.to_dict()
        appointments.append({
            'id': doc.id,
            'service_id': data.get('service_id'),
            'user_id': data.get('user_id'),
            'start': data.get('start'),
            'end': data.get('end'),
            'status': data.get('status', 'pending'),
            'notes': data.get('notes')
        })
    
    if not appointments:
        return []
    
    # Servis bilgilerini al
    service_ids = list(set([apt['service_id'] for apt in appointments]))
    service_docs = db.get_all([db.collection("services").document(sid) for sid in service_ids])
    service_map = {doc.id: doc.to_dict() for doc in service_docs if doc.exists}
    
    # Sonuçları oluştur
    results = []
    for apt in appointments:
        service_data = service_map.get(apt['service_id'], {})
        
        results.append(AppointmentWithDetails(
            id=apt['id'],
            service_id=apt['service_id'],
            user_id=apt['user_id'],
            start=apt['start'],
            end=apt['end'],
            status=apt['status'],
            notes=apt['notes'],
            service=ServiceBrief(
                id=apt['service_id'],
                title=service_data.get('title'),
                price=service_data.get('price')
            )
        ))
    
    # Tarihe göre sırala
    results.sort(key=lambda x: x.start or datetime.min)
    return results