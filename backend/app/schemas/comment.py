"""
# `app/schemas/comment.py` — Yorum Şema Dokümantasyonu

## Genel Bilgi
Bu dosya, kullanıcıların ürün veya hizmetler için yorum eklemesini ve yorum verilerinin API çıktılarında kullanılmasını sağlayan Pydantic modellerini tanımlar.

---

## Girdi Şemaları (Input)

### `CommentCreate`
Yeni yorum oluşturmak için.
| Alan         | Tip      | Zorunlu | Açıklama |
|--------------|----------|---------|----------|
| target_type  | `"product"` \| `"service"` | ✔ | Yorumun hedef türü |
| target_id    | `str`    | ✔       | Yorumun hedef ID’si (genel yorumlarda `"__all__"`) |
| rating       | `int` (1–5) | ✔   | Puan |
| content      | `str` (1–500 karakter) | ✔ | Yorum metni |

---

## Çıktı Şemaları (Output)

### `CommentOut`
Yorum listeleme veya görüntüleme için.
| Alan         | Tip      | Açıklama |
|--------------|----------|----------|
| id           | `str`    | Yorum ID’si |
| target_type  | `"product"` \| `"service"` | Hedef tür |
| target_id    | `str`    | Hedef ID (genel yorumlarda `"__all__"`) |
| user_id      | `str`    | Yorumu yazan kullanıcı ID’si |
| rating       | `int`    | Puan |
| content      | `str`    | Yorum metni |
| is_deleted   | `bool`   | Yorum silinmiş mi? (varsayılan `False`) |
| created_at   | `datetime` / `null` | Yorum oluşturulma zamanı |

"""
from typing import Optional, Literal
from datetime import datetime
from pydantic import BaseModel, conint, constr

# Public create: 1-5 puan, max 500 karakter; hedefi body'de seçiyoruz.
class CommentCreate(BaseModel):
    target_type: Literal["product", "service"]
    target_id: str
    rating: conint(ge=1, le=5)                    # 1..5
    content: constr(min_length=1, max_length=500) # max 500

class CommentOut(BaseModel):
    id: str
    target_type: Literal["product", "service"]   # genel: ürünler ya da servisler
    target_id: str                                # genel yorumda "__all__" yazacağız
    user_id: str
    rating: int
    content: str
    is_deleted: bool = False
    created_at: Optional[datetime] = None