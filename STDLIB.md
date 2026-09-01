# TermFix Standard-Library Substitution Report

This document is the dependency receipt for TermFix. It explains which third-party packages would normally be considered for this kind of CLI, how TermFix replaces the required functionality with Python's standard library, and where the standard-library-only approach creates deliberate limitations.

## Compliance summary

| Item | TermFix evidence |
|---|---|
| Competition track | Track A — Developer Tools & CLI |
| Target runtime | Python 3.14.7 |
| Minimum supported runtime | Python 3.13 |
| Runtime dependencies | None |
| Dependency manifest | `requirements.txt` is exactly empty |
| Direct imports | 23, verified against `sys.stdlib_module_names` with `__future__` explicitly allowed |
| Vendored third-party code | None |
| Network services | None |
| Build command | `python -I -S termfix.py build` |
| Portable artifact | `dist/termfix.pyz` plus an auditable JSON manifest |

TermFix does not claim to be a drop-in replacement for every package named below. It replaces only the focused functionality that this application needs. That narrower scope is what makes a dependable zero-dependency implementation possible.

## What packages would normally be used

| Common third-party choice | Why a project might use it | TermFix standard-library replacement |
|---|---|---|
| Click or Typer | Command trees, options, help text and validation | `argparse`, typed `dataclasses` and `Enum` values |
| prompt-toolkit | Interactive prompts, key handling and terminal sessions | `input`, `sys`, `os`, a bounded state machine and internal shell commands |
| Rich or Colorama | Color, styled status messages and Unicode presentation | Small ANSI styling helpers, TTY checks, encoding checks and an ASCII `[i]` fallback |
| RapidFuzz or TheFuzz | Fast fuzzy matching and ranked spelling suggestions | `difflib.SequenceMatcher` plus role-specific thresholds, local evidence and refusal rules |
| whichcraft-style executable-discovery helpers | Finding runnable programs across platforms | `os.scandir`, `os.access`, `os.environ`, `shutil.which`, `pathlib` and Windows `PATHEXT` handling |
| tree-sitter, astroid or language-specific parsers | Extracting code structure and project symbols | Python `ast`; conservative `re` patterns for JavaScript, Java, C and C++ after comments and strings are masked |
| pytest | Test discovery, fixtures, assertions and mocking | `unittest`, `unittest.mock` and `tempfile.TemporaryDirectory` |
| PyInstaller, build or packaging helpers | Producing a distributable application and metadata | `zipfile`, `hashlib`, `json`, `tempfile` and atomic `os.replace` operations |

Within TermFix's bounded feature set, those substitutions remove the need to install a CLI framework, terminal UI framework, fuzzy-matching library, source parser, test runner or packaging library.

## Complete standard-library inventory

The build manifest records these 23 direct imports. `unittest.mock` is used through the standard-library `unittest` package and therefore does not add another root import.

| Module | Purpose in TermFix |
|---|---|
| `__future__` | Enables postponed annotation behavior consistently. |
| `argparse` | Defines the public command-line interface and validates arguments. |
| `ast` | Parses Python source, extracts declarations, checks syntax and audits imports without executing code. |
| `builtins` | Supplies Python built-in names for language-aware identifier classification. |
| `dataclasses` | Represents candidates, corrections, safety findings, build results and diagnostic records. |
| `difflib` | Calculates deterministic token similarity for bounded candidate sets. |
| `enum` | Defines explicit states for risk, language, confidence, errors, color and paste review. |
| `hashlib` | Produces SHA-256 integrity and reproducibility evidence. |
| `io` | Builds and validates the portable archive in memory and supports test output streams. |
| `json` | Writes and reads the deterministic build manifest. |
| `keyword` | Supplies the official Python keyword vocabulary. |
| `os` | Reads environment and filesystem facts, checks executable permissions and performs atomic replacement. |
| `pathlib` | Handles source, directory, build and correction paths without hand-built path strings. |
| `re` | Recognizes bounded error forms, safety patterns, language clues and non-Python declarations. |
| `secrets` | Generates unpredictable paste-review unlock codes so buffered input cannot approve commands. |
| `shlex` | Splits interactive input into an argument vector without invoking a system shell. |
| `shutil` | Resolves real executables on `PATH`. |
| `subprocess` | Runs approved commands and isolated verification processes with `shell=False`. |
| `sys` | Provides runtime information, standard streams, encoding capabilities and the stdlib module index. |
| `tempfile` | Creates isolated evaluation fixtures and safely staged build files. |
| `time` | Measures evaluation duration for an informational user-facing report. |
| `unittest` | Provides the embedded test suite and mocks for execution-boundary tests. |
| `zipfile` | Creates and inspects the deterministic `.pyz` portable application. |

## The difficult parts

### Proof-backed matching without a fuzzy-search package

`difflib` can calculate textual similarity, but similarity alone is not proof. TermFix first assigns a token role and then limits its candidates to local evidence: actual executables on `PATH`, real neighboring files or directories, a small option profile, recognized error details, or declarations from explicitly named source files. Weak or conflicting candidates are refused. This is less broad than a machine-learning spell checker, but it is deterministic, explainable and safer for commands.

### Interactive terminal behavior without a UI framework

The standard library has no complete cross-platform terminal UI toolkit. TermFix implements a deliberately small interface using normal streams, ANSI sequences and explicit prompt states. Color is optional and never carries meaning by itself. Unicode `ⓘ` is used only when the output encoding supports it; otherwise TermFix shows `[i]`.

The cost is that TermFix does not provide advanced line editing, history search, mouse interaction or framework-level terminal emulation. Those features were excluded rather than approximated unreliably.

### Multiline paste safety

A simple input loop cannot safely distinguish newly pasted commands from later approval answers. TermFix therefore separates paste handling into collection, analysis, unlock and per-line decision states. `secrets.token_hex` generates a new 64-bit review code only after collection. Buffered paste text is ignored while that code is requested, every command is reviewed separately, and there is no Run-all action.

### Source awareness without compiler front ends

Python receives accurate syntax-tree inspection through `ast`. The standard library has no JavaScript, Java, C or C++ parser, so TermFix masks comments and strings and applies conservative declaration patterns. Inspection is read-only, never imports or executes project code, and is limited to 16 explicitly named files of at most 1 MiB each.

This can recognize useful local declarations, but it is not a compiler or language server. Macros, generated code, complex templates and cross-project symbol resolution remain outside the claim.

### Reproducible packaging without build tooling

TermFix constructs its own `.pyz` archive using `zipfile`. It normalizes line endings, fixes ZIP metadata and timestamps, records hashes and stages replacements before atomically publishing both the archive and its manifest. This produces byte-for-byte identical output from unchanged source.

The artifact remains a Python application and requires a compatible Python interpreter; it is not a native standalone executable. That trade-off keeps the shipped content small, inspectable and dependency-free.

## Safety enabled by the standard library

The absence of a shell framework is also a design advantage. TermFix passes an argument list directly to `subprocess.run(..., shell=False)`. Corrected commands need explicit approval, high-risk commands are blocked, risk-increasing corrections are withheld, and a reviewed paste item is revalidated immediately before execution.

`re`, `dataclasses` and `Enum` support deterministic risk rules. `hashlib` verifies artifacts. `secrets` protects the paste approval boundary. These controls reduce accidental execution risk, although TermFix remains a developer assistant rather than a security sandbox.

## What is not a dependency

- The Python interpreter is the declared runtime.
- Programs discovered on the user's `PATH` are local evidence and possible user-approved targets; they are not bundled by TermFix.
- Source files named by the user are inspected read-only and are never imported, compiled or copied into the artifact.
- AI assistance was part of development, not part of the runtime. TermFix performs no API or network calls.
- The portable archive contains TermFix's own normalized source only. No external source code, wheel, binary or package is vendored.

## Reproducible verification

Run the checks with isolated mode and site-package loading disabled:

```powershell
python -I -S termfix.py self-test
python -I -S termfix.py evaluate
python -I -S termfix.py demo
python -I -S termfix.py build
python -I -S termfix.py doctor
python -I -S dist/termfix.pyz doctor
```

The current verified baseline is:

- 206/206 embedded tests passed;
- 21/21 acceptance cases passed;
- 9/9 safe demonstration scenarios passed;
- 23/23 direct imports identified as standard-library modules;
- zero non-standard-library imports;
- zero bytes in `requirements.txt`; and
- a byte-for-byte reproducible portable build.

`build` refuses to create an artifact unless the isolated embedded suite passes. `doctor` then checks source syntax, imports, the empty dependency manifest, archive structure, hashes and recorded test evidence using local read-only inspection.

## Honest conclusion

Python's standard library supplied every primitive TermFix needed, but not a finished terminal assistant. The work was in combining modest tools—argument parsing, filesystem inspection, deterministic similarity, syntax trees, regular expressions, subprocess isolation and ZIP handling—into one cautious pipeline.

The result is not a general shell, compiler, language server or AI command generator. It is a focused terminal assistant that can prove useful corrections, explain its evidence, review multiline pastes and guard execution without installing or contacting anything else.
