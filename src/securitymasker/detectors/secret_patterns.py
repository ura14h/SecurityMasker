"""Built-in developer-secret patterns (§15).

Secrets default to the ``environment_reference`` profile + ``env_reference`` restore
policy, so their real value is never returned to the client — generated code uses
``${SECURITYMASKER_SECRET_...}`` instead (§9.9, §10, §27). These live behind the
``RegexDetector`` interface; an external secret scanner can be added as another
detector later (§15) without touching the core.
"""

from __future__ import annotations

import re

from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy

_ENV = ReplacementProfile.ENVIRONMENT_REFERENCE.value
_ENV_RESTORE = RestorePolicy.ENV_REFERENCE.value
_PRIORITY = 200

# Order does not matter here; central overlap resolution keeps the longest/highest.
# Every pattern is prefix/format-anchored (not bare entropy) to keep precision high
# and avoid flagging ordinary hashes, commit ids, or fixtures (doc/06 P1-4).
SECRET_PATTERNS: list[RegexEntry] = [
    RegexEntry(r"sk-ant-[A-Za-z0-9_-]{20,}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.99),
    RegexEntry(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.95),
    RegexEntry(r"gh[pousr]_[A-Za-z0-9]{20,}", EntityType.OAUTH_TOKEN.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.97),
    RegexEntry(r"github_pat_[A-Za-z0-9_]{22,}", EntityType.OAUTH_TOKEN.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.97),
    RegexEntry(r"AKIA[0-9A-Z]{16}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.97),
    RegexEntry(r"ASIA[0-9A-Z]{16}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.9),
    # Slack bot/user/app tokens and incoming-webhook URLs.
    RegexEntry(r"xox[baprs]-[A-Za-z0-9-]{10,}", EntityType.OAUTH_TOKEN.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.97),
    RegexEntry(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}",
               EntityType.OAUTH_TOKEN.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.95),
    # Stripe live/test secret and restricted keys.
    RegexEntry(r"[rs]k_(?:live|test)_[A-Za-z0-9]{16,}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.97),
    # Google API key and OAuth access token.
    RegexEntry(r"AIza[0-9A-Za-z_-]{35}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.95),
    RegexEntry(r"ya29\.[0-9A-Za-z_-]{20,}", EntityType.OAUTH_TOKEN.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.9),
    # Package-registry tokens.
    RegexEntry(r"npm_[A-Za-z0-9]{36}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.95),
    RegexEntry(r"pypi-AgEIcHlwaS[A-Za-z0-9_-]{20,}", EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.95),
    RegexEntry(
        r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}",
        EntityType.JWT.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.9,
    ),
    RegexEntry(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
        EntityType.PRIVATE_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.99, flags=re.MULTILINE,
    ),
    RegexEntry(
        r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s\"']+",
        EntityType.DB_CONNECTION_STRING.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.9,
    ),
    # Azure Storage account key inside a connection string (mask only the value).
    RegexEntry(
        r"AccountKey=(?P<k>[A-Za-z0-9+/]{40,}={0,2})",
        EntityType.API_KEY.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.9, group=1,
    ),
    # Basic-auth URL: mask only the credentials group, keep the rest structural.
    RegexEntry(
        r"https?://(?P<cred>[^\s:@/]+:[^\s:@/]+)@[^\s/]+",
        EntityType.PASSWORD.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.9, group=1,
    ),
    # Context-gated assignment: a secret-ish key name followed by a long value.
    # Masks only the value group; the key name anchors it against false positives.
    RegexEntry(
        r"(?i)(?:api[_-]?key|secret|token|password|access[_-]?key)\s*[=:]\s*['\"]?"
        r"(?P<val>[A-Za-z0-9_\-]{16,})",
        EntityType.GENERIC_SECRET.value, _ENV, _ENV_RESTORE, _PRIORITY, 0.85, group=1,
    ),
]


def build_secret_detector() -> RegexDetector:
    return RegexDetector(SECRET_PATTERNS, name="secret_patterns")
