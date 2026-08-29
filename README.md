# TermFix

TermFix is a zero-dependency Python CLI for proof-backed terminal command correction.

This repository currently contains **Step 2**: executable-name inspection. It checks the first command token, searches real executables on the local `PATH`, and prints the strongest reliable correction with evidence. It does not execute the inspected command.

## Try it

```powershell
python termfix.py check -- pyhton app.py
```

Run the embedded standard-library tests:

```powershell
python -I -S termfix.py self-test
```

## Step 2 guarantees

- Standard library only; `requirements.txt` is empty.
- Suggested executables are discovered locally on `PATH`.
- Weak matches are rejected.
- Arguments after the executable stay unchanged.
- Candidate ranking is deterministic.
- `check` contains no command-execution pathway.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Executable exists; no correction required |
| `1` | Executable missing and no reliable correction found |
| `2` | Invalid CLI usage |
| `3` | Reliable correction available |
| `70` | Unexpected internal failure |

Target runtime: Python 3.14.7. Step 2 is locally tested with Python 3.13.12.

Track: **A — Developer Tools & CLI**.
