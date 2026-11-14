# راهنمای تحویل طراحی کامل GoalPulse به تیم ۴ نفره

**وضعیت:** طراحی کامل برای شروع پیاده‌سازی؛ هیچ‌کدام از هفت میکروسرویس هنوز صرفاً با وجود این فایل‌ها پیاده‌سازی نشده‌اند. قراردادهای قبلی v1 دست‌نخورده‌اند. دو OpenAPI جدید فعلاً پیشنهادی‌اند و باید تأیید شوند.

## راه‌اندازی برنامه توسعه

۱. `AGENTS.md` و `docs/implementation/README.md` را در جلسه مشترک مرور کنید.
۲. Developer 1 پیشنهاد API داخلی `contracts/openapi/internal.proposed.v1.yaml` را با Developerهای ۲، ۳ و ۴ نهایی و ICP-01/08 را ثبت کند. تا پیش از تأیید، همه با Mockهای مشترک کار کنند.
۳. همه فایل تسک خودشان را باز کنند: `tasks/developer-1.md` تا `developer-4.md`.
۴. هر Task یک Branch جدا داشته باشد؛ PR و تست اجباری باشد؛ هیچ Agentای قراردادهای v1 یا سرویس شخص دیگری را بی‌اجازه تغییر ندهد.
۵. در اولین نقطه ادغام با داده Mock، یک هدف خرید تک‌محصول را از مکالمه تا Notification اجرا کنید.
۶. بعد Context.dev واقعی را وصل کنید، داده‌های دارای زمان/منبع/اعتبار ذخیره کنید و تست‌های بلیت/قیمت نامعلوم یا موجودی نامعلوم را درست پوشش دهید.
۷. پس از پایدارشدن مسیر اصلی، خرید ماهانه سبد کافه، داشبورد و Package برای Agent خارجی را اضافه کنید.

## مالکیت و تسک‌ها

| نفر | سرویس‌ها | شناسه‌ها | تعداد |
|---|---|---|---|
| Developer 1 | Goal Management، Gateway، Infra و Integration | D1-PL, D1-GM, D1-GW, D1-INT, D1-DOC | ۱۲ |
| Developer 2 | Goal Intelligence + Frontend | D2-GI, D2-FE | ۹ |
| Developer 3 | Discovery & Monitoring، Data & Evidence | D3-DM, D3-DE | ۱۱ |
| Developer 4 | Goal Evaluation، Notification، Agent Integration | D4-GE, D4-NS, D4-AI | ۱۲ |
| **جمع** | **۷ میکروسرویس + Gateway و UI** | | **۴۴** |

## فایل‌ها برای هر Coding Agent

- پایه: `AGENTS.md` + `docs/implementation/README.md`
- دقیق: `docs/implementation/0N-name.md` مربوط به سرویس خودش
- تسک: `tasks/developer-N.md` + شناسه تسک
- قراردادهای مرجع: `contracts/schemas/`, `contracts/openapi/gateway.v1.yaml`, `contracts/event-routing.yaml`
- رابط داخلی (فقط بعد از تأیید): `contracts/openapi/internal.proposed.v1.yaml`
- قالب Prompt: `docs/implementation/coding-agent-task-usage.md`

## چه کاری اول و به‌صورت تک‌نفره انجام می‌شود؟

**Developer 1 با تأیید جمعی:** ثبت تصمیم‌های ICP-01 و ICP-08، قراردادهای داخلی و هویت. همه تسک‌های API وابسته به این گیت‌اند؛ فایل‌های مشترک نباید هم‌زمان توسط چند Agent ویرایش شوند.

## بعد از آن چه کارهایی موازی‌اند؟

- Dev 1: پایگاه‌های جدا، Goal Management، JWT و Gateway.
- Dev 2: مکالمه با LLM، Draft، تأیید هدف، Frontend.
- Dev 3: اتصال Context.dev، منبع مشترک، Observation، Webhook.
- Dev 4: ارزیابی قطعی با Mock، سپس Notification و Handoff.

## سه نقطه کنترل کلیدی

- **Gate A:** قراردادهای داخلی و احراز هویت تأییدشده، تست JSON Schema موفق.
- **Gate B:** مسیر کامل با Mock Data موفق؛ هنوز وابسته به Context.dev نیست.
- **Gate C:** منبع واقعی Context.dev و شواهد تازه، با تفکیک `unknown` از `satisfied`.

پس از آن Gate D برای Monitor+Notification+Handoff و Gate E برای سبد چندکالایی و دموی نهایی است.

## دو ریسک مهم

۱. `Observation.v1` فقط `delivery_to_fi` دارد؛ بنابراین نسخه اول برای ارسال به فنلاند طراحی شده و Travel خارج از Scope قابل‌اجرای این قرارداد است.
۲. برای Pause/Resume و ویرایش Goal هنوز Event جدیدی در نسخه v1 وجود ندارد. برای MVP از Reconciliation و بررسی وضعیت Goal در لحظه اعلان استفاده کنید؛ Event جدید را خودسرانه ایجاد نکنید.

## تست و معیار تمام‌شدن طراحی

```bash
python scripts/validate_contracts.py
python -m pytest -q
python scripts/build_task_index.py
```

روی ماشین آماده Docker، Health Check عملی را هم انجام دهید. پاس‌شدن تست قرارداد به معنی اجراشدن هفت میکروسرویس نیست.
