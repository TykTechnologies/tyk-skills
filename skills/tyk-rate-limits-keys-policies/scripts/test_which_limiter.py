import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

import which_limiter as wl


class GatewayCases(unittest.TestCase):
    """Each case matches the behaviour of Tyk Gateway 5.16.0."""

    def test_one_gateway_counts_in_memory(self):
        result = wl.decide(10, 60, gateways=1)
        self.assertEqual(result["limiter"], wl.MEMORY)
        self.assertFalse(result["survives_gateway_restart"])
        self.assertEqual(result["allowed_per_window"]["per_gateway"], 10)

    def test_two_gateways_low_limit_uses_redis(self):
        result = wl.decide(10, 60, gateways=2)
        self.assertEqual(result["limiter"], wl.REDIS_ROLLING)
        self.assertTrue(result["shared_across_gateways"])
        self.assertEqual(result["allowed_per_window"], {"all_gateways": 10})

    def test_two_gateways_above_threshold_splits_in_memory(self):
        result = wl.decide(120, 10, gateways=2)  # 12 per second > 2 x 5
        self.assertEqual(result["limiter"], wl.MEMORY)
        self.assertEqual(result["allowed_per_window"], {"per_gateway": 60, "all_gateways_evenly_loaded": 120})

    def test_exactly_at_threshold_uses_redis(self):
        self.assertEqual(wl.decide(100, 10, gateways=2)["limiter"], wl.REDIS_ROLLING)  # 10 per second == 2 x 5

    def test_custom_threshold(self):
        self.assertEqual(wl.decide(100, 10, gateways=2, drl_threshold=1)["limiter"], wl.MEMORY)
        self.assertEqual(wl.decide(15, 10, gateways=2, drl_threshold=1)["limiter"], wl.REDIS_ROLLING)

    def test_redis_rolling_flag_wins_on_one_gateway(self):
        self.assertEqual(wl.decide(120, 10, gateways=1, redis_rolling=True)["limiter"], wl.REDIS_ROLLING)

    def test_drl_sentinel_changes_only_the_redis_fallback(self):
        # On Gateway 5.16.0, 5 per 60 s on two Gateways with drl_enable_sentinel_rate_limiter
        # writes rate-limit-<hash> and .BLOCKED.
        fallback = wl.decide(5, 60, gateways=2, drl_sentinel=True)
        self.assertEqual(fallback["limiter"], wl.REDIS_SENTINEL)
        self.assertIn("4 requests", fallback["summary"])
        self.assertEqual(wl.decide(120, 10, gateways=2, drl_sentinel=True)["limiter"], wl.MEMORY)
        self.assertEqual(wl.decide(5, 60, gateways=1, drl_sentinel=True)["limiter"], wl.MEMORY)
        self.assertNotIn("4 requests", wl.decide(5, 10, sentinel=True)["summary"])

    def test_sentinel_and_fixed_window(self):
        self.assertEqual(wl.decide(5, 10, sentinel=True)["limiter"], wl.REDIS_SENTINEL)
        self.assertEqual(wl.decide(5, 10, fixed_window=True)["limiter"], wl.REDIS_FIXED)

    def test_zero_rate_is_unlimited(self):
        self.assertEqual(wl.decide(0, 0)["limiter"], wl.NONE)


class SourceOrder(unittest.TestCase):
    """Precedence when several switches are on (gateway/session_manager.go at v5.16.0)."""

    def test_fixed_window_beats_sentinel_beats_rolling(self):
        self.assertEqual(wl.decide(5, 10, redis_rolling=True, sentinel=True, fixed_window=True)["limiter"], wl.REDIS_FIXED)
        self.assertEqual(wl.decide(5, 10, redis_rolling=True, sentinel=True)["limiter"], wl.REDIS_SENTINEL)

    def test_share_never_below_one(self):
        self.assertEqual(wl.decide(100, 1, gateways=3)["limiter"], wl.MEMORY)  # 100 per second > 3 x 5
        self.assertEqual(wl.decide(100, 1, gateways=3)["allowed_per_window"]["per_gateway"], 33)


class Cli(unittest.TestCase):
    def test_text_and_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(wl.main(["--rate", "10", "--per", "60", "--gateways", "2"]), 0)
        self.assertIn("limiter: redis-rolling-window", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(wl.main(["--rate", "10", "--per", "60", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["limiter"], wl.MEMORY)

    def test_bad_input(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(wl.main(["--rate", "10", "--per", "0"]), 2)
            self.assertEqual(wl.main(["--rate", "10", "--per", "60", "--gateways", "0"]), 2)


if __name__ == "__main__":
    unittest.main()
