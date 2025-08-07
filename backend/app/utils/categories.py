from firebase_admin import firestore

db = firestore.client()

def resolve_category_name(name: str, expected_type: str) -> tuple[str, dict]:
    """
    • name → Firestore’da benzersiz olmalı
    • expected_type: 'product' | 'service'
    Döner: (category_id, category_doc_dict)
    """
    q = (
        db.collection("categories")
        .where("name", "==", name)
        .where("type", "==", expected_type)
        .limit(1)
        .get()
    )
    if not q:
        raise ValueError(f"Kategori bulunamadı: {name}")
    doc = q[0]
    return doc.id, doc.to_dict()
