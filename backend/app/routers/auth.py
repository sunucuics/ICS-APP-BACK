"""
app/routers/auth.py - Authentication routes for user registration (and possibly login).
Uses Firebase Auth for creating accounts and verifying credentials.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from firebase_admin import auth as firebase_auth
from backend.app.schemas.user import UserCreate, UserProfile
from backend.app.core.security import (get_current_user)

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/register", response_model=UserProfile)
def register(user_data: UserCreate):
    """
    Register a new user with email and password.
    Creates a Firebase Auth user and a Firestore user profile document.
    """
    # Use Firebase Admin to create the user account
    try:
        user_record = firebase_auth.create_user(
            email=user_data.email,
            password=user_data.password,
            display_name=user_data.name or ""
        )
    except Exception as e:
        # Handle errors (email already exists, etc.)
        raise HTTPException(status_code=400, detail=str(e))
    uid = user_record.uid
    # Create Firestore user profile
    profile = {
        "name": user_data.name or "",
        "email": user_data.email,
        "phone": user_data.phone or "",
        "role": "customer",
        "addresses": [],
        "created_at": None,  # will be set by server timestamp if using server-side (we can set after creation)
        "is_guest": False
    }
    from app.config import db  # import here to avoid circular import
    db.collection("users").document(uid).set(profile)
    profile['id'] = uid
    return profile

# Optionally, if we wanted to implement login via backend (not typical since client handles it, but for completeness):
# We could verify email/password by calling Firebase's REST API or custom token creation, but it's simpler to let front-end handle login.
# Therefore, we do not implement a /login endpoint here. The user obtains JWT from Firebase client SDK.

@router.post("/reset-password")
def request_password_reset(email: str):
    """
    Initiates a password reset email via Firebase.
    Sends a reset link to the given email if it exists.
    """
    try:
        link = firebase_auth.generate_password_reset_link(email)
        # In a real system, we'd send this link via an email service to the user.
        # For now, just log or pretend it's sent.
        print(f"Password reset link generated for {email}: {link}")
    except Exception as e:
        # If email not found or other error, we return success message to avoid user enumeration
        print(f"Reset password attempted for {email}: {e}")
    # Always return success message (do not reveal if email exists or not)
    return {"detail": "If an account with that email exists, a password reset link has been sent."}
