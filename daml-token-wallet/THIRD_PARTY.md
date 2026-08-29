# Third-party Daml interfaces

The production package compiles against three stable Canton Token Standard V1
API DARs shipped in the pinned Splice LocalNet 0.6.8 bundle. They are licensed
under Apache-2.0 and are referenced by versioned filename rather than a
`current` alias.

| Package | Package ID |
|---|---|
| `splice-api-token-metadata-v1-1.0.0` | `4ded6b668cb3b64f7a88a30874cd41c75829f5e064b3fbbadf41ec7e8363354f` |
| `splice-api-token-holding-v1-1.0.0` | `718a0f77e505a8de22f188bd4c87fe74101274e9d4cb1bfac7d09aec7158d35b` |
| `splice-api-token-transfer-instruction-v1-1.0.0` | `55ba4deb0ad4662c4168b39859738a0e91388d252286480c7331b3f71a517281` |

The repository stores the exact files in `../dars/`; both Daml packages use
repository-relative references. The copied bytes have these SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `splice-api-token-metadata-v1-1.0.0.dar` | `455eb160cb5abd4ae9918a6fbb9dad471f721adda39f0e5c76feef08d05637fc` |
| `splice-api-token-holding-v1-1.0.0.dar` | `ef75f8eb41a65810221784fdb78bb9dfac7cb22245aba14fa7cb7f69c34e0175` |
| `splice-api-token-transfer-instruction-v1-1.0.0.dar` | `e4c73aa7ae73fb2fc330b938ffb99f568792321640ba4b9472902aa8d742c994` |

The Apache-2.0 licence shipped with the pinned Splice bundle is stored at
`../third-party-licenses/splice-apache-2.0.txt`. Do not replace these dependencies
with `splice-amulet`: the mandate should remain coupled to the stable interfaces,
not one concrete token implementation version.
