# SaaS billing contract

Billing belongs to the SaaS control plane. Existing tenants retain access until
explicit subscription enrollment. Operators configure names, positive prices,
currencies and monthly/annual intervals; no default commercial prices are seeded.
Only active superusers with the existing OTP gate manage plans. Tenant HR model
permissions confer no billing administration rights.

## Plan catalog

Store integer minor units, accepting decimal input without rounding. Supported
Paystack currencies use two decimal places. Publishing is an explicit CSRF-protected
POST to Paystack's fixed API host, with server-side credentials and finite timeout.
Freeze commercial terms when publication begins. Archive plans instead of deleting
provider-linked records. A timeout or malformed response leaves publication uncertain:
never blindly repeat a POST that may already have succeeded. Reconcile against the
Paystack dashboard using the explicit verify-and-link action, which fetches an
existing plan and checks its terms and environment before linking it. No provider payloads or credentials
are logged. Local drafts can be edited without configuring a Paystack account.

## Remaining subscription delivery

Tenant-primary-admin checkout must use server-side pricing and trusted callback URLs.
Callbacks and signed webhooks must verify the provider transaction, exact price,
currency, customer and environment before idempotent activation. Duplicate events
must never extend service twice. Keep paid access through its paid-through date when
canceling renewal. Payment cannot override a manual suspension or deletion.
Subscription, payment ledger, notifications, trial expiry and feature enforcement
remain separate verifiable slices; the catalog alone does not enable these behaviors.

Provider reference: https://paystack.com/docs/api/plan
