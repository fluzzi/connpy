"""Pure authentication, composition, masking, sanitization, and rate-limit helpers."""

from __future__ import annotations

import ast
import json
import math
import re
import threading
import time
import traceback
from collections import OrderedDict
from collections.abc import Callable, Iterable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NoReturn

from connpy.services.exceptions import ConnpyError

MAX_FALLBACK_ENTRIES = 5
MAX_PROFILE_DEPTH = 5
MAX_COMPOSE_VARIANTS = 10
MAX_COMPOSE_EXPR_LEN = 4096
MAX_AUTH_STREAM_BYTES = 16384
MAX_TRACKER_ENTRIES = 10000
DEFAULT_MAX_AUTH_PROMPTS = 2
DEFAULT_MAX_TOTAL_AUTH_ATTEMPTS = 6
DEFAULT_MAX_HOP_HOST_ATTEMPTS = 6
MAX_EOF_FALLBACK_ADVANCES = 2
DEFAULT_TRACKER_TTL_SECONDS = 300

MIN_AUTH_PROMPTS = 1
MAX_AUTH_PROMPTS_CAP = 5
MIN_TOTAL_AUTH_ATTEMPTS = 1
MAX_TOTAL_AUTH_ATTEMPTS_CAP = 10
MIN_HOP_HOST_ATTEMPTS = 1
MAX_HOP_HOST_ATTEMPTS_CAP = 10
MAX_JSON_INPUT_BYTES = 8192
MAX_JSON_INPUT_DEPTH = 4

SAFE_ERROR_TEMPLATES: dict[str, str] = {
    "AUTH_FAILED": "[AUTH_FAILED] Authentication failed",
    "AUTH_TIMEOUT": "[AUTH_TIMEOUT] Authentication timed out",
    "JUMP_AUTH_FAILED": "[JUMP_AUTH_FAILED] Jumphost authentication failed",
    "TRANSPORT_ERROR": "[TRANSPORT_ERROR] Transport connection failed",
    "HOST_BUDGET_EXHAUSTED": "[HOST_BUDGET_EXHAUSTED] Host authentication attempt budget exhausted",
    "USER_BUDGET_EXHAUSTED": "[USER_BUDGET_EXHAUSTED] User authentication attempt budget exhausted",
    "EOF_BUDGET_EXHAUSTED": "[EOF_BUDGET_EXHAUSTED] EOF fallback advance budget exhausted",
    "NO_CREDS": "[NO_CREDS] No usable authentication credentials available",
    "SPAWN_ERROR": "[SPAWN_ERROR] Failed to spawn transport process",
}

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
_PROFILE_REF_BODY_RE = re.compile(r"[A-Za-z0-9_.$:/-]+", re.ASCII)
_RSA_BLOB_RE = re.compile(r"""^b(["']).+\1$""", re.DOTALL)
_INSECURE_HOSTKEY_RE = re.compile(
    r"(?:^|\s)(?:-o\s*)?stricthostkeychecking\s*=\s*no(?:\s|$)",
    re.IGNORECASE,
)

_NON_SECRET_KEYS: frozenset[str] = frozenset({
    "tokens_per_min",
    "max_tokens",
    "token_count",
    "ssh_key_path",
    "keyfile",
    "fallback_used",
    "allow_fallback",
    "allow_ambiguous_single_hop_prompt",
    "disable_fallback_on_insecure_hostkey",
    "max_auth_prompts",
    "max_total_auth_attempts",
    "max_hop_host_attempts",
    "max_global_user_attempts",
    "token_prefix",
})

_MASKED_SECRET_EXACT_KEYS: frozenset[str] = frozenset({
    "password",
    "passwords",
    "passphrase",
    "passphrases",
    "secret",
    "secrets",
    "token",
    "tokens",
    "api_token",
    "auth_token",
    "access_token",
    "refresh_token",
    "id_token",
    "bearer_token",
    "session_token",
    "pat_token",
    "api_key",
    "apikey",
    "client_secret",
    "private_key",
    "engineer_api_key",
    "architect_api_key",
    "engineer_auth",
    "architect_auth",
    "admin_password",
    "tacacs_password",
    "local_password",
    "backup_password",
    "enable_secret",
    "enable_password",
    "ssh_passphrase",
    "user_password",
    "jump_password",
    "jumphost_password",
    "credentials",
})

_MASKED_SECRET_SUFFIXES: tuple[str, ...] = (
    "_password",
    "_passwords",
    "_passphrase",
    "_passphrases",
    "_secret",
    "_secrets",
    "_token",
    "_api_key",
)


class SanitizedAuthError(ConnpyError):
    """Authentication error guaranteed free of secrets and chained tracebacks."""

    __suppress_context__ = True

    def __init__(self, message: str = "", code: str = "AUTH_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.__suppress_context__ = True
        self.__cause__ = None

    @property
    def __context__(self) -> None:
        """Never retain or expose an underlying exception context."""
        return None

    @__context__.setter
    def __context__(self, _value: BaseException | None) -> None:
        return

    def __reduce__(self) -> tuple[type[SanitizedAuthError], tuple[str, str]]:
        msg = str(self.args[0]) if self.args else ""
        return (self.__class__, (msg, str(self.code)))


@dataclass(frozen=True)
class AuthCandidate:
    """Immutable credential candidate entry for primary or backup authentication."""

    user: str
    password_spec: str | tuple[str, ...]
    is_fallback: bool = False
    label: str = ""


@dataclass(frozen=True)
class RuntimeAuthCandidate:
    """Per-call resolved credential candidate with decrypted secret."""

    user: str
    password: str
    has_password: bool
    is_fallback: bool = False
    label: str = ""


@dataclass
class _BudgetEntry:
    """Mutable internal counter entry stored inside AuthAttemptTracker."""

    count: int
    limit: int
    last_ts: float


def clear_exception_frames(exc: BaseException | None) -> None:
    """Clear local variables on all frames in an exception's traceback chain."""
    seen: set[int] = set()
    stack: list[BaseException] = []
    if exc is not None:
        stack.append(exc)
    while stack:
        cur = stack.pop()
        obj_id = id(cur)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        cause = getattr(cur, "__cause__", None)
        ctx = getattr(cur, "__context__", None)
        if cause is not None:
            stack.append(cause)
        if ctx is not None:
            stack.append(ctx)

        tb = getattr(cur, "__traceback__", None)
        if tb is not None:
            try:
                traceback.clear_frames(tb)
            except RuntimeError:
                node = tb
                while node is not None:
                    with suppress(RuntimeError, AttributeError):
                        node.tb_frame.clear()
                    node = node.tb_next

        with suppress(AttributeError, TypeError):
            cur.__traceback__ = None
        with suppress(AttributeError, TypeError):
            cur.__cause__ = None
        with suppress(AttributeError, TypeError):
            cur.__context__ = None


def redact_known_secrets(
    text: str,
    secrets: Iterable[str] = (),
    *,
    min_len: int = 4,
) -> str:
    """Replace any non-trivial secret strings in text with '***'."""
    if not text or not secrets:
        return text
    out = str(text)
    unique_secrets = sorted(
        {s for s in secrets if isinstance(s, str) and len(s) >= min_len},
        key=len,
        reverse=True,
    )
    for sec in unique_secrets:
        out = out.replace(sec, "***")
    return out


def sanitize_log_message(message: str, *, secrets: Iterable[str] = ()) -> str:
    """Strip ANSI sequences, control chars, and known secrets from a log line."""
    text = str(message) if message is not None else ""
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    return redact_known_secrets(text, secrets=secrets, min_len=1)


def sanitize_exception(
    orig_exc: BaseException | None,
    *,
    code: str = "AUTH_FAILED",
    secrets: Iterable[str] = (),
    context_label: str = "",
    clear_orig_frames: bool = True,
) -> SanitizedAuthError:
    """Convert any exception into a SanitizedAuthError without leaking secrets or tracebacks."""
    resolved_code = code if code in SAFE_ERROR_TEMPLATES else "AUTH_FAILED"
    try:
        if isinstance(orig_exc, SanitizedAuthError):
            existing_code = (
                orig_exc.code if orig_exc.code in SAFE_ERROR_TEMPLATES else resolved_code
            )
            safe_existing = sanitize_log_message(str(orig_exc), secrets=secrets).strip()
            msg = safe_existing or SAFE_ERROR_TEMPLATES[existing_code]
            return SanitizedAuthError(msg, code=existing_code)

        msg = SAFE_ERROR_TEMPLATES[resolved_code]
        if context_label:
            safe_ctx = sanitize_log_message(context_label, secrets=secrets).strip()
            if safe_ctx:
                msg = f"{msg} ({safe_ctx})"
        return SanitizedAuthError(msg, code=resolved_code)
    finally:
        if clear_orig_frames and orig_exc is not None:
            clear_exception_frames(orig_exc)


def raise_sanitized_auth_error(
    orig_exc: BaseException | None = None,
    *,
    code: str = "AUTH_FAILED",
    secrets: Iterable[str] = (),
    context_label: str = "",
) -> NoReturn:
    """Clear original frames and raise SanitizedAuthError with cause and context suppressed."""
    err: SanitizedAuthError | None = None
    try:
        err = sanitize_exception(
            orig_exc,
            code=code,
            secrets=secrets,
            context_label=context_label,
            clear_orig_frames=True,
        )
    finally:
        if orig_exc is not None:
            clear_exception_frames(orig_exc)
        del orig_exc
        del secrets
    err.__context__ = None
    err.__cause__ = None
    raise err from None


def validate_strict_int_range(
    value: Any,
    name: str,
    min_val: int,
    max_val: int,
) -> int:
    """Validate that value is a strict plain int (not bool/float/str) in [min_val, max_val]."""
    if type(value) is not int or value < min_val or value > max_val:
        raise ValueError(
            f"Invalid {name}: expected integer in [{min_val}, {max_val}], got {value!r}"
        )
    return value


def _check_depth(obj: Any, max_depth: int, cur_depth: int = 0) -> None:
    """Verify container nesting depth does not exceed max_depth iteratively."""
    stack: list[tuple[Any, int]] = [(obj, cur_depth)]
    while stack:
        cur_obj, depth = stack.pop()
        if isinstance(cur_obj, (dict, list, tuple)):
            next_depth = depth + 1
            if next_depth > max_depth:
                raise ValueError(f"JSON nesting exceeds maximum depth of {max_depth}")
            if isinstance(cur_obj, dict):
                for k, v in cur_obj.items():
                    if not isinstance(k, str):
                        raise TypeError("JSON object keys must be strings")
                    stack.append((v, next_depth))
            else:
                for item in cur_obj:
                    stack.append((item, next_depth))


def validate_json_depth_and_size(
    raw_or_obj: Any,
    *,
    max_bytes: int = MAX_JSON_INPUT_BYTES,
    max_depth: int = MAX_JSON_INPUT_DEPTH,
) -> Any:
    """Parse (if str) and enforce byte length and nesting depth limits on JSON inputs."""
    if isinstance(raw_or_obj, str):
        if len(raw_or_obj) > max_bytes or len(raw_or_obj.encode("utf-8", errors="replace")) > max_bytes:
            raise ValueError(f"JSON input exceeds maximum size of {max_bytes} bytes")
        try:
            parsed = json.loads(raw_or_obj)
        except RecursionError:
            raise ValueError(
                f"JSON nesting exceeds maximum depth of {max_depth}"
            ) from None
    else:
        parsed = raw_or_obj
    _check_depth(parsed, max_depth, 0)
    return parsed


def is_rsa_blob(value: Any) -> bool:
    """Return True if value matches an encrypted RSA bytes literal string."""
    return isinstance(value, str) and bool(_RSA_BLOB_RE.match(value))


def _tokenize_compose_body(body: str) -> list[tuple[str, str]]:
    """Tokenize compose expression body in linear O(n) time following EBNF precedence."""
    tokens: list[tuple[str, str]] = []
    n = len(body)
    i = 0
    expect_term = True

    while i < n:
        if body[i].isspace():
            i += 1
            continue

        if not expect_term:
            if body[i] != "+":
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="invalid compose syntax: expected '+'",
                )
            i += 1
            expect_term = True
            continue

        # 1. rsa_blob (b'...' / b"...") evaluated before bare_lit
        if body[i] == "b" and i + 1 < n and body[i + 1] in ("'", '"'):
            quote = body[i + 1]
            j = i + 2
            closed = False
            while j < n:
                if body[j] == "\\":
                    if j + 1 >= n:
                        raise_sanitized_auth_error(
                            code="AUTH_FAILED",
                            context_label="unterminated escape in rsa_blob",
                        )
                    j += 2
                elif body[j] == quote:
                    j += 1
                    closed = True
                    break
                else:
                    j += 1
            if not closed:
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="unterminated rsa_blob",
                )
            if j < n and not body[j].isspace() and body[j] != "+":
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="unexpected trailing characters after rsa_blob",
                )
            blob_str = body[i:j]
            try:
                parsed_blob = ast.literal_eval(blob_str)
            except Exception as exc:  # noqa: BLE001
                raise_sanitized_auth_error(
                    exc,
                    code="AUTH_FAILED",
                    context_label="invalid rsa_blob literal",
                )
            if not isinstance(parsed_blob, bytes):
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="invalid rsa_blob type",
                )
            tokens.append(("rsa_blob", blob_str))
            i = j
            expect_term = False
            continue

        # 2. escaped_lit (@@...)
        if body.startswith("@@", i):
            j = i + 2
            while j < n and not body[j].isspace() and body[j] != "+":
                if body[j] in ("'", '"'):
                    raise_sanitized_auth_error(
                        code="AUTH_FAILED",
                        context_label="unexpected quote in escaped_lit",
                    )
                j += 1
            tokens.append(("escaped_lit", "@" + body[i + 2 : j]))
            i = j
            expect_term = False
            continue

        # 3. profile_ref (@[A-Za-z0-9_.$:/-]+)
        if body[i] == "@":
            match = _PROFILE_REF_BODY_RE.match(body, i + 1)
            if match is None:
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="invalid profile reference in compose",
                )
            end = match.end()
            if end < n and not body[end].isspace() and body[end] != "+":
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="invalid characters in profile reference",
                )
            tokens.append(("profile_ref", match.group(0)))
            i = end
            expect_term = False
            continue

        # 4. quoted_lit ('...' or "...")
        if body[i] in ("'", '"'):
            quote = body[i]
            j = i + 1
            chars: list[str] = []
            closed = False
            while j < n:
                if body[j] == "\\":
                    if j + 1 >= n:
                        raise_sanitized_auth_error(
                            code="AUTH_FAILED",
                            context_label="unterminated escape in quoted_lit",
                        )
                    nxt = body[j + 1]
                    if nxt in (quote, "\\"):
                        chars.append(nxt)
                    else:
                        chars.append("\\" + nxt)
                    j += 2
                elif body[j] == quote:
                    j += 1
                    closed = True
                    break
                else:
                    chars.append(body[j])
                    j += 1
            if not closed:
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="unterminated quoted literal in compose",
                )
            if j < n and not body[j].isspace() and body[j] != "+":
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="unexpected characters after quoted literal",
                )
            tokens.append(("quoted_lit", "".join(chars)))
            i = j
            expect_term = False
            continue

        # 5. bare_lit (without '+', whitespace, initial '@', or quotes)
        if body[i] == "+":
            raise_sanitized_auth_error(
                code="AUTH_FAILED",
                context_label="unexpected '+' operator in compose",
            )
        j = i
        while j < n and not body[j].isspace() and body[j] != "+":
            if body[j] in ("'", '"'):
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="unexpected quote in bare literal",
                )
            j += 1
        tokens.append(("bare_lit", body[i:j]))
        i = j
        expect_term = False

    if expect_term or not tokens:
        raise_sanitized_auth_error(
            code="AUTH_FAILED",
            context_label="empty or incomplete compose expression",
        )
    return tokens


def _resolve_compose_internal(
    expr: str,
    *,
    profiles: dict[str, Any] | None,
    decrypt_fn: Callable[[str], str] | None,
    depth: int,
    visited: frozenset[str],
    max_depth: int,
    max_variants: int,
) -> list[str]:
    if not isinstance(expr, str) or len(expr) > MAX_COMPOSE_EXPR_LEN:
        raise_sanitized_auth_error(
            code="AUTH_FAILED",
            context_label="compose expression exceeds maximum length",
        )

    body = expr.removeprefix("compose:")
    if not body or not body.strip():
        raise_sanitized_auth_error(
            code="AUTH_FAILED",
            context_label="empty compose expression",
        )

    tokens = _tokenize_compose_body(body)
    prof_map = profiles if isinstance(profiles, dict) else {}
    variants: list[str] = [""]

    for tok_type, tok_val in tokens:
        if tok_type in ("escaped_lit", "quoted_lit", "bare_lit"):
            term_vals = [tok_val]
        elif tok_type == "rsa_blob":
            if decrypt_fn is None:
                raise_sanitized_auth_error(
                    code="NO_CREDS",
                    context_label="cannot decrypt compose rsa_blob without decrypt_fn",
                )
            san_err: SanitizedAuthError | None = None
            decrypted: Any = None
            try:
                decrypted = decrypt_fn(tok_val)
            except Exception as exc:  # noqa: BLE001
                san_err = sanitize_exception(
                    exc,
                    code="AUTH_FAILED",
                    context_label="failed to decrypt compose rsa_blob",
                )
            if san_err is not None:
                san_err.__context__ = None
                raise san_err from None
            term_vals = [str(decrypted)]
        elif tok_type == "profile_ref":
            if tok_val in visited:
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="circular profile reference in compose",
                )
            if depth + 1 > max_depth:
                raise_sanitized_auth_error(
                    code="AUTH_FAILED",
                    context_label="compose profile recursion depth exceeded",
                )
            prof_data = prof_map.get(tok_val)
            if not isinstance(prof_data, dict):
                raise_sanitized_auth_error(
                    code="NO_CREDS",
                    context_label="referenced compose profile not found",
                )
            raw_pw = prof_data.get("password")
            if raw_pw in (None, "", []):
                raise_sanitized_auth_error(
                    code="NO_CREDS",
                    context_label="referenced compose profile has empty password",
                )
            raw_items = list(raw_pw) if isinstance(raw_pw, (list, tuple)) else [raw_pw]
            term_vals = []
            next_visited = visited | {tok_val}
            for item in raw_items:
                if not isinstance(item, str) or item == "":
                    raise_sanitized_auth_error(
                        code="NO_CREDS",
                        context_label="invalid password entry in compose profile",
                    )
                if item.startswith("compose:"):
                    sub_vals = _resolve_compose_internal(
                        item,
                        profiles=prof_map,
                        decrypt_fn=decrypt_fn,
                        depth=depth + 1,
                        visited=next_visited,
                        max_depth=max_depth,
                        max_variants=max_variants,
                    )
                    term_vals.extend(sub_vals)
                elif (
                    item.startswith("@")
                    and _PROFILE_REF_BODY_RE.fullmatch(item[1:])
                    and item[1:] in prof_map
                ):
                    sub_vals = _resolve_compose_internal(
                        f"compose:@{item[1:]}",
                        profiles=prof_map,
                        decrypt_fn=decrypt_fn,
                        depth=depth + 1,
                        visited=next_visited,
                        max_depth=max_depth,
                        max_variants=max_variants,
                    )
                    term_vals.extend(sub_vals)
                elif is_rsa_blob(item):
                    if decrypt_fn is None:
                        raise_sanitized_auth_error(
                            code="NO_CREDS",
                            context_label="cannot decrypt profile password rsa_blob without decrypt_fn",
                        )
                    san_item_err: SanitizedAuthError | None = None
                    dec_item: Any = None
                    try:
                        dec_item = decrypt_fn(item)
                    except Exception as exc:  # noqa: BLE001
                        san_item_err = sanitize_exception(
                            exc,
                            code="AUTH_FAILED",
                            context_label="failed to decrypt profile password in compose",
                        )
                    if san_item_err is not None:
                        san_item_err.__context__ = None
                        raise san_item_err from None
                    term_vals.append(str(dec_item))
                else:
                    term_vals.append(item)
                if len(term_vals) > max_variants:
                    raise_sanitized_auth_error(
                        code="AUTH_FAILED",
                        context_label="compose variants limit exceeded",
                    )
        else:
            raise_sanitized_auth_error(
                code="AUTH_FAILED",
                context_label="unknown token in compose expression",
            )

        if len(variants) * len(term_vals) > max_variants:
            raise_sanitized_auth_error(
                code="AUTH_FAILED",
                context_label="compose variants limit exceeded",
            )
        variants = [prefix + suffix for prefix in variants for suffix in term_vals]

    return variants


def parse_compose_expression(
    expr: str,
    *,
    profiles: dict[str, Any] | None = None,
    decrypt_fn: Callable[[str], str] | None = None,
    max_depth: int = MAX_PROFILE_DEPTH,
    max_variants: int = MAX_COMPOSE_VARIANTS,
) -> list[str]:
    """Parse and evaluate a compose: password expression into up to max_variants strings."""
    return _resolve_compose_internal(
        expr,
        profiles=profiles,
        decrypt_fn=decrypt_fn,
        depth=0,
        visited=frozenset(),
        max_depth=max_depth,
        max_variants=max_variants,
    )


def validate_compose_syntax(expr: str) -> None:
    """Validate compose: syntax and length without resolving profiles or decrypting."""
    if not isinstance(expr, str) or len(expr) > MAX_COMPOSE_EXPR_LEN:
        raise_sanitized_auth_error(
            code="AUTH_FAILED",
            context_label="compose expression exceeds maximum length",
        )
    body = expr.removeprefix("compose:")
    if not body or not body.strip():
        raise_sanitized_auth_error(
            code="AUTH_FAILED",
            context_label="empty compose expression",
        )
    _tokenize_compose_body(body)


def extract_compose_profile_refs(expr: str) -> set[str]:
    """Return profile names referenced via @profile tokens in a compose: expression."""
    if not isinstance(expr, str) or not expr.startswith("compose:"):
        return set()
    if len(expr) > MAX_COMPOSE_EXPR_LEN:
        return set()
    body = expr[len("compose:") :]
    if not body or not body.strip():
        return set()
    try:
        tokens = _tokenize_compose_body(body)
    except SanitizedAuthError:
        return set()
    return {tok_val for tok_type, tok_val in tokens if tok_type == "profile_ref"}


def is_compose_expression(value: Any) -> bool:
    """Return True if value is a string with the explicit compose: prefix."""
    return isinstance(value, str) and value.startswith("compose:")


def _mask_secret_key(key_lower: str) -> bool:
    if key_lower in _NON_SECRET_KEYS:
        return False
    if key_lower in _MASKED_SECRET_EXACT_KEYS:
        return True
    return key_lower.endswith(_MASKED_SECRET_SUFFIXES)


def mask_node_secrets(obj: Any) -> Any:
    """Recursively mask sensitive node/profile credentials with '***' using a closed key list."""
    visiting: set[int] = set()

    def _recurse(val: Any, *, in_fallback_list: bool = False) -> Any:
        if isinstance(val, dict):
            obj_id = id(val)
            if obj_id in visiting:
                return "***"
            visiting.add(obj_id)
            try:
                out: dict[Any, Any] = {}
                for k, v in val.items():
                    k_lower = k.lower() if isinstance(k, str) else str(k).lower()
                    if _mask_secret_key(k_lower):
                        out[k] = "***"
                    elif k_lower in ("fallback", "_fallback"):
                        if isinstance(v, (dict, list, tuple)):
                            out[k] = _recurse(v, in_fallback_list=True)
                        elif v in ("", None):
                            out[k] = v
                        else:
                            out[k] = "***"
                    else:
                        out[k] = _recurse(v, in_fallback_list=False)
                return out
            finally:
                visiting.discard(obj_id)

        if isinstance(val, list):
            obj_id = id(val)
            if obj_id in visiting:
                return "***"
            visiting.add(obj_id)
            try:
                return [
                    "***"
                    if (in_fallback_list and isinstance(item, str) and item != "")
                    else _recurse(item, in_fallback_list=in_fallback_list)
                    for item in val
                ]
            finally:
                visiting.discard(obj_id)

        if isinstance(val, tuple):
            obj_id = id(val)
            if obj_id in visiting:
                return "***"
            visiting.add(obj_id)
            try:
                return tuple(
                    "***"
                    if (in_fallback_list and isinstance(item, str) and item != "")
                    else _recurse(item, in_fallback_list=in_fallback_list)
                    for item in val
                )
            finally:
                visiting.discard(obj_id)

        return val

    return _recurse(obj)


def validate_fallback_entries(
    raw_fallback: Any,
    *,
    max_entries: int = MAX_FALLBACK_ENTRIES,
) -> list[dict[str, Any] | str]:
    """Validate and normalize fallback config entries without expanding profiles."""
    if raw_fallback in (None, "", []):
        return []

    parsed = raw_fallback
    if isinstance(raw_fallback, str):
        stripped = raw_fallback.strip()
        if not stripped:
            return []
        if stripped.startswith(("[", "{")):
            if (
                len(stripped) > MAX_JSON_INPUT_BYTES
                or len(stripped.encode("utf-8", errors="replace")) > MAX_JSON_INPUT_BYTES
            ):
                raise ValueError(
                    f"JSON input exceeds maximum size of {MAX_JSON_INPUT_BYTES} bytes"
                )
            try:
                parsed = validate_json_depth_and_size(stripped)
            except json.JSONDecodeError:
                return [stripped]
        else:
            if stripped.startswith("compose:"):
                validate_compose_syntax(stripped)
            return [stripped]

    if isinstance(parsed, dict):
        items: list[Any] = [parsed]
    elif isinstance(parsed, (list, tuple)):
        items = list(parsed)
    else:
        raise TypeError(
            "Fallback must be a list of credential dicts/strings, a dict, @profile, or JSON"
        )

    if len(items) > max_entries:
        raise ValueError(f"Fallback exceeds maximum of {max_entries} entries")

    normalized: list[dict[str, Any] | str] = []
    allowed_entry_keys = {"user", "password"}
    for idx, entry in enumerate(items):
        if isinstance(entry, str):
            if not entry:
                raise ValueError(f"Fallback entry {idx} cannot be empty")
            if entry.startswith("compose:"):
                validate_compose_syntax(entry)
            normalized.append(entry)
        elif isinstance(entry, dict):
            unknown_keys = set(entry.keys()) - allowed_entry_keys
            if unknown_keys:
                raise ValueError(
                    f"Unsupported keys in fallback entry {idx}: {sorted(unknown_keys)}"
                )
            user_val = entry.get("user", "")
            pw_val = entry.get("password", "")
            if user_val is not None and not isinstance(user_val, str):
                raise ValueError(f"Fallback entry {idx} 'user' must be a string")
            if isinstance(pw_val, (list, tuple)):
                if not pw_val or not all(isinstance(p, str) and p != "" for p in pw_val):
                    raise ValueError(
                        f"Fallback entry {idx} 'password' list must contain non-empty strings"
                    )
                for p in pw_val:
                    if p.startswith("compose:"):
                        validate_compose_syntax(p)
            elif pw_val is not None and not isinstance(pw_val, str):
                raise ValueError(f"Fallback entry {idx} 'password' must be a string or list")
            elif isinstance(pw_val, str) and pw_val.startswith("compose:"):
                validate_compose_syntax(pw_val)

            if not (user_val or pw_val):
                raise ValueError(
                    f"Fallback entry {idx} must specify 'user' and/or 'password'"
                )
            normalized.append(deepcopy(entry))
        else:
            raise TypeError(f"Fallback entry {idx} must be a dict or string")

    return normalized


def _is_profile_ref(value: Any) -> bool:
    """Return True if value matches @profile reference syntax (not compose or literal)."""
    return (
        isinstance(value, str)
        and value.startswith("@")
        and bool(_PROFILE_REF_BODY_RE.fullmatch(value[1:]))
    )


def _resolve_profile_field(
    profiles: dict[str, Any],
    prof_name: str,
    field: str,
    *,
    depth: int = 0,
    visited: frozenset[str] = frozenset(),
) -> Any:
    if prof_name in visited or depth >= MAX_PROFILE_DEPTH:
        raise ValueError(f"Circular or overly deep profile reference: {prof_name}")
    if prof_name not in profiles or profiles[prof_name] is None:
        raise ValueError(f"Referenced profile does not exist: {prof_name}")
    prof = profiles[prof_name]
    if not isinstance(prof, dict):
        raise TypeError(f"Invalid profile definition for {prof_name!r}: expected dict")
    val = prof.get(field, "")
    next_visited = visited | {prof_name}
    if _is_profile_ref(val):
        return _resolve_profile_field(
            profiles,
            val[1:],
            field,
            depth=depth + 1,
            visited=next_visited,
        )
    if field == "password" and isinstance(val, (list, tuple)):
        resolved_items: list[Any] = []
        for item in val:
            if _is_profile_ref(item):
                sub = _resolve_profile_field(
                    profiles,
                    item[1:],
                    field,
                    depth=depth + 1,
                    visited=next_visited,
                )
                if isinstance(sub, list):
                    resolved_items.extend(deepcopy(sub))
                elif sub is not None:
                    resolved_items.append(deepcopy(sub))
            else:
                resolved_items.append(deepcopy(item))
        return resolved_items
    return deepcopy(val)


def _resolve_fallback_entries_internal(
    raw_fallback: Any,
    profiles: dict[str, Any] | None,
    *,
    max_entries: int = MAX_FALLBACK_ENTRIES,
    _depth: int = 0,
    _visited: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    entries = validate_fallback_entries(raw_fallback, max_entries=max_entries)
    if not entries:
        return []
    prof_map = profiles if isinstance(profiles, dict) else {}
    resolved: list[dict[str, Any]] = []

    for entry in entries:
        if isinstance(entry, str):
            if entry.startswith("compose:"):
                resolved.append({"user": "", "password": entry})
            elif _is_profile_ref(entry):
                ref_name = entry[1:]
                if ref_name in _visited or _depth >= MAX_PROFILE_DEPTH:
                    raise ValueError(
                        f"Circular or overly deep fallback profile reference: {ref_name}"
                    )
                if ref_name not in prof_map or prof_map[ref_name] is None:
                    raise ValueError(f"Referenced fallback profile does not exist: {ref_name}")
                if not isinstance(prof_map[ref_name], dict):
                    raise TypeError(
                        f"Invalid fallback profile definition for {ref_name!r}: expected dict"
                    )

                prof_obj = prof_map[ref_name]
                u = _resolve_profile_field(
                    prof_map, ref_name, "user", depth=_depth, visited=_visited
                )
                p = _resolve_profile_field(
                    prof_map, ref_name, "password", depth=_depth, visited=_visited
                )
                own_user = str(u) if u not in (None, "") else ""
                own_pw: Any = p if p not in (None, "", []) else ""
                prof_fb = prof_obj.get("fallback")
                if prof_fb not in (None, "", []):
                    has_own = (
                        bool(own_user)
                        or (isinstance(own_pw, list) and bool(own_pw))
                        or (isinstance(own_pw, str) and own_pw != "")
                    )
                    if has_own:
                        if len(resolved) >= max_entries:
                            raise ValueError(
                                f"Fallback exceeds maximum of {max_entries} entries"
                            )
                        resolved.append({"user": own_user, "password": own_pw})
                    sub_entries = _resolve_fallback_entries_internal(
                        prof_fb,
                        prof_map,
                        max_entries=max_entries,
                        _depth=_depth + 1,
                        _visited=_visited | {ref_name},
                    )
                    for sub in sub_entries:
                        if len(resolved) >= max_entries:
                            raise ValueError(
                                f"Fallback exceeds maximum of {max_entries} entries"
                            )
                        resolved.append(sub)
                else:
                    if len(resolved) >= max_entries:
                        raise ValueError(
                            f"Fallback exceeds maximum of {max_entries} entries"
                        )
                    resolved.append({"user": own_user, "password": own_pw})
            else:
                resolved.append({"user": "", "password": entry})
        elif isinstance(entry, dict):
            user_raw = entry.get("user", "")
            pw_raw = entry.get("password", "")
            if _is_profile_ref(user_raw):
                u_val = _resolve_profile_field(
                    prof_map, user_raw[1:], "user", depth=_depth, visited=_visited
                )
                user_res = str(u_val) if u_val not in (None, "") else ""
            else:
                user_res = user_raw or ""

            if isinstance(pw_raw, (list, tuple)):
                pw_res_list: list[str] = []
                for p_item in pw_raw:
                    if _is_profile_ref(p_item):
                        resolved_pw = _resolve_profile_field(
                            prof_map, p_item[1:], "password", depth=_depth, visited=_visited
                        )
                        if isinstance(resolved_pw, list):
                            pw_res_list.extend(str(x) for x in resolved_pw if x != "")
                        elif resolved_pw not in (None, ""):
                            pw_res_list.append(str(resolved_pw))
                    else:
                        pw_res_list.append(str(p_item))
                if not pw_res_list:
                    raise ValueError("Fallback entry password list resolved to empty")
                pw_res: str | list[str] = pw_res_list
            elif _is_profile_ref(pw_raw):
                resolved_pw = _resolve_profile_field(
                    prof_map, pw_raw[1:], "password", depth=_depth, visited=_visited
                )
                pw_res = resolved_pw if resolved_pw is not None else ""
            else:
                pw_res = pw_raw if pw_raw is not None else ""

            resolved.append({"user": user_res, "password": pw_res})

        if len(resolved) > max_entries:
            raise ValueError(f"Fallback exceeds maximum of {max_entries} entries")

    return resolved


def resolve_fallback_entries(
    raw_fallback: Any,
    profiles: dict[str, Any] | None,
    *,
    max_entries: int = MAX_FALLBACK_ENTRIES,
) -> list[dict[str, Any]]:
    """Normalize fallback entries and resolve any @profile references safely."""
    return _resolve_fallback_entries_internal(
        raw_fallback,
        profiles,
        max_entries=max_entries,
    )


def _extract_profile_refs_internal(value: Any, seen: set[int]) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("compose:"):
            refs.update(extract_compose_profile_refs(stripped))
        elif stripped.startswith("@") and _PROFILE_REF_BODY_RE.fullmatch(stripped[1:]):
            refs.add(stripped[1:])
        elif (
            stripped.startswith(("[", "{"))
            and len(stripped) <= MAX_JSON_INPUT_BYTES
            and len(stripped.encode("utf-8", errors="replace")) <= MAX_JSON_INPUT_BYTES
        ):
            with suppress(ValueError, TypeError, RecursionError):
                parsed = json.loads(stripped)
                refs.update(_extract_profile_refs_internal(parsed, seen))
    elif isinstance(value, (list, tuple, dict)):
        obj_id = id(value)
        if obj_id in seen:
            return refs
        seen.add(obj_id)
        try:
            items = value.values() if isinstance(value, dict) else value
            for item in items:
                with suppress(RecursionError):
                    refs.update(_extract_profile_refs_internal(item, seen))
        finally:
            seen.discard(obj_id)
    return refs


def extract_profile_refs_from_value(value: Any) -> set[str]:
    """Extract all @profile names referenced directly or via compose:/fallback in a value."""
    return _extract_profile_refs_internal(value, set())


def validate_auth_budget_tags(tags: Any) -> None:
    """Validate optional auth budget and policy keys in a node/profile tags dict."""
    if not isinstance(tags, dict):
        return
    if "max_auth_prompts" in tags and tags["max_auth_prompts"] is not None:
        validate_strict_int_range(
            tags["max_auth_prompts"],
            "tags.max_auth_prompts",
            MIN_AUTH_PROMPTS,
            MAX_AUTH_PROMPTS_CAP,
        )
    if "max_total_auth_attempts" in tags and tags["max_total_auth_attempts"] is not None:
        validate_strict_int_range(
            tags["max_total_auth_attempts"],
            "tags.max_total_auth_attempts",
            MIN_TOTAL_AUTH_ATTEMPTS,
            MAX_TOTAL_AUTH_ATTEMPTS_CAP,
        )
    if "max_hop_host_attempts" in tags and tags["max_hop_host_attempts"] is not None:
        validate_strict_int_range(
            tags["max_hop_host_attempts"],
            "tags.max_hop_host_attempts",
            MIN_HOP_HOST_ATTEMPTS,
            MAX_HOP_HOST_ATTEMPTS_CAP,
        )
    if "max_global_user_attempts" in tags and tags["max_global_user_attempts"] is not None:
        validate_strict_int_range(
            tags["max_global_user_attempts"],
            "tags.max_global_user_attempts",
            1,
            1000,
        )
    for bool_key in (
        "allow_fallback",
        "allow_ambiguous_single_hop_prompt",
        "disable_fallback_on_insecure_hostkey",
    ):
        if bool_key in tags and tags[bool_key] is not None and not isinstance(tags[bool_key], bool):
            raise ValueError(f"Invalid tags.{bool_key}: expected boolean")
    if "fallback" in tags and tags["fallback"] not in (None, "", []):
        validate_fallback_entries(tags["fallback"])


def validate_config_auth_settings(cfg: Any) -> None:
    """Validate global config auth budget and policy keys."""
    if not isinstance(cfg, dict):
        raise TypeError("Config section must be a dictionary")
    if "max_auth_prompts" in cfg and cfg["max_auth_prompts"] is not None:
        validate_strict_int_range(
            cfg["max_auth_prompts"],
            "config.max_auth_prompts",
            MIN_AUTH_PROMPTS,
            MAX_AUTH_PROMPTS_CAP,
        )
    if "max_total_auth_attempts" in cfg and cfg["max_total_auth_attempts"] is not None:
        validate_strict_int_range(
            cfg["max_total_auth_attempts"],
            "config.max_total_auth_attempts",
            MIN_TOTAL_AUTH_ATTEMPTS,
            MAX_TOTAL_AUTH_ATTEMPTS_CAP,
        )
    if "max_hop_host_attempts" in cfg and cfg["max_hop_host_attempts"] is not None:
        validate_strict_int_range(
            cfg["max_hop_host_attempts"],
            "config.max_hop_host_attempts",
            MIN_HOP_HOST_ATTEMPTS,
            MAX_HOP_HOST_ATTEMPTS_CAP,
        )
    if "max_global_user_attempts" in cfg and cfg["max_global_user_attempts"] is not None:
        validate_strict_int_range(
            cfg["max_global_user_attempts"],
            "config.max_global_user_attempts",
            1,
            1000,
        )
    if (
        "disable_fallback_on_insecure_hostkey" in cfg
        and cfg["disable_fallback_on_insecure_hostkey"] is not None
        and not isinstance(cfg["disable_fallback_on_insecure_hostkey"], bool)
    ):
        raise ValueError("Invalid config.disable_fallback_on_insecure_hostkey: expected boolean")


def host_exact_re(host: str) -> re.Pattern[str]:
    """Compile an exact host boundary regex with zero FQDN subdomain wildcard risk."""
    escaped = re.escape(str(host))
    return re.compile(
        rf"(?<![A-Za-z0-9._-]){escaped}(?![A-Za-z0-9._-])(?=$|[\s:\]'\"])",
        re.IGNORECASE | re.ASCII,
    )


def normalize_prompt_line(before_bytes: bytes, after_bytes: bytes) -> str:
    """Return the normalized final prompt line stripped of ANSI and control sequences."""
    combined = (before_bytes or b"") + (after_bytes or b"")
    text = combined.decode("utf-8", errors="replace")
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    return _CONTROL_CHAR_RE.sub("", lines[-1]).strip()


def has_insecure_hostkey_checking(
    options: str = "",
    tags: Any = None,
    hop_metas: Iterable[dict[str, Any]] = (),
) -> bool:
    """Return True if StrictHostKeyChecking=no is present on target, mosh_ssh_options, or hops."""
    candidates: list[str] = []
    if options:
        candidates.append(str(options))
    if isinstance(tags, dict):
        for k in ("mosh_ssh_options", "ssh_options"):
            v = tags.get(k)
            if v:
                candidates.append(str(v))
    for hop in hop_metas or ():
        if isinstance(hop, dict):
            h_opts = hop.get("options", "")
            if h_opts:
                candidates.append(str(h_opts))
            h_tags = hop.get("tags")
            if isinstance(h_tags, dict):
                for k in ("mosh_ssh_options", "ssh_options"):
                    v = h_tags.get(k)
                    if v:
                        candidates.append(str(v))
    return any(_INSECURE_HOSTKEY_RE.search(c) for c in candidates)


class AuthAttemptTracker:
    """Thread-safe intra-process authentication attempt tracker with safe anti-flood eviction."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TRACKER_TTL_SECONDS,
        max_entries: int = MAX_TRACKER_ENTRIES,
        default_max_host_attempts: int = DEFAULT_MAX_TOTAL_AUTH_ATTEMPTS,
        default_max_hop_attempts: int = DEFAULT_MAX_HOP_HOST_ATTEMPTS,
        default_max_global_user_attempts: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise TypeError(f"Invalid ttl_seconds: expected number > 0, got {ttl_seconds!r}")
        ttl_val = float(ttl_seconds)
        if not math.isfinite(ttl_val) or ttl_val <= 0:
            raise ValueError(f"Invalid ttl_seconds: expected > 0, got {ttl_seconds!r}")
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError(f"Invalid max_entries: expected int >= 1, got {max_entries!r}")
        if max_entries < 1:
            raise ValueError(f"Invalid max_entries: expected >= 1, got {max_entries!r}")
        self._ttl_seconds = ttl_val
        self._max_entries = max_entries
        self._default_max_host_attempts = int(default_max_host_attempts)
        self._default_max_hop_attempts = int(default_max_hop_attempts)
        self._default_max_global_user_attempts = default_max_global_user_attempts
        self._clock = clock
        self._lock = threading.Lock()
        self._unblocked: OrderedDict[Any, _BudgetEntry] = OrderedDict()
        self._blocked: OrderedDict[Any, _BudgetEntry] = OrderedDict()

    def _purge_expired_unlocked(self, now: float) -> None:
        for bucket in (self._unblocked, self._blocked):
            expired_keys = [
                k for k, entry in bucket.items() if (now - entry.last_ts) >= self._ttl_seconds
            ]
            for k in expired_keys:
                del bucket[k]

    def _evict_if_full_unlocked(self, now: float) -> None:
        if (len(self._unblocked) + len(self._blocked)) < self._max_entries:
            return
        self._purge_expired_unlocked(now)
        while (len(self._unblocked) + len(self._blocked)) >= self._max_entries:
            if self._unblocked:
                self._unblocked.popitem(last=False)
            elif self._blocked:
                self._blocked.popitem(last=False)
            else:
                break

    def _get_live_entry_unlocked(self, key: Any, now: float) -> _BudgetEntry | None:
        if key in self._unblocked:
            entry = self._unblocked[key]
            if (now - entry.last_ts) >= self._ttl_seconds:
                del self._unblocked[key]
                return None
            return entry
        if key in self._blocked:
            entry = self._blocked[key]
            if (now - entry.last_ts) >= self._ttl_seconds:
                del self._blocked[key]
                return None
            return entry
        return None

    def _can_acquire_unlocked(self, key: Any, limit: int, now: float) -> bool:
        if limit <= 0:
            return False
        entry = self._get_live_entry_unlocked(key, now)
        if entry is None:
            return True
        if entry.count >= limit:
            entry.limit = limit
            if key in self._unblocked:
                del self._unblocked[key]
                self._blocked[key] = entry
            return False
        return True

    def _commit_acquire_unlocked(self, key: Any, limit: int, now: float) -> None:
        entry = self._get_live_entry_unlocked(key, now)
        if entry is None:
            self._evict_if_full_unlocked(now)
            new_entry = _BudgetEntry(count=1, limit=limit, last_ts=now)
            if 1 >= limit:
                self._blocked[key] = new_entry
            else:
                self._unblocked[key] = new_entry
            return

        entry.count += 1
        entry.limit = limit
        entry.last_ts = now
        if entry.count >= limit:
            if key in self._unblocked:
                del self._unblocked[key]
            self._blocked[key] = entry
            self._blocked.move_to_end(key)
        else:
            if key in self._blocked:
                del self._blocked[key]
            self._unblocked[key] = entry
            self._unblocked.move_to_end(key)

    def try_acquire(self, key: Any, *, limit: int) -> bool:
        """Attempt to consume 1 attempt slot for key within limit. Returns False if exhausted."""
        if limit <= 0:
            return False
        now = self._clock()
        with self._lock:
            if not self._can_acquire_unlocked(key, limit, now):
                return False
            self._commit_acquire_unlocked(key, limit, now)
            return True

    def try_acquire_target_attempt(
        self,
        *,
        host: str,
        port: str = "",
        protocol: str = "ssh",
        user: str = "",
        host_limit: int | None = None,
        user_limit: int | None = None,
    ) -> tuple[bool, str | None]:
        """Atomically verify and consume both user and host attempt slots under one lock."""
        eff_host_limit = self._default_max_host_attempts if host_limit is None else int(host_limit)
        eff_user_limit = self._default_max_global_user_attempts if user_limit is None else user_limit
        host_key = self._host_key(host, port, protocol)
        user_key = self._user_key(user) if (user and eff_user_limit is not None) else None
        now = self._clock()
        with self._lock:
            if user_key is not None and not self._can_acquire_unlocked(
                user_key, int(eff_user_limit), now
            ):
                return False, "USER_BUDGET_EXHAUSTED"
            if not self._can_acquire_unlocked(host_key, eff_host_limit, now):
                return False, "HOST_BUDGET_EXHAUSTED"
            if user_key is not None:
                self._commit_acquire_unlocked(user_key, int(eff_user_limit), now)
            self._commit_acquire_unlocked(host_key, eff_host_limit, now)
            return True, None

    def remaining(self, key: Any, *, limit: int) -> int:
        """Return remaining attempt quota for key."""
        now = self._clock()
        with self._lock:
            entry = self._get_live_entry_unlocked(key, now)
            if entry is None:
                return max(0, limit)
            return max(0, limit - entry.count)

    @staticmethod
    def _host_key(host: str, port: str = "", protocol: str = "ssh") -> tuple[str, str, str, str]:
        return ("host", str(host).lower(), str(port or "").strip(), str(protocol or "").lower())

    @staticmethod
    def _hop_key(host: str, port: str = "", protocol: str = "ssh") -> tuple[str, str, str, str]:
        return ("hop", str(host).lower(), str(port or "").strip(), str(protocol or "").lower())

    @staticmethod
    def _user_key(user: str) -> tuple[str, str]:
        return ("user", str(user))

    def try_acquire_host(
        self,
        host: str,
        port: str = "",
        protocol: str = "ssh",
        *,
        limit: int | None = None,
    ) -> bool:
        """Consume 1 target-host authentication attempt if within budget."""
        eff_limit = self._default_max_host_attempts if limit is None else int(limit)
        return self.try_acquire(self._host_key(host, port, protocol), limit=eff_limit)

    def try_acquire_hop(
        self,
        host: str,
        port: str = "",
        protocol: str = "ssh",
        *,
        limit: int | None = None,
    ) -> bool:
        """Consume 1 jumphost authentication attempt if within budget."""
        eff_limit = self._default_max_hop_attempts if limit is None else int(limit)
        return self.try_acquire(self._hop_key(host, port, protocol), limit=eff_limit)

    def try_acquire_user(
        self,
        user: str,
        *,
        limit: int | None = None,
    ) -> bool:
        """Consume 1 global username authentication attempt if a global limit is active."""
        if not user:
            return True
        eff_limit = self._default_max_global_user_attempts if limit is None else limit
        if eff_limit is None:
            return True
        return self.try_acquire(self._user_key(user), limit=int(eff_limit))

    def remaining_host(
        self,
        host: str,
        port: str = "",
        protocol: str = "ssh",
        *,
        limit: int | None = None,
    ) -> int:
        """Return remaining target-host attempt quota."""
        eff_limit = self._default_max_host_attempts if limit is None else int(limit)
        return self.remaining(self._host_key(host, port, protocol), limit=eff_limit)

    def remaining_hop(
        self,
        host: str,
        port: str = "",
        protocol: str = "ssh",
        *,
        limit: int | None = None,
    ) -> int:
        """Return remaining jumphost attempt quota."""
        eff_limit = self._default_max_hop_attempts if limit is None else int(limit)
        return self.remaining(self._hop_key(host, port, protocol), limit=eff_limit)

    def remaining_user(
        self,
        user: str,
        *,
        limit: int | None = None,
    ) -> int | None:
        """Return remaining global username quota, or None if unlimited."""
        eff_limit = self._default_max_global_user_attempts if limit is None else limit
        if eff_limit is None:
            return None
        return self.remaining(self._user_key(user), limit=int(eff_limit))

    def is_blocked(self, key: Any) -> bool:
        """Return True if key currently has an active exhausted budget entry."""
        now = self._clock()
        with self._lock:
            entry = self._get_live_entry_unlocked(key, now)
            return entry is not None and entry.count >= entry.limit

    def entry_count(self) -> int:
        """Return total number of tracked entries (before TTL expiry)."""
        with self._lock:
            return len(self._unblocked) + len(self._blocked)

    def reset(self) -> None:
        """Clear all tracked entries."""
        with self._lock:
            self._unblocked.clear()
            self._blocked.clear()
