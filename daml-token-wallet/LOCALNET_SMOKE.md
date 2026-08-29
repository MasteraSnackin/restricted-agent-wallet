# D1 LocalNet smoke evidence

Checked on 29 August 2026 against the loopback-only Splice 0.6.8 LocalNet.
This records that completed local run; it is not DevNet, production or
continuously current evidence.

## Artefacts

| Artefact | Evidence |
|---|---|
| Production package | `cantor8-d1-token-wallet` 0.1.2 |
| Production package ID | `87453fdcde8589c700016732f55e79469196f4434573c649d905200ec325dc90` |
| Production DAR SHA-256 | `7031afa11cdbec85f21f795cbb3a37cf36ce8daf02fb62f0045e097096c0b532` |
| Test-only package ID | `5851e05c58b059245a667e80e8f8a4b5724ec65a56f9cee3c647316d79161543` |
| Test-only DAR SHA-256 | `150b9c527a25f78dce5102db4826fb5b0987576566b92064b965daad74af7af6` |
| Test-only package installed | no, verified through `/v2/packages` |
| Concrete LocalNet Amulet package | `90987abecbcb1d004b063ddfe3b4b5d46cf3814ce89114a86c8cd75ff3cb8a4b` |

## Least-privilege users

The participant returned these exact rights before the payment:

- `d1-agent-user`: `CanActAs d1-agent-local-1`; `CanReadAs
  app_user_cantor8-local-1`.
- `d1-owner-user`: `CanActAs app_user_cantor8-local-1` only.
- `d1-merchant-auditor-user`: `CanReadAs corridor-clear-1` only.

The agent user had no owner `CanActAs` and no `ParticipantAdmin` right.

## Passing run

Command:

```bash
python3 daml-token-wallet/agent_wallet.py smoke
```

Mandate `d1-localnet-20260829T121453Z-c8e709` had a total cap of 2.0
Amulet, an expiry of `2026-08-29T13:14:53Z`, and one exact allowed Party:
`corridor-clear-1::1220861da3d9e0f6870a03394fd80a81584c3799653a18250a8247547b67421a758d`.

The registry returned `transferKind=direct` and five disclosed contracts. The
single root `ChargeToken` transaction had update ID
`1220341a623e439dc77c314f44c9613539604d92aeab077e1def461f90a31ec0b0f2`.
It atomically created:

- successor mandate
  `00aef9f374bd660e27c01161041583d50f2f93c0539f56ecbee8197d427f130a07ca1212206a4eb9ad6e5cde59177dbbffefb1c24f578c5e1f3399a5a1d45404ee97322ddf`;
- co-signed charge audit
  `00bfc92274ba3aefc613feed33b028304181d43811942d2cab74787bbdcc86bca2ca121220bf3284e3aa831140ce55d9e04abcc15f74ab748032b706ca153056616d5d03f8`;
- receiver Holding
  `0087261e3811338eaa008186f936b712222db87cabbe20b7e62c757a5097171121ca121220b4c709af1236603a3e9c93ffbee5c0a1ba2214731e6f51a02000dfc08293d8d7`.

The merchant's independently read Amulet balance moved from `1.8750000000` to
`2.1250000000`, an exact `0.2500000000` increase. The owner balance moved from
`7318.8290000960` to `7318.5790000960` for this local transaction.

The read-only statement command renders the co-signed audit as:

> Mandate d1-localnet-20260829T121453Z-c8e709: at
> 2026-08-29T12:14:53.951957Z, agent d1-agent-local-1 paid corridor-clear-1
> 0.2500000000 Amulet. Memo: Hormuz route-risk data subscription. Permission:
> exact Party allow-list corridor-clear-1; cumulative spend
> 0.2500000000/2.0000000000; valid until 2026-08-29T13:14:53Z.

The actual command output retains the full unique Party IDs rather than the
shortened Party prefixes used in the quotation above.

## Rejection evidence

All attempted through `d1-agent-user` unless stated otherwise:

| Attempt | Ledger result |
|---|---|
| Cumulative over-cap charge | HTTP 400 `DAML_FAILURE`: `charge would exceed the total cap` |
| Different Party | HTTP 400 `DAML_FAILURE`: `counterparty is not allowed` |
| Direct `TransferFactory_Transfer` bypass | HTTP 400 `DAML_AUTHORIZATION_ERROR`: owner authoriser required; only agent supplied |
| Agent exercises owner revocation | HTTP 400 `DAML_AUTHORIZATION_ERROR`: owner authoriser required; only agent supplied |
| Charge after owner revocation | HTTP 404 `CONTRACT_NOT_FOUND` for the consumed stable control CID |

The owner revocation committed with update ID
`12202169dc7e73492d3fe91cd8036489b6beb142d4f7d4af445f32efe0b1f467af05`
and created revocation audit
`00c19defe9a4b73d26021afb979c277f61079a3926bd0c03c241ed245e793729a7ca12122014f6d76dced17e3e522a4a8a4546b6534be4cdb860c6401bb99df5defd7a853d`.
The stable control was consumed after the successful charge had already created
a successor mandate.

The merchant balance remained `2.1250000000` after every rejected command.
Thus none of the adversarial attempts moved value.

## Contract tests

The separate mock package passed all three scripts:

- `testTokenExactCapAndExpiry`: 12 transactions;
- `testTokenRollbackAndGuards`: 21 transactions; and
- `testTokenPolicyAndStableRevocation`: 26 transactions.

Total: 59 successful test-ledger transactions. The mock Holding and factory and
all six external production templates were instantiated. The pending mock
instruction is deliberately created only inside an aborted transaction; the
test verifies no instance survives rollback. The mock package was not uploaded
to LocalNet.

## Known boundary

The first real-factory integration attempt exposed and rolled back a private
factory pre-fetch that required DSO stakeholder authority. Version 0.1.1 removes
that redundant pre-fetch and relies on the vetted public
`TransferFactory_Transfer` boundary's `expectedAdmin` validation while pinning
the same administrator in the mandate, instrument and input holdings. The
passing run above uses version 0.1.2, which additionally co-signs the control
and revocation audit so neither party can unilaterally use implicit `Archive`
to bypass the audited owner-only `Revoke` path.
