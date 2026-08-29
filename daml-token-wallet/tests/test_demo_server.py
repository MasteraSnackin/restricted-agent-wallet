from __future__ import annotations

import base64
import http.client
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_server as demo


AGENT = "d1-agent-local-1::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d"
OWNER = "app_user_cantor8-local-1::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d"
MERCHANT = "corridor-clear-1::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d"


def installed_packages(*, include_test: bool = False) -> dict[str, list[str]]:
    package_ids = [
        demo.PRODUCTION_PACKAGE_ID,
        demo.AMULET_PACKAGE_ID,
        demo.HOLDING_PACKAGE_ID,
        demo.TRANSFER_PACKAGE_ID,
    ]
    if include_test:
        package_ids.append(demo.TEST_PACKAGE_ID)
    return {"packageIds": package_ids}


def localnet_token(
    subject: str = demo.AGENT_USER,
    *,
    audience: str = demo.LOCALNET_AUDIENCE,
    algorithm: str = "HS256",
    token_type: str = "JWT",
) -> str:
    def segment(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = segment({"alg": algorithm, "typ": token_type})
    payload = segment({"sub": subject, "aud": audience})
    signature = base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode()
    return f"{header}.{payload}.{signature}"


def valid_request(request_id: str | None = None) -> dict[str, str]:
    return {
        "requestId": request_id or str(uuid.uuid4()),
        "mandateId": "d1-demo-test",
        "counterparty": MERCHANT,
        "amount": "0.25",
        "memo": "Route-risk data subscription",
    }


def mandate_event(*, control: str = "control-1") -> dict:
    return {
        "templateId": demo.TEMPLATES["TokenMandate"],
        "contractId": "mandate-cid-1",
        "createArgument": {
            "mandateId": "d1-demo-test",
            "owner": OWNER,
            "spender": AGENT,
            "expectedAdmin": "DSO::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d",
            "instrumentId": {
                "admin": "DSO::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d",
                "id": "Amulet",
            },
            "cap": "2.0",
            "spent": "0.0",
            "allowedCounterparties": [MERCHANT],
            "expiresAt": "2099-08-29T14:00:00Z",
            "control": control,
        },
    }


class FakeClient:
    user_id = demo.AGENT_USER
    agent_party = AGENT
    owner_party = OWNER
    rights = [("CanActAs", AGENT), ("CanReadAs", OWNER)]

    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.submissions = 0

    def dashboard_events(self) -> dict[str, list[dict]]:
        return {
            "TokenMandate": [mandate_event()],
            "TokenMandateControl": [
                {
                    "templateId": demo.TEMPLATES["TokenMandateControl"],
                    "contractId": "control-1",
                    "createArgument": {"mandateId": "d1-demo-test"},
                }
            ],
            "TokenActivationAudit": [],
            "TokenChargeAudit": [],
            "TokenRevocationAudit": [],
        }

    def resolve_mandate(self, mandate_id: str) -> dict:
        if mandate_id != "d1-demo-test":
            raise demo.PublicError(404, "MANDATE_NOT_FOUND", "No current mandate matches that ID")
        return mandate_event()

    def prepare_transfer(self, mandate: dict, **_kwargs) -> dict:
        return {
            "factoryId": "factory-1",
            "transferKind": "direct",
            "choiceContext": {"values": {}},
            "disclosures": [{"contractId": str(index)} for index in range(5)],
            "requestedAt": "2026-08-29T13:00:00Z",
            "executeBefore": "2026-08-29T13:05:00Z",
            "inputHoldingCids": ["holding-1"],
        }

    def submit_charge(self, _mandate: dict, _prepared: dict, **_kwargs) -> dict:
        self.submissions += 1
        if self.reject:
            raise demo.PublicError(
                400,
                "DAML_FAILURE",
                "charge would exceed the total cap",
                source="LEDGER",
            )
        audit = {
            "mandateId": "d1-demo-test",
            "spender": AGENT,
            "counterparty": MERCHANT,
            "amount": "0.25",
            "instrumentId": {"id": "Amulet"},
            "memo": "Route-risk data subscription",
            "chargedAt": "2026-08-29T13:00:00Z",
            "allowedCounterparties": [MERCHANT],
            "spentAfter": "0.25",
            "totalCap": "2.0",
            "expiresAt": "2099-08-29T14:00:00Z",
            "receiverHoldingCids": ["receiver-1"],
        }
        return {
            "transaction": {
                "updateId": "update-1",
                "events": [
                    {
                        "CreatedEvent": {
                            "templateId": demo.TEMPLATES["TokenMandate"],
                            "contractId": "mandate-cid-2",
                            "createArgument": mandate_event()["createArgument"] | {"spent": "0.25"},
                        }
                    },
                    {
                        "CreatedEvent": {
                            "templateId": demo.TEMPLATES["TokenChargeAudit"],
                            "contractId": "audit-1",
                            "createArgument": audit,
                        }
                    },
                ],
            }
        }


class IdentityClient(demo.FixedAgentClient):
    def __init__(self, responses: dict[str, dict], *, token: str | None = None):
        self.responses = responses
        super().__init__(
            token or localnet_token(),
            ledger_base="http://127.0.0.1:2975",
            registry_base="http://127.0.0.1:4000",
        )

    def _request(self, url: str, **_kwargs):
        path = url.split("127.0.0.1:2975", 1)[-1]
        return self.responses[path]


class NoUpstreamClient(demo.FixedAgentClient):
    def _request(self, *_args, **_kwargs):
        raise AssertionError("token validation must finish before any upstream request")


class DemoServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "operations.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def store(self) -> demo.OperationStore:
        return demo.OperationStore(self.db_path)

    def test_endpoints_and_bind_are_loopback_only(self) -> None:
        self.assertEqual(
            demo.require_endpoint("ledger", "http://localhost:2975", 2975),
            "http://localhost:2975",
        )
        with self.assertRaises(demo.PublicError):
            demo.require_endpoint("ledger", "https://validator.example/api", 2975)
        with self.assertRaises(demo.PublicError):
            demo.require_loopback_bind("0.0.0.0")

    def test_fixed_agent_identity_requires_exact_rights(self) -> None:
        user = {
            "user": {
                "id": demo.AGENT_USER,
                "primaryParty": AGENT,
                "isDeactivated": False,
            }
        }
        rights = {
            "rights": [
                {"kind": {"CanActAs": {"value": {"party": AGENT}}}},
                {"kind": {"CanReadAs": {"value": {"party": OWNER}}}},
            ]
        }
        client = IdentityClient(
            {
                f"/v2/users/{demo.AGENT_USER}": user,
                f"/v2/users/{demo.AGENT_USER}/rights": rights,
                "/v2/packages": installed_packages(),
            }
        )
        self.assertEqual(client.agent_party, AGENT)
        self.assertEqual(client.owner_party, OWNER)

        rights["rights"].append({"kind": {"ParticipantAdmin": {"value": {}}}})
        with self.assertRaisesRegex(demo.PublicError, "exactly CanActAs agent"):
            IdentityClient(
                {
                    f"/v2/users/{demo.AGENT_USER}": user,
                    f"/v2/users/{demo.AGENT_USER}/rights": rights,
                    "/v2/packages": installed_packages(),
                }
            )

    def test_token_claims_are_fixed_before_any_upstream_request(self) -> None:
        for subject in ("participant_admin", "d1-owner-user", "another-user"):
            with self.subTest(subject=subject):
                with self.assertRaisesRegex(demo.PublicError, "subject must be d1-agent-user"):
                    NoUpstreamClient(localnet_token(subject))

        with self.assertRaisesRegex(demo.PublicError, "wrong LocalNet audience"):
            NoUpstreamClient(localnet_token(audience="https://wrong.example"))
        with self.assertRaisesRegex(demo.PublicError, "pinned LocalNet HS256 JWT form"):
            NoUpstreamClient(localnet_token(algorithm="none"))
        with self.assertRaisesRegex(demo.PublicError, "three-part JWT"):
            NoUpstreamClient("not-a-jwt")

    def test_correct_agent_subject_reaches_exact_rights_check(self) -> None:
        user = {
            "user": {
                "id": demo.AGENT_USER,
                "primaryParty": AGENT,
                "isDeactivated": False,
            }
        }
        rights = {
            "rights": [
                {"kind": {"CanActAs": {"value": {"party": AGENT}}}},
                {"kind": {"CanReadAs": {"value": {"party": OWNER}}}},
            ]
        }
        client = IdentityClient(
            {
                f"/v2/users/{demo.AGENT_USER}": user,
                f"/v2/users/{demo.AGENT_USER}/rights": rights,
                "/v2/packages": installed_packages(),
            },
            token=localnet_token(),
        )
        self.assertEqual(client.user_id, demo.AGENT_USER)
        self.assertEqual(client.rights, [("CanActAs", AGENT), ("CanReadAs", OWNER)])

    def test_startup_pins_live_package_boundary(self) -> None:
        client = object.__new__(demo.FixedAgentClient)
        client.ledger = lambda _path: installed_packages()
        client._load_and_verify_packages()
        self.assertIn(demo.PRODUCTION_PACKAGE_ID, client.installed_package_ids)
        self.assertNotIn(demo.TEST_PACKAGE_ID, client.installed_package_ids)

        client.ledger = lambda _path: installed_packages(include_test=True)
        with self.assertRaisesRegex(demo.PublicError, "test-only D1 mock package"):
            client._load_and_verify_packages()

        client.ledger = lambda _path: {
            "packageIds": [
                demo.AMULET_PACKAGE_ID,
                demo.HOLDING_PACKAGE_ID,
                demo.TRANSFER_PACKAGE_ID,
            ]
        }
        with self.assertRaisesRegex(demo.PublicError, "Required pinned"):
            client._load_and_verify_packages()

    def test_acs_filter_uses_package_name_and_pins_returned_package_id(self) -> None:
        client = object.__new__(demo.FixedAgentClient)
        client.agent_party = AGENT
        captured = {}

        def ledger(path, body=None, **_kwargs):
            if path == "/v2/state/ledger-end":
                return {"offset": 42}
            captured["body"] = body
            return [
                {
                    "contractEntry": {
                        "JsActiveContract": {
                            "createdEvent": mandate_event(),
                        }
                    }
                },
                {
                    "contractEntry": {
                        "JsActiveContract": {
                            "createdEvent": mandate_event()
                            | {"templateId": "untrusted-package:TokenMandate:TokenMandate"},
                        }
                    }
                },
            ]

        client.ledger = ledger
        rows = client.active_events([demo.TEMPLATES["TokenMandate"]])
        filter_value = (
            captured["body"]["filter"]["filtersByParty"][AGENT]["cumulative"][0]
            ["identifierFilter"]["TemplateFilter"]["value"]["templateId"]
        )
        self.assertEqual(filter_value, demo.TEMPLATE_FILTERS["TokenMandate"])
        self.assertTrue(filter_value.startswith("#cantor8-d1-token-wallet:"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["templateId"], demo.TEMPLATES["TokenMandate"])

    def test_charge_schema_rejects_authority_fields(self) -> None:
        request = valid_request()
        request["actAs"] = OWNER
        with self.assertRaisesRegex(demo.PublicError, "only requestId"):
            demo.validate_charge_request(request)

    def test_amount_remains_a_plain_decimal_string(self) -> None:
        parsed = demo.validate_charge_request(valid_request())
        self.assertEqual(parsed["amount"], "0.25")
        for invalid in ("1e2", "NaN", "Infinity", "-1", "00.25", "0"):
            request = valid_request()
            request["amount"] = invalid
            with self.assertRaises(demo.PublicError):
                demo.validate_charge_request(request)

    def test_memo_rejects_ascii_control_characters(self) -> None:
        for control in ("\t", "\n", "\r"):
            with self.subTest(control=repr(control)):
                request = valid_request()
                request["memo"] = f"route{control}risk"
                with self.assertRaisesRegex(demo.PublicError, "printable characters"):
                    demo.validate_charge_request(request)

    def test_request_id_is_persistent_and_idempotent(self) -> None:
        client = FakeClient()
        store = self.store()
        request = demo.validate_charge_request(valid_request())
        first = demo.execute_charge(client, store, request)
        second = demo.execute_charge(client, store, request)
        self.assertEqual(first["status"], "COMMITTED")
        self.assertTrue(second["replayed"])
        self.assertEqual(client.submissions, 1)

        changed = dict(request, amount="0.50")
        with self.assertRaisesRegex(demo.PublicError, "different payment intent"):
            demo.execute_charge(client, store, changed)

    def test_ledger_rejection_is_stored_separately_from_audit(self) -> None:
        client = FakeClient(reject=True)
        store = self.store()
        request = demo.validate_charge_request(valid_request())
        result = demo.execute_charge(client, store, request)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["evidenceSource"], "LEDGER")
        self.assertEqual(result["code"], "DAML_FAILURE")
        self.assertEqual(store.recent()[0]["status"], "REJECTED")

    def test_missing_control_projects_revoked_but_keeps_mandate(self) -> None:
        client = FakeClient()
        events = client.dashboard_events()
        events["TokenMandateControl"] = []
        client.dashboard_events = lambda: events
        result = demo.project_dashboard(
            client,
            self.store(),
            csrf_token="test-csrf",
            config={},
        )
        self.assertEqual(result["mandates"][0]["status"], "REVOKED")
        self.assertFalse(result["mandates"][0]["controlActive"])

    def test_http_surface_has_no_owner_or_generic_ledger_route(self) -> None:
        server = demo.DemoHTTPServer(
            ("127.0.0.1", 0),
            demo.DemoHandler,
            client=FakeClient(),
            store=self.store(),
            config={},
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
            self.assertEqual(response.getheader("Server"), "Cantor8-D1")
            response.read()

            body = json.dumps(valid_request()).encode()
            connection.request(
                "POST",
                "/api/owner/revoke",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": f"http://127.0.0.1:{port}",
                    "X-D1-CSRF": server.csrf_token,
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()

            connection.request("GET", "/../agent_wallet.py")
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
