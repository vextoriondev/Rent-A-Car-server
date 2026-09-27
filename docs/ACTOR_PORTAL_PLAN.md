# Actor accounts (owner / driver / customer login) — MVP portal

Trimmed slice of `BACKEND_MODEL_PLAN.md` milestones **M1** ("Identities, actors...") and **M10** ("Actor portal API"), pulled forward ahead of schedule at the user's request. Spans both repos — backend paths below are relative to this repo (`Rent-A-Car-server/`); frontend paths are relative to the sibling repo `Rent-A-Car-client/`.

## Context

The app has one kind of login today: staff (`Membership` role admin/accountant/operations/viewer), just wired up with a real register/login flow (`rentals/api.py` `/auth/login`, `/auth/register`, `/auth/logout`; frontend `app/(auth)/login`, `app/(auth)/register`). The user's actual ask is different: the three *parties* on an agreement — owner, driver, customer — should each be able to log in and see only their own numbers. Today a `Party` (owner/driver/customer) is just a name+phone+email row staff types in; there's no login attached to it at all. The existing "Actor portal preview" page (`Rent-A-Car-client/app/(dashboard)/actor-preview/page.tsx`) is explicitly a **staff-only mockup** ("Not an authenticated portal") reading from the local demo store — it proves the intended UI shape but isn't real.

`docs/BACKEND_MODEL_PLAN.md` already tracks this as its own future milestone, deliberately deferred behind messaging (M7): *"Owners/drivers log in, or messages only? → Messages first (M7); portal later (M10)."* The user has now confirmed the shape they want, ahead of that schedule:

- **Only admin staff create actor profiles** — no self-registration. Confirmed: *"only admin can create profiles and assign them... i think i can do the auth staff from the django admin."*
- **Credential: username + password**, issued via the Django admin (no invite-link/OTP flow to build).

Given that, this plan implements a trimmed, safe slice of M1+M10: enough for a real owner/driver/customer login that sees *only* their own statement, built by reusing the existing (already-tested, invariant-preserving) `services.statement()` — no changes to money logic at all, just a new identity link and a projection/filter of existing output.

## Backend changes

**`rentals/models.py`** — add one new model, purely additive:
```python
class PartyUser(models.Model):
    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name='login')
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rentflow_party')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```
One party ↔ one login user (matches "the profile *is* the login," per the user's mental model). Run `makemigrations`/`migrate` against the Neon dev branch — additive only, no data migration needed.

**`rentals/admin.py`** — register `PartyUser` alongside the existing plain `admin.site.register(...)` calls (matches the file's current style: no custom ModelAdmin elsewhere). Staff flow: create the `User` (username+password) on Django's built-in `/admin/auth/user/add/` as today, then link it to the right `Party` via `/admin/rentals/partyuser/add/`. Add `list_display=('party','user','active')` for findability — everything else stays default.

**`rentals/api.py`**:
1. Import `PartyUser`.
2. Add `actor_party(request)` helper: looks up `PartyUser.objects.filter(user=request.user, active=True).select_related('party')`; raises 403 if none.
3. Extend `/me` (additive field, byte-compatible with the existing `organizations` field per the plan doc's own rule):
   ```python
   party=PartyUser.objects.filter(user=request.user,active=True).select_related('party').first()
   ...'party':{'id':party.party_id,'kind':party.party.kind,'name':party.party.name,'organization_id':party.party.organization_id} if party else None
   ```
4. New endpoint `GET /portal/statement?month=<date>`: resolves the actor's party, finds their agreements by kind (`vehicle__owner=party` for owners, `driver=party`, `customer=party`), finds each agreement's `Period` for that month, calls the **unmodified** `services.statement(period)`, and returns only the slice for that party's kind (`data['owner']` / `data['driver']` / `data['customer']`) — **never** `data['operator']`, **never** the other two parties' blocks. This is the security boundary the plan doc calls out explicitly ("a driver never sees customer rent or owner terms").

**`rentals/management/commands/seed_demo.py`** (verification aid, dev-only) — after creating the three demo parties, also create one `User`+`PartyUser` per party (e.g. `owner_demo`/`driver_demo`/`customer_demo`, shared demo password) so the security boundary can be verified against the known worked-example numbers (customer due ৳30,000 / owner due ৳12,000 / driver due ৳5,000) without hand-creating accounts in Django admin. Clearly a dev/test convenience, not a production path.

## Frontend changes (`Rent-A-Car-client/`)

**`lib/api.ts`**:
- Add `ActorAccountError` class (thrown by `liveSnapshot` when `/me` succeeds but the user has a `party`, not a staff org — signals "this is an actor, not staff").
- Update `liveSnapshot()`: when no admin/accountant org is found, check `me.party` — if present, throw `ActorAccountError` instead of the current generic "sign in to Django admin" message.
- Add `portalStatement(month:string)` → `apiRequest('/portal/statement?month='+month)`.

**`lib/store-context.tsx`** — in the existing `liveSnapshot().catch(...)` (already redirects to `/login` on 401 from earlier work), add: `if(e instanceof ActorAccountError){router.replace('/portal');return}`. So a staff dashboard visit by an actor account now bounces to their portal instead of dead-ending on an error banner.

**`app/(auth)/login/page.tsx`** — after `login()` succeeds, fetch `/me` once and route based on its shape: staff org present → `/`, else `party` present → `/portal`, else show an error ("account not linked to a workspace or profile — contact your administrator"). Replaces the current unconditional `router.push('/')`.

**New `app/(portal)/portal/page.tsx`** — the real actor page (replaces nothing; the old `actor-preview` staff mockup is untouched and stays where it is). On mount: fetch `/backend/me` (redirect to `/login` on 401; redirect to `/` if it turns out to be a staff account) and `/backend/portal/statement?month=2026-09-01` (same hardcoded September-2026 period the rest of the app uses). Renders using the **existing** `.phone-frame`/`.phone-head`/`.phone-content`/`.phone-greeting`/`.phone-card`/`.phone-due`/`.phone-lines` CSS (already isolated to this one feature, safe to reuse) fed by the real API response instead of the demo store — one card per agreement, labelled by kind (owner/driver/customer), showing only that party's due/paid/net figures. Includes a sign-out button (reuses the existing `logout()` helper from `lib/api.ts`).

## Out of scope (explicitly, per the user's answers and to keep this a safe slice)

- No self-registration for actors, no invite links, no OTP/magic-link.
- No changes to `services.py` money logic — the portal endpoint only projects existing `statement()` output.
- No multi-agreement aggregation across periods, no messages/inbox (M7), no documents (M8) — just the current month's statement, matching what `actor-preview` already promised visually.
- `docs/BACKEND_MODEL_PLAN.md` checkboxes for M1/M10 stay unchecked (this is a deliberately trimmed slice, not the full milestone — no `Branch`, no `policies.py`, no multi-role-per-user).

## Verification

1. `python manage.py makemigrations` (expect exactly one new migration for `PartyUser`) → `migrate` against Neon.
2. Re-run `seed_demo --username admin`; confirm it now also creates the three demo actor logins.
3. `manage.py check`.
4. Curl-verify the security boundary directly against the backend: log in as `owner_demo`, hit `/api/v1/me` (expect `party.kind=="owner"`), hit `/api/v1/portal/statement?month=2026-09-01` (expect only an `owner` block with `due: "12000.00"`, no `customer`/`driver`/`operator` keys anywhere in the response). Repeat for `driver_demo` (due 5000, no customer/owner data) and `customer_demo` (due 30000, no owner/driver data).
5. `npm run typecheck && npm run build` on the frontend (`Rent-A-Car-client/`).
6. Browser: sign in as `owner_demo` at `/login` → confirm redirect to `/portal` and the page shows only owner figures. Sign in as `admin` → confirm still redirects to `/` (no regression on the staff flow just built).
