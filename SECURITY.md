# TermFix Security Model

TermFix is a developer assistant, not a security sandbox. Its safety model reduces accidental execution risk while keeping every decision visible to the user.

## Threats considered

The current controls address:

- a typo being corrected into a destructive command;
- a destructive command being entered directly;
- compound shell syntax launching hidden additional commands;
- explicit shell command strings bypassing argument-level checks;
- a broad path or device target making deletion unusually dangerous;
- pasted commands becoming accidental approval input;
- local evidence changing between review and execution;
- credential values appearing in correction output;
- tampered portable artifacts or activation targets; and
- source inspection accidentally executing or modifying project code.

## Execution boundary

Every guarded child process receives an argument vector and `shell=False`. TermFix does not pass user input to a system shell for interpretation.

Corrected commands require explicit `y` or `yes`. High-risk commands, compound shell operators and explicit shell-command payloads are blocked. A correction that raises the conservative risk classification is withheld.

The `check`, `safety`, `context`, `doctor` and `activate` actions do not execute the inspected command. `demo` and `evaluate` do not execute user commands; they use small temporary fixtures that are deleted automatically.

## Risk classification

TermFix assigns one of three labels:

- **Low:** recognized read-only behavior;
- **Medium:** unknown behavior, code execution or possible state change; or
- **High:** recognized destructive behavior or a dangerous target/structure.

Rules cover deletion, formatting, shutdown and reboot, disk management, destructive Git operations, force and recursive flags, overwrite redirection, broad targets, device targets, nested destructive commands and explicit shell command text.

Low means that conservative evidence supports a read-only interpretation. It is not a mathematical guarantee that an external program is harmless.

## Paste protection

Paste mode:

- never executes collected lines immediately;
- analyzes every line before review;
- limits command count, line length and total input size;
- generates an unpredictable 64-bit review code after collection;
- ignores already-buffered lines while waiting for that code;
- provides no Run-all shortcut;
- prevents blocked, invalid and unavailable lines from running; and
- revalidates each selected line immediately before execution.

An interrupt, embedded line break event or exceeded limit during collection closes the TermFix shell to prevent leftover buffered input from reaching the ordinary prompt.

## Local data and privacy

TermFix works offline and contains no telemetry or network client. Evidence comes from the local `PATH`, relevant neighboring paths, supplied errors and explicitly named source files.

Source inspection is bounded and read-only. TermFix does not recursively scan the project, compile source, import project modules or silently edit a file. Obvious credential assignments and separate credential values are redacted from diagnostics. Unparseable raw paste text is hidden rather than echoed.

## Artifact integrity

The portable build records SHA-256 hashes, archive structure, normalized source hash, standard-library imports, empty-requirements evidence and passing-test count.

Doctor rejects stale, malformed or modified evidence. PowerShell activation is generated only for a validated portable build. Generated activation paths reject control characters and use quoted literals. Every activated launch recalculates the archive hash, and session deactivation checks ownership markers before removing its aliases and functions.

Activation prints reviewable PowerShell code; it does not execute that code, edit `PATH` or a profile, touch the registry or request administrator access.

## Known boundaries

- TermFix cannot prove the internal behavior of every executable.
- Medium-risk commands may still have side effects after the user chooses to run them.
- `shell=False` intentionally prevents PowerShell, CMD and Bash aliases, pipelines, redirection and chaining inside `termfix>`.
- Commands intended to create a new path may be refused until command-specific argument semantics are available.
- Language and stderr recognition are bounded profiles rather than full compiler implementations.
- TermFix does not replace operating-system permissions, backups, source control or a security sandbox.

Security-sensitive behavior should be reported through the repository’s GitHub issue tracker without including real credentials.
