FaheS + Chargily Pay

الأسعار:
- تحليل واحد: 150 دج
- أسبوعي: 300 دج
- شهري: 900 دج

تشغيل:
export CHARGILY_SECRET_KEY="test_sk_xxxxxxxx"
export CHARGILY_MODE="test"
export PUBLIC_BASE_URL="https://your-domain.com"
export FAHES_ADMIN_KEY="fahes-admin"
python server.py

Webhook:
https://your-domain.com/api/pay/webhook
