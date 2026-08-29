from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_wallet as wallet


PRODUCTION_PACKAGE_ID = wallet.main_package_id(wallet.PRODUCTION_DAR)
OTHER_PACKAGE_ID = "0" * 64
VIEWER = "owner::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d"


def created_response(package_id: str, template_name: str) -> dict:
    return {
        "transaction": {
            "events": [
                {
                    "CreatedEvent": {
                        "templateId": wallet.exact_template_id(
                            package_id, template_name
                        ),
                        "contractId": "contract-1",
                        "createArgument": {"mandateId": "mandate-1"},
                    }
                }
            ]
        }
    }


def active_contract(package_id: str, template_name: str) -> dict:
    return {
        "contractEntry": {
            "JsActiveContract": {
                "createdEvent": {
                    "templateId": wallet.exact_template_id(
                        package_id, template_name
                    ),
                    "contractId": "contract-1",
                    "createArgument": {"mandateId": "mandate-1"},
                }
            }
        }
    }


class AgentWalletPackageBoundaryTests(unittest.TestCase):
    def test_created_event_must_use_exact_production_package(self) -> None:
        event = wallet.find_created(
            created_response(PRODUCTION_PACKAGE_ID, "TokenMandate"),
            "TokenMandate",
            package_id=PRODUCTION_PACKAGE_ID,
        )
        self.assertEqual(
            event["templateId"],
            wallet.exact_template_id(PRODUCTION_PACKAGE_ID, "TokenMandate"),
        )

        with self.assertRaisesRegex(wallet.c8lab.LabError, "unexpected package ID"):
            wallet.find_created(
                created_response(OTHER_PACKAGE_ID, "TokenMandate"),
                "TokenMandate",
                package_id=PRODUCTION_PACKAGE_ID,
            )

    def test_acs_queries_by_name_then_pins_returned_package(self) -> None:
        with (
            mock.patch.object(wallet.c8lab, "ledger_end", return_value=41),
            mock.patch.object(
                wallet.c8lab,
                "call",
                return_value=[active_contract(PRODUCTION_PACKAGE_ID, "TokenMandateControl")],
            ) as call,
        ):
            rows = wallet.active_template_events(
                VIEWER,
                "TokenMandateControl",
                package_id=PRODUCTION_PACKAGE_ID,
                user_id=wallet.OWNER_USER,
            )

        self.assertEqual(len(rows), 1)
        body = call.call_args.args[1]
        template_id = body["filter"]["filtersByParty"][VIEWER]["cumulative"][0][
            "identifierFilter"
        ]["TemplateFilter"]["value"]["templateId"]
        self.assertEqual(
            template_id,
            wallet.template_filter("TokenMandateControl"),
        )

        with (
            mock.patch.object(wallet.c8lab, "ledger_end", return_value=42),
            mock.patch.object(
                wallet.c8lab,
                "call",
                return_value=[active_contract(OTHER_PACKAGE_ID, "TokenMandateControl")],
            ),
            self.assertRaisesRegex(wallet.c8lab.LabError, "unexpected template"),
        ):
            wallet.active_template_events(
                VIEWER,
                "TokenMandateControl",
                package_id=PRODUCTION_PACKAGE_ID,
                user_id=wallet.OWNER_USER,
            )

    def test_exact_template_id_rejects_unpinned_name(self) -> None:
        with self.assertRaisesRegex(wallet.c8lab.LabError, "malformed"):
            wallet.exact_template_id(
                "#cantor8-d1-token-wallet",
                "TokenMandateProposal",
            )


if __name__ == "__main__":
    unittest.main()
