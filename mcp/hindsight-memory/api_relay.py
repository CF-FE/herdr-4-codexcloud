"""One-shot loopback REST relay for the existing Hindsight 1.2 service.

The caller owns the retry deadline and stable retain IDs. This layer never
retries, follows redirects, polls, or treats an async ACK as durable storage.
CLI input and output are one JSON object per process, carried over existing SSH.
"""

import argparse
import http.client
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import sys
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID


DEFAULT_ROOT = Path(r"D:\Hindsight")
CONNECT_TIMEOUT = 15.0
REQUEST_TIMEOUT = 90.0
ACTIONS = frozenset({
    "recall", "retain", "reflect", "get_operation", "get_document",
    "list_operations", "list_banks", "health",
})


class RelayError(ValueError):
    """A request/configuration error established before any HTTP submission."""


def _prepare(request):
    if not isinstance(request, dict):
        raise RelayError("Request must be an object.")
    allowed = {"action", "bank_id", "payload", "operation_id", "document_id",
               "query", "timeout_seconds"}
    if request.keys() - allowed:
        raise RelayError("Unknown request field.")
    action = request.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise RelayError("Unsupported action.")
    bank = request.get("bank_id")
    if action not in {"health", "list_banks"} or bank is not None:
        if not isinstance(bank, str) or not bank.strip() or bank != bank.strip() or bank.casefold() == "default":
            raise RelayError("An explicit non-default project bank_id is required.")
    payload = request.get("payload", {})
    if not isinstance(payload, dict):
        raise RelayError("payload must be an object.")
    payload = dict(payload)
    timeout = request.get("timeout_seconds", REQUEST_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise RelayError("timeout_seconds must be a finite positive number.")
    timeout = min(float(timeout), REQUEST_TIMEOUT)
    bank_path = "/v1/default/banks/" + quote(bank or "", safe="")
    operation = request.get("operation_id")
    document = request.get("document_id")
    for key, needed in (("operation_id", {"retain", "get_operation"}),
                        ("document_id", {"retain", "get_document"})):
        value = request.get(key)
        if action in needed:
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise RelayError(key + " is required.")
            if key == "operation_id":
                try:
                    UUID(value)
                except ValueError:
                    raise RelayError("operation_id must be a caller-allocated UUID.") from None
        elif value is not None:
            raise RelayError(key + " is not used by this action.")
    query = request.get("query")
    if query is not None:
        if action not in {"recall", "reflect", "list_banks"} or not isinstance(query, str):
            raise RelayError("query is not valid for this action.")
        key = "q" if action == "list_banks" else "query"
        if key in payload and payload[key] != query:
            raise RelayError("Conflicting query values.")
        payload[key] = query
    method, path, body = "GET", "", None
    if action == "retain":
        fields = {"content", "context", "timestamp", "document_id", "metadata",
                  "tags", "update_mode", "strategy"}
        if payload.keys() - fields:
            raise RelayError("Unsupported retain item field; source/hash belong in metadata.")
        if not isinstance(payload.get("content"), str) or not payload["content"].strip():
            raise RelayError("retain content is required.")
        if "document_id" in payload and payload["document_id"] != document:
            raise RelayError("Conflicting document_id values.")
        metadata = payload.get("metadata")
        if metadata is not None and (not isinstance(metadata, dict) or
                any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items())):
            raise RelayError("retain metadata must contain string keys and values.")
        if "tags" in payload and payload["tags"] is not None and (
                not isinstance(payload["tags"], list) or any(not isinstance(v, str) for v in payload["tags"])):
            raise RelayError("retain tags must be a list of strings.")
        for field in ("context", "timestamp", "strategy", "update_mode"):
            if field in payload and payload[field] is not None and not isinstance(payload[field], str):
                raise RelayError("retain " + field + " must be a string.")
        payload["document_id"] = document
        method, path = "POST", bank_path + "/memories"
        body = {"items": [payload], "async": True, "operation_id": operation}
    elif action in {"recall", "reflect"}:
        if not isinstance(payload.get("query"), str) or not payload["query"].strip():
            raise RelayError("query is required.")
        if payload.keys() & {"bank_id", "operation_id", "document_id", "items", "async"}:
            raise RelayError("Routing fields must not appear in the query payload.")
        method = "POST"
        path = bank_path + ("/memories/recall" if action == "recall" else "/reflect")
        body = payload
    elif action == "get_operation":
        path = bank_path + "/operations/" + quote(operation, safe="")
    elif action == "get_document":
        path = bank_path + "/documents/" + quote(document, safe="")
    elif action == "list_operations":
        path = bank_path + "/operations"
    elif action == "list_banks":
        path = "/v1/default/banks"
    else:
        path = "/health"
    if method == "GET":
        fields = {
            "get_operation": {"include_payload": bool},
            "get_document": {},
            "list_operations": {"status": str, "type": str, "limit": int,
                                "offset": int, "exclude_parents": bool},
            "list_banks": {"q": str, "limit": int, "offset": int},
            "health": {},
        }[action]
        if payload.keys() - fields.keys():
            raise RelayError("Unsupported query parameter.")
        for key, value in payload.items():
            expected = fields[key]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise RelayError("Invalid query parameter type.")
            if key == "offset" and value < 0:
                raise RelayError("offset must be nonnegative.")
            if key == "limit" and (value < (1 if action == "list_operations" else 0)
                                  or (action == "list_operations" and value > 100)):
                raise RelayError("limit is outside the native range.")
        if payload:
            path += "?" + urlencode({k: str(v).lower() if isinstance(v, bool) else v
                                    for k, v in payload.items()})
    encoded = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return action, bank, method, path, encoded, timeout


def _configuration(root):
    values = {}
    for name in ("mcp-client.env", "shared.env"):
        try:
            lines = (Path(root) / "config" / name).read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            raise RelayError("Local Hindsight configuration could not be read.") from None
        for line in lines:
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    parsed = urlsplit(values.get("HINDSIGHT_WIN_LOCAL_MCP_URL", ""))
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port or 80
    except ValueError:
        raise RelayError("Invalid loopback MCP URL configuration.") from None
    if parsed.scheme != "http" or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RelayError("The relay requires an HTTP loopback origin.")
    token = values.get("HINDSIGHT_API_TENANT_API_KEY", "")
    if not token or not token.isascii() or any(c.isspace() for c in token):
        raise RelayError("Hindsight service authentication is not configured.")
    return parsed.hostname, port, token


def _redact(value, token):
    if isinstance(value, str):
        value = value.replace(token, "<service-token-redacted>") if token else value
        return re.sub(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{12,}", "<api-key-redacted>", value)
    if isinstance(value, list):
        return [_redact(v, token) for v in value]
    if isinstance(value, dict):
        return {_redact(k, token): _redact(v, token) for k, v in value.items()}
    return value


def handle_request(request, *, root: Path = DEFAULT_ROOT) -> dict:
    """Return one receipt; HTTP/transport errors never trigger a second request."""
    started = time.monotonic()
    result = {"state": "NOT_SUBMITTED", "http_status": None, "body": None,
              "attempts": 0, "retries": 0, "request_started": False}
    if isinstance(request, dict):
        result.update({k: request[k] for k in ("action", "bank_id", "operation_id", "document_id")
                       if isinstance(request.get(k), str)})
    token = ""
    connection = timer = None
    try:
        action, bank, method, path, body, budget = _prepare(request)
        host, port, token = _configuration(root)
        deadline = started + budget
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RelayError("Request budget expired before connection.")
        connection = http.client.HTTPConnection(host, port, timeout=min(CONNECT_TIMEOUT, remaining))
        result["attempts"] = 1
        connection.connect()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RelayError("Request budget expired before submission.")
        connected_socket = connection.sock
        connected_socket.settimeout(remaining)

        def expire_request():
            try:
                connected_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        # A socket timeout alone resets for every read. This request-scoped
        # deadline also bounds a slow stream; it is not a retry/background queue.
        timer = threading.Timer(remaining, expire_request)
        timer.daemon = True
        timer.start()
        result["request_started"] = True
        connection.request(method, path, body=body, headers={
            "Authorization": "Bearer " + token, "Accept": "application/json",
            "Content-Type": "application/json",
        })
        response = connection.getresponse()
        result["http_status"] = response.status
        retry_after = response.getheader("Retry-After")
        if retry_after is not None:
            result["response_headers"] = {"Retry-After": retry_after}
        raw = response.read()
        try:
            native = json.loads(raw)
            json.dumps(native, allow_nan=False)
        except (ValueError, UnicodeDecodeError):
            result.update(state="UNKNOWN", response_text=raw.decode("utf-8", errors="replace"),
                          error={"code": "INVALID_RESPONSE", "message": "Upstream response is not JSON."})
            return _redact(result, token)
        result["body"] = native
        if not isinstance(native, dict):
            raise RelayError("Upstream JSON response must be an object.")
        result["state"] = "RESPONSE"
        if 200 <= response.status < 300:
            checks = {
                "health": ("status", str), "list_banks": ("banks", list),
                "list_operations": ("operations", list), "recall": ("results", list),
                "reflect": ("text", str), "get_document": ("original_text", str),
                "get_operation": ("status", str),
            }
            if action == "retain":
                try:
                    matches = UUID(str(native.get("operation_id"))) == UUID(request["operation_id"])
                except ValueError:
                    matches = False
                if (native.get("success") is not True or native.get("async") is not True
                        or native.get("bank_id") != bank or not matches):
                    raise RelayError("Async retain acknowledgement does not match the submission.")
                result["state"] = "SUBMITTED"
            else:
                key, expected = checks[action]
                if not isinstance(native.get(key), expected):
                    raise RelayError("Upstream response does not match the native action shape.")
                if action == "get_operation":
                    try:
                        matches = UUID(str(native.get("operation_id"))) == UUID(request["operation_id"])
                    except ValueError:
                        matches = False
                    if not matches:
                        raise RelayError("Operation response does not match the requested ID.")
                if action == "get_document" and native.get("id") != request["document_id"]:
                    raise RelayError("Document response does not match the requested ID.")
                if "bank_id" in native and bank is not None and native["bank_id"] != bank:
                    raise RelayError("Upstream response bank does not match the request.")
        elif action == "retain" and (response.status >= 500 or response.status == 408):
            result["state"] = "UNKNOWN"
    except RelayError as exc:
        result.update(state="UNKNOWN" if result["request_started"] else "NOT_SUBMITTED",
                      error={"code": "INVALID_RESPONSE" if result["request_started"] else "INVALID_REQUEST",
                             "message": str(exc)})
    except (OSError, http.client.HTTPException) as exc:
        result.update(state="UNKNOWN" if result["request_started"] else "NOT_SUBMITTED",
                      error={"code": "REQUEST_FAILED" if result["request_started"] else "CONNECT_FAILED",
                             "reason": type(exc).__name__})
    except (ValueError, TypeError) as exc:
        result.update(state="UNKNOWN" if result["request_started"] else "NOT_SUBMITTED",
                      error={"code": "INVALID_RESPONSE" if result["request_started"] else "INVALID_REQUEST",
                             "reason": type(exc).__name__})
    finally:
        if timer is not None:
            timer.cancel()
        if connection is not None:
            connection.close()
    return _redact(result, token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="Trusted local configuration root; never taken from stdin.")
    args = parser.parse_args(argv)
    try:
        request = json.loads(sys.stdin.readline())
    except ValueError:
        request = None
    result = handle_request(request, root=args.root)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 2 if result.get("error", {}).get("code") == "INVALID_REQUEST" else 0


if __name__ == "__main__":
    raise SystemExit(main())
