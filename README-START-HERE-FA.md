# GoalPulse Phase 2 — DECIDED MVP delivery pack

**Reference implementation baseline:** read-only Codex audit of current team repository at commit `5186edf`, 2026-11-14. This ZIP contains **documents, tasks, sample policy and Codex prompts, NOT source code fixes or the current repo**. Files must be reviewed/merged into the actual repository, and differences since audited commit must be checked. The project owner delegated MVP product decisions; this pack records the selected decisions. It is **not evidence of a merged ADR or of passing live tests**.

## کار فوری تیم (بدون جلسه طولانی)

1. **Developer 1** در یک Branch جدید این پوشه را به `docs/phase2/` و ADR را به `docs/adr/` منتقل کند. `AGENTS.md` را از نو جایگزین نکند؛ بند کوچک موجود در `docs/phase2/AGENTS-ADDENDUM.txt` را به انتهای فایل فعلی اضافه کند. مستندات قراردادی frozen تغییر نکنند.
2. **این تصمیم‌ها به‌عنوان Product Owner's MVP baseline انتخاب شده‌اند.** D1 فقط یک بررسی سریع اجراپذیری/امنیت و تعارض با کد فعلی انجام دهد و تصمیم‌ها را در `docs/implementation/DECISIONS.md` با همان شناسه‌ها ثبت کند. **اگر موردی از نظر امنیت یا قرارداد در کد واقعی قابل اجرا نیست، جلوی Merge را بگیرد و مسئله را مشخص کند؛ بدون اختراع جایگزین**.
3. چهار نفر هم‌زمان از روی `tasks/phase2/developer-N.md` کار کنند. Dev2 ابتدا Node22 Build و LLM واقعی؛ Dev3 Evidence Policy و Context.dev؛ Dev4 GE worker؛ Dev1 Gateway/Stack.
4. خط اصلی Demo: کاربر یک Goal خرید یک محصول تعریف می‌کند، LLM واقعی سؤال می‌پرسد، کاربر تایید می‌کند، PostgreSQL/RabbitMQ goal را فعال می‌کنند، Context.dev واقعی Evidence می‌دهد، GE tri-state تصمیم می‌گیرد، UI وضعیت مستند را نشان می‌دهد، Verified فقط با شواهد کافی Notification می‌سازد. Agent package در P1 و auto-purchase خارج از محدوده.
5. Demo/prod simulation cannot use silent mock. In CI and deterministic test use explicit fixture labels. All keys remain private in `.env`, never added to repo.

## خروجی‌های اصلی
- `docs/adr/0003-phase2-mvp-decisions.md` — **تصمیم‌های قطعی طراحی** (decision baseline after owner delegation).
- `docs/phase2/ARCHITECTURE.md` — نحوه اتصال هفت سرویس و پایش.
- `docs/phase2/CONTRACT-CHANGE-PLAN.md` — مسیر اضافه‌کردن endpointهای نسخه‌دار بدون شکستن v1.
- `docs/phase2/EXECUTION-GATES.md` — چه زمانی موازی و چه زمانی ادغام.
- `docs/phase2/RELEASE-TESTS.md` — سناریوهای E2E و معیار واقعی بودن.
- `docs/phase2/RUNTIME-ENV.md` — لیست متغیرها و اجرای امن.
- `tasks/phase2/` — Backlog، چهار فایل developer و فایل‌های ریز Issue.
- `prompts/` — پرامپت فعال‌سازی و اجرای یک Task برای Codex/Claude.
- `contracts/phase2/` — decisions و شکل توافق‌شده endpointهای جدید؛ **نه یک API تصویب‌شده داخل Repository تا وقتی PR merged نشده**.

## محدودیت‌ها و حقیقت Demo
- فقط `product_purchase` P0؛ `recurring_procurement` P1؛ سفر با نسخه v1 پشتیبانی نمی‌شود.
- Context.dev هیچ تضمینی برای قیمت، موجودی یا هزینه حمل در همه سایت‌ها نمی‌دهد.
- **`checked` به معنای ارزیابی مستند و تازهٔ اطلاعات عمومی است، نه تضمین قیمت قطعی checkout.** اگر شواهد کامل نیست، به‌جای Verified، Candidate/Unknown نشان دهید.
- هیچ agent یا job مجاز به خرید، پرداخت یا رزرو نیست.

## آغاز توسعه
From the repository root, once docs are merged:

```text
Read AGENTS.md, docs/adr/0003-phase2-mvp-decisions.md, docs/phase2/ARCHITECTURE.md,
tasks/phase2/MASTER-BACKLOG.md and your tasks/phase2/developer-N.md.
Implement only the first dependency-ready P0 task on your branch.
Never modify frozen v1 contracts or another owner's service unilaterally.
Use no real provider calls or persistent DB mutations without explicit permission.
Report evidence per acceptance criterion.
```

**Do not mistake decisions authored here for test results or for actual changes in the team's current source tree.**
