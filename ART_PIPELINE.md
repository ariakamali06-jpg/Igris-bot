# نقشه راه ساخت آبجکت‌ها و بکگراندهای بازی با AI رایگان

> وضعیت: عملیاتی و تست‌شده. اسکریپت آماده: `scripts/gen_asset.py`
> همه‌ی ابزارهای این نقشه رایگان‌اند — بدون کارت اعتباری، بدون کلید API.

---

## ۱. خط لوله (Pipeline) کلی

```
پرامپت استاندارد (انگلیسی)
        │
        ▼
[1] تولید تصویر ── Kavel anonymous API (اصلی، 1024px، ~40-70 ثانیه)
        │            └─ Pollinations.ai (پشتیبان، فوری، فشار = 402)
        ▼
[2] برش واترمارک ── حاشیه 35px کناره + 70px پایین
        │
        ├── حالت آبجکت ── [3] chroma-key حذف پس‌زمینه ماجنتا → PNG شفاف → برش bbox → آپسکیل 2x
        │
        └── حالت بکگراند ── [3] ذخیره JPG باکیفیت
        ▼
[4] بازبینی چشمی (کیفیت/یکدستی) ── در صورت رد → تغییر پرامپت و تکرار
        │
        ▼
[5] نام‌گذاری و چیدمان در assets/
```

---

## ۲. ابزارها و مدل‌ها (رایگان، به ترتیب اولویت)

### الف) موتورهای بدون اکانت/کلید — قلب خط لوله (تست‌شده ✅)

| ابزار | کاربرد | سقف | نکته |
|---|---|---|---|
| **Kavel anonymous API** | آبجکت + بکگراند، 1024px، کیفیت بالا | هر `anon-id` جدید ۵ اعتبار → عملاً نامحدود با uuid تازه | واترمارک حاشیه پایین؛ برش خودکار در اسکریپت |
| **Pollinations.ai** | پشتیبان سریع (FLUX-class) | نامحدودِ اسمی؛ پرامپت کوتاه + فاصله‌گذاری الزامی | پارامتر اضافه (seed/width/nologo) = خطای 402 |

### ب) پلتفرم‌های وب با پلن رایگان — برای انیمیشن، tileset و تنوع سبک

| ابزار | پلن رایگان | بهترین برای |
|---|---|---|
| **Recraft** (recraft.ai) | ۳۰ تولید در روز، بدون کارت | استایل کنترل‌شده، وکتور، UI آیکون، بازنویسی سبک |
| **Scenario** (scenario.com) | اعتبار روزانه | آموزش مدل روی سبک خودمان → یکدستی کل assetها (بهترین در این کار) |
| **Makko** (makko.ai) | ۱۵۰ اعتبار هنری در ماه + ۷۰ درخواست هفتگی | کاراکتر، انیمیشن، پس‌زمینه، کل پکیج ۲D |
| **SpriteCook** (spritecook.ai) | رایگان شروع | sprite sheet، walk cycle، خروجی Unity/Godot/Phaser |
| **PixelLab** (pixellab.ai) | تریال رایگان | اگر سبک بازی pixel-art شد |
| **Hotpot.ai** | رایگان | بکگراند تکرارشونده (tileable pattern) |
| **Ludo** (ludo.ai) | ۳۰ اعتبار هنگام ثبت‌نام | sprite sheet + parallax layer |
| **Hugging Face Spaces** | رایگان | مدل‌های FLUX/SDXL تحت وب، بدون نصب |

### ج) پس‌پردازش محلی (رایگان، روی همین سرور)

- **Pillow** (نصب‌شده ✅): برش واترمارک، chroma-key، آپسکیل LANCZOS
- **rembg** (اختیاری): برای حذف پس‌زمینه‌ی پیچیده وقتی آبجکت روی زمینه‌ی غیریکدست افتاد → `uv pip install "rembg[cli]"`

---

## ۳. گام‌به‌گام اجرایی

### گام ۰ — سند سبک (Style Bible) — قبل از هر تولید
یک فایل متنی بساز (`assets/STYLE.md`) و این‌ها را ثبت کن و تغییرش نده:
- نام سبک: مثلاً «dark fantasy 2D painted, bold outlines, flat shading»
- پالت رنگ، میزان جزئیات، نورپردازی
- این عبارت **پیشوند ثابت** همه‌ی پرامپت‌هاست → کلید یکدستی بصری assetها

### گام ۱ — پرامپت‌ها (قالب آماده، انگلیسی)

**آبجکت:**
```
{OBJECT}, 2D game inventory item, clean bold outlines, flat shading,
single object centered, isolated on flat solid pure magenta #FF00FF background,
no frame, no circle, no border, no shadow, no text
```

**بکگراند:**
```
{SCENE}, 2D game background, painted, high detail, no text, no watermark
```
- برای side-scroller اضافه کن: `side scroller view, layered depth far mid near`
- برای top-down اضافه کن: `top-down view, symmetrical, playable space in center`

### گام ۲ — اجرا با اسکریپت (تست‌شده ✅)

```bash
# یک آبجکت با پس‌زمینه شفاف
/opt/hermes/.venv/bin/python /data/igris_bot/scripts/gen_asset.py \
    object "single iron sword" -o assets/obj/sword

# یک بکگراند
/opt/hermes/.venv/bin/python /data/igris_bot/scripts/gen_asset.py \
    bg "night medieval city street with torches" -o assets/bg/city_night --ar 16:9

# دسته‌ای: فایل لیست پرامپت (هر خط یک پرامپت، # کامنت)
/opt/hermes/.venv/bin/python /data/igris_bot/scripts/gen_asset.py \
    batch prompts.txt --type object --outdir assets/obj
```

خروجی:
- آبجکت → PNG شفاف، برش‌خورده دور شیء، آپسکیل‌شده 2x
- بکگراند → JPG باکیفیت بدون واترمارک

### گام ۳ — بازبینی کیفیت (چک‌لیست هر تصویر)
- [ ] شیء کامل است، سوراخ یا بریدگی ندارد؟
- [ ] پس‌زمینه واقعاً شفاف/یکدست است؟
- [ ] واترمارک نمانده؟
- [ ] با بقیه‌ی assetها هم‌سبک است؟ (پیشوند پرامپت ثابت بوده؟)
- [ ] بکگراند فضای بازی در مرکز دارد (کاراکتر روی آن خوانا می‌نشیند)؟

رد شد → پرامپت را اصلاح کن (نه تکرار کور) و دوباره بگیر. هر آبجکت را ۲-۳ بار تولید کن و بهترین را نگه دار.

### گام ۴ — ساختار پوشه‌ها و نام‌گذاری
```
assets/
  STYLE.md
  obj/      sword_01.png, potion_01.png ...   (شفاف، نام انگلیسی_شماره)
  bg/       city_night.jpg, forest_day.jpg ...
  tile/     floor_stone.jpg ...
  ui/       btn_attack.png ...
  prompts/  prompts.txt ...
```

### گام ۵ — لایه‌های parallax و tileset
- **Parallax**: یک بکگراند را در ۳ پرامپت جدا بساز — `far layer: sky and mountains only` / `mid layer: buildings, transparent bg` / `near layer: foreground rocks, transparent bg` (لایه‌های جلو با حالت `object` یعنی زمینه ماجنتا).
- **Tileset**: از پرامپت `seamless texture, tileable, top-down` استفاده کن و لبه‌ها را در Pillow با اندازه‌ی بلوک 128/256px ببُر؛ Hotpot.ai گزینه‌ی آماده‌تری برای tileable دارد.

---

## ۴. دام‌های شناخته‌شده (در تست واقعی تأیید شده)

1. **Pollinations 402/500**: با درخواست ساده (بدون پارامتر) بعد از ۱-۲ درخواست پشت‌سرهم یا پرامپت خیلی بلند می‌گیرد. اما URL «اندازه‌دار» (`?width&height&nologo&seed`) تست شد و **کار می‌کند** (کیفیت ۲:۳/۱۶:۹ می‌دهد) و با `seed` تصادفی خروجی تکراری کش نمی‌شود؛ فقط اگر رد شود خودکار به URL ساده برمی‌گردد. فاصله ۶-۸ ثانیه بین درخواست‌ها.
2. **واترمارک Kavel**: حاشیه‌ی پایین ~۷۰px. هیچ‌وقت بدون برش ذخیره نکن؛ خروجی را چشمی چک کن (گاهی لبه‌ی واترمارک بالاتر می‌آید → پایین را ۹۰px هم ببُر).
3. **مدل به «دایره/قاب» علاقه دارد**: اگر آبجکت داخل قاب دایره‌ای افتاد، کلمه‌های `no frame, no circle, no border, isolated` را تقویت کن یا پرامپت را ساده‌تر کن.
4. **chroma-key تهاجمی**: اگر رنگ شبیه ماجنتا داخل خود شیء است، آستانه‌های `lo/hi` اسکریپت (60/130) را پایین بیاور وگرنه شیء سوراخ می‌شود.
5. **عدم یکدستی سبک**: بزرگ‌ترین ریسک پروژه‌های AI-art. تنها راه‌حل: پیشوند پرامپت ثابت + در صورت نیاز آموزش LoRA روی ۵-۱۰ نمونه‌ی منتخب خودمان در Scenario.
6. **/data پارتیشن پر است** (92%) — تصاویر خروجی را در همان `/data/igris_bot/assets/` نگه دار (چند MB مشکلی ندارد) اما venv و کش سنگین را هرگز داخل /data نساز.

---

## ۵. مسیر رشد (اگر بعداً خواستی حرفه‌ای‌تر شود)

1. **یکدستی**: ثبت‌نام در Scenario → آموزش مدل روی سبک منتخب → همه‌ی تولیدها از یک مدل.
2. **انیمیشن**: Makko یا SpriteCook برای walk/attack/idle → خروجی sprite sheet.
3. **حجم بالا**: Ludo یا Layer.ai برای batch بزرگ.
4. **آپسکیل واقعی**: Real-ESRGAN (رایگان، Hugging Face Space) به‌جای LANCZOS وقتی سایز نهایی بالای 2048px خواستی.

---

## ۶. خط لوله کاراکتر (تست‌شده روی ۳ کاراکتر، شهریور ۱۴۰۵)

```
chars.txt (هر خط یک کاراکتر، بدون زمین/کف)
   │  gen_asset.py batch ... --type char --raw   (بدون chroma-key، خام)
   ▼
polish_rembg.py  (اجرا با /opt/data/venvs/rembg/bin/python و HOME=/opt/data)
   [1] rembg/u2netp  → ماسک subject        (مدل کوچک ۴.۶MB فقط روی /opt)
   [2] fill holes    → سوراخ‌های داخل بدنه
   [3] کامپوننت‌ها    → اجزای کوچک (<۱.۵٪) حذف (آشغال/شمشیر افتاده)
   [4] luma erosion  → حلقه‌ی نورانی روشن دور سیاه‌پوش‌ها (luma>118 لبه فقط)
   [5] crop → resize → pixel_snap(گرید ۱۶۰) → quantize 32 رنگ → contrast
   ▼
qa_green.py / کامپوزیت تیره + vision QA   (نمره ≥۶ قبول)
```

**دام‌های ویژه‌ی کاراکتر (تأییدشده):**
- chroma-key/flood برای کاراکتر **بی‌فایده است**: مدل رایگان زمینه‌ی ماجنتا/سفید را رعایت نمی‌کند و صحنه می‌کشد. فقط rembg جواب می‌دهد.
- **halo نورانی**: پرامپت‌های «dark sorceress» همیشه درخشش دور سیاه‌پوش می‌کشند → luma erosion مرحلهٔ ۴ اضافه شد؛ با این حال این کلاس بدترین نمره را می‌گیرد (۳-۴ از ۱۰).
- `chars.txt` خط ۱ کامنت است → برای تولید تکی با `sed -n '4p'` (نه `3p`) خط را بگیر، وگرنه کاراکتر اشتبایی رندر می‌شود.
- Pollinations بدون `seed` همان خروجی قبلی را از کش می‌دهد (دو فایل بایت‌به‌بایت یکسان) → حتماً URL اندازه‌دار با seed بگیر.
- rembg با پیش‌فرض onnxruntime کانتینر را OOM-kill می‌کند (cgroup ~۹۵۴MB) → u2netp + تک‌thread + غیرفعال‌کردن arena (در اسکریپت لحاظ شده) وگرنه exit 137.
- مدل‌ها را با `HOME=/opt/data` دانلود کن؛ `/data` فقط ~۳۸MB جا دارد.

**وضعیت تحویلی فعلی (assets/):**
- `char/000.png` شوالیه (۶/۱۰) — `char/001.png` گردن‌کُش (۶/۱۰) — `char/002.png` قاتل (۶/۱۰)
- `bg/000.jpg` غروب شهر (۶/۱۰) — `bg/001.jpg` قلعه‌ی ماه خون (۷/۱۰) — `bg/002.jpg` کلیسای داخلی (۸/۱۰)
- نمره‌ها با vision QA روی کامپوزیت تیره؛ منبع همه رایگان، بدون کلید.
