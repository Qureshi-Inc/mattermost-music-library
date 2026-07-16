"""Firebase Cloud Messaging push notifications.

Initializes the Firebase Admin SDK from a service-account key and provides
a helper to send pushes to a list of device tokens. All failures are logged
and swallowed so a push problem never breaks the calling request.
"""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False
_available = False


def _ensure_init() -> bool:
    """Initialize firebase_admin once. Returns True if usable."""
    global _initialized, _available
    if _initialized:
        return _available

    _initialized = True
    try:
        import firebase_admin
        from firebase_admin import credentials

        cred_path = os.environ.get(
            "FIREBASE_CREDENTIALS", "/app/data/firebase-service-account.json"
        )
        if not os.path.exists(cred_path):
            logger.warning("FCM disabled: credentials not found at %s", cred_path)
            _available = False
            return False

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(cred_path))
        _available = True
        logger.info("Firebase Admin initialized for FCM")
    except Exception as e:
        logger.warning("FCM disabled: init failed: %s", e)
        _available = False
    return _available


def send_push(tokens: list[str], title: str, body: str, data: dict | None = None) -> None:
    """Send a push notification to the given device tokens.

    Returns the list of tokens that are invalid/unregistered so the caller
    can prune them (returns nothing on total failure — never raises).
    """
    if not tokens:
        return
    if not _ensure_init():
        return

    try:
        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            android=messaging.AndroidConfig(priority="high"),
        )
        response = messaging.send_each_for_multicast(message)
        logger.info(
            "FCM push sent: %d success, %d failure",
            response.success_count,
            response.failure_count,
        )
    except Exception as e:
        logger.warning("FCM push failed: %s", e)
