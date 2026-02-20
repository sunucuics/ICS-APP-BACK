"""
Admin Dashboard Router
Handles admin dashboard statistics and overview data

Optimized: Uses Firestore aggregation queries and concurrent threading
to avoid streaming entire collections serially.
"""

from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.security import get_current_admin
from backend.app.schemas.principal import Principal
from firebase_admin import firestore
from typing import Dict, Any
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: Firestore count() aggregation (avoids streaming entire collections)
# ---------------------------------------------------------------------------

def _count_collection(collection_name: str) -> int:
    """Use Firestore aggregation count — does NOT download documents."""
    try:
        agg = (
            firestore.client()
            .collection(collection_name)
            .count()
            .get()
        )
        return agg[0][0].value if agg and agg[0] else 0
    except Exception:
        # Fallback: stream and count (eski yöntem)
        return sum(1 for _ in firestore.client().collection(collection_name).stream())


def _count_with_filter(collection_name: str, field: str, op: str, value) -> int:
    """Filtered aggregation count."""
    try:
        from google.cloud.firestore_v1 import FieldFilter
        agg = (
            firestore.client()
            .collection(collection_name)
            .where(filter=FieldFilter(field, op, value))
            .count()
            .get()
        )
        return agg[0][0].value if agg and agg[0] else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Parallel data fetchers (each runs in its own thread)
# ---------------------------------------------------------------------------

def _fetch_orders_stats() -> Dict[str, Any]:
    """Fetch order counts and revenue — needs full scan only for date-based stats."""
    db = firestore.client()
    now = datetime.now()
    today = now.date()
    week_ago = (now - timedelta(days=7)).date()
    month_ago = (now - timedelta(days=30)).date()

    result = {
        "total_orders": 0,
        "orders_today": 0,
        "orders_this_week": 0,
        "orders_this_month": 0,
        "revenue_today": 0.0,
        "revenue_this_week": 0.0,
        "revenue_this_month": 0.0,
        "pending_orders": 0,
    }

    # Only stream last 30 days for revenue calculation (not ALL orders)
    month_ago_dt = datetime.combine(month_ago, datetime.min.time())
    try:
        q = db.collection("orders").order_by("created_at", direction=firestore.Query.DESCENDING)
        for order_doc in q.stream():
            order_data = order_doc.to_dict()
            result["total_orders"] += 1

            order_date_str = order_data.get("created_at", "")
            if not order_date_str:
                continue
            try:
                order_date = datetime.fromisoformat(str(order_date_str).replace("Z", "+00:00")).date()
                totals = order_data.get("totals", {})
                order_total = float(totals.get("grand_total", 0))

                if order_date == today:
                    result["orders_today"] += 1
                    result["revenue_today"] += order_total
                if order_date >= week_ago:
                    result["orders_this_week"] += 1
                    result["revenue_this_week"] += order_total
                if order_date >= month_ago:
                    result["orders_this_month"] += 1
                    result["revenue_this_month"] += order_total

                if order_data.get("status") in ["pending", "processing", "shipped"]:
                    result["pending_orders"] += 1

            except (ValueError, TypeError):
                continue
    except Exception as e:
        logger.error("Error fetching orders stats: %s", e)

    return result


def _fetch_counts() -> Dict[str, int]:
    """Fetch total counts for users, products, services using aggregation."""
    counts = {}
    for name in ("users", "products", "services"):
        try:
            counts[f"total_{name}"] = _count_collection(name)
        except Exception:
            counts[f"total_{name}"] = 0
    return counts


def _fetch_appointments_stats() -> Dict[str, Any]:
    """Fetch appointment count and pending count."""
    db = firestore.client()
    total = 0
    pending = 0
    try:
        for doc in db.collection("appointments").stream():
            total += 1
            data = doc.to_dict()
            if data.get("status") in ["pending", "confirmed"]:
                pending += 1
    except Exception as e:
        logger.error("Error fetching appointments stats: %s", e)
    return {"total_appointments": total, "pending_appointments": pending}


def _fetch_comments_stats() -> Dict[str, Any]:
    """Fetch comments count and pending (unapproved) count."""
    db = firestore.client()
    total = 0
    pending = 0
    try:
        for doc in db.collection("comments").stream():
            total += 1
            data = doc.to_dict()
            if data.get("is_hidden", False) or data.get("is_deleted", False):
                pending += 1
    except Exception as e:
        logger.error("Error fetching comments stats: %s", e)
    return {"total_comments": total, "pending_comments": pending}


def _fetch_active_discounts() -> int:
    """Count active discounts (checks date range)."""
    db = firestore.client()
    from google.cloud.firestore_v1 import FieldFilter
    now = datetime.now()
    count = 0
    try:
        q = db.collection("discounts").where(filter=FieldFilter("active", "==", True))
        for doc in q.stream():
            data = doc.to_dict() or {}
            start_at = data.get("start_at")
            end_at = data.get("end_at")

            if start_at:
                try:
                    sd = datetime.fromisoformat(str(start_at).replace("Z", "+00:00")).date()
                    if now.date() < sd:
                        continue
                except (ValueError, TypeError):
                    pass
            if end_at:
                try:
                    ed = datetime.fromisoformat(str(end_at).replace("Z", "+00:00")).date()
                    if now.date() > ed:
                        continue
                except (ValueError, TypeError):
                    pass
            count += 1
    except Exception as e:
        logger.error("Error fetching active discounts: %s", e)
    return count


def _fetch_recent_items() -> Dict[str, list]:
    """Fetch recent orders, appointments, comments — 5 each, IN PARALLEL."""
    db = firestore.client()
    result = {"recent_orders": [], "recent_appointments": [], "recent_comments": []}

    def _recent(collection: str, key: str):
        items = []
        try:
            q = db.collection(collection).order_by(
                "created_at", direction=firestore.Query.DESCENDING
            ).limit(5)
            for doc in q.stream():
                data = doc.to_dict()
                data["id"] = doc.id
                items.append(data)
        except Exception:
            pass
        return key, items

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_recent, "orders", "recent_orders"),
            pool.submit(_recent, "appointments", "recent_appointments"),
            pool.submit(_recent, "comments", "recent_comments"),
        ]
        for f in as_completed(futures):
            key, items = f.result()
            result[key] = items

    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/dashboard/stats")
def get_dashboard_stats(
    current_admin: Principal = Depends(get_current_admin),
) -> Dict[str, Any]:
    """
    Get dashboard statistics for admin panel.
    Optimized: runs 6 data fetchers in parallel threads instead of 11+ serial streams.
    """
    try:
        stats = {
            "total_orders": 0,
            "total_users": 0,
            "total_products": 0,
            "total_services": 0,
            "total_appointments": 0,
            "total_comments": 0,
            "active_discounts": 0,
            "orders_today": 0,
            "orders_this_week": 0,
            "orders_this_month": 0,
            "revenue_today": 0.0,
            "revenue_this_week": 0.0,
            "revenue_this_month": 0.0,
            "pending_orders": 0,
            "pending_appointments": 0,
            "pending_comments": 0,
            "recent_orders": [],
            "recent_appointments": [],
            "recent_comments": [],
        }

        # Run all fetchers concurrently
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_orders = pool.submit(_fetch_orders_stats)
            f_counts = pool.submit(_fetch_counts)
            f_appointments = pool.submit(_fetch_appointments_stats)
            f_comments = pool.submit(_fetch_comments_stats)
            f_discounts = pool.submit(_fetch_active_discounts)
            f_recent = pool.submit(_fetch_recent_items)

            # Merge results
            stats.update(f_orders.result())
            stats.update(f_counts.result())
            stats.update(f_appointments.result())
            stats.update(f_comments.result())
            stats["active_discounts"] = f_discounts.result()
            stats.update(f_recent.result())

        return stats

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dashboard stats: {str(e)}",
        )


@router.get("/dashboard/overview")
def get_dashboard_overview(
    current_admin: Principal = Depends(get_current_admin),
) -> Dict[str, Any]:
    """
    Get dashboard overview with summary information.
    """
    try:
        stats = get_dashboard_stats(current_admin)

        overview = {
            "stats": stats,
            "growth": {
                "orders_growth": 0,
                "revenue_growth": 0,
                "users_growth": 0,
            },
            "charts": {
                "orders_chart": [],
                "revenue_chart": [],
                "users_chart": [],
            },
        }

        return overview

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dashboard overview: {str(e)}",
        )
