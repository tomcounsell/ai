# Brief: pin the CI runner image

## Recommendation

Pin the CI runner to `ubuntu-24.04` instead of `ubuntu-latest`.

## Evidence

- Two of the last five CI failures on main were caused by the runner image changing under us, on 2026-08-19 and 2026-09-02 [1].
- GitHub's runner documentation says `ubuntu-latest` moves to a new major version with two weeks' notice, and names the pinned labels [2].
- Pinning costs one line in `.github/workflows/ci.yml` and a calendar reminder to bump it [3].

## References

1. `ops/ci-failures-2026-q3.md`, rows 3 and 5.
2. https://docs.github.com/en/actions/using-github-hosted-runners/using-github-hosted-runners "When a new major version of Ubuntu becomes the latest, the -latest label migrates to it over a period of at least two weeks, announced in advance."
3. `.github/workflows/ci.yml`, line 9.
