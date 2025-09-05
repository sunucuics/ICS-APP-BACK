from fastapi import APIRouter, Depends, HTTPException, status, Form
from pydantic import EmailStr
from app.core.security import get_current_user
from app.schemas.delete import DeleteVerifyRequest
from app.services import account_delete as svc

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/delete-account/initiate", summary="Hesap silme talebi başlat (e-posta kodlu)")
async def initiate_delete_account(current_user = Depends(get_current_user)):
    uid = current_user["id"]
    email: EmailStr | None = current_user.get("email")
    name = current_user.get("name") or ""
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kullanıcının e-posta adresi yok")
    await svc.initiate(uid, email, name)
    return {"detail": "Doğrulama kodu e-postana gönderildi (30 dk geçerli)."}

@router.post("/delete-account/verify", summary="E-posta kodunu doğrula ve hesabı sil")
async def verify_delete_account(payload: DeleteVerifyRequest, current_user = Depends(get_current_user)):
    uid = current_user["id"]
    try:
        svc.verify_and_delete(uid, payload.code)
    except ValueError as e:
        msg = str(e)
        if msg == "NO_ACTIVE_REQUEST":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aktif bir silme talebi yok")
        if msg == "EXPIRED":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kodun süresi geçmiş; lütfen tekrar iste")
        if msg == "TOO_MANY_ATTEMPTS":
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Çok fazla hatalı deneme; tekrar kod iste")
        if msg == "INVALID_CODE":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kod hatalı")
        raise
    return {"detail": "Hesabın ve verilerin kalıcı olarak silindi."}
