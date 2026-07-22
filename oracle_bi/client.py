from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import requests


class OracleBIError(RuntimeError):
    """Raised when Oracle BI authentication or an API request fails."""


@dataclass(frozen=True)
class OracleBIConfig:
    auth_server: str
    application_server: str
    org_identifier: str
    client_id: str
    username: str
    password: str
    application_name: str = "Meal Compliance Dashboard"
    timeout_seconds: int = 45
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        required = {
            "auth_server": self.auth_server,
            "application_server": self.application_server,
            "org_identifier": self.org_identifier,
            "client_id": self.client_id,
            "username": self.username,
            "password": self.password,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError("Missing Oracle BI configuration: " + ", ".join(missing))


@dataclass
class TokenBundle:
    id_token: str
    refresh_token: str
    expires_at_monotonic: float

    def is_valid(self, safety_seconds: int = 300) -> bool:
        return bool(self.id_token) and time.monotonic() + safety_seconds < self.expires_at_monotonic


class OracleBIClient:
    """Oracle MICROS Business Intelligence API client.

    Authentication follows Oracle's OIDC Authorization Code Flow with PKCE. Tokens
    are held in process memory only; this client never writes them to disk.
    """

    def __init__(self, config: OracleBIConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "MealComplianceDashboard/3.3",
            }
        )
        self.tokens: TokenBundle | None = None

    @property
    def authorize_url(self) -> str:
        return self._auth_url("/oidc-provider/v1/oauth2/authorize")

    @property
    def signin_url(self) -> str:
        return self._auth_url("/oidc-provider/v1/oauth2/signin")

    @property
    def token_url(self) -> str:
        return self._auth_url("/oidc-provider/v1/oauth2/token")

    def _auth_url(self, path: str) -> str:
        return self.config.auth_server.rstrip("/") + path

    def _api_url(self, endpoint: str) -> str:
        return (
            self.config.application_server.rstrip("/")
            + f"/bi/v1/{self.config.org_identifier}/{endpoint}"
        )

    @staticmethod
    def _new_pkce_pair() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        # RFC 7636 permits 43-128 characters.
        verifier = verifier[:128]
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return verifier, challenge

    @staticmethod
    def _safe_error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("message") or payload.get("title")
                code = payload.get("code") or payload.get("o:errorCode")
                pieces = [str(x) for x in (detail, code) if x not in (None, "")]
                if pieces:
                    return " · ".join(pieces)[:500]
        except ValueError:
            pass
        text = (response.text or "").strip().replace("\n", " ")
        return text[:500] or "No response details"

    def authenticate(self, *, force_full: bool = False) -> TokenBundle:
        if not force_full and self.tokens and self.tokens.is_valid():
            return self.tokens

        if not force_full and self.tokens and self.tokens.refresh_token:
            try:
                return self._refresh_tokens(self.tokens.refresh_token)
            except OracleBIError:
                # A missed refresh window or post-upgrade invalidation requires PKCE again.
                self.tokens = None

        return self._full_pkce_authentication()

    def _full_pkce_authentication(self) -> TokenBundle:
        verifier, challenge = self._new_pkce_pair()
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "scope": "openid",
            "redirect_uri": "apiaccount://callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        response = self.session.get(
            self.authorize_url,
            params=params,
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_ssl,
        )
        if not response.ok:
            raise OracleBIError(
                f"Oracle authorization failed (HTTP {response.status_code}): "
                f"{self._safe_error_message(response)}"
            )

        response = self.session.post(
            self.signin_url,
            data={
                "username": self.config.username,
                "password": self.config.password,
                "orgname": self.config.org_identifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_ssl,
        )
        if not response.ok:
            raise OracleBIError(
                f"Oracle API account sign-in failed (HTTP {response.status_code}): "
                f"{self._safe_error_message(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OracleBIError("Oracle sign-in returned a non-JSON response.") from exc

        if not payload.get("success"):
            detail = payload.get("error") or payload.get("nextOp") or "Sign-in was not successful"
            raise OracleBIError(f"Oracle API account sign-in failed: {detail}")

        redirect_url = str(payload.get("redirectUrl") or "")
        auth_code = parse_qs(urlparse(redirect_url).query).get("code", [""])[0]
        if not auth_code:
            raise OracleBIError("Oracle sign-in did not return an authorization code.")

        response = self.session.post(
            self.token_url,
            data={
                "scope": "openid",
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "code_verifier": verifier,
                "code": auth_code,
                "redirect_uri": "apiaccount://callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_ssl,
        )
        return self._consume_token_response(response)

    def _refresh_tokens(self, refresh_token: str) -> TokenBundle:
        response = self.session.post(
            self.token_url,
            data={
                "scope": "openid",
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "refresh_token": refresh_token,
                "redirect_uri": "apiaccount://callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_ssl,
        )
        return self._consume_token_response(response)

    def _consume_token_response(self, response: requests.Response) -> TokenBundle:
        if not response.ok:
            raise OracleBIError(
                f"Oracle token request failed (HTTP {response.status_code}): "
                f"{self._safe_error_message(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OracleBIError("Oracle token endpoint returned a non-JSON response.") from exc

        id_token = str(payload.get("id_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        if not id_token:
            raise OracleBIError("Oracle token response did not contain id_token.")

        try:
            expires_in = int(payload.get("expires_in", 1209600))
        except (TypeError, ValueError):
            expires_in = 1209600

        self.tokens = TokenBundle(
            id_token=id_token,
            refresh_token=refresh_token,
            expires_at_monotonic=time.monotonic() + max(60, expires_in),
        )
        return self.tokens

    def post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        tokens = self.authenticate()
        response = self.session.post(
            self._api_url(endpoint),
            json=payload,
            headers={
                "Authorization": f"Bearer {tokens.id_token}",
                "Content-Type": "application/json",
            },
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_ssl,
        )

        if response.status_code == 401:
            tokens = self.authenticate(force_full=True)
            response = self.session.post(
                self._api_url(endpoint),
                json=payload,
                headers={
                    "Authorization": f"Bearer {tokens.id_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_ssl,
            )

        if not response.ok:
            raise OracleBIError(
                f"Oracle {endpoint} failed (HTTP {response.status_code}): "
                f"{self._safe_error_message(response)}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OracleBIError(f"Oracle {endpoint} returned a non-JSON response.") from exc
        if not isinstance(data, dict):
            raise OracleBIError(f"Oracle {endpoint} returned an unexpected response type.")
        return data

    def get_locations(self) -> dict[str, Any]:
        return self.post(
            "getLocationDimensions",
            {"applicationName": self.config.application_name},
        )

    def get_employees(self, loc_ref: str) -> dict[str, Any]:
        return self.post(
            "getEmployeeDimensions",
            {"locRef": str(loc_ref), "applicationName": self.config.application_name},
        )

    def get_job_codes(self, loc_ref: str) -> dict[str, Any]:
        return self.post(
            "getJobCodeDimensions",
            {"locRef": str(loc_ref), "applicationName": self.config.application_name},
        )

    def get_latest_business_date(self, loc_ref: str) -> dict[str, Any]:
        return self.post(
            "getLatestBusDt",
            {"locRef": str(loc_ref), "applicationName": self.config.application_name},
        )

    def get_timecards(
        self,
        loc_ref: str,
        business_date: date,
        *,
        include_adjustments: bool = True,
        changed_since_utc: str | None = None,
        emp_num: int | None = None,
        ext_payroll_id: str | None = None,
    ) -> dict[str, Any]:
        if emp_num is not None and ext_payroll_id:
            raise ValueError("Use either emp_num or ext_payroll_id, not both.")

        payload: dict[str, Any] = {
            "locRef": str(loc_ref),
            "busDt": business_date.isoformat(),
            "includeAdjustments": bool(include_adjustments),
            "applicationName": self.config.application_name,
        }
        if changed_since_utc:
            payload["changedSinceUTC"] = changed_since_utc
        if emp_num is not None:
            payload["empNum"] = int(emp_num)
        if ext_payroll_id:
            payload["extPayrollID"] = str(ext_payroll_id)
        data = self.post("getTimeCardDetails", payload)
        # Oracle normally echoes locRef and businessDates. Preserve request metadata
        # as a defensive control so coverage and adjustment-inclusion checks remain
        # reproducible even when a gateway omits an echoed request field.
        data.setdefault("locRef", str(loc_ref))
        data["_requestedBusDt"] = business_date.isoformat()
        data["_includeAdjustmentsRequested"] = bool(include_adjustments)
        return data

    def get_timecards_range(
        self,
        loc_ref: str,
        start_date: date,
        end_date: date,
        *,
        include_adjustments: bool = True,
        maximum_days: int = 31,
    ) -> list[dict[str, Any]]:
        if end_date < start_date:
            raise ValueError("End date cannot be earlier than start date.")
        day_count = (end_date - start_date).days + 1
        if day_count > maximum_days:
            raise ValueError(f"Date range cannot exceed {maximum_days} days per run.")

        results: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            results.append(
                self.get_timecards(
                    loc_ref,
                    current,
                    include_adjustments=include_adjustments,
                )
            )
            current += timedelta(days=1)
        return results


def iter_timecards(payloads: Iterable[dict[str, Any]]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """Yield (loc_ref, business_date, timecard) from Oracle response payloads."""
    for payload in payloads:
        loc_ref = str(payload.get("locRef") or "")
        business_days = payload.get("businessDates", []) or []
        if not business_days and isinstance(payload.get("timeCardDetails"), list):
            business_days = [{
                "busDt": payload.get("busDt") or payload.get("_requestedBusDt") or "",
                "timeCardDetails": payload.get("timeCardDetails", []),
            }]
        adjustments_requested = payload.get("_includeAdjustmentsRequested")
        for business_day in business_days:
            if not isinstance(business_day, dict):
                continue
            bus_dt = str(business_day.get("busDt") or payload.get("_requestedBusDt") or "")
            for timecard in business_day.get("timeCardDetails", []) or []:
                if isinstance(timecard, dict):
                    if adjustments_requested is None:
                        yield loc_ref, bus_dt, timecard
                    else:
                        enriched = dict(timecard)
                        enriched["_adjustmentsRequested"] = adjustments_requested
                        yield loc_ref, bus_dt, enriched
