#!/usr/bin/env python3
"""Say which Tyk Gateway rate limiter enforces a limit, and where its counter lives.

The rules are the ones Tyk Gateway 5.16.0 applies per request, with one or several Gateways:

1. `enable_fixed_window_rate_limiter`  -> fixed window counter in Redis
2. `enable_sentinel_rate_limiter`      -> Redis rolling window plus a block key for `per` seconds
3. `enable_redis_rolling_limiter`      -> Redis rolling window
4. otherwise, with G Gateways sharing one Redis and T = `drl_threshold` (0 means 5):
   G <= 1, or rate/per > G x T  -> each Gateway counts in its own memory, with 1/G of the limit
   rate/per <= G x T            -> Redis rolling window, or the sentinel variant when
                                   `drl_enable_sentinel_rate_limiter` is on

Standard library only.

    which_limiter.py --rate 10 --per 60 --gateways 2
"""
from __future__ import annotations

import argparse
import json
import sys

DEFAULT_DRL_THRESHOLD = 5.0

MEMORY = "in-memory"
REDIS_ROLLING = "redis-rolling-window"
REDIS_SENTINEL = "redis-sentinel"
REDIS_FIXED = "redis-fixed-window"
NONE = "none"


def decide(rate, per, gateways=1, drl_threshold=0.0, redis_rolling=False, sentinel=False, fixed_window=False,
           drl_sentinel=False):
    """Return a dict describing the limiter for a limit of `rate` requests per `per` seconds."""
    if gateways < 1:
        raise ValueError("gateways must be at least 1")
    if rate <= 0:
        return {
            "limiter": NONE,
            "counter": "none",
            "shared_across_gateways": False,
            "allowed_per_window": None,
            "survives_gateway_restart": None,
            "summary": "A rate of 0 or less means no rate limit: every request passes the rate check.",
        }
    if per <= 0:
        raise ValueError("per must be greater than 0 seconds when rate is set")

    threshold = drl_threshold or DEFAULT_DRL_THRESHOLD
    per_second = rate / per

    if fixed_window:
        limiter, why = REDIS_FIXED, "enable_fixed_window_rate_limiter is on"
    elif sentinel:
        limiter, why = REDIS_SENTINEL, "enable_sentinel_rate_limiter is on"
    elif redis_rolling:
        limiter, why = REDIS_ROLLING, "enable_redis_rolling_limiter is on"
    elif gateways <= 1:
        limiter, why = MEMORY, "one Gateway: the default limiter never uses Redis"
    elif gateways * threshold < per_second:
        limiter, why = MEMORY, (
            f"{per_second:g} requests per second is above drl_threshold x Gateways "
            f"({threshold:g} x {gateways} = {gateways * threshold:g})"
        )
    else:
        limiter, why = REDIS_ROLLING, (
            f"{per_second:g} requests per second is at or below drl_threshold x Gateways "
            f"({threshold:g} x {gateways} = {gateways * threshold:g})"
        )
        if drl_sentinel:
            limiter, why = REDIS_SENTINEL, why + "; drl_enable_sentinel_rate_limiter is on"

    if limiter == MEMORY:
        share = max(1, int(rate // gateways))
        return {
            "limiter": limiter,
            "reason": why,
            "counter": "each Gateway's memory; nothing is written to Redis",
            "shared_across_gateways": False,
            "allowed_per_window": {"per_gateway": share, "all_gateways_evenly_loaded": share * gateways},
            "survives_gateway_restart": False,
            "summary": (
                f"Each Gateway allows {share} requests per {per:g} s window on its own"
                + ("." if gateways == 1 else f", whichever Gateway the traffic reaches: all traffic on one Gateway gets {share}, not {int(rate)}.")
                + " The window starts at the first request and refills in full when it ends. A Gateway restart clears the count."
            ),
        }

    notes = {
        REDIS_ROLLING: (
            "One counter for all Gateways: a Redis sorted set `rate-limit-<key hash>` holding one entry per request, "
            "rejected requests included, so a client that keeps calling above the limit stays blocked until its own rate drops below it."
        ),
        REDIS_SENTINEL: (
            "One counter for all Gateways, as the rolling window, plus a `rate-limit-<key hash>.BLOCKED` key that rejects "
            f"every request for {per:g} s once the limit is passed."
        ),
        REDIS_FIXED: (
            f"One counter for all Gateways: a Redis string `rate-limit-<key hash>/<window start>` per {per:g} s window aligned to the clock. "
            "The full allowance returns at each window boundary, even to a client that keeps calling."
        ),
    }
    summary = notes[limiter]
    if limiter == REDIS_SENTINEL and not sentinel:
        summary += (
            " On Gateway 5.16.0 with 5 per 60 s on two Gateways, this fallback allows 4 requests, "
            "one fewer than the limit."
        )
    return {
        "limiter": limiter,
        "reason": why,
        "counter": "Redis, shared by every Gateway on that Redis",
        "shared_across_gateways": True,
        "allowed_per_window": {"all_gateways": int(rate)},
        "survives_gateway_restart": True,
        "summary": summary,
    }


def render(result):
    lines = [f"limiter: {result['limiter']}"]
    if result.get("reason"):
        lines.append(f"why: {result['reason']}")
    lines.append(f"counter: {result['counter']}")
    if result["allowed_per_window"]:
        lines.append("allowed per window: " + ", ".join(f"{name.replace('_', ' ')} = {value}" for name, value in result["allowed_per_window"].items()))
    if result["survives_gateway_restart"] is not None:
        lines.append(f"count survives a Gateway restart: {'yes' if result['survives_gateway_restart'] else 'no'}")
    lines.append(result["summary"])
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rate", type=float, required=True, help="requests allowed per window")
    parser.add_argument("--per", type=float, required=True, help="window length in seconds")
    parser.add_argument("--gateways", type=int, default=1, help="Gateways sharing one Redis (default 1)")
    parser.add_argument("--drl-threshold", type=float, default=0.0, help="drl_threshold; 0 or unset means 5")
    parser.add_argument("--redis-rolling", action="store_true", help="enable_redis_rolling_limiter is true")
    parser.add_argument("--sentinel", action="store_true", help="enable_sentinel_rate_limiter is true")
    parser.add_argument("--fixed-window", action="store_true", help="enable_fixed_window_rate_limiter is true")
    parser.add_argument("--drl-sentinel", action="store_true", help="drl_enable_sentinel_rate_limiter is true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = decide(args.rate, args.per, args.gateways, args.drl_threshold, args.redis_rolling, args.sentinel, args.fixed_window,
                        args.drl_sentinel)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
