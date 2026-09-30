# Brief: move the nightly report to a materialized view

## Recommendation

Replace the nightly `report_daily` cron with a materialized view refreshed at 02:00 UTC.

## Evidence

- The cron's median runtime over the last 30 days is 41 minutes [1].
- Materialized views in Postgres 18 support `REFRESH ... CONCURRENTLY` without blocking readers [2].
- The same change in the invoicing service cut its nightly window from 38 to 6 minutes [3].

## References

1. `ops/dashboards/nightly-runtime.md`, section "report_daily", 30-day median.
2. PostgreSQL 18 documentation, REFRESH MATERIALIZED VIEW, https://www.postgresql.org/docs/18/sql-refreshmaterializedview.html "Refresh the materialized view without locking out concurrent selects on the materialized view."
3. `ops/postmortems/2026-07-invoicing-nightly.md`
