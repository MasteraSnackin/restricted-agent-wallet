#!/usr/bin/env python3
"""Loopback-only D1 judge console with a fixed agent capability.

This process intentionally receives one pre-minted ``d1-agent-user`` bearer
token. It has no provisioning, user-management, package-upload or owner
revocation route. The browser supplies only payment intent; every ledger
identity, template, choice, Holding, factory, disclosure and command envelope
is derived on the server and the spending policy remains authoritative in Daml.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "demo"
DEFAULT_DB = ROOT / ".daml" / "demo-operations.sqlite3"
DEFAULT_CONFIG = ROOT / ".daml" / "demo-config.json"

AGENT_USER = "d1-agent-user"
LOCALNET_AUDIENCE = "https://canton.network.global"
PRODUCTION_PACKAGE_ID = (
    "87453fdcde8589c700016732f55e79469196f4434573c649d905200ec325dc90"
)
TEST_PACKAGE_ID = (
    "5851e05c58b059245a667e80e8f8a4b5724ec65a56f9cee3c647316d79161543"
)
AMULET_PACKAGE_ID = (
    "90987abecbcb1d004b063ddfe3b4b5d46cf3814ce89114a86c8cd75ff3cb8a4b"
)
HOLDING_PACKAGE_ID = (
    "718a0f77e505a8de22f188bd4c87fe74101274e9d4cb1bfac7d09aec7158d35b"
)
TRANSFER_PACKAGE_ID = (
    "55ba4deb0ad4662c4168b39859738a0e91388d252286480c7331b3f71a517281"
)
MODULE = "TokenMandate"
PACKAGE_NAME = "cantor8-d1-token-wallet"


def template_id(name: str) -> str:
    return f"{PRODUCTION_PACKAGE_ID}:{MODULE}:{name}"


TEMPLATES = {
    name: template_id(name)
    for name in (
        "TokenMandate",
        "TokenMandateControl",
        "TokenActivationAudit",
        "TokenChargeAudit",
        "TokenRevocationAudit",
    )
}
TEMPLATE_FILTERS = {
    name: f"#{PACKAGE_NAME}:{MODULE}:{name}"
    for name in TEMPLATES
}
HOLDING = f"{HOLDING_PACKAGE_ID}:Splice.Api.Token.HoldingV1:Holding"
HOLDING_FILTER = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"
TRANSFER_FACTORY = (
    f"{TRANSFER_PACKAGE_ID}:Splice.Api.Token.TransferInstructionV1:TransferFactory"
)

AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,10})?$")
PARTY_RE = re.compile(r"^[^\s:]{1,255}::[A-Za-z0-9._-]{16,512}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
JWT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_BODY_BYTES = 8_192
CHARGE_LOCK = threading.Lock()


class PublicError(Exception):
    """A bounded error that is safe to return to the local browser."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        correlation_id: str | None = None,
        source: str = "SERVICE",
        uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = " ".join(message.split())[:700]
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.source = source
        self.uncertain = uncertain

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "correlationId": self.correlation_id,
            "source": self.source,
        }


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise PublicError(500, "INVALID_LEDGER_TIME", "Ledger returned an invalid time") from error


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def require_endpoint(label: str, value: str, port: int) -> str:
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port != port
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise PublicError(
            500,
            "UNSAFE_ENDPOINT",
            f"{label} must be a loopback HTTP endpoint on port {port}",
        )
    return value.rstrip("/")


def require_loopback_bind(host: str) -> None:
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise PublicError(
            500,
            "UNSAFE_BIND",
            "The D1 demo server may bind only to a loopback address",
        )


def decode_jwt_json(segment: str, label: str) -> dict[str, Any]:
    if not JWT_SEGMENT_RE.fullmatch(segment):
        raise PublicError(500, "INVALID_TOKEN", f"Agent token {label} is malformed")
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        value = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicError(500, "INVALID_TOKEN", f"Agent token {label} is malformed") from error
    if not isinstance(value, dict):
        raise PublicError(500, "INVALID_TOKEN", f"Agent token {label} must be a JSON object")
    return value


def validate_localnet_agent_token(token: str) -> dict[str, Any]:
    """Check public LocalNet JWT claims before making any upstream request.

    This intentionally does not authenticate the signature. The participant
    remains the authentication authority when it receives the bearer token.
    The local check prevents an owner or participant-admin token from being
    accidentally installed in this narrowly scoped process.
    """
    if not token or any(char.isspace() for char in token):
        raise PublicError(500, "INVALID_TOKEN", "Agent bearer token is missing or malformed")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise PublicError(500, "INVALID_TOKEN", "Agent bearer token must be a three-part JWT")
    header = decode_jwt_json(parts[0], "header")
    payload = decode_jwt_json(parts[1], "payload")
    if not JWT_SEGMENT_RE.fullmatch(parts[2]):
        raise PublicError(500, "INVALID_TOKEN", "Agent token signature is malformed")
    try:
        signature = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    except binascii.Error as error:
        raise PublicError(500, "INVALID_TOKEN", "Agent token signature is malformed") from error
    if len(signature) != hashlib.sha256().digest_size:
        raise PublicError(500, "INVALID_TOKEN", "Agent token must use an HS256-sized signature")
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise PublicError(500, "INVALID_TOKEN", "Agent token must use the pinned LocalNet HS256 JWT form")
    if payload.get("aud") != LOCALNET_AUDIENCE:
        raise PublicError(500, "INVALID_TOKEN_AUDIENCE", "Agent token has the wrong LocalNet audience")
    if payload.get("sub") != AGENT_USER:
        raise PublicError(500, "INVALID_TOKEN_SUBJECT", "Agent token subject must be d1-agent-user")
    return payload


def decode_right(right: dict[str, Any]) -> tuple[str, str]:
    kinds = right.get("kind", {})
    if len(kinds) != 1:
        return ("MalformedRight", "")
    kind, payload = next(iter(kinds.items()))
    return (kind, (payload or {}).get("value", {}).get("party", ""))


def human_statement(audit: dict[str, Any]) -> str:
    instrument = audit.get("instrumentId", {}).get("id", "unknown instrument")
    allowed = json.dumps(audit.get("allowedCounterparties", []))
    return (
        f"Mandate {audit.get('mandateId')}: at {audit.get('chargedAt')}, "
        f"agent {audit.get('spender')} paid {audit.get('counterparty')} "
        f"{audit.get('amount')} {instrument}. Memo: {audit.get('memo')}. "
        f"Permission: exact Party allow-list {allowed}; cumulative spend "
        f"{audit.get('spentAfter')}/{audit.get('totalCap')}; valid until "
        f"{audit.get('expiresAt')}."
    )


def public_upstream_error(
    error: Exception,
    *,
    source: str,
    uncertain: bool = False,
) -> PublicError:
    if isinstance(error, urllib.error.HTTPError):
        raw = error.read().decode("utf-8", errors="replace")[:20_000]
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        cause = body.get("cause") or body.get("message") or f"{source} returned HTTP {error.code}"
        code = body.get("code") or f"HTTP_{error.code}"
        correlation = body.get("correlationId") or body.get("traceId")
        return PublicError(
            error.code,
            str(code),
            str(cause),
            correlation_id=str(correlation) if correlation else None,
            source=source,
            uncertain=uncertain and error.code >= 500,
        )
    if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
        return PublicError(
            503,
            "UPSTREAM_UNAVAILABLE",
            f"{source} could not be reached; the operation was not automatically retried",
            source=source,
            uncertain=uncertain,
        )
    return PublicError(
        500,
        "UPSTREAM_ERROR",
        f"{source} returned an unexpected response",
        source=source,
        uncertain=uncertain,
    )


class FixedAgentClient:
    """A role-bound Ledger API client with no caller-selectable identity."""

    def __init__(
        self,
        token: str,
        *,
        ledger_base: str = "http://localhost:2975",
        registry_base: str = "http://localhost:4000",
        registry_host: str = "scan.localhost",
    ) -> None:
        self._token_claims = validate_localnet_agent_token(token)
        self._token = token
        self.ledger_base = require_endpoint("Ledger API", ledger_base, 2975)
        self.registry_base = require_endpoint("Token registry", registry_base, 4000)
        if registry_host != "scan.localhost":
            raise PublicError(
                500,
                "UNSAFE_REGISTRY_HOST",
                "Registry Host header must be scan.localhost for the pinned LocalNet",
            )
        self.registry_host = registry_host
        self.user_id = AGENT_USER
        self.agent_party = ""
        self.owner_party = ""
        self.rights: list[tuple[str, str]] = []
        self.installed_package_ids: set[str] = set()
        self._load_and_verify_identity()
        self._load_and_verify_packages()

    def _request(
        self,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        source: str,
        uncertain: bool = False,
    ) -> Any:
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=request_headers,
            method="POST" if body is not None else "GET",
        )
        try:
            raw = urllib.request.urlopen(request, timeout=30).read()
        except Exception as error:
            raise public_upstream_error(error, source=source, uncertain=uncertain) from error
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise PublicError(
                502,
                "INVALID_UPSTREAM_JSON",
                f"{source} returned invalid JSON",
                source=source,
                uncertain=uncertain,
            ) from error

    def ledger(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        uncertain: bool = False,
    ) -> Any:
        if not path.startswith("/v2/"):
            raise PublicError(500, "INVALID_LEDGER_PATH", "Internal Ledger API path is invalid")
        return self._request(
            self.ledger_base + path,
            body=body,
            headers={"Authorization": f"Bearer {self._token}"},
            source="LEDGER",
            uncertain=uncertain,
        )

    def registry(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path != "/registry/transfer-instruction/v1/transfer-factory":
            raise PublicError(500, "INVALID_REGISTRY_PATH", "Internal registry path is invalid")
        return self._request(
            self.registry_base + path,
            body=body,
            headers={"Host": self.registry_host},
            source="REGISTRY",
        )

    def _load_and_verify_identity(self) -> None:
        user_response = self.ledger(f"/v2/users/{AGENT_USER}")
        user = user_response.get("user", {})
        if user.get("id") != AGENT_USER or user.get("isDeactivated"):
            raise PublicError(500, "INVALID_AGENT_USER", "Expected active d1-agent-user")
        primary = user.get("primaryParty", "")
        rights_response = self.ledger(f"/v2/users/{AGENT_USER}/rights")
        rights = sorted(decode_right(right) for right in rights_response.get("rights", []))
        act_as = [party for kind, party in rights if kind == "CanActAs"]
        read_as = [party for kind, party in rights if kind == "CanReadAs"]
        if (
            len(rights) != 2
            or len(act_as) != 1
            or len(read_as) != 1
            or act_as[0] != primary
            or not primary
            or not read_as[0]
            or primary == read_as[0]
        ):
            raise PublicError(
                500,
                "OVERPRIVILEGED_AGENT",
                "d1-agent-user must have exactly CanActAs agent and CanReadAs owner",
            )
        self.agent_party = primary
        self.owner_party = read_as[0]
        self.rights = rights

    def _load_and_verify_packages(self) -> None:
        response = self.ledger("/v2/packages")
        package_ids = response.get("packageIds", [])
        if not isinstance(package_ids, list) or not all(
            isinstance(package_id, str) for package_id in package_ids
        ):
            raise PublicError(
                500,
                "INVALID_PACKAGE_LIST",
                "Ledger returned an invalid package list",
            )
        installed = set(package_ids)
        required = {
            PRODUCTION_PACKAGE_ID,
            AMULET_PACKAGE_ID,
            HOLDING_PACKAGE_ID,
            TRANSFER_PACKAGE_ID,
        }
        missing = sorted(required - installed)
        if missing:
            raise PublicError(
                500,
                "REQUIRED_PACKAGE_MISSING",
                f"Required pinned LocalNet packages are missing: {missing!r}",
            )
        if TEST_PACKAGE_ID in installed:
            raise PublicError(
                500,
                "TEST_PACKAGE_INSTALLED",
                "The test-only D1 mock package must not be installed on the demonstration LocalNet",
            )
        self.installed_package_ids = installed

    def ledger_end(self) -> int:
        return self.ledger("/v2/state/ledger-end")["offset"]

    def active_events(self, template_ids: list[str]) -> list[dict[str, Any]]:
        names_by_id = {identifier: name for name, identifier in TEMPLATES.items()}
        filters = [
            {
                "identifierFilter": {
                    "TemplateFilter": {
                        "value": {
                            "templateId": TEMPLATE_FILTERS[names_by_id[identifier]],
                            "includeCreatedEventBlob": False,
                        }
                    }
                }
            }
            for identifier in template_ids
        ]
        body = {
            "filter": {
                "filtersByParty": {
                    self.agent_party: {"cumulative": filters},
                }
            },
            "verbose": False,
            "activeAtOffset": self.ledger_end(),
        }
        rows = []
        for contract in self.ledger("/v2/state/active-contracts", body):
            event = (
                contract.get("contractEntry", {})
                .get("JsActiveContract", {})
                .get("createdEvent", {})
            )
            if event.get("templateId") in template_ids and event.get("createArgument"):
                rows.append(event)
        return rows

    def holdings(self, owner: str) -> list[dict[str, Any]]:
        body = {
            "filter": {
                "filtersByParty": {
                    owner: {
                        "cumulative": [
                            {
                                "identifierFilter": {
                                    "InterfaceFilter": {
                                        "value": {
                                            "interfaceId": HOLDING_FILTER,
                                            "includeInterfaceView": True,
                                            "includeCreatedEventBlob": False,
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            },
            "verbose": False,
            "activeAtOffset": self.ledger_end(),
        }
        holdings = []
        for item in self.ledger("/v2/state/active-contracts", body):
            event = (
                item.get("contractEntry", {})
                .get("JsActiveContract", {})
                .get("createdEvent", {})
            )
            for interface_view in event.get("interfaceViews", []):
                if interface_view.get("interfaceId") != HOLDING:
                    continue
                view = interface_view.get("viewValue", {})
                holdings.append(
                    {
                        "contractId": event.get("contractId", ""),
                        "owner": view.get("owner", ""),
                        "amount": view.get("amount", "0"),
                        "instrumentId": view.get("instrumentId", {}),
                        "locked": view.get("lock") is not None,
                    }
                )
        return holdings

    def dashboard_events(self) -> dict[str, list[dict[str, Any]]]:
        events = self.active_events(list(TEMPLATES.values()))
        grouped = {name: [] for name in TEMPLATES}
        reverse = {identifier: name for name, identifier in TEMPLATES.items()}
        for event in events:
            grouped[reverse[event["templateId"]]].append(event)
        return grouped

    def resolve_mandate(self, mandate_id: str) -> dict[str, Any]:
        events = self.active_events([TEMPLATES["TokenMandate"]])
        matches = [
            event
            for event in events
            if event.get("createArgument", {}).get("mandateId") == mandate_id
            and event.get("createArgument", {}).get("owner") == self.owner_party
            and event.get("createArgument", {}).get("spender") == self.agent_party
        ]
        if not matches:
            raise PublicError(404, "MANDATE_NOT_FOUND", "No current mandate matches that ID")
        if len(matches) != 1:
            raise PublicError(
                409,
                "AMBIGUOUS_MANDATE",
                "More than one current mandate has that ID; submission is blocked",
            )
        return matches[0]

    def prepare_transfer(
        self,
        mandate_event: dict[str, Any],
        *,
        counterparty: str,
        amount: Decimal,
        memo: str,
    ) -> dict[str, Any]:
        mandate = mandate_event["createArgument"]
        owner = mandate.get("owner")
        instrument = mandate.get("instrumentId", {})
        admin = mandate.get("expectedAdmin")
        if (
            owner != self.owner_party
            or mandate.get("spender") != self.agent_party
            or instrument.get("admin") != admin
            or not instrument.get("id")
        ):
            raise PublicError(409, "INCONSISTENT_MANDATE", "Mandate identity or instrument is inconsistent")

        spendable = []
        available = Decimal("0")
        for holding in self.holdings(owner):
            try:
                holding_amount = Decimal(str(holding["amount"]))
            except InvalidOperation:
                continue
            if (
                not holding["locked"]
                and holding["owner"] == owner
                and holding["instrumentId"] == instrument
                and holding_amount > 0
            ):
                spendable.append(holding)
                available += holding_amount
        if not spendable or available < amount:
            raise PublicError(
                422,
                "INSUFFICIENT_VISIBLE_FUNDS",
                f"Agent can see {decimal_text(available)} spendable {instrument.get('id')}",
                source="PREPARATION",
            )

        requested_at = utc_now().replace(microsecond=0)
        execute_before = requested_at + dt.timedelta(minutes=5)
        transfer_meta = {
            "values": {
                "splice.lfdecentralizedtrust.org/reason": memo,
                "cantor8.local/mandate-id": mandate.get("mandateId"),
            }
        }
        arguments = {
            "expectedAdmin": admin,
            "transfer": {
                "sender": owner,
                "receiver": counterparty,
                "amount": decimal_text(amount),
                "instrumentId": instrument,
                "requestedAt": requested_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "executeBefore": execute_before.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "inputHoldingCids": [holding["contractId"] for holding in spendable],
                "meta": transfer_meta,
            },
            "extraArgs": {
                "context": {"values": {}},
                "meta": {"values": {}},
            },
        }
        factory = self.registry(
            "/registry/transfer-instruction/v1/transfer-factory",
            {"choiceArguments": arguments},
        )
        context = factory.get("choiceContext", {})
        if not factory.get("factoryId"):
            raise PublicError(502, "MISSING_FACTORY", "Registry returned no transfer factory", source="REGISTRY")
        return {
            "factoryId": factory["factoryId"],
            "transferKind": factory.get("transferKind"),
            "choiceContext": context.get("choiceContextData", {}),
            "disclosures": context.get("disclosedContracts", []),
            "requestedAt": arguments["transfer"]["requestedAt"],
            "executeBefore": arguments["transfer"]["executeBefore"],
            "inputHoldingCids": arguments["transfer"]["inputHoldingCids"],
        }

    def submit_charge(
        self,
        mandate_event: dict[str, Any],
        prepared: dict[str, Any],
        *,
        counterparty: str,
        amount: Decimal,
        memo: str,
        request_id: str,
    ) -> dict[str, Any]:
        command = {
            "ExerciseCommand": {
                "templateId": mandate_event["templateId"],
                "contractId": mandate_event["contractId"],
                "choice": "ChargeToken",
                "choiceArgument": {
                    "counterparty": counterparty,
                    "amount": decimal_text(amount),
                    "memo": memo,
                    "factoryCid": prepared["factoryId"],
                    "inputHoldingCids": prepared["inputHoldingCids"],
                    "requestedAt": prepared["requestedAt"],
                    "executeBefore": prepared["executeBefore"],
                    "choiceContext": prepared["choiceContext"],
                },
            }
        }
        envelope: dict[str, Any] = {
            "commands": [command],
            "commandId": f"d1-ui-charge-{request_id}",
            "actAs": [self.agent_party],
            "readAs": [self.owner_party],
            "userId": AGENT_USER,
        }
        if prepared["disclosures"]:
            envelope["disclosedContracts"] = [
                {
                    "templateId": item["templateId"],
                    "contractId": item["contractId"],
                    "createdEventBlob": item["createdEventBlob"],
                    "synchronizerId": item.get("synchronizerId", ""),
                }
                for item in prepared["disclosures"]
            ]
        return self.ledger(
            "/v2/commands/submit-and-wait-for-transaction",
            {"commands": envelope},
            uncertain=True,
        )


class OperationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    request_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def begin(self, request_id: str, request_hash: str, request_json: str) -> dict[str, Any] | None:
        now = iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise PublicError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That requestId was already used for different payment intent",
                    )
                if row["response_json"]:
                    result = json.loads(row["response_json"])
                    result["replayed"] = True
                    return result
                return {
                    "requestId": request_id,
                    "status": "UNCERTAIN",
                    "evidenceSource": "SERVICE",
                    "code": "PENDING_OR_INTERRUPTED",
                    "message": "This request is already pending or was interrupted; it was not resubmitted",
                    "createdAt": row["created_at"],
                    "replayed": True,
                }
            connection.execute(
                """
                INSERT INTO operations (
                    request_id, request_hash, request_json, status,
                    response_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', NULL, ?, ?)
                """,
                (request_id, request_hash, request_json, now, now),
            )
        return None

    def finish(self, request_id: str, result: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE operations
                SET status = ?, response_json = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (result["status"], json.dumps(result, sort_keys=True), iso(), request_id),
            )

    def recent(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, request_json, status, response_json,
                       created_at, updated_at
                FROM operations ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        output = []
        for row in rows:
            request = json.loads(row["request_json"])
            response = json.loads(row["response_json"]) if row["response_json"] else {}
            output.append(
                {
                    "requestId": row["request_id"],
                    "mandateId": request.get("mandateId"),
                    "counterparty": request.get("counterparty"),
                    "amount": request.get("amount"),
                    "memo": request.get("memo"),
                    "status": response.get("status", row["status"]),
                    "evidenceSource": response.get("evidenceSource", "SERVICE"),
                    "code": response.get("code"),
                    "message": response.get("message"),
                    "updateId": response.get("updateId"),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
            )
        return output


def source_excerpt(title: str, start: str, end: str) -> dict[str, Any]:
    path = ROOT / "daml" / "TokenMandate.daml"
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start_index = next(index for index, line in enumerate(lines) if start in line)
        end_index = next(
            index for index, line in enumerate(lines[start_index + 1 :], start_index + 1)
            if end in line
        )
    except StopIteration as error:
        raise PublicError(500, "SOURCE_EXCERPT_MISSING", f"Could not locate {title} source proof") from error
    selected = lines[start_index:end_index]
    return {
        "title": title,
        "path": "daml/TokenMandate.daml",
        "startLine": start_index + 1,
        "endLine": end_index,
        "text": "\n".join(
            f"{number:>4}  {line}"
            for number, line in enumerate(selected, start_index + 1)
        ),
    }


def proof_payload() -> dict[str, Any]:
    return {
        "tests": {
            "tokenStandardTransactions": 59,
            "phaseOneTransactions": 61,
            "pendingRollbackCovered": True,
            "exactExpiryCovered": True,
            "unilateralArchiveBypassCovered": True,
        },
        "packages": {
            "productionPackageId": PRODUCTION_PACKAGE_ID,
            "expectedAmuletPackageId": AMULET_PACKAGE_ID,
            "testOnlyPackageId": TEST_PACKAGE_ID,
            "testOnlyPackageExpectedInstalled": False,
        },
        "recordedSmoke": {
            "scope": "Splice 0.6.8 loopback LocalNet; not DevNet or production",
            "mandateId": "d1-localnet-20260829T121453Z-c8e709",
            "amount": "0.25",
            "merchantBalanceBefore": "1.8750000000",
            "merchantBalanceAfter": "2.1250000000",
            "merchantDelta": "0.2500000000",
            "registryTransferKind": "direct",
            "disclosedContractCount": 5,
            "rejections": [
                "Cumulative over-cap charge",
                "Different Party",
                "Direct TransferFactory bypass",
                "Agent revocation",
                "Post-owner-revocation charge",
            ],
        },
        "sourceExcerpts": [
            source_excerpt("Owner-only stable revocation", "choice Revoke", "template TokenMandate"),
            source_excerpt("Agent controller and Daml policy", "controller spender", "gate <- fetch control"),
            source_excerpt("Stable revocation gate", "gate <- fetch control", "inputAmounts <-"),
            source_excerpt("Atomic Token Standard transfer", "transferResult <- exercise", "-- The stable interface"),
        ],
    }


def load_demo_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicError(500, "INVALID_DEMO_CONFIG", "Demo configuration is invalid") from error
    return value if isinstance(value, dict) else {}


def project_dashboard(
    client: FixedAgentClient,
    store: OperationStore,
    *,
    csrf_token: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    grouped = client.dashboard_events()
    controls = {event["contractId"]: event for event in grouped["TokenMandateControl"]}
    charges = grouped["TokenChargeAudit"]
    activations = grouped["TokenActivationAudit"]
    revocations = grouped["TokenRevocationAudit"]
    now = utc_now()

    grouped_mandates: dict[str, list[dict[str, Any]]] = {}
    for event in grouped["TokenMandate"]:
        argument = event["createArgument"]
        if argument.get("owner") != client.owner_party or argument.get("spender") != client.agent_party:
            continue
        grouped_mandates.setdefault(argument.get("mandateId", ""), []).append(event)

    mandates = []
    for mandate_id, events in grouped_mandates.items():
        event = events[0]
        argument = event["createArgument"]
        cap = Decimal(str(argument.get("cap", "0")))
        spent = Decimal(str(argument.get("spent", "0")))
        control_active = argument.get("control") in controls
        expired = parse_time(argument.get("expiresAt", "")) <= now
        if len(events) != 1:
            state = "AMBIGUOUS"
        elif not control_active:
            state = "REVOKED"
        elif expired:
            state = "EXPIRED"
        else:
            state = "ACTIVE"
        mandate_charges = [
            row for row in charges if row["createArgument"].get("mandateId") == mandate_id
        ]
        mandates.append(
            {
                "mandateId": mandate_id,
                "status": state,
                "contractCid": event["contractId"],
                "controlCid": argument.get("control"),
                "controlActive": control_active,
                "owner": argument.get("owner"),
                "agent": argument.get("spender"),
                "instrumentId": argument.get("instrumentId"),
                "cap": decimal_text(cap),
                "spent": decimal_text(spent),
                "remaining": decimal_text(cap - spent),
                "expiresAt": argument.get("expiresAt"),
                "allowedCounterparties": argument.get("allowedCounterparties", []),
                "statements": [human_statement(row["createArgument"]) for row in mandate_charges],
                "chargeAudits": [
                    {
                        "contractId": row["contractId"],
                        "amount": row["createArgument"].get("amount"),
                        "counterparty": row["createArgument"].get("counterparty"),
                        "chargedAt": row["createArgument"].get("chargedAt"),
                        "spentAfter": row["createArgument"].get("spentAfter"),
                    }
                    for row in mandate_charges
                ],
                "activationAudits": [
                    {
                        "contractId": row["contractId"],
                        "activatedAt": row["createArgument"].get("activatedAt"),
                    }
                    for row in activations
                    if row["createArgument"].get("mandateId") == mandate_id
                ],
                "revocationAudits": [
                    {
                        "contractId": row["contractId"],
                        "revokedAt": row["createArgument"].get("revokedAt"),
                        "reason": row["createArgument"].get("reason"),
                    }
                    for row in revocations
                    if row["createArgument"].get("mandateId") == mandate_id
                ],
            }
        )
    mandates.sort(key=lambda item: item["expiresAt"], reverse=True)

    return {
        "csrfToken": csrf_token,
        "scope": "Loopback Splice 0.6.8 LocalNet demonstration; not DevNet or production",
        "checkedAt": iso(now),
        "identity": {
            "userId": AGENT_USER,
            "agentParty": client.agent_party,
            "ownerParty": client.owner_party,
            "rights": [{"kind": kind, "party": party} for kind, party in client.rights],
            "ownerAuthorityPresent": False,
            "participantAdminPresent": False,
        },
        "mandates": mandates,
        "operations": store.recent(),
        "proof": proof_payload(),
        "suggestedForbiddenParty": config.get("forbiddenCounterparty", ""),
        "limitations": [
            "LocalNet uses a known unsafe HS256 development secret; this is not production custody isolation.",
            "The browser process has only a fixed d1-agent-user bearer token; owner revocation stays in Terminal.",
            "The cap covers requested transfer principal, not separate Canton traffic or token fees.",
            "Rejected ledger transactions roll back, so they appear in this operational log rather than as audit contracts.",
            "The memo is agent-authored text, not independently verified reasoning.",
            "No LLM, MCP server, DevNet deployment or autonomous planner is claimed.",
        ],
    }


def validate_charge_request(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise PublicError(400, "INVALID_JSON", "Request body must be a JSON object")
    expected = {"requestId", "mandateId", "counterparty", "amount", "memo"}
    if set(value) != expected:
        raise PublicError(
            400,
            "INVALID_FIELDS",
            "Request must contain only requestId, mandateId, counterparty, amount and memo",
        )
    if not all(isinstance(value[key], str) for key in expected):
        raise PublicError(400, "INVALID_TYPES", "Every request field must be a string")
    try:
        parsed_uuid = uuid.UUID(value["requestId"])
    except ValueError as error:
        raise PublicError(400, "INVALID_REQUEST_ID", "requestId must be a UUID") from error
    if parsed_uuid.version != 4:
        raise PublicError(400, "INVALID_REQUEST_ID", "requestId must be a UUIDv4")
    mandate_id = value["mandateId"]
    if not mandate_id or len(mandate_id) > 160 or CONTROL_RE.search(mandate_id):
        raise PublicError(400, "INVALID_MANDATE_ID", "mandateId is malformed")
    counterparty = value["counterparty"]
    if not PARTY_RE.fullmatch(counterparty):
        raise PublicError(400, "INVALID_COUNTERPARTY", "counterparty must be an exact Canton Party ID")
    amount_text = value["amount"]
    if not AMOUNT_RE.fullmatch(amount_text):
        raise PublicError(400, "INVALID_AMOUNT", "amount must be a plain positive decimal string")
    amount = Decimal(amount_text)
    if amount <= 0:
        raise PublicError(400, "INVALID_AMOUNT", "amount must be greater than zero")
    memo = value["memo"]
    if not memo or len(memo) > 160 or CONTROL_RE.search(memo):
        raise PublicError(400, "INVALID_MEMO", "memo must contain 1 to 160 printable characters")
    return {
        "requestId": str(parsed_uuid),
        "mandateId": mandate_id,
        "counterparty": counterparty,
        "amount": decimal_text(amount),
        "memo": memo,
    }


def execute_charge(
    client: FixedAgentClient,
    store: OperationStore,
    request: dict[str, str],
) -> dict[str, Any]:
    canonical_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    with CHARGE_LOCK:
        existing = store.begin(request["requestId"], request_hash, canonical_json)
        if existing:
            return existing
        try:
            mandate = client.resolve_mandate(request["mandateId"])
            prepared = client.prepare_transfer(
                mandate,
                counterparty=request["counterparty"],
                amount=Decimal(request["amount"]),
                memo=request["memo"],
            )
            response = client.submit_charge(
                mandate,
                prepared,
                counterparty=request["counterparty"],
                amount=Decimal(request["amount"]),
                memo=request["memo"],
                request_id=request["requestId"],
            )
            transaction = response.get("transaction", {})
            created = [
                event["CreatedEvent"]
                for event in transaction.get("events", [])
                if "CreatedEvent" in event
            ]
            audits = [
                event for event in created if event.get("templateId") == TEMPLATES["TokenChargeAudit"]
            ]
            successors = [
                event for event in created if event.get("templateId") == TEMPLATES["TokenMandate"]
            ]
            if len(audits) != 1 or len(successors) != 1:
                raise PublicError(
                    502,
                    "INCOMPLETE_TRANSACTION_EVIDENCE",
                    "Committed transaction did not return exactly one audit and successor mandate",
                    source="LEDGER",
                    uncertain=True,
                )
            audit = audits[0]
            result = {
                "requestId": request["requestId"],
                "mandateId": request["mandateId"],
                "status": "COMMITTED",
                "evidenceSource": "LEDGER",
                "code": "OK",
                "message": "Charge committed atomically on the ledger",
                "updateId": transaction.get("updateId"),
                "chargeAuditCid": audit.get("contractId"),
                "nextMandateCid": successors[0].get("contractId"),
                "receiverHoldingCids": audit.get("createArgument", {}).get("receiverHoldingCids", []),
                "statement": human_statement(audit.get("createArgument", {})),
                "registryTransferKind": prepared.get("transferKind"),
                "disclosedContractCount": len(prepared.get("disclosures", [])),
                "submittedAs": {
                    "userId": AGENT_USER,
                    "actAs": [client.agent_party],
                    "readAs": [client.owner_party],
                },
                "createdAt": iso(),
                "replayed": False,
            }
        except PublicError as error:
            result = {
                "requestId": request["requestId"],
                "mandateId": request["mandateId"],
                "status": "UNCERTAIN" if error.uncertain else "REJECTED",
                "evidenceSource": error.source,
                "code": error.code,
                "message": error.message,
                "correlationId": error.correlation_id,
                "submittedAs": {
                    "userId": AGENT_USER,
                    "actAs": [client.agent_party],
                    "readAs": [client.owner_party],
                },
                "createdAt": iso(),
                "replayed": False,
            }
        store.finish(request["requestId"], result)
        return result


class DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        client: FixedAgentClient,
        store: OperationStore,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, handler)
        self.client = client
        self.store = store
        self.config = config
        self.csrf_token = secrets.token_urlsafe(32)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "Cantor8-D1"

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format_string % args}")

    def security_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; font-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )

    def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.security_headers(content_type)
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def send_json(self, status: int, value: Any) -> None:
        self.send_bytes(
            status,
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def send_error_payload(self, error: PublicError) -> None:
        self.send_json(error.status, {"error": error.payload()})

    def validate_host(self) -> None:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            raise PublicError(421, "INVALID_HOST", "Unexpected Host header")

    def do_GET(self) -> None:
        try:
            self.validate_host()
            path = urllib.parse.urlparse(self.path).path
            if path == "/healthz":
                self.send_json(200, {"status": "ok", "scope": "loopback-agent-only"})
                return
            if path == "/api/state":
                self.send_json(
                    200,
                    project_dashboard(
                        self.server.client,
                        self.server.store,
                        csrf_token=self.server.csrf_token,
                        config=self.server.config,
                    ),
                )
                return
            static = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            }
            if path not in static:
                raise PublicError(404, "NOT_FOUND", "Route not found")
            filename, content_type = static[path]
            self.send_bytes(200, (STATIC_ROOT / filename).read_bytes(), content_type)
        except PublicError as error:
            self.send_error_payload(error)
        except OSError:
            self.send_error_payload(PublicError(500, "STATIC_ASSET_ERROR", "Static asset is unavailable"))

    def do_POST(self) -> None:
        try:
            self.validate_host()
            if urllib.parse.urlparse(self.path).path != "/api/charge":
                raise PublicError(404, "NOT_FOUND", "Route not found")
            if self.headers.get("Origin") not in self.server.allowed_origins:
                raise PublicError(403, "INVALID_ORIGIN", "Charge requests must be same-origin")
            if not secrets.compare_digest(
                self.headers.get("X-D1-CSRF", ""), self.server.csrf_token
            ):
                raise PublicError(403, "INVALID_CSRF", "Charge request is missing its session nonce")
            if self.headers.get_content_type() != "application/json":
                raise PublicError(415, "INVALID_CONTENT_TYPE", "Charge request must be JSON")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as error:
                raise PublicError(411, "INVALID_LENGTH", "Content-Length is required") from error
            if length <= 0 or length > MAX_BODY_BYTES:
                raise PublicError(413, "BODY_TOO_LARGE", "Charge request body is empty or too large")
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise PublicError(400, "INVALID_JSON", "Charge request contains invalid JSON") from error
            request = validate_charge_request(body)
            result = execute_charge(self.server.client, self.server.store, request)
            self.send_json(200, {"operation": result})
        except PublicError as error:
            self.send_error_payload(error)

    def do_OPTIONS(self) -> None:
        self.send_error_payload(PublicError(405, "METHOD_NOT_ALLOWED", "CORS is not enabled"))


def read_token(path: str) -> str:
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PublicError(500, "TOKEN_FILE_ERROR", "Could not read the agent token file") from error
    if not token or any(char.isspace() for char in token):
        raise PublicError(500, "INVALID_TOKEN", "Agent token file is empty or malformed")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description="Cantor8 D1 loopback judge console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument(
        "--agent-token-file",
        default=os.environ.get("D1_AGENT_TOKEN_FILE"),
        help="file descriptor or private file containing a d1-agent-user bearer token",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    try:
        require_loopback_bind(args.host)
        if not args.agent_token_file:
            raise PublicError(
                500,
                "TOKEN_FILE_REQUIRED",
                "Use --agent-token-file; credentials are never accepted in arguments or browser requests",
            )
        client = FixedAgentClient(read_token(args.agent_token_file))
        store = OperationStore(args.database)
        config = load_demo_config(args.config)
        project_dashboard(
            client,
            store,
            csrf_token="startup-check",
            config=config,
        )
        server = DemoHTTPServer(
            (args.host, args.port),
            DemoHandler,
            client=client,
            store=store,
            config=config,
        )
        print(f"D1 judge console: http://{args.host}:{args.port}/")
        print("Capability: d1-agent-user only; owner revocation remains in Terminal")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("D1 judge console stopped")
        finally:
            server.server_close()
    except (PublicError, OSError) as error:
        message = error.message if isinstance(error, PublicError) else str(error)
        print(f"ERROR: {message}", file=os.sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
