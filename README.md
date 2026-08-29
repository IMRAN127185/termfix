# TermFix

TermFix is a zero-dependency Python CLI for proof-backed terminal command correction.

The repository currently contains:

- **Step 2:** correct the executable token using real commands discovered on `PATH`.
- **Step 3:** correct file and directory arguments using real entries from the relevant local directory.

Every suggestion carries local evidence. TermFix never executes the inspected command.

## Try it

```powershell
python termfix.py check -- pyhton app.py
```

Correct a misspelled local path:

```powershell
python termfix.py check -- python src/mian.py --verbose
```

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
- Weak matches are rejected.
- Tokens that do not require correction stay unchanged.
- Candidate ranking is deterministic.
- `check` contains no command-execution pathway.

Step 3 does not rename files, edit source code, correct command options, or completely parse PowerShell/Bash syntax.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Executable exists; no correction required |
| `1` | Executable or path missing and no reliable correction found |
| `2` | Invalid CLI usage |
| `3` | Reliable correction available |
| `70` | Unexpected internal failure |

Target runtime: Python 3.14.7. Steps 2-3 are locally tested with Python 3.13.12.

Track: **A — Developer Tools & CLI**.
