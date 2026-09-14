<!--
Thanks for the change. The notes below are the ones specific to svngit;
CONTRIBUTING.md has the detail: https://github.com/sainanc-evertz/svngit/blob/master/CONTRIBUTING.md

Delete anything that does not apply — this is a prompt, not a form to fill in.
-->

## What and why

<!-- What changes, and what problem it solves. The diff already shows the how. -->

## How it was verified

<!--
Which half of the suite covers it, and why that half:

  - unit tests pin down the `svn` argv a command produces
  - integration tests check what Subversion actually does with it

Some bugs are only visible to one. If the change concerns how svngit and svn
interact, an integration test is the one that can fail.
-->

- [ ] `pytest` passes, with Subversion installed so the integration tests
      actually ran rather than skipping
- [ ] `black` and `mypy` are clean
- [ ] For a bug fix: the new test **fails without the fix** — a guard that
      passes against the broken version is worse than none

## Anything not verified

<!--
Say so plainly if some of this could not be checked here — another platform,
a real network server, a large repository. The Windows shim shipped twice
described as unverified, and when CI finally ran it, it looped forever.
Naming the gap is what let that get caught.
-->

## Documentation

<!--
The suite will tell you if a new command is missing from the README, the
command mapping or `--svngit-help`. Two things it cannot check:

  - sample output must be captured, never typed (docs/demo/capture.sh)
  - an option must work, be refused, report that it changes nothing, or be
    recorded in DEFAULT_BEHAVIOUR with a reason
-->
