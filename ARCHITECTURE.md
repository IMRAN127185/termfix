# TermFix Architecture

This document describes the deterministic backend behind TermFix. For the user-facing overview and quick start, see [README.md](README.md).

## Design goals

TermFix is designed around four constraints:

1. corrections must be backed by bounded local evidence;
2. uncertainty must result in refusal rather than invention;
3. a spelling correction must never silently increase execution risk; and
4. the runtime must use only Python’s standard library.

## Analysis pipeline

Every external command entered through `check`, `run`, `shell` or paste review passes through the same high-level pipeline.

### 1. Input parsing

Input is separated into an argument vector without invoking PowerShell, CMD, Bash or another system shell. Invalid quoting is reported without a traceback. Interactive built-ins are recognized before the external-command pipeline.

### 2. Context discovery

TermFix collects only evidence relevant to the supplied command:

- executable candidates from directories listed on `PATH`;
- path candidates from the current or explicitly relevant local directory;
- recognized stderr structures supplied by the program or a previous guarded run;
- language evidence from an explicit override, tool name, source extension, shebang, error shape or direct project marker; and
- declarations from explicitly named source files.

It does not recursively scan the computer or execute discovery code.

### 3. Candidate generation

Independent correction engines examine appropriate token roles:

- **Executable engine:** compares token zero with real commands discovered on `PATH`.
- **Path engine:** compares file and directory tokens with real neighboring entries while preserving the rest of the path.
- **Error engine:** recognizes bounded error families such as command not found, missing file or module, invalid choice, unrecognized argument and permission denied.
- **Python option engine:** recognizes strong matches for a small information-option profile, while leaving arbitrary program arguments, `-m` modules and `-c` code unchanged.
- **Language engine:** classifies keywords and standard-library vocabulary and compares identifiers with declarations proven in explicitly named source files.

Options, URLs and wildcard expressions are not treated as ordinary paths. At most one misspelled path component is corrected in a token.

### 4. Ranking and refusal

Candidates receive deterministic similarity scores. Exact local evidence and stronger role-specific evidence take priority. Weak, unrelated or conflicting candidates are rejected.

Existing local files take priority over a semantic option interpretation. Conflicting strong language evidence produces `Unknown` instead of a guess. Candidate ordering is deterministic so identical evidence produces identical results.

### 5. Safety classification

The original and suggested argument vectors are classified independently:

- **Low:** a recognized read-only form such as `git status` or `python --version`;
- **Medium:** an unknown command or one that may execute code or change state; or
- **High:** a recognized destructive operation, dangerous shell structure, broad destructive target or raw device target.

If a correction increases the conservative risk level, TermFix withholds the runnable suggestion. High-risk results are blocked.

### 6. Explanation and approval

TermFix displays the original input, suggested argument vector, correction count, match confidence and risk. Explain reveals the evidence for each change; Diff shows changed token positions.

Only explicit `y` or `yes` approves a corrected command. Enter, `n`, EOF and non-interactive input cancel it.

### 7. Revalidation and execution

Approved commands are passed to a child process as an argument list with `shell=False`. Paste-mode selections are analyzed again immediately before execution; if the evidence changes, the old review does not authorize the new state.

After a recognized failure, TermFix may offer one stderr-backed corrected retry. That retry requires a new approval and is never repeated indefinitely.

## Language understanding

TermFix provides deep profiles for Python, JavaScript, Java, C and C++.

- Python declarations are extracted with the standard-library AST.
- JavaScript, Java, C and C++ declarations use conservative patterns after comments and strings are masked.
- Source inspection is limited to at most 16 explicitly named files of at most 1 MiB each.
- Malformed or oversized source is not treated as proof.

Additional languages can be detected for context, but TermFix does not claim deep declaration parsing for them.

## Interactive terminal

`shell` maintains only session-local state. Its `cd`, `pwd`, `clear`, `paste`, `help` and `exit` commands are handled internally. Changing directory inside TermFix does not alter the parent terminal.

External commands reuse the same correction, safety, explanation and execution pipeline used by the one-shot CLI actions.

## Paste review state machine

Paste handling deliberately separates four states:

1. **Collection:** accept bounded independent command lines until `.end`.
2. **Analysis:** parse and classify all collected lines without execution.
3. **Unlock:** require a newly generated 64-bit review code; buffered pasted text is ignored here.
4. **Decision:** offer Run, Skip, Explain or Diff separately for each eligible line.

There is no Run-all action. Blocked, unavailable and invalid lines cannot run. Exceeding input limits or interrupting collection closes the TermFix shell so leftover input cannot become normal prompt input.

## Build architecture

`build` first launches the embedded suite in an isolated Python process using `-I -S` and `shell=False`. Only a passing suite can produce:

- `dist/termfix.pyz`; and
- `dist/build-manifest.json`.

The archive contains one uncompressed `__main__.py` with normalized LF line endings, fixed metadata and a fixed ZIP timestamp. The manifest contains stable hashes and audit evidence but no build time or machine-specific path. Unchanged source therefore produces byte-for-byte identical outputs.

Unknown, incomplete, redirected or manually modified build output is not overwritten. Archive and manifest replacement is staged, atomic and rollback-aware.

## Doctor architecture

`doctor` performs read-only inspection of the runtime, current directory, Python discovery, indicator encoding, source syntax, import audit, empty requirements file, archive structure, hashes and recorded test evidence.

Doctor does not run the archived application, install anything, change environment variables or write files.
