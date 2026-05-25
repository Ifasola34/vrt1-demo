"""End-to-end demo of the VRT1 (VERITAS) ecosystem.

A single Python script that exercises ALL FIVE protocol repos in one
flow, producing real signed artifacts at every layer:

  1. Oracle attests an inference                  → veritas
  2. Agent A reviews the attestation              → vrt1-agents
  3. Agent B vouches for Agent A's review         → vrt1-agents
  4. Device emits a signed kWh measurement        → vrt1-kwh
  5. Oracle closes the epoch (Merkle + anchor tx) → veritas
  6. L402 paywall round-trip for premium access   → l402-py
  7. Independent verifier confirms binding chain  → vrt1-verifier
  8. Reputation summary across the agent corpus   → vrt1-agents

All keys, attestations, actions, measurements, the Merkle tree, the
checkpoint event, and the anchor tx are real and verifiable. The
Bitcoin anchor is built but not broadcast (signet by configuration);
Lightning is the in-process mock. Use this to see the protocol stack
working before deciding where to invest funded engineering effort.

Run with:
    pip install -e .
    vrt1-demo
or directly:
    python -m examples.full_chain
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

# --- veritas (oracle + verifier core) -----------------------------
from veritas.anchor import (
    Utxo,
    derive_anchor_pubkey,
    parse_op_return_payload,
)
from veritas.attestation import attestation_digest
from veritas.crypto import OracleKey, derive_anchor_key
from veritas.oracle import Oracle, OracleConfig
from veritas.verifier import verify_full

# --- vrt1-agents --------------------------------------------------
from vrt1_agents.action import make_action, sign_action
from vrt1_agents.reputation import build_vouch_graph, summarize

# --- vrt1-kwh -----------------------------------------------------
from vrt1_kwh.attestation import make_measurement, sign_measurement
from vrt1_kwh.measurer import StubMeasurer

# --- vrt1-verifier (import-only smoke: proves package is installable
# and its imports resolve; the actual verify_full call in step 7 uses
# veritas.verifier directly because the demo passes artifacts in memory
# rather than fetching from Nostr/Bitcoin) ----------------------------
import vrt1_verifier.cli  # noqa: F401

# --- l402-py ------------------------------------------------------
from l402.backends import DeterministicMockBackend as L402MockBackend
from l402.server import authorize, make_challenge


console = Console()


# ---------- helpers -----------------------------------------------


def _header(num: int, title: str) -> None:
    """Print a numbered section header for one demo step."""
    console.print()
    console.print(Rule(f"[bold cyan]Step {num}: {title}[/bold cyan]"))


def _short(hex_str: str, head: int = 12, tail: int = 6) -> str:
    """Truncate long hex strings for readable output."""
    if len(hex_str) <= head + tail + 1:
        return hex_str
    return f"{hex_str[:head]}…{hex_str[-tail:]}"


def _funded_utxo(oracle_key: OracleKey) -> Utxo:
    """Build a plausible UTXO for the oracle's derived anchor key.

    In production this would be a real funded UTXO from a wallet; for
    the demo we fabricate one with the correct pubkey so build_anchor_tx
    accepts it. The resulting tx serializes valid BIP-143 segwit hex
    but is not broadcast.
    """
    priv = derive_anchor_key(oracle_key)
    return Utxo(
        txid="ab" * 32,
        vout=0,
        value_sats=100_000,
        pubkey_compressed=derive_anchor_pubkey(priv),
    )


# ---------- demo steps --------------------------------------------


def step_1_oracle_attests(oracle: Oracle) -> tuple[Any, Any, bool]:
    _header(1, "VERITAS oracle attests an inference")
    signed, evt = oracle.attest(
        "veritas.sentiment.keyword.v1",
        "BTC bullish breakout above resistance — strong rally",
    )
    valid = signed.verify()
    digest = attestation_digest(signed.attestation).hex()
    t = Table(show_header=False, box=None)
    t.add_row("oracle pubkey", _short(signed.attestation.oracle))
    t.add_row("model",         signed.attestation.model)
    t.add_row("input hash",    _short(signed.attestation.input_hash))
    t.add_row("output",        str(signed.attestation.output))
    t.add_row("attestation digest", _short(digest))
    t.add_row("sig valid?",    "[green]yes[/green]" if valid else "[red]no[/red]")
    t.add_row("nostr event id",    _short(evt.id))
    t.add_row("nostr event kind",  str(evt.kind))
    console.print(t)
    return signed, evt, valid


def step_2_agent_review(agent_a: OracleKey, signed_attestation) -> tuple[Any, bool]:
    _header(2, "Agent A reviews the attestation (vrt1-agents)")
    review = sign_action(
        make_action(
            agent_pubkey_hex=agent_a.xonly_pubkey_hex,
            action_type="review",
            target=attestation_digest(signed_attestation.attestation).hex(),
            outcome={
                "verdict": "trustworthy",
                "score": 4,
                "notes": "model output aligns with input sentiment",
            },
        ),
        agent_a,
    )
    valid = review.verify()
    t = Table(show_header=False, box=None)
    t.add_row("agent A pubkey", _short(agent_a.xonly_pubkey_hex))
    t.add_row("action_id",      _short(review.id))
    t.add_row("action_type",    review.action.action_type)
    t.add_row("target",         _short(review.action.target))
    t.add_row("outcome",        str(review.action.outcome))
    t.add_row("sig valid?",     "[green]yes[/green]" if valid else "[red]no[/red]")
    console.print(t)
    return review, valid


def step_3_agent_vouch(agent_b: OracleKey, review) -> tuple[Any, bool]:
    _header(3, "Agent B vouches for Agent A's review (vrt1-agents)")
    vouch = sign_action(
        make_action(
            agent_pubkey_hex=agent_b.xonly_pubkey_hex,
            action_type="vouch",
            target=review.id,
            parent_action=review.id,
        ),
        agent_b,
    )
    valid = vouch.verify()
    t = Table(show_header=False, box=None)
    t.add_row("agent B pubkey", _short(agent_b.xonly_pubkey_hex))
    t.add_row("action_id",      _short(vouch.id))
    t.add_row("vouches for",    _short(vouch.action.parent_action))
    t.add_row("sig valid?",     "[green]yes[/green]" if valid else "[red]no[/red]")
    console.print(t)
    return vouch, valid


def step_4_kwh_measurement(device: OracleKey) -> tuple[Any, bool]:
    _header(4, "kWh device emits a signed power measurement (vrt1-kwh)")
    # 60s window at ~36W average laptop idle = ~600 µWh = 6e-7 kWh.
    stub = StubMeasurer(kwh_per_second=0.00001)
    sample = stub.measure(60.0)
    signed_meas = sign_measurement(
        make_measurement(
            device_pubkey_hex=device.xonly_pubkey_hex,
            sample=sample,
        ),
        device,
    )
    valid = signed_meas.verify()
    t = Table(show_header=False, box=None)
    t.add_row("device pubkey", _short(device.xonly_pubkey_hex))
    t.add_row("measurement_id", _short(signed_meas.id))
    t.add_row("source", signed_meas.measurement.source)
    t.add_row("window", f"{signed_meas.measurement.window_start} → {signed_meas.measurement.window_end}")
    t.add_row("kwh", f"{signed_meas.measurement.kwh:.9f}")
    t.add_row("sig valid?", "[green]yes[/green]" if valid else "[red]no[/red]")
    console.print(t)
    return signed_meas, valid


def step_5_close_epoch(oracle: Oracle) -> tuple:
    _header(5, "Oracle closes the epoch → Merkle root + checkpoint + anchor tx (veritas)")
    epoch = oracle.close_epoch()
    proof = oracle.inclusion_proof(epoch.number, 0)
    t = Table(show_header=False, box=None)
    t.add_row("epoch number", str(epoch.number))
    t.add_row("attestations", str(len(epoch.attestations)))
    t.add_row("merkle root",  _short(epoch.root_hex or ""))
    t.add_row("checkpoint event id", _short(epoch.checkpoint_event.id))
    if epoch.anchor_tx:
        t.add_row("anchor txid",     _short(epoch.anchor_tx.txid))
        t.add_row("anchor fee_sats", str(epoch.anchor_tx.fee_sats))
        op_return = epoch.anchor_tx.op_return_payload
        parsed = parse_op_return_payload(op_return)
        t.add_row("OP_RETURN tag",        parsed["tag"])
        t.add_row("OP_RETURN epoch",      str(parsed["epoch"]))
        t.add_row("OP_RETURN leaf_count", str(parsed["leaf_count"]))
        t.add_row("OP_RETURN root",       _short(parsed["merkle_root"].hex()))
    if epoch.broadcast_result:
        backend = epoch.broadcast_result.backend
        if backend == "null":
            t.add_row("broadcast", "[grey50]disabled (set VERITAS_ANCHOR_BROADCAST=1 to enable)[/grey50]")
        else:
            t.add_row("broadcast", f"{backend} → {'[green]ok[/green]' if epoch.broadcast_result.ok else '[red]failed[/red]'}")
    console.print(t)
    console.print(
        "[green]✓[/green] Merkle tree built with RFC-6962 prefixes; "
        "checkpoint signed; anchor tx serialized as valid BIP-143 segwit hex"
    )
    return epoch, proof


def step_6_l402_paywall(secret: bytes, ln_backend: L402MockBackend) -> bool:
    _header(6, "L402 Lightning paywall round-trip for premium access (l402-py)")
    # Issue a challenge as if a client hit /infer/premium.
    chal = make_challenge(
        secret, ln_backend,
        resource_id="premium:sentiment",
        amount_msat=1000,
    )
    # Client "pays" the (mock) invoice — reveal_preimage simulates settlement.
    preimage_hex = ln_backend.reveal_preimage(chal.payment_hash)
    auth_header = f"L402 {chal.macaroon_token}:{preimage_hex}"
    # Server authorizes the retry.
    ok = authorize(
        secret, ln_backend,
        auth_header_value=auth_header,
        resource_id="premium:sentiment",
    )
    t = Table(show_header=False, box=None)
    t.add_row("invoice (mock)",       chal.invoice_bolt11)
    t.add_row("payment_hash",         _short(chal.payment_hash))
    t.add_row("macaroon token",       _short(chal.macaroon_token, head=32, tail=8))
    t.add_row("preimage (revealed)",  _short(preimage_hex))
    t.add_row("Authorization header", "L402 …" + _short(chal.macaroon_token, head=16, tail=8) + f":{_short(preimage_hex)}")
    t.add_row("authorize() result",   "[green]✓ AUTHORIZED[/green]" if ok else "[red]✗ REJECTED[/red]")
    console.print(t)
    return ok


def step_7_verifier(signed_attestation, nostr_event, proof, epoch) -> bool:
    _header(7, "Independent verifier runs the full binding chain (vrt1-verifier / veritas.verifier)")
    # In a real third-party flow, vrt1-verifier fetches these artifacts
    # from Nostr relays + a Bitcoin explorer. Here we hand them in
    # directly to demonstrate verify_full's binding-chain semantics.
    result = verify_full(
        signed=signed_attestation,
        nostr_event=nostr_event,
        proof=proof,
        checkpoint_event=epoch.checkpoint_event,
        anchor_raw_tx_hex=epoch.anchor_tx.raw_hex,
    )
    t = Table(title="binding chain")
    t.add_column("check")
    t.add_column("status")
    for name, val in [
        ("schnorr (attestation)", result.schnorr_ok),
        ("nostr event signature", result.nostr_event_ok),
        ("merkle inclusion proof", result.merkle_ok),
        ("checkpoint signed content", result.checkpoint_ok),
        ("anchor OP_RETURN (on-chain)", result.anchor_ok),
    ]:
        if val is None:
            t.add_row(name, "[grey50]not provided[/grey50]")
        else:
            t.add_row(
                name,
                "[bold green]✓ pass[/bold green]" if val else "[bold red]✗ fail[/bold red]",
            )
    console.print(t)
    if result.notes:
        console.print("[yellow]notes:[/yellow]")
        for n in result.notes:
            console.print(f"  • {n}")
    return result.ok


def step_8_reputation(agent_a: OracleKey, agent_b: OracleKey, corpus) -> bool:
    _header(8, "Reputation summary from the agent action corpus (vrt1-agents)")
    summ_a = summarize(agent_a.xonly_pubkey_hex, corpus)
    graph = build_vouch_graph(corpus)
    ok = (summ_a.total_actions >= 1
          and len(summ_a.vouches_received_from) >= 1
          and graph.in_degree(agent_a.xonly_pubkey_hex) >= 1)
    t = Table(title=f"agent A reputation ({_short(agent_a.xonly_pubkey_hex)})", show_header=False, box=None)
    t.add_row("valid actions",         str(summ_a.total_actions))
    t.add_row("type counts",           str(summ_a.type_counts))
    t.add_row("vouches received from", str(len(summ_a.vouches_received_from)))
    t.add_row("in-degree (graph)",     str(graph.in_degree(agent_a.xonly_pubkey_hex)))
    console.print(t)
    console.print(
        "[green]✓[/green] one peer (Agent B) vouched; reputation = signed, "
        "cryptographically-bound, peer-verifiable history"
    )
    return ok


# ---------- orchestration -----------------------------------------


def run_demo() -> bool:
    """Run the full chain. Returns True iff every step's verification passes."""
    console.print(Panel.fit(
        "[bold]VRT1 (VERITAS) ecosystem — end-to-end demo[/bold]\n"
        "5 protocol repos exercised in one flow; real signed artifacts at every layer.",
        border_style="cyan",
    ))

    # Fresh in-memory keys per run; nothing persisted past process exit.
    oracle_key = OracleKey.generate()
    agent_a    = OracleKey.generate()
    agent_b    = OracleKey.generate()
    device     = OracleKey.generate()

    # Oracle data dir lives in a tempdir we tear down at exit.
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "demo-data"
        oracle = Oracle(
            oracle_key,
            OracleConfig(
                data_dir=data_dir,
                anchor_utxo=_funded_utxo(oracle_key),
                fee_sats=400,
            ),
        )

        # Steps 1-4 produce signed artifacts inside the open epoch.
        signed, evt, attest_ok = step_1_oracle_attests(oracle)
        review, review_ok = step_2_agent_review(agent_a, signed)
        vouch, vouch_ok   = step_3_agent_vouch(agent_b, review)
        kwh, kwh_ok       = step_4_kwh_measurement(device)

        # Step 5 closes the epoch (Merkle + checkpoint + anchor).
        epoch, proof = step_5_close_epoch(oracle)

        # Step 6: L402 paywall round-trip.
        ln_backend = L402MockBackend()
        l402_secret = os.urandom(32)
        paywall_ok = step_6_l402_paywall(l402_secret, ln_backend)

        # Step 7: independent verifier.
        verified = step_7_verifier(signed, evt, proof, epoch)

        # Step 8: reputation summary across the agent corpus.
        rep_ok = step_8_reputation(agent_a, agent_b, [review, vouch])

        all_ok = (attest_ok and review_ok and vouch_ok and kwh_ok
                  and paywall_ok and verified and rep_ok)

        # Final summary panel.
        console.print()
        if all_ok:
            console.print(Panel.fit(
                "[bold green]ECOSYSTEM COMPOSES ✓[/bold green]\n"
                "Signed attestation, agent actions, kWh measurement, "
                "Bitcoin anchor, L402 paywall, and verifier binding chain "
                "all produced real, peer-verifiable artifacts in one flow.",
                border_style="green",
            ))
        else:
            console.print(Panel.fit(
                "[bold red]ECOSYSTEM CHECK FAILED[/bold red]\n"
                f"attest_ok={attest_ok}  review_ok={review_ok}  vouch_ok={vouch_ok}  "
                f"kwh_ok={kwh_ok}  paywall_ok={paywall_ok}  verified={verified}  "
                f"rep_ok={rep_ok}",
                border_style="red",
            ))

        # Receipts for forensic inspection / further use.
        console.print()
        console.print(Panel.fit(
            "Receipts (paths after this process exits are gone — copy what you want):\n"
            f"  data_dir:               {data_dir}\n"
            f"  attestation_id:         {attestation_digest(signed.attestation).hex()}\n"
            f"  review_id:              {review.id}\n"
            f"  vouch_id:               {vouch.id}\n"
            f"  measurement_id:         {kwh.id}\n"
            f"  merkle_root:            {epoch.root_hex}\n"
            f"  checkpoint_event_id:    {epoch.checkpoint_event.id}\n"
            f"  anchor_txid:            {epoch.anchor_tx.txid if epoch.anchor_tx else 'n/a'}\n",
            border_style="grey50",
            title="artifact ids",
            title_align="left",
        ))
        return all_ok


def main() -> int:
    """Entry point for the `vrt1-demo` console script."""
    return 0 if run_demo() else 1


if __name__ == "__main__":
    raise SystemExit(main())
