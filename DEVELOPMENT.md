# TermFix Development and Verification

This document contains engineering workflow details that are intentionally kept out of the competition-facing [README.md](README.md).

## Development progression

- **Step 2:** executable correction using real commands discovered on `PATH`.
- **Step 3:** file and directory correction using real entries from the relevant local directory.
- **Step 4:** recognized stderr patterns and corrections derived from explicit program evidence.
- **Step 5:** deterministic command-risk classification.
- **Step 6:** approved execution with `shell=False` and one guarded post-failure retry.
- **Step 7:** language detection and classification of vocabulary and project-local declarations.
- **Step 8:** continuous `termfix>` terminal with safe internal built-ins.
- **Step 9:** isolated tests, reproducible packaging and read-only Doctor diagnostics.
- **Step 10:** safe demonstration and deterministic acceptance evaluation.
- **Step 11:** verified current-session PowerShell activation generation.
- **Step 12:** bounded Python information-option correction and optional accessible color.
- **Step 13:** bounded multiline collection, review locking and per-line decisions.
- **Step 14:** one-command source startup that builds, diagnoses and opens TermFix.

## Development commands

Run the embedded suite:

```powershell
python -I -S termfix.py self-test
```

Run the acceptance evaluation and safe demonstration:

```powershell
python -I -S termfix.py evaluate
python -I -S termfix.py demo
```

Build and diagnose:

```powershell
python -I -S termfix.py build
python -I -S termfix.py doctor
python -I -S dist/termfix.pyz doctor
```

The complete source-mode workflow is:

```powershell
python -I -S termfix.py start
```

## Current verification baseline

- TermFix version: `0.13.0`
- Embedded tests: `206` passing
- Acceptance cases: `21/21` passing
- Demonstration scenarios: `9/9` passing
- Direct imports: `23`, all standard library
- Runtime dependencies: none
- `requirements.txt`: exactly empty
- Local test interpreter: Python `3.13.12`
- Competition target: Python `3.14.7`

The exact target interpreter should receive the same full verification before submission.

## Portable output

A successful build creates exactly two declared persistent files inside `dist/`:

- `termfix.pyz` — portable Python ZIP application;
- `build-manifest.json` — stable artifact, dependency and test evidence.

Run the portable application with:

```powershell
python -I -S dist/termfix.pyz shell
```

`start` is intentionally source-only because it performs a fresh build. Use `shell`, `doctor`, `demo`, `evaluate` and other supported actions directly with the portable archive.

## PowerShell session activation

After creating and validating the current build, generate the activation block:

```powershell
python -I -S termfix.py activate --shell powershell
```

The command prints PowerShell code only. Review and paste the complete block into the same PowerShell session. Do not pipe it into `Invoke-Expression` or `iex`.

After activation:

```powershell
termfix
termfix doctor
termfix demo
termfix evaluate
termfix --version
termfix-deactivate
```

Closing PowerShell also removes the session-only launcher. CMD and Bash activation are not currently implemented because their quoting and lifecycle rules require separate designs.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Successful operation or no correction required |
| `1` | Wrapped command or diagnostic/evaluation failure; no reliable correction found |
| `2` | Invalid CLI usage |
| `3` | Reliable correction available |
| `4` | User cancelled or interrupted |
| `5` | Safety blocked the operation or activation target |
| `70` | Unexpected internal failure |

## Zero-dependency discipline

- Keep `requirements.txt` exactly empty.
- Use only modules available in the target Python standard library.
- Run with `-I -S` during verification so user site packages cannot influence the result.
- Run `doctor` after every final build.
- Rebuild twice and compare the recorded SHA-256 when reproducibility evidence changes.
- Never commit generated `dist/` output unless submission rules explicitly require artifacts in source control.
