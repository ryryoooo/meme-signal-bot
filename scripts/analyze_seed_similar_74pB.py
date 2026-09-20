#!/usr/bin/env python3
"""Seed 74pB similar-wallet analysis.

Full RPC pipeline was used to produce sol-wallets/raw/seed_74pB/*.
Re-finish from cache without thrash:

  PYTHONUNBUFFERED=1 SEED_RESUME=1 python3 -c "exec(open('scripts/_finish_seed_74pB.py').read())"

Or re-run peer hunter: scripts/hunt_sol_dump_dip.py (pre_grad_dip + post_grad_dip).

Outputs:
  sol-wallets/seed_74pB_entry_analysis.md
  sol-wallets/sol_smart_similar_74pB.jsonl
  sol-wallets/sol_smart_pre_grad_dip_74pB.jsonl
  sol-wallets/sol_smart_post_grad_dip_74pB.jsonl
"""
print("Use cached finish / hunt_sol_dump_dip.py — see sol-wallets/summary_sol_smart_similar_74pB.md")
