#!/usr/bin/env python3
"""Run the D1 wallet against the disposable Splice LocalNet.

This adapter makes no spending-policy decisions. It selects owner holdings,
obtains the registry's opaque choice context and disclosures, and submits one
root TokenMandate exercise. All cap, counterparty, expiry, input, revocation
and audit rules remain in Daml.

The smoke command creates least-privilege local users, performs one real
Amulet payment, and proves the important rejection paths. It hard-fails unless
the configured endpoints match the pinned loopback-only LocalNet boundary.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
import urllib.parse
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT_ROOT))

import c8lab  # noqa: E402


PACKAGE_NAME = "cantor8-d1-token-wallet"
MODULE_NAME = "TokenMandate"
TRANSFER_FACTORY = c8lab.TRANSFER_FACTORY

AGENT_USER = "d1-agent-user"
OWNER_USER = "d1-owner-user"
AUDITOR_USER = "d1-merchant-auditor-user"
LOCAL_AMULET_PACKAGE_ID = (
    "90987abecbcb1d004b063ddfe3b4b5d46cf3814ce89114a86c8cd75ff3cb8a4b"
)
PRODUCTION_DAR = (
    TOOLKIT_ROOT
    / "daml-token-wallet"
    / ".daml"
    / "dist"
    / "cantor8-d1-token-wallet-0.1.2.dar"
)
TEST_DAR = (
    TOOLKIT_ROOT
    / "daml-token-wallet-test"
    / ".daml"
    / "dist"
    / "cantor8-d1-token-wallet-test-0.1.0.dar"
)
DEFAULT_DEMO_CONFIG = TOOLKIT_ROOT / "daml-token-wallet" / ".daml" / "demo-config.json"


def template_filter(template_name: str) -> str:
    """Return the package-name form required by JSON API v2 ACS filters."""
    return f"#{PACKAGE_NAME}:{MODULE_NAME}:{template_name}"


def exact_template_id(package_id: str, template_name: str) -> str:
    """Return one production-package-pinned template identifier."""
    if not re.fullmatch(r"[0-9a-f]{64}", package_id):
        raise c8lab.LabError("production package ID is malformed")
    return f"{package_id}:{MODULE_NAME}:{template_name}"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def main_package_id(dar_path: Path) -> str:
    with zipfile.ZipFile(dar_path) as archive:
        manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8")
    unfolded = manifest.replace("\r\n ", "").replace("\n ", "")
    match = re.search(r"^Main-Dalf: .*?([0-9a-f]{64})\.dalf$", unfolded, re.MULTILINE)
    if not match:
        raise c8lab.LabError(f"could not read the main package ID from {dar_path}")
    return match.group(1)


def iso(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def require_loopback_endpoint(label: str, value: str, expected_port: int) -> None:
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port != expected_port
    ):
        raise c8lab.LabError(
            f"{label} must be the default loopback HTTP endpoint on port "
            f"{expected_port}; found {value!r}"
        )


def require_localnet_endpoints() -> None:
    if c8lab.IDP:
        raise c8lab.LabError(
            "this smoke runner is intentionally LocalNet-only; unset C8_IDP"
        )
    require_loopback_endpoint("C8_BASE", c8lab.BASE, 2975)
    require_loopback_endpoint("C8_REGISTRY", c8lab.REGISTRY, 4000)
    if c8lab.REGISTRY_HOST != "scan.localhost":
        raise c8lab.LabError(
            "C8_REGISTRY_HOST must be 'scan.localhost' for the pinned LocalNet"
        )
    if c8lab.REGISTRY_PREFIX:
        raise c8lab.LabError(
            "C8_REGISTRY_PREFIX must be empty for the pinned LocalNet"
        )


def exact_party(prefix: str) -> str:
    for party in c8lab.local_parties():
        if party.split("::", 1)[0] == prefix:
            return party
    raise c8lab.LabError(f"no exact local Party prefix '{prefix}'")


def ensure_party(hint: str) -> str:
    try:
        return exact_party(hint)
    except c8lab.LabError:
        return c8lab.allocate_party(hint, grant_to=None)


def encoded_right(kind: str, party: str) -> dict[str, Any]:
    return {"kind": {kind: {"value": {"party": party}}}}


def decoded_right(right: dict[str, Any]) -> tuple[str, str]:
    kinds = right.get("kind", {})
    if len(kinds) != 1:
        return ("MalformedRight", json.dumps(right, sort_keys=True))
    kind, payload = next(iter(kinds.items()))
    party = (payload or {}).get("value", {}).get("party", "")
    return (kind, party)


def read_user_rights(user_id: str) -> list[tuple[str, str]]:
    response = c8lab.call(f"/v2/users/{user_id}/rights", sub=c8lab.ADMIN)
    return sorted(decoded_right(right) for right in response.get("rights", []))


def read_own_user_context(user_id: str) -> tuple[str, list[tuple[str, str]]]:
    """Resolve one fixed user's own primary Party and rights without admin."""
    response = c8lab.call(f"/v2/users/{user_id}", sub=user_id)
    user = response.get("user", {})
    if user.get("id") != user_id or user.get("isDeactivated"):
        raise c8lab.LabError(f"{user_id} is missing or deactivated")
    primary_party = user.get("primaryParty", "")
    rights_response = c8lab.call(f"/v2/users/{user_id}/rights", sub=user_id)
    rights = sorted(
        decoded_right(right) for right in rights_response.get("rights", [])
    )
    return primary_party, rights


def ensure_user(
    user_id: str,
    primary_party: str,
    required_rights: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    users = c8lab.call("/v2/users", sub=c8lab.ADMIN).get("users", [])
    existing = next((user for user in users if user.get("id") == user_id), None)
    if existing is None:
        body = {
            "user": {
                "id": user_id,
                "primaryParty": primary_party,
                "isDeactivated": False,
                "identityProviderId": "",
            },
            "rights": [encoded_right(kind, party) for kind, party in required_rights],
        }
        c8lab.call("/v2/users", body, sub=c8lab.ADMIN)
    else:
        if existing.get("isDeactivated"):
            raise c8lab.LabError(f"{user_id} exists but is deactivated")
        if existing.get("primaryParty", "") != primary_party:
            raise c8lab.LabError(
                f"{user_id} primary Party is not the expected Party"
            )

    actual = read_user_rights(user_id)
    required = sorted(required_rights)
    if actual != required:
        raise c8lab.LabError(
            f"{user_id} rights are not least-privilege: "
            f"expected {required!r}, found {actual!r}"
        )
    return actual


def verify_localnet_packages() -> dict[str, Any]:
    """Fail closed unless the current production/test package boundary holds."""
    if not PRODUCTION_DAR.exists():
        raise c8lab.LabError(f"production DAR is missing: {PRODUCTION_DAR}")
    production_package_id = main_package_id(PRODUCTION_DAR)
    test_package_id = main_package_id(TEST_DAR) if TEST_DAR.exists() else None
    installed_packages = set(
        c8lab.call("/v2/packages", sub=c8lab.ADMIN).get("packageIds", [])
    )
    if LOCAL_AMULET_PACKAGE_ID not in installed_packages:
        raise c8lab.LabError(
            "the expected Splice 0.6.8 Amulet package is not installed"
        )
    if production_package_id not in installed_packages:
        raise c8lab.LabError(
            "the current production D1 package is not installed on LocalNet"
        )
    if test_package_id and test_package_id in installed_packages:
        raise c8lab.LabError(
            "the test-only mock package is installed on LocalNet; use a clean "
            "LocalNet before treating this as production-package evidence"
        )
    return {
        "productionPackageId": production_package_id,
        "testPackageId": test_package_id,
        "testPackageInstalled": False,
        "amuletPackageId": LOCAL_AMULET_PACKAGE_ID,
    }


def submit_transaction(
    commands: list[dict[str, Any]],
    *,
    user_id: str,
    act_as: list[str],
    read_as: list[str] | None = None,
    disclosures: list[dict[str, Any]] | None = None,
    command_label: str,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "commands": commands,
        "commandId": f"d1-{command_label}-{uuid.uuid4()}",
        "actAs": act_as,
        "userId": user_id,
    }
    if read_as:
        envelope["readAs"] = read_as
    if disclosures:
        envelope["disclosedContracts"] = [
            {
                "templateId": item["templateId"],
                "contractId": item["contractId"],
                "createdEventBlob": item["createdEventBlob"],
                "synchronizerId": item.get("synchronizerId", ""),
            }
            for item in disclosures
        ]
    return c8lab.call(
        "/v2/commands/submit-and-wait-for-transaction",
        {"commands": envelope},
        sub=user_id,
    )


def transaction_id(response: dict[str, Any]) -> str:
    return response.get("transaction", {}).get("updateId", "")


def created_events(response: dict[str, Any]) -> list[dict[str, Any]]:
    events = response.get("transaction", {}).get("events", [])
    return [event["CreatedEvent"] for event in events if "CreatedEvent" in event]


def find_created(
    response: dict[str, Any],
    template_name: str,
    *,
    package_id: str,
) -> dict[str, Any]:
    expected = exact_template_id(package_id, template_name)
    suffix = f":{MODULE_NAME}:{template_name}"
    named = [
        event for event in created_events(response)
        if event.get("templateId", "").endswith(suffix)
    ]
    unexpected = [
        event.get("templateId", "")
        for event in named
        if event.get("templateId") != expected
    ]
    if unexpected:
        raise c8lab.LabError(
            f"created {template_name} used an unexpected package ID: "
            f"{unexpected!r}"
        )
    matches = [event for event in named if event.get("templateId") == expected]
    if len(matches) != 1:
        raise c8lab.LabError(
            f"expected one created {expected}, found {len(matches)}"
        )
    return matches[0]


def exercise_command(
    template_id: str,
    contract_id: str,
    choice: str,
    argument: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ExerciseCommand": {
            "templateId": template_id,
            "contractId": contract_id,
            "choice": choice,
            "choiceArgument": argument,
        }
    }


def amulet_balance(party: str, *, user_id: str, admin: str) -> Decimal:
    return sum(
        (
            Decimal(str(holding["amount"]))
            for holding in c8lab.holdings(party, sub=user_id)
            if holding["instrument"] == "Amulet"
            and holding["admin"] == admin
        ),
        Decimal("0"),
    )


def active_charge_audits(agent: str, *, package_id: str) -> list[dict[str, Any]]:
    rows = active_template_events(
        agent,
        "TokenChargeAudit",
        package_id=package_id,
        user_id=AGENT_USER,
    )
    return sorted(
        [
            {
                "contractId": event.get("contractId", ""),
                "audit": event["createArgument"],
            }
            for event in rows
        ],
        key=lambda row: row["audit"].get("chargedAt", ""),
    )


def active_template_events(
    viewer: str,
    template_name: str,
    *,
    package_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """Query by package name, then fail closed on the exact returned package."""
    expected = exact_template_id(package_id, template_name)
    body = {
        "filter": {
            "filtersByParty": {
                viewer: {
                    "cumulative": [
                        {
                            "identifierFilter": {
                                "TemplateFilter": {
                                    "value": {
                                        "templateId": template_filter(template_name),
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
        "activeAtOffset": c8lab.ledger_end(user_id),
    }
    contracts = c8lab.call(
        "/v2/state/active-contracts", body, sub=user_id
    )
    events = []
    for contract in contracts:
        event = (
            contract.get("contractEntry", {})
            .get("JsActiveContract", {})
            .get("createdEvent", {})
        )
        if event and event.get("templateId") != expected:
            raise c8lab.LabError(
                f"active {template_name} resolved to unexpected template "
                f"{event.get('templateId')!r}; expected {expected!r}"
            )
        if event.get("createArgument"):
            events.append(event)
    return events


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


def spendable_holdings(owner: str, *, user_id: str, admin: str) -> list[dict[str, Any]]:
    return [
        holding
        for holding in c8lab.holdings(owner, sub=user_id)
        if not holding["locked"]
        and holding["instrument"] == "Amulet"
        and holding["admin"] == admin
        and Decimal(str(holding["amount"])) > 0
    ]


def prepare_transfer(
    *,
    owner: str,
    receiver: str,
    amount: Decimal,
    memo: str,
    mandate_id: str,
    admin: str,
) -> dict[str, Any]:
    holdings = spendable_holdings(owner, user_id=AGENT_USER, admin=admin)
    available = sum(
        (Decimal(str(holding["amount"])) for holding in holdings),
        Decimal("0"),
    )
    if not holdings or available < amount:
        raise c8lab.LabError(
            f"owner has {available} unlocked Amulet but {amount} is required"
        )

    requested_at = utc_now()
    execute_before = requested_at + dt.timedelta(minutes=5)
    transfer_meta = {
        "values": {
            "splice.lfdecentralizedtrust.org/reason": memo,
            "cantor8.local/mandate-id": mandate_id,
        }
    }
    arguments = {
        "expectedAdmin": admin,
        "transfer": {
            "sender": owner,
            "receiver": receiver,
            "amount": str(amount),
            "instrumentId": {"admin": admin, "id": "Amulet"},
            "requestedAt": iso(requested_at),
            "executeBefore": iso(execute_before),
            "inputHoldingCids": [holding["contractId"] for holding in holdings],
            "meta": transfer_meta,
        },
        "extraArgs": {
            "context": {"values": {}},
            "meta": {"values": {}},
        },
    }
    factory = c8lab.registry(
        "/registry/transfer-instruction/v1/transfer-factory",
        {"choiceArguments": arguments},
    )
    choice_context = factory.get("choiceContext", {})
    if not factory.get("factoryId"):
        raise c8lab.LabError("registry returned no transfer factory contract ID")
    return {
        "amount": amount,
        "memo": memo,
        "receiver": receiver,
        "requestedAt": iso(requested_at),
        "executeBefore": iso(execute_before),
        "inputHoldingCids": [holding["contractId"] for holding in holdings],
        "factoryId": factory["factoryId"],
        "transferKind": factory.get("transferKind"),
        "choiceContext": choice_context.get("choiceContextData", {}),
        "disclosures": choice_context.get("disclosedContracts", []),
        "factoryArguments": arguments,
    }


def submit_charge(
    mandate_event: dict[str, Any],
    prepared: dict[str, Any],
    *,
    owner: str,
    agent: str,
) -> dict[str, Any]:
    argument = {
        "counterparty": prepared["receiver"],
        "amount": str(prepared["amount"]),
        "memo": prepared["memo"],
        "factoryCid": prepared["factoryId"],
        "inputHoldingCids": prepared["inputHoldingCids"],
        "requestedAt": prepared["requestedAt"],
        "executeBefore": prepared["executeBefore"],
        "choiceContext": prepared["choiceContext"],
    }
    return submit_transaction(
        [
            exercise_command(
                mandate_event["templateId"],
                mandate_event["contractId"],
                "ChargeToken",
                argument,
            )
        ],
        user_id=AGENT_USER,
        act_as=[agent],
        read_as=[owner],
        disclosures=prepared["disclosures"],
        command_label="charge",
    )


def submit_direct_bypass(
    prepared: dict[str, Any],
    *,
    owner: str,
    agent: str,
) -> dict[str, Any]:
    arguments = copy.deepcopy(prepared["factoryArguments"])
    arguments["extraArgs"]["context"] = prepared["choiceContext"]
    return submit_transaction(
        [
            exercise_command(
                TRANSFER_FACTORY,
                prepared["factoryId"],
                "TransferFactory_Transfer",
                arguments,
            )
        ],
        user_id=AGENT_USER,
        act_as=[agent],
        read_as=[owner],
        disclosures=prepared["disclosures"],
        command_label="direct-bypass",
    )


def concise_error(error: Exception) -> str:
    text = str(error).replace("\n", " ")
    return " ".join(text.split())[:700]


def expect_rejection(
    name: str,
    operation: Callable[[], Any],
    expected_fragments: tuple[str, ...],
) -> dict[str, Any]:
    try:
        operation()
    except c8lab.LabError as error:
        message = concise_error(error)
        lower = message.lower()
        if expected_fragments and not any(fragment in lower for fragment in expected_fragments):
            raise c8lab.LabError(
                f"{name} was rejected for an unexpected reason: {message}"
            ) from error
        return {"rejected": True, "evidence": message}
    raise c8lab.LabError(f"{name} unexpectedly succeeded")


def smoke(args: argparse.Namespace) -> dict[str, Any]:
    require_localnet_endpoints()
    owner = exact_party(args.owner)
    merchant = exact_party(args.merchant)
    outsider = exact_party(args.outsider)
    agent = ensure_party(args.agent)
    admin = c8lab.admin_party()
    package_boundary = verify_localnet_packages()
    production_package_id = package_boundary["productionPackageId"]
    test_package_id = package_boundary["testPackageId"]

    agent_rights = ensure_user(
        AGENT_USER,
        agent,
        [("CanActAs", agent), ("CanReadAs", owner)],
    )
    owner_rights = ensure_user(
        OWNER_USER,
        owner,
        [("CanActAs", owner)],
    )
    auditor_rights = ensure_user(
        AUDITOR_USER,
        "",
        [("CanReadAs", merchant)],
    )

    now = utc_now()
    mandate_id = f"d1-localnet-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    expires_at = now + dt.timedelta(minutes=args.expires_minutes)
    proposal_response = submit_transaction(
        [
            {
                "CreateCommand": {
                    "templateId": exact_template_id(
                        production_package_id, "TokenMandateProposal"
                    ),
                    "createArguments": {
                        "mandateId": mandate_id,
                        "owner": owner,
                        "spender": agent,
                        "expectedAdmin": admin,
                        "instrumentId": {"admin": admin, "id": "Amulet"},
                        "cap": str(args.cap),
                        "allowedCounterparties": [merchant],
                        "expiresAt": iso(expires_at),
                    },
                }
            }
        ],
        user_id=OWNER_USER,
        act_as=[owner],
        command_label="proposal",
    )
    proposal = find_created(
        proposal_response,
        "TokenMandateProposal",
        package_id=production_package_id,
    )

    activation_response = submit_transaction(
        [
            exercise_command(
                proposal["templateId"], proposal["contractId"], "Accept", {}
            )
        ],
        user_id=AGENT_USER,
        act_as=[agent],
        read_as=[owner],
        command_label="accept",
    )
    control = find_created(
        activation_response,
        "TokenMandateControl",
        package_id=production_package_id,
    )
    mandate = find_created(
        activation_response,
        "TokenMandate",
        package_id=production_package_id,
    )
    activation_audit = find_created(
        activation_response,
        "TokenActivationAudit",
        package_id=production_package_id,
    )

    owner_before = amulet_balance(owner, user_id=AGENT_USER, admin=admin)
    merchant_before = amulet_balance(merchant, user_id=AUDITOR_USER, admin=admin)
    prepared_success = prepare_transfer(
        owner=owner,
        receiver=merchant,
        amount=args.amount,
        memo=args.memo,
        mandate_id=mandate_id,
        admin=admin,
    )
    if prepared_success["transferKind"] != "direct":
        raise c8lab.LabError(
            "merchant has no accepted transfer preapproval; registry returned "
            f"{prepared_success['transferKind']!r}, so no payment was submitted"
        )

    charge_response = submit_charge(
        mandate, prepared_success, owner=owner, agent=agent
    )
    next_mandate = find_created(
        charge_response,
        "TokenMandate",
        package_id=production_package_id,
    )
    charge_audit = find_created(
        charge_response,
        "TokenChargeAudit",
        package_id=production_package_id,
    )
    audit_argument = charge_audit.get("createArgument", {})
    if Decimal(audit_argument.get("amount", "0")) != args.amount:
        raise c8lab.LabError("charge audit amount does not match the requested amount")
    if Decimal(audit_argument.get("spentAfter", "0")) != args.amount:
        raise c8lab.LabError("successor cumulative spend did not advance exactly once")
    if not audit_argument.get("receiverHoldingCids"):
        raise c8lab.LabError("charge audit contains no receiver holding IDs")

    owner_after = amulet_balance(owner, user_id=AGENT_USER, admin=admin)
    merchant_after = amulet_balance(merchant, user_id=AUDITOR_USER, admin=admin)
    merchant_delta = merchant_after - merchant_before
    if merchant_delta != args.amount:
        raise c8lab.LabError(
            f"merchant balance changed by {merchant_delta}, expected {args.amount}"
        )

    over_cap = prepare_transfer(
        owner=owner,
        receiver=merchant,
        amount=args.cap,
        memo="Deliberate over-cap smoke attempt",
        mandate_id=mandate_id,
        admin=admin,
    )
    over_cap_result = expect_rejection(
        "over-cap charge",
        lambda: submit_charge(next_mandate, over_cap, owner=owner, agent=agent),
        ("exceed the total cap",),
    )

    forbidden = prepare_transfer(
        owner=owner,
        receiver=outsider,
        amount=Decimal("0.05"),
        memo="Deliberate forbidden-counterparty smoke attempt",
        mandate_id=mandate_id,
        admin=admin,
    )
    forbidden_result = expect_rejection(
        "forbidden-counterparty charge",
        lambda: submit_charge(next_mandate, forbidden, owner=owner, agent=agent),
        ("counterparty is not allowed",),
    )

    bypass = prepare_transfer(
        owner=owner,
        receiver=merchant,
        amount=Decimal("0.05"),
        memo="Deliberate direct-transfer bypass attempt",
        mandate_id=mandate_id,
        admin=admin,
    )
    bypass_result = expect_rejection(
        "direct TransferFactory bypass",
        lambda: submit_direct_bypass(bypass, owner=owner, agent=agent),
        ("authorization", "authorizer"),
    )

    agent_revoke_result = expect_rejection(
        "agent revocation",
        lambda: submit_transaction(
            [
                exercise_command(
                    control["templateId"],
                    control["contractId"],
                    "Revoke",
                    {"reason": "Agent must not control owner revocation"},
                )
            ],
            user_id=AGENT_USER,
            act_as=[agent],
            read_as=[owner],
            command_label="agent-revoke",
        ),
        ("authorization", "authorizer"),
    )

    revocation_response = submit_transaction(
        [
            exercise_command(
                control["templateId"],
                control["contractId"],
                "Revoke",
                {"reason": "Owner completed D1 LocalNet revocation smoke check"},
            )
        ],
        user_id=OWNER_USER,
        act_as=[owner],
        command_label="owner-revoke",
    )
    revocation_audit = find_created(
        revocation_response,
        "TokenRevocationAudit",
        package_id=production_package_id,
    )

    post_revoke = prepare_transfer(
        owner=owner,
        receiver=merchant,
        amount=Decimal("0.05"),
        memo="Deliberate post-revocation smoke attempt",
        mandate_id=mandate_id,
        admin=admin,
    )
    post_revoke_result = expect_rejection(
        "post-revocation charge",
        lambda: submit_charge(next_mandate, post_revoke, owner=owner, agent=agent),
        (
            "contract_not_found",
            "could not be found",
            "not active",
            "inactive",
            "dependency",
        ),
    )

    merchant_final = amulet_balance(merchant, user_id=AUDITOR_USER, admin=admin)
    if merchant_final != merchant_after:
        raise c8lab.LabError(
            "a rejected adversarial command changed the merchant balance"
        )

    return {
        "status": "passed",
        "scope": (
            "loopback Splice 0.6.8 LocalNet with the expected Amulet package; "
            "not DevNet or production"
        ),
        "package": {
            "name": PACKAGE_NAME,
            "packageId": production_package_id,
            "currentTestOnlyMockPackageId": test_package_id,
            "currentTestOnlyMockPackageInstalled": False,
        },
        "identities": {
            "owner": owner,
            "agent": agent,
            "merchant": merchant,
            "forbiddenCounterparty": outsider,
            "users": {
                AGENT_USER: agent_rights,
                OWNER_USER: owner_rights,
                AUDITOR_USER: auditor_rights,
            },
        },
        "mandate": {
            "mandateId": mandate_id,
            "cap": str(args.cap),
            "allowedCounterparties": [merchant],
            "expiresAt": iso(expires_at),
            "proposalUpdateId": transaction_id(proposal_response),
            "activationUpdateId": transaction_id(activation_response),
            "activationAuditCid": activation_audit["contractId"],
            "stableControlCid": control["contractId"],
        },
        "realAmuletCharge": {
            "amount": str(args.amount),
            "memo": args.memo,
            "registryTransferKind": prepared_success["transferKind"],
            "disclosedContractCount": len(prepared_success["disclosures"]),
            "updateId": transaction_id(charge_response),
            "nextMandateCid": next_mandate["contractId"],
            "chargeAuditCid": charge_audit["contractId"],
            "receiverHoldingCids": audit_argument["receiverHoldingCids"],
            "ownerBalanceBefore": str(owner_before),
            "ownerBalanceAfter": str(owner_after),
            "merchantBalanceBefore": str(merchant_before),
            "merchantBalanceAfter": str(merchant_after),
            "merchantDelta": str(merchant_delta),
            "humanStatement": human_statement(audit_argument),
        },
        "rejections": {
            "overCap": over_cap_result,
            "forbiddenCounterparty": forbidden_result,
            "directFactoryBypass": bypass_result,
            "agentCannotRevoke": agent_revoke_result,
            "postRevocation": post_revoke_result,
        },
        "revocation": {
            "updateId": transaction_id(revocation_response),
            "auditCid": revocation_audit["contractId"],
            "stableControlConsumedAfterSuccessorCreated": True,
        },
        "rejectedCommandsChangedMerchantBalance": False,
        "limitations": [
            "LocalNet authentication uses the known unsafe development HS256 secret.",
            "The cap covers requested transfer principal, not separate Canton traffic or token fees.",
            "A LocalNet pass does not prove DevNet vetting, synchronizer, identity-provider or production behaviour.",
        ],
    }


def decimal_argument(value: str) -> Decimal:
    parsed = Decimal(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def provision(args: argparse.Namespace) -> dict[str, Any]:
    """Create one fresh active demo mandate, then exit before HTTP starts."""
    require_localnet_endpoints()
    package_boundary = verify_localnet_packages()
    production_package_id = package_boundary["productionPackageId"]
    owner = exact_party(args.owner)
    merchant = exact_party(args.merchant)
    outsider = exact_party(args.outsider)
    agent = ensure_party(args.agent)
    admin = c8lab.admin_party()

    agent_rights = ensure_user(
        AGENT_USER,
        agent,
        [("CanActAs", agent), ("CanReadAs", owner)],
    )
    owner_rights = ensure_user(
        OWNER_USER,
        owner,
        [("CanActAs", owner)],
    )
    auditor_rights = ensure_user(
        AUDITOR_USER,
        "",
        [("CanReadAs", merchant)],
    )

    now = utc_now()
    mandate_id = args.mandate_id or (
        f"d1-demo-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    )
    expires_at = now + dt.timedelta(minutes=args.expires_minutes)
    proposal_response = submit_transaction(
        [
            {
                "CreateCommand": {
                    "templateId": exact_template_id(
                        production_package_id, "TokenMandateProposal"
                    ),
                    "createArguments": {
                        "mandateId": mandate_id,
                        "owner": owner,
                        "spender": agent,
                        "expectedAdmin": admin,
                        "instrumentId": {"admin": admin, "id": "Amulet"},
                        "cap": str(args.cap),
                        "allowedCounterparties": [merchant],
                        "expiresAt": iso(expires_at),
                    },
                }
            }
        ],
        user_id=OWNER_USER,
        act_as=[owner],
        command_label="demo-proposal",
    )
    proposal_event = find_created(
        proposal_response,
        "TokenMandateProposal",
        package_id=production_package_id,
    )
    activation_response = submit_transaction(
        [
            exercise_command(
                proposal_event["templateId"],
                proposal_event["contractId"],
                "Accept",
                {},
            )
        ],
        user_id=AGENT_USER,
        act_as=[agent],
        read_as=[owner],
        command_label="demo-accept",
    )
    control = find_created(
        activation_response,
        "TokenMandateControl",
        package_id=production_package_id,
    )
    mandate = find_created(
        activation_response,
        "TokenMandate",
        package_id=production_package_id,
    )
    activation_audit = find_created(
        activation_response,
        "TokenActivationAudit",
        package_id=production_package_id,
    )

    config = {
        "scope": "loopback Splice 0.6.8 LocalNet; not DevNet or production",
        "createdAt": iso(now),
        "productionPackageId": package_boundary["productionPackageId"],
        "testOnlyPackageId": package_boundary["testPackageId"],
        "testOnlyPackageInstalled": False,
        "amuletPackageId": package_boundary["amuletPackageId"],
        "owner": owner,
        "agent": agent,
        "allowedCounterparty": merchant,
        "forbiddenCounterparty": outsider,
        "expectedAdmin": admin,
        "instrument": "Amulet",
        "mandateId": mandate_id,
        "cap": str(args.cap),
        "expiresAt": iso(expires_at),
        "users": {
            AGENT_USER: agent_rights,
            OWNER_USER: owner_rights,
            AUDITOR_USER: auditor_rights,
        },
    }
    config_path = args.config.resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    config_path.chmod(0o600)

    return {
        "status": "active",
        "scope": config["scope"],
        "mandateId": mandate_id,
        "cap": str(args.cap),
        "spent": "0.0",
        "allowedCounterparties": [merchant],
        "expiresAt": iso(expires_at),
        "proposalUpdateId": transaction_id(proposal_response),
        "activationUpdateId": transaction_id(activation_response),
        "stableControlCid": control["contractId"],
        "mandateCid": mandate["contractId"],
        "activationAuditCid": activation_audit["contractId"],
        "configPath": str(config_path),
        "agentRights": agent_rights,
        "ownerRights": owner_rights,
        "next": (
            "start demo_server.py with a d1-agent-user token; owner revocation "
            "remains the separate owner-revoke command"
        ),
    }


def owner_revoke(args: argparse.Namespace) -> dict[str, Any]:
    """Consume one stable gate as the fixed owner user; no admin is required."""
    require_localnet_endpoints()
    owner, rights = read_own_user_context(OWNER_USER)
    if rights != [("CanActAs", owner)]:
        raise c8lab.LabError(
            f"{OWNER_USER} rights are not owner-only: {rights!r}"
        )
    production_package_id = main_package_id(PRODUCTION_DAR)
    controls = active_template_events(
        owner,
        "TokenMandateControl",
        package_id=production_package_id,
        user_id=OWNER_USER,
    )
    matches = [
        event
        for event in controls
        if event.get("createArgument", {}).get("mandateId") == args.mandate_id
        and event.get("createArgument", {}).get("owner") == owner
    ]
    if not matches:
        raise c8lab.LabError(
            f"no active stable control found for mandate {args.mandate_id!r}"
        )
    if len(matches) != 1:
        raise c8lab.LabError(
            f"multiple active stable controls found for mandate {args.mandate_id!r}"
        )
    control = matches[0]
    response = submit_transaction(
        [
            exercise_command(
                control["templateId"],
                control["contractId"],
                "Revoke",
                {"reason": args.reason},
            )
        ],
        user_id=OWNER_USER,
        act_as=[owner],
        command_label="owner-revoke",
    )
    audit = find_created(
        response,
        "TokenRevocationAudit",
        package_id=production_package_id,
    )
    return {
        "status": "revoked",
        "mandateId": args.mandate_id,
        "ownerUser": OWNER_USER,
        "owner": owner,
        "updateId": transaction_id(response),
        "stableControlCid": control["contractId"],
        "revocationAuditCid": audit["contractId"],
        "reason": args.reason,
    }


def statements(args: argparse.Namespace) -> dict[str, Any]:
    agent = exact_party(args.agent)
    rows = active_charge_audits(
        agent,
        package_id=main_package_id(PRODUCTION_DAR),
    )
    if args.mandate_id:
        rows = [
            row for row in rows
            if row["audit"].get("mandateId") == args.mandate_id
        ]
    return {
        "agent": agent,
        "count": len(rows),
        "statements": [
            {
                "chargeAuditCid": row["contractId"],
                "mandateId": row["audit"].get("mandateId"),
                "statement": human_statement(row["audit"]),
            }
            for row in rows
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Cantor8 D1 LocalNet wallet adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke_parser = subparsers.add_parser(
        "smoke", help="run a real Amulet success and adversarial rejection checks"
    )
    smoke_parser.add_argument("--owner", default="app_user_cantor8-local-1")
    smoke_parser.add_argument("--agent", default="d1-agent-local-1")
    smoke_parser.add_argument("--merchant", default="corridor-clear-1")
    smoke_parser.add_argument("--outsider", default="participant")
    smoke_parser.add_argument("--cap", type=decimal_argument, default=Decimal("2.0"))
    smoke_parser.add_argument("--amount", type=decimal_argument, default=Decimal("0.25"))
    smoke_parser.add_argument("--expires-minutes", type=int, default=60)
    smoke_parser.add_argument(
        "--memo", default="Hormuz route-risk data subscription"
    )
    statements_parser = subparsers.add_parser(
        "statements", help="read human statements from active charge audits"
    )
    statements_parser.add_argument("--agent", default="d1-agent-local-1")
    statements_parser.add_argument("--mandate-id")
    provision_parser = subparsers.add_parser(
        "provision",
        help="create one fresh active judge-console mandate, then exit",
    )
    provision_parser.add_argument("--owner", default="app_user_cantor8-local-1")
    provision_parser.add_argument("--agent", default="d1-agent-local-1")
    provision_parser.add_argument("--merchant", default="corridor-clear-1")
    provision_parser.add_argument("--outsider", default="participant")
    provision_parser.add_argument("--cap", type=decimal_argument, default=Decimal("2.0"))
    provision_parser.add_argument("--expires-minutes", type=int, default=120)
    provision_parser.add_argument("--mandate-id")
    provision_parser.add_argument("--config", type=Path, default=DEFAULT_DEMO_CONFIG)
    revoke_parser = subparsers.add_parser(
        "owner-revoke",
        help="revoke one stable control using only d1-owner-user",
    )
    revoke_parser.add_argument("--mandate-id", required=True)
    revoke_parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    try:
        if args.command == "smoke":
            if args.amount > args.cap:
                raise c8lab.LabError("success amount must not exceed the cap")
            if args.expires_minutes < 10:
                raise c8lab.LabError("expiry must leave at least ten minutes")
            print(json.dumps(smoke(args), indent=2))
        elif args.command == "statements":
            print(json.dumps(statements(args), indent=2))
        elif args.command == "provision":
            if args.expires_minutes < 10 or args.expires_minutes > 24 * 60:
                raise c8lab.LabError(
                    "demo expiry must be between 10 minutes and 24 hours"
                )
            if args.mandate_id and (
                len(args.mandate_id) > 160
                or any(ord(char) < 32 for char in args.mandate_id)
            ):
                raise c8lab.LabError("mandate ID is malformed")
            print(json.dumps(provision(args), indent=2))
        elif args.command == "owner-revoke":
            if (
                not args.mandate_id
                or len(args.mandate_id) > 160
                or any(ord(char) < 32 for char in args.mandate_id)
            ):
                raise c8lab.LabError("mandate ID is malformed")
            if (
                not args.reason
                or len(args.reason) > 160
                or any(ord(char) < 32 for char in args.reason)
            ):
                raise c8lab.LabError(
                    "revocation reason must contain 1 to 160 printable characters"
                )
            print(json.dumps(owner_revoke(args), indent=2))
    except c8lab.LabError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
