# vrt1-demo

[![CI](https://github.com/Ifasola34/vrt1-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/Ifasola34/vrt1-demo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**End-to-end demo of the VRT1 (VERITAS) ecosystem.**

One Python script that exercises all five protocol repos in a single flow — produces real signed artifacts at every layer, then independently verifies the binding chain. The point: show that the ecosystem composes, not just that each library has tests.

```
oracle attests an inference
   ↓                      veritas        →  SignedAttestation
agent A reviews it
   ↓                      vrt1-agents    →  SignedAction (review)
agent B vouches for A
   ↓                      vrt1-agents    →  SignedAction (vouch)
device emits kWh
   ↓                      vrt1-kwh       →  SignedMeasurement
oracle closes the epoch
   ↓                      veritas        →  Merkle root + checkpoint + Bitcoin anchor tx
L402 paywall round-trip
   ↓                      l402-py        →  AUTHORIZED
independent verifier checks the full chain
   ↓                      vrt1-verifier  →  binding chain ok=True
reputation summary
   ↓                      vrt1-agents    →  signed peer-vouched reputation
```

---

## Run it

```bash
git clone https://github.com/Ifasola34/vrt1-demo.git
cd vrt1-demo
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e .
vrt1-demo
```

You'll see a rich-printed walkthrough of all 8 steps, ending with:

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ ECOSYSTEM COMPOSES ✓                                                         │
│ Signed attestation, agent actions, kWh measurement, Bitcoin anchor, L402     │
│ paywall, and verifier binding chain all produced real, peer-verifiable       │
│ artifacts in one flow.                                                       │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Followed by a panel listing the artifact ids — attestation digest, action ids, measurement id, Merkle root, checkpoint event id, and Bitcoin anchor txid. All real, all cryptographically bound to each other.

---

## What this demo is and isn't

**This IS:**
- A working integration of all 5 VRT1 ecosystem repos
- Real BIP-340 Schnorr signatures, real RFC-6962 Merkle tree with on-chain `OP_RETURN` cross-check, real L402 macaroon HMAC + preimage check, real independent verifier
- A regression dam — the CI smoke test catches integration breakage when any of the five libraries changes its public API
- Useful as a starting template for anyone building on top of VRT1

**This IS NOT:**
- A production server (the L402 paywall uses the in-process mock backend, not real LND/Phoenixd/CLN)
- A live broadcast (the anchor tx is built and serialized but not pushed to the Bitcoin network by default — set `VERITAS_ANCHOR_BROADCAST=1` to enable mempool.space POSTs on signet)
- An optimized reference implementation (each step prints rich output for clarity; production code would skip the formatting)

---

## What gets verified

The independent verifier (`vrt1-verifier`, internally `veritas.verifier.verify_full`) runs five binding checks against the artifacts the oracle produces:

| Check | What it asserts |
|---|---|
| **Schnorr (attestation)** | Oracle's BIP-340 signature over the canonical attestation payload is valid under the oracle's pubkey |
| **Nostr event** | The wrapping NIP-01 event id + Schnorr sig are valid, AND the event pubkey matches the attestation's oracle field |
| **Merkle inclusion** | The attestation digest is the actual leaf at the claimed index in the tree, and the proof reconstructs to the claimed root |
| **Checkpoint signed content** | The signed checkpoint event commits to the same root + epoch + leaf_count as the proof |
| **Anchor OP_RETURN (on-chain)** | The serialized anchor tx's OP_RETURN payload commits to the same root + epoch + leaf_count as the checkpoint |

Any single mismatch fails the chain. Round-2 + round-3 security reviews of each ecosystem repo added 23+ adversarial tests per layer to prove these checks can't be silently weakened.

---

## The five repos this demo uses

| Repo | Tests | What it does |
|---|---|---|
| [`veritas`](https://github.com/Ifasola34/veritas) | 115 | The oracle + protocol core: BIP-340, RFC-6962 Merkle, Bitcoin anchor, NIP-01 events |
| [`vrt1-verifier`](https://github.com/Ifasola34/vrt1-verifier) | 47 | Third-party verifier with no oracle code in the trust path |
| [`vrt1-agents`](https://github.com/Ifasola34/vrt1-agents) | 64 | Signed agent actions + peer-vouched reputation primitives |
| [`vrt1-kwh`](https://github.com/Ifasola34/vrt1-kwh) | 89 | Signed kWh attestations (proof-of-energy substrate) |
| [`l402-py`](https://github.com/Ifasola34/l402-py) | 86 | Lightning paywall (L402/LSAT) — both server + client sides |

All MIT, all pinned to `main` of each upstream repo. The CI smoke test installs each upstream fresh on every push so an API break in any of them surfaces here within a couple of minutes.

---

## Tests

```bash
$ pytest -q
1 passed in 0.04s
```

One test (`tests/test_smoke.py`) runs the full demo and asserts every step passes — attestation signature, agent review, agent vouch, kWh measurement, L402 paywall, verifier binding chain, and reputation summary. That's the entire contract this repo enforces: "the five libraries compose, and every artifact they produce is valid."

CI runs the smoke test on Python 3.10, 3.11, 3.12, 3.13 — and additionally runs the `vrt1-demo` CLI as a sanity check that the rich-printed output renders.

---

## License

MIT — see [`LICENSE`](LICENSE).
