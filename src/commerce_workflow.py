from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class MailBoundary(Protocol):
    def send_email(self, *, to: str, subject: str, html: str, idempotency_key: str) -> dict:
        return {}


class CustomerState(StrEnum):
    PENDING = "pending_verification"
    VERIFIED = "verified"


class OrderState(StrEnum):
    CHECKED_OUT = "checked_out"
    FULFILLED = "fulfilled"
    RECEIPT_SENT = "receipt_sent"


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=12)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def email_has_address_shape(cls, value: str) -> str:
        local, separator, domain = value.strip().partition("@")
        if not separator or not local or "." not in domain:
            raise ValueError("Enter a valid email address")
        return value.strip()


class SignupResult(BaseModel):
    user_id: str
    state: CustomerState
    verification_message_id: str
    verification_url: str


class LineItem(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)


class CheckoutRequest(BaseModel):
    order_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    items: list[LineItem] = Field(min_length=1)


class OrderResult(BaseModel):
    order_id: str
    state: OrderState
    total_cents: int
    message_id: str | None = None


@dataclass
class Customer:
    user_id: str
    email: str
    state: CustomerState


@dataclass
class Order:
    order_id: str
    user_id: str
    total_cents: int
    state: OrderState
    message_id: str | None = None


class CommerceWorkflow:
    def __init__(self, mail: MailBoundary) -> None:
        self.mail = mail
        self.customers: dict[str, Customer] = {}
        self.orders: dict[str, Order] = {}

    def remember_customer(self, user_id: str, email: str) -> None:
        self.customers[user_id] = Customer(user_id, email, CustomerState.PENDING)

    def verify_customer(self, user_id: str, email: str | None = None) -> Customer:
        customer = self._customer(user_id)
        if email is not None and customer.email != email:
            raise ValueError("Verification link does not match customer")
        customer.state = CustomerState.VERIFIED
        return customer

    def checkout(self, request: CheckoutRequest) -> OrderResult:
        customer = self._customer(request.user_id)
        if customer.state != CustomerState.VERIFIED:
            raise ValueError("Email verification is required before checkout")
        total = sum(item.quantity * item.unit_price_cents for item in request.items)
        order = Order(request.order_id, request.user_id, total, OrderState.CHECKED_OUT)
        self.orders[request.order_id] = order
        return self._result(order)

    def fulfill(self, order_id: str) -> OrderResult:
        order = self._order(order_id)
        if order.state != OrderState.CHECKED_OUT:
            raise ValueError("Only a checked-out order can be fulfilled")
        order.state = OrderState.FULFILLED
        return self._result(order)

    def send_receipt(self, order_id: str, idempotency_key: str) -> OrderResult:
        order = self._order(order_id)
        if order.state == OrderState.RECEIPT_SENT:
            return self._result(order)
        if order.state != OrderState.FULFILLED:
            raise ValueError("Receipt delivery follows fulfillment")
        customer = self._customer(order.user_id)
        data = self.mail.send_email(
            to=customer.email,
            subject=f"Receipt for order {order.order_id}",
            html=f"<p>Your order <strong>{order.order_id}</strong> is fulfilled.</p><p>Total: {order.total_cents} cents.</p>",
            idempotency_key=idempotency_key,
        )
        order.message_id = str(data["message_id"])
        order.state = OrderState.RECEIPT_SENT
        return self._result(order)

    def _customer(self, user_id: str) -> Customer:
        if user_id not in self.customers:
            raise KeyError("Customer not found")
        return self.customers[user_id]

    def _order(self, order_id: str) -> Order:
        if order_id not in self.orders:
            raise KeyError("Order not found")
        return self.orders[order_id]

    @staticmethod
    def _result(order: Order) -> OrderResult:
        return OrderResult(
            order_id=order.order_id,
            state=order.state,
            total_cents=order.total_cents,
            message_id=order.message_id,
        )


class VerificationLinks:
    def __init__(self, secret: str, public_url: str, ttl_seconds: int = 1800) -> None:
        self.secret = secret.encode()
        self.public_url = public_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    def create(self, user_id: str, email: str) -> str:
        expires = int(time.time()) + self.ttl_seconds
        message = f"{user_id}:{email}:{expires}"
        signature = hmac.new(self.secret, message.encode(), hashlib.sha256).hexdigest()
        return f"{self.public_url}/verify?user_id={user_id}&email={email}&expires={expires}&signature={signature}"

    def validate(self, user_id: str, email: str, expires: int, signature: str) -> bool:
        if expires < int(time.time()):
            return False
        message = f"{user_id}:{email}:{expires}"
        expected = hmac.new(self.secret, message.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
