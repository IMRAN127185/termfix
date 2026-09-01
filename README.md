# TermFix

> An offline, zero-dependency terminal assistant that corrects command mistakes using local evidence and never runs a correction without permission.

- **Track:** A — Developer Tools & CLI
- **Runtime:** Python standard library only
- **Version:** 0.13.0
- **License:** MIT

## The problem

Terminal mistakes are easy to make and often difficult to understand. A misspelled executable, filename, option or project function can produce an unclear error, while blindly correcting a destructive command can make the situation worse.

Editors provide spelling suggestions and explanations, but a normal terminal usually waits until after Enter is pressed to report a failure.

## The solution

TermFix provides an interactive `termfix>` terminal that examines a command before execution. It can:

- correct executable names using programs proven to exist on `PATH`;
- correct file and directory arguments using entries proven to exist locally;
- recognize selected Python option mistakes such as `vrsion → --version`;
- understand keywords, standard-library names and declared functions in named source files;
- classify command risk before execution;
- explain what changed and why; and
- require explicit approval before running any corrected command.

TermFix works offline, installs no packages, does not send commands to an API and does not silently edit source files.

## Thirty-second demonstration

Start TermFix and enter a command containing two mistakes:

```text
termfix> pythod vrsion

Original:
  pythod vrsion

Suggestion:
  python --version  ⓘ

[y] Run   [e] Explain   [d] Diff   [Enter/n] Cancel
```

TermFix proves that `python` exists on the local `PATH`, recognizes `--version` from its bounded Python option profile, and waits for the user. Selecting Explain or Diff shows the evidence without executing anything.

A destructive command is handled differently:

```text
termfix> rm -rf /

Risk: High
Status: BLOCKED
```

The command is not offered a working Run action.

## Why TermFix is different

| Capability | TermFix approach |
|---|---|
| Correction evidence | Real executables, local paths, recognized errors and explicitly named source files |
| False-positive control | Weak or conflicting matches are rejected instead of guessed |
| Source awareness | Recognizes language vocabulary and project-local declarations without running the source |
| Safety | Rechecks risk after correction and blocks corrections that increase risk |
| Execution | Uses an argument vector with `shell=False`; corrected commands need explicit approval |
| Privacy | Entirely local and offline, with obvious credential values redacted from diagnostics |
| Dependencies | Python standard library only; `requirements.txt` is exactly empty |

## Quick start

Requirements:

- Python 3.13 or newer;
- no package installation; and
- a local clone of this repository.

Clone and enter the repository:

```powershell
git clone https://github.com/IMRAN127185/termfix.git
cd termfix
```

For the complete verified startup, run one command:

```powershell
python -I -S termfix.py start
```

`start` locates the project directory, runs the complete isolated test suite, creates the deterministic portable build, runs Doctor and opens `termfix>`. If the build or Doctor fails, the interactive terminal is not opened.

To start immediately without rebuilding:

```powershell
python -I -S termfix.py shell
```

From any PowerShell directory, use the full path and replace the dashed
`YOUR-TERMFIX-FOLDER` placeholder with the name of the folder that contains
`termfix.py`:

```powershell
python "D:\YOUR-TERMFIX-FOLDER\termfix.py" start
```

For example, this repository is currently stored in `D:\Zero Dependency Hackathon`,
so the complete command is:

```powershell
python "D:\Zero Dependency Hackathon\termfix.py" start
```

That one command automatically:

1. locates the folder containing TermFix;
2. runs all 206 isolated tests;
3. creates and verifies the deterministic portable build;
4. runs the read-only Doctor checks; and
5. opens the interactive `termfix>` terminal only after those checks pass.

If the build or Doctor fails, startup stops and the TermFix terminal is not
opened. Exiting TermFix does not change the parent PowerShell directory because
a child process cannot permanently change its parent shell.

## Main features

### Evidence-backed correction

TermFix does not use an online language model or a global computer scan. Each runnable correction must have bounded local evidence:

- executable → a real `PATH` entry;
- file or directory → a real neighboring entry;
- program option → a strong match from a small built-in profile;
- error correction → a recognized error pattern with an explicit candidate; or
- code identifier → active-language vocabulary or a declaration in an explicitly named source file.

### Source-aware diagnostics

Deep symbol profiles are available for Python, JavaScript, Java, C and C++. For example, when `main.c` declares `calculate`, TermFix can distinguish:

```text
while       → C keyword
printf      → C standard-library function
calculate   → Function declared in main.c
calcluate   → Possible typo of the local function calculate
```

Additional languages can be detected conservatively, including TypeScript, C#, Go, Rust, Ruby, PHP, Kotlin, Swift, Dart, Lua, Perl, R, Julia, shell and PowerShell. TermFix does not claim deep parsing for these additional languages.

### Interactive explanations

For a correction, the user can choose:

```text
[y] Run   [e] Explain   [d] Diff   [Enter/n] Cancel
```

`ⓘ` is displayed when supported, with `[i]` as the ASCII fallback. Optional ANSI color improves scanning but never carries meaning by itself.

### Safe multiline paste review

Type `paste` before entering several independent commands:

```text
termfix> paste
paste[1]> pythod vrsion
paste[2]> git status
paste[3]> rm -rf /
paste[4]> .end
```

TermFix analyzes every line before review, displays a status for each one and executes nothing automatically. A new unpredictable review code prevents already-buffered pasted text from becoming approval input. Every line is reviewed individually; there is no Run-all shortcut.

## How it works

```text
User command
     ↓
Parse into an argument vector
     ↓
Discover PATH, local path, error and language evidence
     ↓
Generate only strong, locally provable corrections
     ↓
Classify the original and corrected command risk
     ↓
Show suggestion, evidence and diff
     ↓
Explicit approval → revalidate → execute with shell=False
```

Corrections that become more dangerous are withheld. A selected pasted command is analyzed again immediately before execution so stale evidence cannot bypass review.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete deterministic pipeline.

## Command reference

| Command | Purpose |
|---|---|
| `python termfix.py start` | Build, diagnose and open the interactive terminal |
| `python termfix.py shell` | Open the interactive terminal immediately |
| `python termfix.py check -- COMMAND` | Inspect a command without executing it |
| `python termfix.py safety -- COMMAND` | Explain the command’s risk classification |
| `python termfix.py run -- COMMAND` | Preflight and guard execution |
| `python termfix.py context --identifier NAME -- COMMAND` | Inspect language and symbol evidence |
| `python termfix.py build` | Test and create a deterministic `.pyz` application |
| `python termfix.py doctor` | Validate runtime, dependency and build evidence read-only |
| `python termfix.py demo` | Show nine safe user-facing scenarios |
| `python termfix.py evaluate` | Run 21 deterministic acceptance cases |
| `python termfix.py self-test` | Run the embedded standard-library test suite |

Inside `termfix>`, the built-ins `cd`, `pwd`, `clear`, `paste`, `help` and `exit` run internally without launching a system shell.

## Verification

The current source is verified by:

- **206/206 embedded tests passed** under `python -I -S`;
- **21/21 acceptance cases passed**;
- **9/9 demonstration scenarios passed**;
- **23/23 direct imports identified as standard-library modules**;
- an exactly empty `requirements.txt`;
- source-mode and portable-archive Doctor checks; and
- byte-for-byte reproducible portable builds.

Run the evidence locally:

```powershell
python -I -S termfix.py self-test
python -I -S termfix.py evaluate
python -I -S termfix.py demo
python -I -S termfix.py build
python -I -S termfix.py doctor
```

The build writes only `dist/termfix.pyz` and `dist/build-manifest.json`. The manifest records hashes, standard-library import proof, empty-requirements proof and the passing test count without build times or machine-specific paths.

## Safety boundaries

- Corrected commands are never executed silently.
- High-risk commands and risk-increasing corrections are blocked.
- Child processes always receive an argument list with `shell=False`.
- `check`, `safety`, `context`, `doctor` and `activate` are read-only.
- Source inspection is bounded and never executes or edits source code.
- Paste mode has count and size limits and requires individual review.
- A Low label means conservative evidence was found; it is not a universal safety guarantee.

See [SECURITY.md](SECURITY.md) for the complete threat model, controls and limitations.

## Current limitations

- TermFix is an explicit interactive terminal; it does not watch ordinary PowerShell, CMD or Bash input automatically.
- Paste mode accepts several independent one-line commands, not pipelines, heredocs, shell continuations or multiline scripts.
- Shell aliases, redirection, pipelines and command chaining are unavailable inside `termfix>` because execution intentionally uses `shell=False`.
- Deep source-symbol understanding is currently limited to Python, JavaScript, Java, C and C++.
- Source parsing is conservative and is not a replacement for a compiler or full language server.
- The exact competition target is Python 3.14.7; current local verification was performed with compatible Python 3.13.12 and should be repeated on 3.14.7 before submission.
- `start` requires `termfix.py` because it creates a fresh build. The portable `dist/termfix.pyz` supports commands such as `shell` and `doctor`, but it does not rebuild itself.

## Technical documentation

- [STDLIB.md](STDLIB.md) — complete dependency receipt, package substitutions and honest trade-offs.
- [ARCHITECTURE.md](ARCHITECTURE.md) — correction algorithms, evidence ranking and execution flow.
- [SECURITY.md](SECURITY.md) — threat model, risk rules, privacy and safety boundaries.
- [DEVELOPMENT.md](DEVELOPMENT.md) — development history, builds, tests, activation and exit codes.

## Competition compliance

TermFix is entered in **Track A — Developer Tools & CLI**. Its runtime dependency list is empty, it uses Python’s standard library exclusively, and it works without network access.

The project is available under the [MIT License](LICENSE).
