#!/bin/bash

# Production Backend BIN Test Script
# Kullanım: ./test_bin_production.sh KART_BIN AUTH_TOKEN

BIN=${1:-"454358"}  # İlk 6 hane (veya 8 hane)
TOKEN=${2:-""}

BACKEND_URL="https://api.innovacraftstudio.com"

echo "🔍 Production Backend BIN Testi"
echo "================================"
echo "Backend URL: $BACKEND_URL"
echo "BIN: $BIN"
echo ""

if [ -z "$TOKEN" ]; then
    echo "⚠️  Auth token verilmedi, token olmadan deneniyor..."
    echo ""
fi

# 1. BIN Detail Sorgusu
echo "📋 1. BIN Detail Sorgusu:"
echo "------------------------"

if [ -z "$TOKEN" ]; then
    curl -X POST "$BACKEND_URL/paytr/direct/bin-detail" \
      -H "Content-Type: application/json" \
      -d "{\"bin_number\": \"$BIN\", \"debug_on\": 1}" \
      -w "\n\nHTTP Status: %{http_code}\n" \
      2>/dev/null
else
    curl -X POST "$BACKEND_URL/paytr/direct/bin-detail" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $TOKEN" \
      -d "{\"bin_number\": \"$BIN\", \"debug_on\": 1}" \
      -w "\n\nHTTP Status: %{http_code}\n" \
      2>/dev/null
fi

echo ""
echo ""

# 2. Installment Quote Sorgusu
echo "📋 2. Installment Quote Sorgusu (100 TL için):"
echo "----------------------------------------------"

if [ -z "$TOKEN" ]; then
    curl -X POST "$BACKEND_URL/paytr/direct/installment-quote" \
      -H "Content-Type: application/json" \
      -d "{\"bin_number\": \"$BIN\", \"amount_tl\": 100.0}" \
      -w "\n\nHTTP Status: %{http_code}\n" \
      2>/dev/null
else
    curl -X POST "$BACKEND_URL/paytr/direct/installment-quote" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $TOKEN" \
      -d "{\"bin_number\": \"$BIN\", \"amount_tl\": 100.0}" \
      -w "\n\nHTTP Status: %{http_code}\n" \
      2>/dev/null
fi

echo ""
echo ""
echo "✅ Test tamamlandı!"
echo ""
echo "🔍 Kontrol Edilmesi Gerekenler:"
echo "  - cardType: 'credit' olmalı (debit değil)"
echo "  - brand: 'visa', 'mastercard' gibi bir değer olmalı ('none' değil)"
echo "  - installments: 2 adet olmalı (peşin + 3 taksit)"
