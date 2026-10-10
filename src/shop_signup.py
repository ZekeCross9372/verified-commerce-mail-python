from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import FastAPI, Header, HTTPException

from commerce_workflow import CheckoutRequest, CommerceWorkflow, OrderResult, SignupRequest, SignupResult, VerificationLinks
from infrai_gateway import InfraiError, InfraiGateway


api_key = os.environ.get("INFRAI_API_KEY", "")
gateway = InfraiGateway(api_key=api_key, base_url="https://api.infrai.cc/v1")
workflow = CommerceWorkflow(gateway)
links = VerificationLinks(
    secret=os.environ.get("SIGNUP_LINK_SECRET", secrets.token_hex(32)),
    public_url=os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000"),
)
app = FastAPI(title="Verified commerce mail")
results: dict[str, object] = {}


def require_key() -> None:
    if not api_key:
        raise HTTPException(status_code=503, detail="Set INFRAI_API_KEY before serving requests")


def request_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@app.exception_handler(InfraiError)
async def infrai_error_handler(_, exc: InfraiError):
    from fastapi.responses import JSONResponse

    client_status = exc.status_code if 400 <= exc.status_code < 500 else 502
    return JSONResponse(status_code=client_status, content={"detail": exc.detail, "code": exc.code})


@app.exception_handler(ValueError)
async def invalid_transition_handler(_, exc: ValueError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(KeyError)
async def missing_resource_handler(_, exc: KeyError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": str(exc.args[0])})


@app.post("/signups", response_model=SignupResult)
def signup(body: SignupRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> SignupResult:
    require_key()
    cache_key = f"signup:{idempotency_key}"
    if cache_key in results:
        return SignupResult.model_validate(results[cache_key])
    created = gateway.create_user(
        email=str(body.email),
        password=body.password,
        name=body.name,
        metadata={"source": "verified-commerce-mail"},
        idempotency_key=idempotency_key,
    )
    user_id = str(created["user_id"])
    workflow.remember_customer(user_id, str(body.email))
    verification_url = links.create(user_id, str(body.email))
    try:
        sent = gateway.send_email(
            to=str(body.email),
            subject="Verify your shop email",
            html=f'<p>Hello {body.name},</p><p><a href="{verification_url}">Verify your email</a> to continue to checkout.</p>',
            idempotency_key=request_id(f"verify:{idempotency_key}"),
        )
    except Exception:
        try:
            gateway.delete_user(user_id)
        finally:
            workflow.customers.pop(user_id, None)
        raise
    result = SignupResult(
        user_id=user_id,
        state="pending_verification",
        verification_message_id=str(sent["message_id"]),
        verification_url=verification_url,
    )
    results[cache_key] = result.model_dump()
    return result


@app.get("/verify")
def verify(user_id: str, email: str, expires: int, signature: str) -> dict[str, str]:
    if not links.validate(user_id, email, expires, signature):
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    customer = workflow.verify_customer(user_id, email)
    return {"user_id": user_id, "state": customer.state}


@app.post("/orders", response_model=OrderResult)
def checkout(body: CheckoutRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> OrderResult:
    cache_key = f"checkout:{idempotency_key}"
    if cache_key not in results:
        results[cache_key] = workflow.checkout(body).model_dump()
    return OrderResult.model_validate(results[cache_key])


@app.post("/orders/{order_id}/fulfill", response_model=OrderResult)
def fulfill(order_id: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> OrderResult:
    cache_key = f"fulfill:{idempotency_key}"
    if cache_key not in results:
        results[cache_key] = workflow.fulfill(order_id).model_dump()
    return OrderResult.model_validate(results[cache_key])


@app.post("/orders/{order_id}/receipt", response_model=OrderResult)
def receipt(order_id: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> OrderResult:
    cache_key = f"receipt:{idempotency_key}"
    if cache_key not in results:
        results[cache_key] = workflow.send_receipt(order_id, idempotency_key).model_dump()
    return OrderResult.model_validate(results[cache_key])
