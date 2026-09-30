To: bfetzer@psyoptimal.com
Subject: Re: 180 Report

Hi Bryan,

Thanks for sending the 180 report over. I have pushed the fix for the
duplicated rows to the `cori/report-dedupe` branch; the PR is open and the
tests pass on CI. Once you or Will approve it I will merge and redeploy, and
the next nightly run will produce clean numbers.

One more thing for the team: the deploy key for the reporting box rotates on
Friday, so anyone running the report by hand needs the new key from the ops
channel before then.

Best,
Tom
