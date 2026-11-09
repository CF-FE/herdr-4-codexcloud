"""Small HTTP client for the frozen Decision Service v1 contract."""
from http.client import IncompleteRead
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class DecisionServiceError(ValueError):
    def __init__(self, code, status=None):
        self.code = code
        self.status = status
        super().__init__(code)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DecisionService:
    ERROR_CODES = {
        "invalid_request", "unauthenticated", "forbidden_scope", "not_found", "input_conflict",
        "revision_conflict", "request_in_progress", "unavailable",
    }

    def __init__(self, base_url, token, timeout=15):
        url = urlsplit(base_url)
        if (url.scheme not in {"https", "http"} or not url.hostname or url.username is not None
                or url.query or url.fragment or url.path.rstrip("/")
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise DecisionServiceError("invalid_service_url")
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            raise DecisionServiceError("invalid_service_token")
        if not math.isfinite(timeout) or timeout <= 0:
            raise DecisionServiceError("invalid_service_timeout")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = build_opener(NoRedirects())

    @classmethod
    def from_env(cls):
        return cls(os.getenv("DECISION_SERVICE_URL", ""), os.getenv("DECISION_SERVICE_TOKEN", ""),
                   float(os.getenv("DECISION_SERVICE_TIMEOUT", "15")))

    def request(self, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        request = Request(self.base_url + path, data=body, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            with error:
                try:
                    code = json.load(error)["error"]["code"]
                except (KeyError, ValueError, TypeError, OSError, IncompleteRead):
                    code = None
            raise DecisionServiceError(
                code if isinstance(code, str) and code in self.ERROR_CODES else "http_error", error.code
            ) from None
        except (URLError, OSError, IncompleteRead):
            raise DecisionServiceError("transport_error") from None
        except ValueError:
            raise DecisionServiceError("invalid_service_response") from None
        if not isinstance(result, dict):
            raise DecisionServiceError("invalid_service_response")
        return result

    def evaluate(self, request):
        return self.request("/v1/decisions/evaluate", request)

    def get(self, *, decision_id=None, request_id=None):
        if bool(decision_id) == bool(request_id):
            raise ValueError("provide exactly one of decision_id and request_id")
        path = "/" + quote(decision_id, safe="") if decision_id else "?" + urlencode({"request_id": request_id})
        return self.request("/v1/decisions" + path)

    def feedback(self, event):
        receipt = self.request("/v1/decision-feedback", event)
        if (receipt.get("event_id") != event["event_id"] or receipt.get("revision") != event["revision"]
                or receipt.get("received") is not True or not isinstance(receipt.get("receipt_id"), str)
                or not receipt["receipt_id"]):
            raise DecisionServiceError("invalid_feedback_receipt")
        return receipt
