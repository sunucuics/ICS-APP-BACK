# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import settings
from backend.app.routers import (
    auth, users, categories, products,
    services, carts, appointments,
    discounts, comments, auth_delete, featured,
    admin_dashboard, analytics, notifications,
    settings as settings_router
)
from backend.app.routers import categories as categories_router
from backend.app.routers import products as products_router
from backend.app.routers import services as services_router
from backend.app.routers import shipping_manual as orders_router
from backend.app.routers import appointments as appointments_router
from backend.app.routers import comments as comments_router
from backend.app.routers import users as users_router

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Tek bir scheduler instance'ı oluştur
scheduler = AsyncIOScheduler()

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

# YENİ: public sipariş/kargo uçları shipping_manual içinden gelir
app.include_router(orders_router.router)

app.include_router(appointments_router.router)
app.include_router(comments_router.router)
app.include_router(auth_delete.router)

# Include admin routers (with prefix /admin)
app.include_router(admin_dashboard.router, prefix="/admin")
app.include_router(categories_router.admin_router, prefix="/admin")
app.include_router(products_router.admin_router, prefix="/admin")
app.include_router(services_router.admin_router, prefix="/admin")

# YENİ: admin sipariş/kargo uçları da shipping_manual içinden gelir
app.include_router(orders_router.admin_router, prefix="/admin")

app.include_router(appointments_router.admin_router, prefix="/admin")
app.include_router(discounts.router, prefix="/admin")  # all routes in discounts are admin-protected via dependencies
app.include_router(comments_router.admin_router, prefix="/admin")
app.include_router(users_router.admin_router, prefix="/admin")
app.include_router(featured.admin_router, prefix="/admin")
app.include_router(analytics.router, prefix="/admin")
app.include_router(notifications.router, prefix="/admin")
app.include_router(settings_router.router, prefix="/admin")


@app.on_event("startup")
async def _startup_scheduler():
    if not scheduler.running:
        scheduler.start()
    # Job'u güvenle ekle (varsa üstüne yaz)


@app.on_event("shutdown")
async def _shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    # hafif bir liveness check: app ayakta mı, scheduler durumu nedir?
    import firebase_admin
    return {
        "status": "ok",
        "scheduler": "running" if scheduler.running else "stopped",
        "firebase": "initialized" if firebase_admin._apps else "not_initialized"
    }


# (opsiyonel) readiness: dış servislere bağlanmayı da burada deneyebilirsin
@app.get("/readyz", include_in_schema=False)
async def readyz():
    return {"status": "ready"}


# Run the app directly with uvicorn (for development)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
