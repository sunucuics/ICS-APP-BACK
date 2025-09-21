"""
Admin Dashboard Router
Handles admin dashboard statistics and overview data
"""

from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.auth import get_current_admin
from backend.app.schemas.principal import Principal
from firebase_admin import firestore
from typing import Dict, Any
from datetime import datetime, timedelta

router = APIRouter()

@router.get("/dashboard/stats")
async def get_dashboard_stats(
    current_admin: Principal = Depends(get_current_admin)
) -> Dict[str, Any]:
    """
    Get dashboard statistics for admin panel
    Returns overview statistics including counts and recent activity
    """
    try:
        db = firestore.client()
        
        # Get current date and date ranges
        now = datetime.now()
        today = now.date()
        week_ago = (now - timedelta(days=7)).date()
        month_ago = (now - timedelta(days=30)).date()
        
        # Initialize stats dictionary
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
            "recent_comments": []
        }
        
        # Get orders count and revenue
        orders_ref = db.collection('orders')
        orders = orders_ref.stream()
        
        for order_doc in orders:
            order_data = order_doc.to_dict()
            stats["total_orders"] += 1
            
            # Check order date and calculate revenue
            order_date_str = order_data.get('created_at', '')
            if order_date_str:
                try:
                    order_date = datetime.fromisoformat(order_date_str.replace('Z', '+00:00')).date()
                    # Get order total from totals.grand_total
                    totals = order_data.get('totals', {})
                    order_total = float(totals.get('grand_total', 0))
                    
                    if order_date == today:
                        stats["orders_today"] += 1
                        stats["revenue_today"] += order_total
                    if order_date >= week_ago:
                        stats["orders_this_week"] += 1
                        stats["revenue_this_week"] += order_total
                    if order_date >= month_ago:
                        stats["orders_this_month"] += 1
                        stats["revenue_this_month"] += order_total
                        
                    # Check for pending orders
                    if order_data.get('status') in ['pending', 'processing', 'shipped']:
                        stats["pending_orders"] += 1
                        
                except (ValueError, TypeError):
                    continue
        
        # Get users count
        users_ref = db.collection('users')
        users = users_ref.stream()
        stats["total_users"] = sum(1 for _ in users)
        
        # Get products count
        products_ref = db.collection('products')
        products = products_ref.stream()
        stats["total_products"] = sum(1 for _ in products)
        
        # Get services count
        services_ref = db.collection('services')
        services = services_ref.stream()
        stats["total_services"] = sum(1 for _ in services)
        
        # Get appointments count and pending
        appointments_ref = db.collection('appointments')
        appointments = appointments_ref.stream()
        
        for appointment_doc in appointments:
            appointment_data = appointment_doc.to_dict()
            stats["total_appointments"] += 1
            
            if appointment_data.get('status') in ['pending', 'confirmed']:
                stats["pending_appointments"] += 1
        
        # Get comments count and pending
        comments_ref = db.collection('comments')
        comments = comments_ref.stream()
        
        for comment_doc in comments:
            comment_data = comment_doc.to_dict()
            stats["total_comments"] += 1
            
            if not comment_data.get('approved', False):
                stats["pending_comments"] += 1
        
        # Get active discounts count
        discounts_ref = db.collection('discounts')
        discounts = discounts_ref.stream()
        
        for discount_doc in discounts:
            discount_data = discount_doc.to_dict()
            
            # Check if discount is active
            is_active = discount_data.get('active', False)
            if is_active:
                # Check date range if specified
                start_at = discount_data.get('start_at')
                end_at = discount_data.get('end_at')
                
                if start_at:
                    try:
                        start_date = datetime.fromisoformat(start_at.replace('Z', '+00:00')).date()
                        if now.date() < start_date:
                            continue
                    except (ValueError, TypeError):
                        pass
                
                if end_at:
                    try:
                        end_date = datetime.fromisoformat(end_at.replace('Z', '+00:00')).date()
                        if now.date() > end_date:
                            continue
                    except (ValueError, TypeError):
                        pass
                
                stats["active_discounts"] += 1
        
        # Get recent orders (last 5)
        recent_orders_ref = db.collection('orders').order_by('created_at', direction=firestore.Query.DESCENDING).limit(5)
        recent_orders = recent_orders_ref.stream()
        
        for order_doc in recent_orders:
            order_data = order_doc.to_dict()
            order_data['id'] = order_doc.id
            stats["recent_orders"].append(order_data)
        
        # Get recent appointments (last 5)
        recent_appointments_ref = db.collection('appointments').order_by('created_at', direction=firestore.Query.DESCENDING).limit(5)
        recent_appointments = recent_appointments_ref.stream()
        
        for appointment_doc in recent_appointments:
            appointment_data = appointment_doc.to_dict()
            appointment_data['id'] = appointment_doc.id
            stats["recent_appointments"].append(appointment_data)
        
        # Get recent comments (last 5)
        recent_comments_ref = db.collection('comments').order_by('created_at', direction=firestore.Query.DESCENDING).limit(5)
        recent_comments = recent_comments_ref.stream()
        
        for comment_doc in recent_comments:
            comment_data = comment_doc.to_dict()
            comment_data['id'] = comment_doc.id
            stats["recent_comments"].append(comment_data)
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch dashboard stats: {str(e)}")

@router.get("/dashboard/overview")
async def get_dashboard_overview(
    current_admin: Principal = Depends(get_current_admin)
) -> Dict[str, Any]:
    """
    Get dashboard overview with summary information
    """
    try:
        # Get basic stats
        stats = await get_dashboard_stats(current_admin)
        
        # Calculate growth percentages (simplified - in real app you'd compare with previous periods)
        overview = {
            "stats": stats,
            "growth": {
                "orders_growth": 0,  # Placeholder - would calculate from previous period
                "revenue_growth": 0,  # Placeholder - would calculate from previous period
                "users_growth": 0,   # Placeholder - would calculate from previous period
            },
            "charts": {
                "orders_chart": [],  # Placeholder for chart data
                "revenue_chart": [], # Placeholder for chart data
                "users_chart": [],   # Placeholder for chart data
            }
        }
        
        return overview
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch dashboard overview: {str(e)}")
