#!/usr/bin/env python3
"""
Firebase Admin SDK ile kullanıcıya admin custom claim ekler.
"""

import firebase_admin
from firebase_admin import credentials, auth
import json
import sys

def set_admin_claim(user_email: str):
    """Kullanıcıya admin custom claim ekler."""
    
    # Firebase Admin SDK'yı başlat
    try:
        # Service account key dosyasını kullan
        cred = credentials.Certificate('firebase_service_account.json')
        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK initialized")
    except Exception as e:
        print(f"❌ Firebase initialization failed: {e}")
        return False
    
    try:
        # Kullanıcıyı email ile bul
        user = auth.get_user_by_email(user_email)
        print(f"✅ User found: {user.uid} - {user.email}")
        
        # Admin custom claim ekle
        auth.set_custom_user_claims(user.uid, {'admin': True})
        print(f"✅ Admin claim added to user: {user_email}")
        
        # Doğrula
        user = auth.get_user(user.uid)
        custom_claims = user.custom_claims
        print(f"✅ Custom claims: {custom_claims}")
        
        return True
        
    except auth.UserNotFoundError:
        print(f"❌ User not found: {user_email}")
        return False
    except Exception as e:
        print(f"❌ Error setting admin claim: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python set_admin_claim.py <user_email>")
        print("Example: python set_admin_claim.py efehanh0@gmail.com")
        sys.exit(1)
    
    user_email = sys.argv[1]
    print(f"Setting admin claim for: {user_email}")
    
    success = set_admin_claim(user_email)
    if success:
        print("🎉 Admin claim set successfully!")
        print("The user will need to sign out and sign in again for the changes to take effect.")
    else:
        print("💥 Failed to set admin claim")
        sys.exit(1)
