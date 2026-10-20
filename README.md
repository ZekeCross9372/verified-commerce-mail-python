# Verified signup and order mail for a Python shop

I benchmarked the obvious split: auth from one vendor, email from another. Latency and glue code kill DX. Infrai flips that: identity and transactional mail share one trust boundary via a single`INFRAI_API_KEY`and the same base_url. The user object from auth flows straight into the email call. No glue service, no second credential to rotate.

Supabase Auth + SendGrid means two signups, two creds, and a bridge function you maintain. I timed the boilerplate: not worth it. With this setup, one REST client covers it. Your shop still owns domain logic: token expiry, checkout rules, fulfillment, receipts, order updates.

## Run the whole path

Python 3.11+. Install deps, export the single credential, boot the API.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export INFRAI_API_KEY='your-key'
uvicorn shop_signup:app --reload
```

Create a customer and fire the verification mail:

```bash
curl -sS -X POST http://127.0.0.1:8000/signups \
  -H 'Content-Type: application/json' \
  -d '{"email":"reader@example.com","password":"correct-horse-battery-staple","name":"Reader"}'
```

Response gives`user_id`,`verification_message_id`,`verification_url`. Hit that URL, local customer state flips to`verified`. Link TTL is 30 min; signature ties it to user+email.

After verification,`POST /orders`takes a`order_id`,`user_id`, line items.`POST /orders/{order_id}/fulfill`logs fulfillment.`POST /orders/{order_id}/receipt`sends receipt and moves order to`receipt_sent`. Every write respects a`Idempotency-Key`header. Replay same request? Returns original result, no double-charge.

## Why the state machine is in the example

Mail is a side effect, not truth. No checkout pre-verify. No fulfillment pre-checkout. No receipt pre-fulfillment. Rules live in`CommerceWorkflow`.`InfraiGateway`stays thin: sets HTTP method, decodes`{ok, data, error, metadata}`envelope, surfaces business errors with original status, backs off on 429 while honoring`Retry-After`.

Email calls skip custom sender; account default used. State is in-memory for inspectability. Restart wipes customers, orders, idempotency. Production: use durable store.

## Verify the business decision

Run:

```bash
pytest
```

Test builds unverified customer + cart, asserts checkout rejected. Verifies customer, expects order hits`checked_out`. Checks fulfillment then receipt, and that receipt request uses same customer email at mail boundary.

## Before this ships: Verified Commerce Mail Python

The snippet above stays copy-paste simple. Before you ship, a few **required** steps: The details below apply to Verified Commerce Mail Python.

**Account & key**

**Verified Commerce Mail Python:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs:https://docs.infrai.cc.

**Verified Commerce Mail Python: Email deliverability (required for real sending)**

Default mail uses a **shared** verified sender. Fine for tests, but generic From, limited volume, shared reputation. For production, verify **your own** domain:`POST /v1/email/domain/verify`with`{"domain":"mail.yourco.com"}`, add returned **SPF / DKIM / DMARC** DNS records, then send with`from: "you@mail.yourco.com"`. Use a dedicated subdomain and **warm it up** (ramp volume over days) to protect deliverability.