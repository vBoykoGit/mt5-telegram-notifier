"""Windows Firewall helper: add inbound rule for webhook port.

Requires administrator rights. On non-Windows or if the rule cannot be added,
returns (False, message) so the app can show a hint without failing.
"""

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

RULE_NAME = "MT5 Notifier Webhook"
TIMEOUT_SEC = 10


def ensure_firewall_rule(port: int) -> tuple[bool, str]:
    """Add or update a Windows Firewall inbound rule for TCP on the given port.

    On non-Windows (e.g. Linux/macOS) does nothing and returns (True, "").
    On Windows: deletes existing rule with RULE_NAME (if any), then adds a new
    rule allowing TCP inbound on `port`. Requires administrator privileges;
    if elevation is missing, returns (False, "Запустите приложение от имени администратора")
    or a short error message.

    Returns:
        (True, "") on success or on non-Windows.
        (False, "error message") on failure.
    """
    if sys.platform != "win32":
        return True, ""

    if port <= 0 or port > 65535:
        return False, "Недопустимый порт"

    try:
        delete_cmd = [
            "netsh", "advfirewall", "firewall", "delete", "rule",
            f"name={RULE_NAME}",
        ]
        subprocess.run(
            delete_cmd,
            capture_output=True,
            timeout=TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        log.warning("Firewall delete rule timed out")
        return False, "Таймаут брандмауэра"
    except Exception as exc:
        log.debug("Firewall delete rule: %s", exc)

    try:
        add_cmd = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={RULE_NAME}",
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={port}",
        ]
        result = subprocess.run(
            add_cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip() or "Недостаточно прав"
            if "access is denied" in err.lower() or "отказано" in err.lower():
                return False, "Запустите приложение от имени администратора"
            log.warning("Firewall add rule failed: %s", err)
            return False, err[:80]
        log.info("Firewall rule added for TCP port %d", port)
        return True, ""
    except subprocess.TimeoutExpired:
        log.warning("Firewall add rule timed out")
        return False, "Таймаут брандмауэра"
    except FileNotFoundError:
        return False, "netsh не найден"
    except Exception as exc:
        log.exception("Firewall add rule error")
        return False, str(exc)[:80]
