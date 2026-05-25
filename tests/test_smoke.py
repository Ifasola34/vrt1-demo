"""Smoke test: the full end-to-end chain must run clean.

If anything in the ecosystem breaks compatibility (a satellite changes
its public API, an attestation field is renamed, a Nostr event kind
shifts), this test catches it on the next push because CI installs
each dep from the upstream main branch.

This is intentionally a single end-to-end run, not a granular suite —
the per-repo unit tests live in their own repos. This test answers
exactly one question: "do all five libraries still compose?"
"""

from __future__ import annotations

import pytest

from examples.full_chain import run_demo


def test_full_chain_runs_clean():
    """Run the entire VRT1 ecosystem demo. The function returns True
    iff the L402 paywall authorizes AND the verifier's binding chain
    reports ok=True with every check passing.
    """
    assert run_demo() is True
