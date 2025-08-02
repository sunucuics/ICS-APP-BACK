"""
app/main.py - Application entry point.
Sets up FastAPI app, includes routers, configures middleware, and starts background scheduler.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import settings
from backend.app.routers import auth, users, categories, products, services, carts, orders, appointments, discounts, comments
from backend.app.routers import categories as categories_router
from backend.app.routers import products as products_router
from backend.app.routers import services as services_router
from backend.app.routers import orders as orders_router
from backend.app.routers import appointments as appointments_router
from backend.app.routers import comments as comments_router
from backend.app.integrations.shipping_provider import update_tracking_statuses
from firebase_admin import firestore

# Initialize FastAPI app
app = FastAPI(
    title="E-Commerce & Service Booking API",
    description="Backend API for an e-commerce and appointment booking application.",
    version="1.0.0"
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

# Include admin routers (with prefix /admin)
app.include_router(categories_router.admin_router, prefix="/admin")
app.include_router(products_router.admin_router, prefix="/admin")
app.include_router(services_router.admin_router, prefix="/admin")
app.include_router(orders_router.admin_router, prefix="/admin")
app.include_router(appointments_router.admin_router, prefix="/admin")
app.include_router(discounts.router, prefix="/admin")  # all routes in discounts are admin-protected via dependencies
app.include_router(comments_router.admin_router, prefix="/admin")

# Background scheduler for tracking updates
# We'll use APScheduler if tracking integration is enabled
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
if settings.tracking_api_key:
    # schedule tracking status updates every 30 minutes
    scheduler.add_job(update_tracking_statuses, 'interval', minutes=30)

# Start and stop scheduler with app events
@app.on_event("startup")
def on_startup():
    if settings.tracking_api_key:
        scheduler.start()
        print("Background scheduler started for shipment tracking.")

@app.on_event("shutdown")
def on_shutdown():
    try:
        scheduler.shutdown()
    except Exception:
        pass
