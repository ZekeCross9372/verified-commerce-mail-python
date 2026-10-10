import pytest

from commerce_workflow import CheckoutRequest, CommerceWorkflow, LineItem, OrderState
import shop_signup
from commerce_workflow import SignupRequest


class RecordingMail:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_email(self, *, to: str, subject: str, html: str, idempotency_key: str) -> dict:
        self.calls.append({"to": to, "subject": subject, "html": html, "idempotency_key": idempotency_key})
        return {"message_id": "msg_receipt_1"}


def test_verification_controls_checkout_and_receipt_order() -> None:
    mail = RecordingMail()
    workflow = CommerceWorkflow(mail)
    workflow.remember_customer("usr_42", "buyer@example.com")
    checkout = CheckoutRequest(
        order_id="order_7",
        user_id="usr_42",
        items=[LineItem(sku="lamp-blue", quantity=2, unit_price_cents=2500)],
    )

    with pytest.raises(ValueError, match="verification is required"):
        workflow.checkout(checkout)

    workflow.verify_customer("usr_42")
    placed = workflow.checkout(checkout)
    assert placed.state == OrderState.CHECKED_OUT
    assert placed.total_cents == 5000

    fulfilled = workflow.fulfill("order_7")
    receipt = workflow.send_receipt("order_7", "receipt-order-7")
    assert fulfilled.state == OrderState.FULFILLED
    assert receipt.state == OrderState.RECEIPT_SENT
    assert receipt.message_id == "msg_receipt_1"
    assert mail.calls[0]["to"] == "buyer@example.com"


def test_failed_verification_mail_deletes_created_user(monkeypatch) -> None:
    class FailingGateway:
        deleted = None

        def create_user(self, **kwargs):
            return {"user_id": "usr_test_cleanup"}

        def send_email(self, **kwargs):
            raise RuntimeError("mail failed")

        def delete_user(self, user_id):
            self.deleted = user_id

    gateway = FailingGateway()
    monkeypatch.setattr(shop_signup, "gateway", gateway)
    monkeypatch.setattr(shop_signup, "api_key", "test-key")
    monkeypatch.setattr(shop_signup, "workflow", CommerceWorkflow(gateway))
    with pytest.raises(RuntimeError, match="mail failed"):
        shop_signup.signup(
            SignupRequest(email="chenhua@changba.com", password="long-test-password", name="Test"),
            idempotency_key="failed-mail-test",
        )
    assert gateway.deleted == "usr_test_cleanup"
    assert "usr_test_cleanup" not in shop_signup.workflow.customers
