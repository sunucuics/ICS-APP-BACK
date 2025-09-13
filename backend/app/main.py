"""
# `app/main.py` — Ana Uygulama Dokümantasyonu

## Genel Bilgi
Bu dosya, FastAPI uygulamasının başlangıç noktasıdır.
Router’lar eklenir, CORS ayarları yapılır, admin ve public endpoint’ler bağlanır, arka planda çalışan scheduler yapılandırılır.

---

## Uygulama Başlatma
- `FastAPI` örneği başlatılır (`title`, `description`, `version`).
- CORS ayarları `settings.allowed_origins` üzerinden yapılır (liste veya `*`).

---

## Router Dahil Etme
**Public Router’lar:**
- `/auth`
- `/users`
- `/categories`
- `/products`
- `/services`
- `/carts`
- `/orders`
- `/appointments`
- `/comments`

**Admin Router’lar (prefix `/admin`):**
- `/categories`
- `/products`
- `/services`
- `/orders`
- `/appointments`
- `/discounts`
- `/comments`

Tüm admin router’lar ilgili modüllerde `get_current_admin` ile korunur.

---

## Arka Plan Scheduler
- **Kütüphane:** APScheduler (`BackgroundScheduler`)
- **İş:** `update_tracking_statuses` (kargo takip durumlarını günceller)
- **Periyot:** 30 dakikada bir (sadece `settings.tracking_api_key` varsa çalışır)

**Olaylar:**
- `startup`: Scheduler başlatılır.
- `shutdown`: Scheduler kapatılır.

---

"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth, users, categories, products, services, carts, orders, appointments, discounts, comments , auth_delete , featured
from app.routers import categories as categories_router
from app.routers import products as products_router
from app.routers import services as services_router
from app.routers import orders as orders_router
from app.routers import appointments as appointments_router
from app.routers import comments as comments_router
from firebase_admin import firestore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.orders_sync import sync_open_orders_once  # job fonksiyonun

# Tek bir scheduler instance'ı oluştur
scheduler = AsyncIOScheduler()
scheduler.add_job(sync_open_orders_once, "interval", minutes=10, id="orders-sync", replace_existing=True)

# Initialize FastAPI app
app = FastAPI(
    title="E-Commerce & Service Booking API",
    description="Backend API for an e-commerce and appointment booking application.",
    version="1.0.0",
    redirect_slashes=False
)

# Configure CORS (allow front-end domain or all origins as specified)
allow_origins = [origin.strip() for origin in settings.allowed_origins.split(',')] if settings.allowed_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include public routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(categories_router.router)
app.include_router(products_router.router)
app.include_router(services_router.router)
app.include_router(carts.router)
app.include_router(orders_router.router)
app.include_router(appointments_router.router)
app.include_router(comments_router.router)
app.include_router(auth_delete.router)

# Include admin routers (with prefix /admin)
app.include_router(categories_router.admin_router, prefix="/admin")
app.include_router(products_router.admin_router, prefix="/admin")
app.include_router(services_router.admin_router, prefix="/admin")
app.include_router(orders_router.admin_router, prefix="/admin")
app.include_router(appointments_router.admin_router, prefix="/admin")
app.include_router(discounts.router, prefix="/admin")  # all routes in discounts are admin-protected via dependencies
app.include_router(comments_router.admin_router, prefix="/admin")
app.include_router(featured.admin_router, prefix="/admin")


@app.on_event("startup")
async def _startup_scheduler():
    if not scheduler.running:
        scheduler.start()
    # Job'u güvenle ekle (varsa üstüne yaz)
    scheduler.add_job(
        sync_open_orders_once,
        "interval",
        minutes=2,
        id="orders-sync",
        replace_existing=True,
    )

@app.on_event("shutdown")
async def _shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

# Run the app directly with uvicorn (for development)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
