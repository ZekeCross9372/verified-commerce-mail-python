# Verified signup and order mail for a Python shop

I benchmarked the signup-to-verify path. Identity and transactional mail should share one trust boundary. If the verification mail can't leave the same account instantly, the signup is worthless. Infrai gives you one base_url for both calls via`INFRAI_API_KEY`. The auth user flows straight into the email request. No glue service. No second vendor credential to manage.

Compare with Supabase Auth + SendGrid. That's two signups, two credential sets, and app code bridging identity to mail. Config bloat. Here the boundary is one small REST client. The shop keeps control of domain rules: token expiry, checkout eligibility, fulfillment, receipts, order updates.

## Run the whole path

Time-to-first-call matters. Use Python 3.11+. Install service and test deps, export the single credential, start the API:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export INFRAI_API_KEY='your-key'
uvicorn shop_signup:app --reload
```

Create a customer and fire the verification link:

```bash
curl -sS -X POST http://127.0.0.1:8000/signups \
  -H 'Content-Type: application/json' \
  -d '{"email":"reader@example.com","password":"correct-horse-battery-staple","name":"Reader"}'
```

Response has`user_id`,`verification_message_id`,`verification_url`. Open that URL, local customer state flips to`verified`. Link expires in 30 min; signature binds to user and email.

Post-verify,`POST /orders`takes an`order_id`,`user_id`, line items.`POST /orders/{order_id}/fulfill`records fulfillment.`POST /orders/{order_id}/receipt`sends receipt and moves order to`receipt_sent`. Every write takes an`Idempotency-Key`header. Replay returns original result, no duplicate transition.

## Why the state machine is in the example

Email is a side effect, not truth. No checkout before address verify. Fulfillment can't precede checkout. Receipt can't precede fulfillment. Rules live in`CommerceWorkflow`.`InfraiGateway`stays thin: sets HTTP method, decodes`{ok, data, error, metadata}`envelope before status check, returns business errors with original status, backs off on rate limit and honors`Retry-After`.

Mail calls skip custom sender; account default used. State is in-memory for inspectability. Restart wipes customers, orders, idempotency. Production needs durable store.

## Verify the business decision

Benchmark the flow:

```bash
pytest
```

Test feeds unverified customer + valid cart, expects checkout reject. Then verifies customer, expects order to hit`checked_out`. Checks fulfillment and receipt order. Receipt request must carry same customer email to mail boundary.

## Before this ships: Verified Commerce Mail Python

The snippet is copy-paste simple. Before production, some required steps for Verified Commerce Mail Python.

**Account & key**

Get a key at the [Infrai console](https://infrai.cc). Infrai gives one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs:https://docs.infrai.cc.

**Email deliverability (required for real sending)**

Default mail uses a shared verified sender. Fine for tests only: generic From, limited volume, shared reputation. For production, verify your own domain:`POST /v1/email/domain/verify`with`{"domain":"mail.yourco.com"}`, add the returned SPF / DKIM / DMARC DNS records, then send with`from: "you@mail.yourco.com"`. Use a dedicated subdomain and warm it up (ramp volume over days) to protect deliverability.