import logging
from types import SimpleNamespace

from app.dingtalk import SafeSDKLogFilter, connection_state


def test_sdk_logger_does_not_expose_connection_tickets_or_exception_text():
    guard = SafeSDKLogFilter()
    info = logging.LogRecord("sdk", logging.INFO, "", 0, "endpoint %s", ({"ticket":"private-ticket"},), None)
    assert guard.filter(info) is False
    warning = logging.LogRecord("sdk", logging.WARNING, "", 0, "raw message %s", ("private-message",), (ValueError,ValueError("private-key"),None))
    warning.exc_text = "private-key"
    assert guard.filter(warning)
    assert "private" not in warning.getMessage()
    assert warning.exc_info is None and warning.exc_text is None


def test_connection_status_requires_open_websocket():
    assert connection_state(SimpleNamespace(websocket=None)) == "connecting"
    assert connection_state(SimpleNamespace(websocket=SimpleNamespace(state=SimpleNamespace(name="OPEN")))) == "connected"
    assert connection_state(SimpleNamespace(websocket=SimpleNamespace(state=SimpleNamespace(name="CLOSED")))) == "connecting"
