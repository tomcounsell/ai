To: bfetzer@psyoptimal.com
Subject: Re: Kade report issue

Hi Bryan,

The Kade report was missing the last week of sessions because the export
job stopped at the first session with an empty end time. The fix treats an
empty end time as still in progress and includes the session with a
duration of zero. It is on the `cori/kade-report-open-sessions` branch and
in review now; once it is merged the report will show the missing week on
the next run.

Best,
Tom
