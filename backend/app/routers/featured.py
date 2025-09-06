# app/routers/featured.py
from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends, Path, status
from app.schemas.featured import FeaturedExpandedDoc
from app.services.featured_service import feature, unfeature, list_items
from app.core.security import get_current_admin

admin_router = APIRouter(
    prefix="/featured",
    tags=["Admin: Featured"],
    dependencies=[Depends(get_current_admin)],
)

def _uid_of(admin) -> str | None:
    return admin.get("uid") if isinstance(admin, dict) else getattr(admin, "uid", None)

# ---------- PRODUCTS (ADMIN) ----------
@admin_router.post(
    "/products/{product_id}",
    response_model=FeaturedExpandedDoc,
    status_code=status.HTTP_201_CREATED,
)
def feature_product(product_id: str = Path(..., min_length=1), admin=Depends(get_current_admin)):
    return feature("products", product_id, _uid_of(admin), expand_detail=True)

@admin_router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unfeature_product(product_id: str = Path(..., min_length=1)):
    unfeature("products", product_id)
    return None

@admin_router.get(
    "/products",
    response_model=List[FeaturedExpandedDoc],
)
def list_featured_products():
    return list_items("products", expand_detail=True)

# ---------- SERVICES (ADMIN) ----------
@admin_router.post(
    "/services/{service_id}",
    response_model=FeaturedExpandedDoc,
    status_code=status.HTTP_201_CREATED,
)
def feature_service(service_id: str = Path(..., min_length=1), admin=Depends(get_current_admin)):
    return feature("services", service_id, _uid_of(admin), expand_detail=True)

@admin_router.delete(
    "/services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unfeature_service(service_id: str = Path(..., min_length=1)):
    unfeature("services", service_id)
    return None

@admin_router.get(
    "/services",
    response_model=List[FeaturedExpandedDoc],
)
def list_featured_services():
    return list_items("services", expand_detail=True)
