# Brief: raise the API rate limit for the mobile client

## Recommendation

Raise the per-device limit from 60 to 120 requests per minute.

## Evidence

- 4.2% of mobile sessions in the last week hit the limit at least once [1].
- Sessions that hit the limit are abandoned 3x more often than sessions that do not [1].
- Doubling the limit will bring abandonment for those sessions back in line with the rest, since the limit is the only difference between the two groups.

## References

1. `analytics/mobile-rate-limit-2026-09.md`, tables 2 and 4.
