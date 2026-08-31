# TermFix

TermFix is a zero-dependency Python CLI for proof-backed terminal command correction.

The repository currently contains:

- **Step 2:** correct the executable token using real commands discovered on `PATH`.
- **Step 3:** correct file and directory arguments using real entries from the relevant local directory.
- **Step 4:** recognize useful stderr patterns and derive corrections from explicit program evidence.
- **Step 5:** classify command risk with deterministic rules before any future execution is considered.
- **Step 6:** run approved commands with `shell=False`, capture failures and offer one safe corrected retry.
- **Step 7:** detect the active source language and classify keywords, standard-library names and project-local declarations.
- **Step 8:** provide a continuous `termfix>` terminal with safe built-ins and the complete correction pipeline.
- **Step 9:** test and package TermFix reproducibly, then diagnose its local environment without changing it.
- **Step 10:** demonstrate the real backend safely and measure it against deterministic acceptance cases.

Every suggestion carries local evidence. `check`, `safety` and `context` never execute the inspected command.

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

Inspect language context and check a possible code identifier without compiling or running it:

```powershell
python termfix.py context --identifier calcluate -- gcc main.c
```

If `main.c` declares a function named `calculate`, TermFix reports C as the language and suggests `calcluate -> calculate` with the file and line as evidence. It can also distinguish a C keyword such as `while` from a C standard-library function such as `printf`.

The same backend runs after a failed `run`: recognized undeclared-name errors from Python, JavaScript, Java, C and C++ are checked against the active profile and named source files. A source-backed suggestion is displayed, but TermFix never edits the source automatically.

When automatic evidence is unavailable, provide an explicit language override:

```powershell
python termfix.py context --language cpp --identifier whlie -- custom-tool source.unknown
```

Deep symbol profiles are included for Python, JavaScript, Java, C and C++. TermFix can detect additional languages—including TypeScript, C#, Go, Rust, Ruby, PHP, Kotlin, Swift, Dart, Lua, Perl, R, Julia, shell and PowerShell—but does not claim deep parsing for them yet.

Start the interactive TermFix terminal once and enter multiple commands:

```powershell
python termfix.py shell
```

```text
termfix> pyhton mian.py

Original:
  pyhton mian.py

Suggestion:
  python main.py  ⓘ

[y] Run   [e] Explain   [d] Diff   [Enter/n] Cancel
```

The interactive terminal includes `cd PATH`, `pwd`, `clear`, `help` and `exit`. Its current directory is session-local, and a misspelled `cd` directory can receive the same evidence-backed correction and approval flow. `Ctrl+C` cancels the current input or interrupts a command without closing TermFix; `Ctrl+D`/EOF exits cleanly. Use `python termfix.py shell --cwd DIRECTORY` to choose the starting directory.

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

## Portable build and doctor

Create a tested portable build:

```powershell
python -I -S termfix.py build
```

The build command first runs the complete embedded test suite in an isolated Python process. It writes exactly two persistent files inside `dist/` only after the tests pass:

- `dist/termfix.pyz` — a portable Python ZIP application.
- `dist/build-manifest.json` — hashes and dependency/test evidence for that archive.

Run the portable application wherever a compatible Python interpreter is available:

```powershell
python -I -S dist/termfix.pyz shell
```

Inspect the current runtime, dependency proof, source, build hash, archive structure and recorded test result:

```powershell
python -I -S termfix.py doctor
python -I -S dist/termfix.pyz doctor
```

`doctor` is strictly read-only. It does not execute the archived program, run tests, install anything, edit `PATH`, modify shell profiles or write files. A missing portable build is a warning; corrupted, modified or stale build evidence is a failure.

## Safe demo and acceptance evaluation

Show seven representative TermFix scenarios:

```powershell
python -I -S termfix.py demo
```

Run the complete Step 10 acceptance evaluation:

```powershell
python -I -S termfix.py evaluate
```

The evaluation currently checks 18 cases covering executable, file and directory correction; false-positive refusal; stderr evidence; destructive-command and shell-operator blocking; language-aware symbol understanding; non-mutation; indicator fallback; internal interactive commands; corrupt-artifact rejection; credential redaction; and deterministic repeated analysis. It also reports observational runtime, but speed is not used as a pass condition.

These commands are optional and intended for development, judging and demonstration. A normal user starts TermFix with `python termfix.py shell`. Neither `demo` nor `evaluate` launches a user command, edits project files, accesses the internet or scans the computer. They create only small isolated fixtures in the operating system's temporary directory and delete them automatically.

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
- Language evidence can come from an explicit override, tool name, source extension, shebang, compiler/runtime error shape or direct project marker.
- Conflicting strong language evidence produces `Unknown`; TermFix does not guess through a conflict.
- Source inspection is limited to at most 16 explicitly named files of at most 1 MiB each.
- Python declarations are read with the standard-library AST. JavaScript, Java, C and C++ declarations use conservative patterns after comments and strings are masked.
- Identifier corrections come only from the active language profile or declarations proven in the explicitly named source files.
- Recognized undeclared-identifier failures receive a compact source-backed diagnostic; source code is never silently changed.
- Interactive input is split into an argument vector without opening PowerShell, CMD, Bash or another system shell.
- The interactive prompt reuses executable, path, stderr, safety and language-aware correction for every external command.
- Interactive `cd`, `pwd`, `clear`, `help` and `exit` are internal operations and do not spawn shell commands.
- The interactive directory exists only inside the TermFix session; the parent terminal directory is not changed.
- `ⓘ` is displayed when the terminal encoding supports it, with `[i]` as the safe fallback.
- Invalid quotes, blank input, `Ctrl+C` and EOF are handled without a traceback.
- A portable build is created only after the full test suite passes under `python -I -S` with `shell=False`.
- The portable archive contains exactly one uncompressed `__main__.py` entry with normalized LF line endings and a fixed 1980 timestamp.
- Rebuilding unchanged source produces byte-for-byte identical archive and manifest files.
- The build manifest records SHA-256 hashes, standard-library import proof, empty-requirements proof and the passing test count without machine-specific paths or build times.
- Existing incomplete, unknown, symlinked or manually modified build output is never overwritten.
- Build files are staged and replaced atomically; an archive replacement is rolled back if the manifest replacement fails.
- `doctor` validates local evidence without executing subprocesses or modifying the filesystem.
- `demo` shows seven user-facing scenarios through the real correction, safety and language backends.
- `evaluate` runs 18 explicit acceptance cases, including false-positive and non-mutation controls.
- Step 10 never launches a user command; its isolated temporary fixtures are removed automatically.
- Acceptance output never exposes its temporary path or test credential value.
- Weak matches are rejected.
- Tokens that do not require correction stay unchanged.
- Candidate ranking is deterministic.
- `check`, `safety` and `context` cannot call the executor.

Only the explicit `run` action, external commands entered inside `shell`, and the isolated self-test started by `build` can launch a child process. The `check`, `safety`, `context` and `doctor` actions remain read-only. `demo` and `evaluate` do not launch commands or change project files, but they briefly create and remove isolated temporary fixtures. TermFix does not rename files, edit source code, recursively scan a project or claim to be a full compiler/parser. A Low label is conservative evidence, not a guarantee. Because all command execution enforces `shell=False`, PowerShell/CMD aliases and shell syntax such as pipelines, redirection and command chaining are not available inside `termfix>`. Use the provided internal built-ins or a real executable. `run` also refuses unresolved path arguments, so commands intended to create a new path may need to be invoked directly until command-specific argument semantics are added. `build` creates only the `dist` directory and its two declared files; it never installs TermFix or changes system configuration.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Successful read-only check/classification; no correction required |
| `1` | Wrapped command failed, launch failed, no reliable correction was found, diagnostics found invalid evidence, or an acceptance/demo case failed |
| `2` | Invalid CLI usage |
| `3` | Reliable correction available |
| `4` | User cancelled or interrupted the operation |
| `5` | Safety blocked a high-risk command or risk-increasing correction |
| `70` | Unexpected internal failure |

Target runtime: Python 3.14.7. Steps 2-10 are locally tested with Python 3.13.12.

Track: **A — Developer Tools & CLI**.
