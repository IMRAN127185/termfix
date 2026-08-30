# TermFix

TermFix is a zero-dependency Python CLI for proof-backed terminal command correction.

The repository currently contains:

- **Step 2:** correct the executable token using real commands discovered on `PATH`.
- **Step 3:** correct file and directory arguments using real entries from the relevant local directory.
- **Step 4:** recognize useful stderr patterns and derive corrections from explicit program evidence.
- **Step 5:** classify command risk with deterministic rules before any future execution is considered.
- **Step 6:** run approved commands with `shell=False`, capture failures and offer one safe corrected retry.

Every suggestion carries local evidence. `check` and `safety` never execute the inspected command.

## Try it

```powershell
python termfix.py check -- pyhton app.py
```

Correct a misspelled local path:

```powershell
python termfix.py check -- python src/mian.py --verbose
```

Analyze a supplied error message without running the command:

```powershell
python termfix.py check --error-text "argument action: invalid choice: 'instal' (choose from 'install', 'remove')" -- tool instal
```

Classify a command without running it:

```powershell
python termfix.py safety -- git reset --hard
```

Run a valid command through the guarded executor:

```powershell
python termfix.py run -- python --version
```

Try a corrected run in an interactive terminal:

```powershell
python termfix.py run -- pyhton --version
```

TermFix displays the suggestion and waits for one of these choices:

```text
[y] Run   [e] Explain   [d] Diff   [Enter/n] Cancel
```

Only `y` or `yes` approves a corrected command. Enter, `n`, EOF and non-interactive input cancel. `e` displays correction evidence and `d` displays the changed token positions. An unchanged command may run immediately because choosing the `run` action already expresses intent; all Step 5 execution blocks still apply.

Step 5 labels commands as:

- **Low:** a recognized read-only form, such as `git status` or `python --version`.
- **Medium:** an unknown command or one that may change state or execute code.
- **High:** a recognized destructive operation, dangerous redirection, broad destructive target or raw device target.

High-risk results are marked `BLOCKED` and return exit code `5`. Unknown commands are never assumed to be Low risk. If a spelling correction would increase risk—for example, an unknown typo becoming a deletion command—TermFix withholds the runnable correction.

Run the embedded standard-library tests:

```powershell
python -I -S termfix.py self-test
```

## Current guarantees

- Standard library only; `requirements.txt` is empty.
- Suggested executables are discovered locally on `PATH`.
- Suggested files and directories are proven to exist locally.
- Path searching is limited to the relevant directory; the computer is not scanned globally.
- At most one misspelled component is corrected in each path token.
- URLs, options and wildcard expressions are not treated as paths.
- Recognized errors include command-not-found, missing file/module, invalid choice, unrecognized argument and permission denied.
- Explicit suggestions and allowed choices from stderr are treated as evidence, not instructions.
- Raw stderr is not repeated in correction output, and obvious credential assignments are redacted.
- Safety rules recognize deletion, formatting, shutdown/reboot, disk-management, destructive Git operations, force/recursive flags, shell operators, overwrite redirection and broad/device targets.
- Every safety label includes the exact rules and evidence that produced it.
- Corrections that would increase the conservative risk level are blocked.
- Corrected execution always requires explicit `y` approval; Enter cancels.
- Child processes receive an argument list and `shell=False` is always enforced.
- High-risk operations, complex shell operators and explicit shell-command strings are blocked by `run`.
- Failed commands may receive one stderr-backed corrected retry, which requires a new approval.
- Weak matches are rejected.
- Tokens that do not require correction stay unchanged.
- Candidate ranking is deterministic.
- `check` and `safety` cannot call the executor.

Only the explicit `run` action can launch a child process. Steps 2-5 remain read-only. TermFix does not rename files, edit source code or completely parse PowerShell/Bash syntax. A Low label is conservative evidence, not a guarantee. Because `run` enforces `shell=False`, shell built-ins such as PowerShell aliases or CMD-only commands are not treated as standalone executables. `run` also refuses unresolved path arguments, so commands intended to create a new path may need to be invoked directly until command-specific argument semantics are added.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Successful read-only check/classification; no correction required |
| `1` | Wrapped command failed, launch failed or no reliable correction was found |
| `2` | Invalid CLI usage |
| `3` | Reliable correction available |
| `4` | User cancelled or interrupted the operation |
| `5` | Safety blocked a high-risk command or risk-increasing correction |
| `70` | Unexpected internal failure |

Target runtime: Python 3.14.7. Steps 2-6 are locally tested with Python 3.13.12.

Track: **A — Developer Tools & CLI**.
