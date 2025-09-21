"""
Analytics router for admin dashboard analytics
"""
from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.auth import get_current_admin
from backend.app.config import db
from typing import Dict, Any
from datetime import datetime, timedelta

router = APIRouter(prefix="/analytics", tags=["Admin: Analytics"], dependencies=[Depends(get_current_admin)])

@router.get("/")
def get_analytics_data(period: str = "week"):
    """
    Get comprehensive analytics data
    """
    try:
        # Get basic counts
        orders_ref = db.collection("orders")
        users_ref = db.collection("users")
        products_ref = db.collection_group("items")
        
        # Count orders
        orders_count = len(list(orders_ref.stream()))
        
        # Count users
        users_count = len(list(users_ref.stream()))
        
        # Count products
        products_count = len(list(products_ref.stream()))
        
        # Get revenue data (last 30 days)
        thirty_days_ago = datetime.now() - timedelta(days=30)
        recent_orders = orders_ref.where("created_at", ">=", thirty_days_ago).stream()
        
        total_revenue = 0.0
        for order_doc in recent_orders:
            order_data = order_doc.to_dict()
            totals = order_data.get("totals", {})
            grand_total = totals.get("grand_total", 0)
            total_revenue += float(grand_total)
        
        # Calculate average order value
        avg_order_value = total_revenue / max(orders_count, 1)
        
        # Create analytics data structure
        analytics_data = {
            "salesReport": {
                "total_sales": total_revenue,
                "total_orders": orders_count,
                "average_order_value": avg_order_value,
                "conversion_rate": 0.0,  # Placeholder
                "period": period,
                "date_range": {
                    "start_date": thirty_days_ago.isoformat(),
                    "end_date": datetime.now().isoformat()
                }
            },
            "userActivity": {
                "total_users": users_count,
                "new_users": 0,  # Placeholder
                "active_users": users_count,
                "retention_rate": 0.0,  # Placeholder
                "period": period
            },
            "revenueChart": {
                "labels": ["Son 7 Gün", "Son 14 Gün", "Son 30 Gün"],
                "values": [total_revenue * 0.3, total_revenue * 0.6, total_revenue],
                "period": period
            },
            "trendAnalysis": {
                "sales_trend": 0.0,  # Placeholder
                "user_trend": 0.0,  # Placeholder
                "order_trend": 0.0,  # Placeholder
                "period": period
            }
        }
        
        return analytics_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")

@router.get("")
def get_analytics_data_no_slash(period: str = "week"):
    """
    Get comprehensive analytics data (no trailing slash)
    """
    return get_analytics_data(period)

@router.get("/overview")
def get_analytics_overview():
    """
    Get analytics overview for admin dashboard
    """
    try:
        # Get basic counts
        orders_ref = db.collection("orders")
        users_ref = db.collection("users")
        products_ref = db.collection_group("items")
        
        # Count orders
        orders_count = len(list(orders_ref.stream()))
        
        # Count users
        users_count = len(list(users_ref.stream()))
        
        # Count products
        products_count = len(list(products_ref.stream()))
        
        # Get revenue data (last 30 days)
        thirty_days_ago = datetime.now() - timedelta(days=30)
        recent_orders = orders_ref.where("created_at", ">=", thirty_days_ago).stream()
        
        total_revenue = 0.0
        for order_doc in recent_orders:
            order_data = order_doc.to_dict()
            totals = order_data.get("totals", {})
            grand_total = totals.get("grand_total", 0)
            total_revenue += float(grand_total)
        
        return {
            "total_orders": orders_count,
            "total_users": users_count,
            "total_products": products_count,
            "revenue_last_30_days": total_revenue,
            "average_order_value": total_revenue / max(orders_count, 1),
            "conversion_rate": 0.0,  # Placeholder
            "top_products": [],  # Placeholder
            "recent_activity": []  # Placeholder
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")

@router.get("/revenue")
def get_revenue_analytics():
    """
    Get revenue analytics
    """
    try:
        orders_ref = db.collection("orders")
        
        # Get revenue by month (last 12 months)
        revenue_by_month = {}
        for i in range(12):
            month_start = datetime.now() - timedelta(days=30 * i)
            month_end = month_start + timedelta(days=30)
            
            month_orders = orders_ref.where("created_at", ">=", month_start).where("created_at", "<", month_end).stream()
            
            month_revenue = 0.0
            for order_doc in month_orders:
                order_data = order_doc.to_dict()
                totals = order_data.get("totals", {})
                grand_total = totals.get("grand_total", 0)
                month_revenue += float(grand_total)
            
            revenue_by_month[month_start.strftime("%Y-%m")] = month_revenue
        
        return {
            "revenue_by_month": revenue_by_month,
            "total_revenue": sum(revenue_by_month.values()),
            "growth_rate": 0.0  # Placeholder
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Revenue analytics error: {str(e)}")

@router.get("/products")
def get_product_analytics():
    """
    Get product analytics
    """
    try:
        # Get top selling products
        orders_ref = db.collection("orders")
        orders = orders_ref.stream()
        
        product_sales = {}
        for order_doc in orders:
            order_data = order_doc.to_dict()
            items = order_data.get("items", [])
            
            for item in items:
                product_id = item.get("product_id")
                quantity = item.get("quantity", 0)
                
                if product_id:
                    if product_id not in product_sales:
                        product_sales[product_id] = 0
                    product_sales[product_id] += quantity
        
        # Sort by sales
        top_products = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            "top_selling_products": top_products,
            "total_products_sold": sum(product_sales.values()),
            "average_products_per_order": 0.0  # Placeholder
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Product analytics error: {str(e)}")
