Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) and Redis 7.2

# Testing a limit so that the test tells the truth

A limit is proved by sending more requests than it allows, inside one window, through every
Gateway that serves the API, and counting the 200s. Each mistake below gives a wrong result
on Gateway 5.16.0.

## The tool

```bash
python3 scripts/burst_test.py \
  --url http://gateway-1:8080/orders/anything/orders \
  --url http://gateway-2:8080/orders/anything/orders \
  --header-env Authorization=PARTNER_A_KEY \
  --count 14 --window 60 --expect-ok 10 --expect-reject 429
```

- `--url` once per Gateway: requests alternate between them.
- `--header-env Name=VARIABLE` reads the key from the environment, so it stays out of the
  command line and the shell history. `--header 'Name: value'` is there for other headers.
- `--count` more than the limit; `--window` the limit's `per` in seconds, so the tool can warn
  when the burst did not fit inside one window.
- `--expect-ok` the number of 2xx responses expected; `--tolerance` when an exact count is not
  the claim; `--expect-reject` the status every rejection must have (429 for a rate limit, 403
  for a quota).
- `--interval` seconds between requests, to test recovery; `--json` for a machine-readable
  summary.
- Exit status 0 pass, 1 an expectation failed, 2 a usage or connection error.

It prints the status sequence, the count per Gateway, the elapsed time, the first rejection's
body and the `X-RateLimit-*` headers of the first response.

`scripts/which_limiter.py --rate R --per P --gateways G` says which limiter will count that
limit and where, before you test.

## A good test

1. **Know the expected number first.** Run `which_limiter.py` with the real number of
   Gateways and the Gateway's limiter settings. With the in-memory limiter on several
   Gateways the expected total depends on how traffic is spread (see mistake 3).
2. **Use a fresh key**, created for the test, so the count starts at zero.
3. **Wait before the burst**: 10 seconds after changing a key or policy, and 10 seconds
   after starting or stopping a Gateway.
4. **Send the burst faster than the window.** The tool warns when it did not.
5. **Send it through every Gateway**, directly, not only through the load balancer.
6. **Check the count, the status and the body** of the first rejection.
7. **Test a second consumer** with a different limit on the same API, to show that the limits
   are per consumer.
8. **Then test recovery**: wait one window and send one more request.

## Mistakes that make a test lie

| # | Mistake | What goes wrong |
| --- | --- | --- |
| 1 | Testing one Gateway and assuming the result holds for several | One Gateway counts in memory; two Gateways count the same limit through Redis, or split it. The limiter is chosen from the number of Gateways (`limiters-and-counters.md`) |
| 2 | Testing through the load balancer only | It hides which Gateway answered. With the in-memory limiter each Gateway has its own count |
| 3 | Expecting the full limit when traffic is uneven | 120 per 10 s on two Gateways, all requests to one: 60 pass, not 120 |
| 4 | Sending the burst too slowly | The count spans two windows: a key limited to 5 per 10 s passes five, and five more after a ten-second pause. Keep the whole burst shorter than `per` |
| 5 | Testing straight after changing a key | The Gateway caches a key for 10 seconds: a burst sent right after a plan upgrade is still rejected under the old plan |
| 6 | Creating a key and changing it, or its policy, within the same second | The in-memory counter is tied to `last_updated`, which has one-second resolution. A key created and upgraded in the same second keeps the old allowance (5 instead of 20). Real changes happen later than creation; scripted tests need a pause |
| 7 | Testing straight after a Gateway starts or stops | For a few seconds the Gateways disagree about how many they are. A 5-per-minute key can let 10 through at the moment the second Gateway comes up, and exactly 5 from about two seconds later. A 5-per-10 s key can get only 2 about four seconds after a Gateway stops |
| 8 | Reusing a key from an earlier test | Its count, or its quota, is partly used. With the Redis rolling window even rejected requests from the earlier test still count |
| 9 | Retrying in a tight loop to see when the limit lifts | With the Redis rolling window, rejected requests keep the window full: one request per second against 5 per 10 s is rejected for as long as it continues. Wait a full window instead |
| 10 | Reading the stored key instead of sending traffic | The stored key can show the new rate while the Gateway enforces the old one (`objects-and-merge-rules.md`, "When a change reaches traffic") |
| 11 | Reading `X-RateLimit-Remaining` as the rate limit | By default those headers describe the quota (`responses-and-headers.md`) |
| 12 | Expecting 429 for a quota | A used-up quota is 403 `Quota exceeded` |
| 13 | Testing a per-consumer limit on an endpoint that also has a limit in the API definition | The shared cap fires first and for every caller; the body says `API Rate Limit Exceeded` |
| 14 | Counting on a restart to leave limits alone | An in-memory count is cleared by a Gateway restart; a count in Redis and every quota survive it |
| 15 | A slow stand-in upstream | A Python `http.server` backend answers through the Gateway in about 44 ms (median) by default and in about 1.8 ms with `disable_nagle_algorithm = True` on its handler class. At 44 ms a burst of 250 no longer fits in a 10-second window |

## Windows and clocks

- In-memory: the window starts at the first request of a burst.
- Redis rolling window: there is no boundary; the count is the requests in the last `per`
  seconds, rejected ones included.
- Fixed window: boundaries are on the clock (`per` 10 gives windows starting at seconds 0,
  10, 20). A burst that straddles a boundary can pass twice the limit: ten requests can pass in
  11 seconds against 5 per 10 s.
- Quota: the period starts at the first request and its end is in `X-RateLimit-Reset`.

For a short test use a short `per` (10 seconds) so that a fresh window is never far away, and
say in the result which window you judged.

## Load, latency and acceptance criteria

This file tests that a limit is correct. Proving it under load, with acceptance criteria and
kept evidence, is the `tyk-poc-runbook` skill.
