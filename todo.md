# White Wizard — Todo

### Stream mode — infinite cycle
Add a `--stream-watch` flag that runs stream mode in a continuous loop, re-executing
the question checklist whenever new git commits are detected (poll `git rev-parse HEAD`
on an interval). Pair with a configurable cooldown in wizard.yaml so it doesn't
hammer the API on rapid-fire commits.
