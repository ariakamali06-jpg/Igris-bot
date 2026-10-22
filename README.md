# 🏙 Igris Bot — تیرامیکس (Bot Tiramix City RPG)

بات تلگرامی شهرسازی + RPG فارسی برای گروه‌ها: اقتصاد زنده، شغل و تحصیل، دئل و رید، کازینو و بازار سیاه، خواص و کلن‌ها، بازار املاک، پت، مارکت سهام/شرط‌بندی، لاتاری، دزدی و امنیت — همه با **کارت کاراکتر تصویری** که به‌صورت پیکسل‌آرت کامپوز می‌شود.

Built with **aiogram 3 + SQLite + Pillow**. Runtime target: **Railway** (single process, single volume).

> ℹ️ کارت کاراکتر و مینی‌اپ کاستومایز از **CDN Everskies** رندر می‌شوند؛ لایه‌های محلی `assets/layers` برای کارت‌های آفلاین (باس/رید) کامپوز می‌شوند.

---

## 🚀 راه‌اندازی سریع

### روی Railway (توصیه‌شده)
1. متغیرهای زیر را در Service → Variables تنظیم کن:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | توکن از [@BotFather] |
| `ADMIN_IDS` | شناسه عددی تلگرام ادمین‌ها (جدا با کاما) |
| `BOT_USERNAME` | `hermesforrexbot` ← **برای ذخیره استایل از مینی‌اپ واجب است (دیپ‌لینک `wear_`)** |

2. Deploy از ریپو — `railway.json` + `Procfile` آماده‌اند؛ دیتابیس روی volume پایدار نگه داشته می‌شود (`DB_PATH`).

### اجرای محلی
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # BOT_TOKEN / ADMIN_IDS را پر کن
python main.py
```

---

## 🧩 سیستم‌های بازی

| ماژول | دستورها/محتوا |
|---|---|
| 👤 کاراکتر | `/start` (ساخت شخصیت)، `/me` کارت کاراکتر، لباس/کاستومایز |
| 💼 اقتصاد | شغل، قرارداد روزانه، تراکنش‌ها، بانک، جریمه/زندان |
| ⚔️ رزم | دئل ۱v۱، رید باسی گروهی، weapons/auras |
| 🎲 قمار | risk (چرخ و قرعه)، xo، penalty، کازینو چندبازی |
| 🛒 فروشگاه | boutique ست‌های روز، shop آیتم‌ها، bazaar P2P، blackmarket |
| 🏘 شهر | املاک، کلن‌ها، پت‌ها، رویداد Cup |
| 📈 مارکت | سهام شبیه‌سازی‌شده، شرط‌بندی قیمت، لاتاری |
| 🛡 امنیت | دزدی (theft)، نگهبان، safe lock |
| 🎨 کاستومایز | مینی‌اپ لایو + ذخیره استایل |

 دستورهای کامل و فرمول‌های سیستم: **[GAME_SYSTEMS.md](GAME_SYSTEMS.md)** — نقشه انتقال قابلیت‌های Ocean: **[OCEAN_PORT.md](OCEAN_PORT.md)**.

---

## 🎨 مینی‌اپ کاستومایز (GitHub Pages)

- **آدرس لایو:** <https://ariakamali06-jpg.github.io/Igris-bot/tools/customizer_webapp.html>
- روت `/` به همین صفحه منتقل می‌شود (redirect).
- ذخیره استایل ۳ مسیر: `sendData` ← دیپ‌لینک `/start wear_<b64>` ← روش دستی.
- منبع مشخصات: `bot_catalog.db` • تولید/به‌روزرسانی کاتالوگ: `tools/catalog_explorer.html` + `services/catalog.py::import_custom_json`

---

## 🗂 ساختار پروژه

```
handlers/     # روترهای aiogram — هر سیستم بازی یک فایل
services/     # موتورهای بازی: اقتصاد، رزم، آواتار، کاتالوگ، کامپوزیتور...
database/     # schema.sql، seed آیتم‌ها، لایه اتصال SQLite
models/       # enums + مدل بازیکن
assets/layers # لایه‌های PNG کارت‌های محلی (body/hair/tops/legs/weapons/auras/bg)
tools/        # مینی‌اپ کاستومایز + کاتالوگ‌ساز
tests/        # pytest — ۳۰+ فایل، کل رفتارهای حساس پوشش داده شده
```

*(پوشه `data/`، `cache/` و `assets/layers/manifest.json` فقط runtime هستند و در ریپو نیستند — به‌جز `data/bot_catalog.db` که کاتالوگ ست‌های boutique است.)*

---

## 🧪 تست و توسعه

```bash
pip install -r requirements-dev.txt
pytest tests -q          # ۲۷۸+ تست پاس
python -m venv .venv     # (بعد از نصب) اسکریپت‌های هنر: scripts/gen_asset.py
```

- خط تولید هنر و چک‌لیست کیفیت: **[ART_PIPELINE.md](ART_PIPELINE.md)** — اسکریپت اصلی `scripts/gen_asset.py` (بدون کلید، رابط Kavel/Pollinations)
- استایل ثابت سبک هنر: `assets/STYLE.md`

---

## 🔧 نکته‌های بهره‌برداری

- `BOT_USERNAME` حتماً ست شود، وگرنه ذخیره استایل از مینی‌اپ از مسیر دیپ‌لینک از کار می‌افتد.
- کامپوزیتور برای لعاب‌دهی مو/چشم/دهان رنگ‌ها را داینامیک اضافه می‌کند (`_customize_figure`) — تغییر رنگ‌ها در `services/compositor.py`.
- لایه‌ها به‌جز `assets/layers/sutemo/*` (پک پسر، اگر اضافه شد) از `ASSETS_DIR` یکجا لود می‌شوند؛ هر PNG باید 512×512 باشد.
- کش کامپوزیت در `cache/composites/*` به کلید هش وابسته است — پاک‌سازی خودکار، امن.

---

ساخته‌شده با ❤ برای گروه‌های تلگرامی فارسی‌زبان
