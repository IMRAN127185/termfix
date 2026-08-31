#!/usr/bin/env python3
"""TermFix Steps 2-8: proof-backed correction and an interactive terminal.

Executable candidates come from PATH. File and directory candidates come from
the relevant local directory. Error evidence comes from recognized stderr
patterns. Safety decisions come from conservative, deterministic command rules.
The check and safety actions never execute commands. The run action uses an
argument vector with shell=False and requires explicit approval for corrections.
Language context and code symbols are inferred from bounded, local evidence only;
source files are inspected but never executed or modified.
The interactive prompt retains these guarantees while accepting repeated commands.
"""

from __future__ import annotations

import argparse
import ast
import builtins
from dataclasses import dataclass
import difflib
from enum import Enum
import json
import keyword
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


APP_NAME = "TermFix"
VERSION = "0.7.0"

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2
EXIT_CORRECTION = 3
EXIT_CANCELLED = 4
EXIT_BLOCKED = 5
EXIT_INTERNAL = 70

MINIMUM_MATCH_SCORE = 70
HIGH_MATCH_SCORE = 85
WINDOWS_EXECUTABLE_EXTENSIONS = (".com", ".exe", ".bat", ".cmd")
MAX_SOURCE_FILES = 16
MAX_SOURCE_BYTES = 1_048_576


class MatchLabel(str, Enum):
    """Human-readable labels for deterministic match scores."""

    HIGH = "High"
    MEDIUM = "Medium"


class ErrorCategory(str, Enum):
    """Error families recognized by the deterministic stderr rule engine."""

    COMMAND_NOT_FOUND = "command not found"
    FILE_NOT_FOUND = "file not found"
    MODULE_NOT_FOUND = "module not found"
    INVALID_CHOICE = "invalid choice"
    UNRECOGNIZED_ARGUMENT = "unrecognized argument"
    PERMISSION_DENIED = "permission denied"
    UNKNOWN = "unknown error"


class RiskLevel(str, Enum):
    """Conservative command-risk labels ordered from least to most risky."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class SourceLanguage(str, Enum):
    """Source languages TermFix can identify from local evidence."""

    UNKNOWN = "Unknown"
    PYTHON = "Python"
    JAVASCRIPT = "JavaScript"
    TYPESCRIPT = "TypeScript"
    C = "C"
    CPP = "C++"
    JAVA = "Java"
    CSHARP = "C#"
    GO = "Go"
    RUST = "Rust"
    RUBY = "Ruby"
    PHP = "PHP"
    KOTLIN = "Kotlin"
    SWIFT = "Swift"
    DART = "Dart"
    LUA = "Lua"
    PERL = "Perl"
    R = "R"
    JULIA = "Julia"
    SHELL = "Shell"
    POWERSHELL = "PowerShell"


class DetectionConfidence(str, Enum):
    """Strength of the evidence used for a language decision."""

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class SymbolKind(str, Enum):
    """Honest categories for profile-backed and project-local identifiers."""

    KEYWORD = "keyword"
    BUILTIN = "built-in"
    STANDARD_LIBRARY = "standard-library symbol"
    LOCAL_FUNCTION = "local function"
    LOCAL_CLASS = "local class"
    LOCAL_VARIABLE = "local variable"
    IMPORT = "import"
    UNKNOWN = "unknown"


RISK_PRIORITY = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}

DEEP_PROFILE_LANGUAGES = frozenset(
    {
        SourceLanguage.PYTHON,
        SourceLanguage.JAVASCRIPT,
        SourceLanguage.JAVA,
        SourceLanguage.C,
        SourceLanguage.CPP,
    }
)

LANGUAGE_ALIASES = {
    "python": SourceLanguage.PYTHON,
    "py": SourceLanguage.PYTHON,
    "javascript": SourceLanguage.JAVASCRIPT,
    "js": SourceLanguage.JAVASCRIPT,
    "typescript": SourceLanguage.TYPESCRIPT,
    "ts": SourceLanguage.TYPESCRIPT,
    "c": SourceLanguage.C,
    "c++": SourceLanguage.CPP,
    "cpp": SourceLanguage.CPP,
    "cxx": SourceLanguage.CPP,
    "java": SourceLanguage.JAVA,
    "c#": SourceLanguage.CSHARP,
    "csharp": SourceLanguage.CSHARP,
    "cs": SourceLanguage.CSHARP,
    "go": SourceLanguage.GO,
    "golang": SourceLanguage.GO,
    "rust": SourceLanguage.RUST,
    "ruby": SourceLanguage.RUBY,
    "php": SourceLanguage.PHP,
    "kotlin": SourceLanguage.KOTLIN,
    "swift": SourceLanguage.SWIFT,
    "dart": SourceLanguage.DART,
    "lua": SourceLanguage.LUA,
    "perl": SourceLanguage.PERL,
    "r": SourceLanguage.R,
    "julia": SourceLanguage.JULIA,
    "shell": SourceLanguage.SHELL,
    "sh": SourceLanguage.SHELL,
    "bash": SourceLanguage.SHELL,
    "powershell": SourceLanguage.POWERSHELL,
    "pwsh": SourceLanguage.POWERSHELL,
}

SOURCE_EXTENSIONS = {
    ".py": SourceLanguage.PYTHON,
    ".pyw": SourceLanguage.PYTHON,
    ".js": SourceLanguage.JAVASCRIPT,
    ".mjs": SourceLanguage.JAVASCRIPT,
    ".cjs": SourceLanguage.JAVASCRIPT,
    ".ts": SourceLanguage.TYPESCRIPT,
    ".tsx": SourceLanguage.TYPESCRIPT,
    ".c": SourceLanguage.C,
    ".h": SourceLanguage.C,
    ".cc": SourceLanguage.CPP,
    ".cpp": SourceLanguage.CPP,
    ".cxx": SourceLanguage.CPP,
    ".hh": SourceLanguage.CPP,
    ".hpp": SourceLanguage.CPP,
    ".hxx": SourceLanguage.CPP,
    ".java": SourceLanguage.JAVA,
    ".cs": SourceLanguage.CSHARP,
    ".go": SourceLanguage.GO,
    ".rs": SourceLanguage.RUST,
    ".rb": SourceLanguage.RUBY,
    ".php": SourceLanguage.PHP,
    ".kt": SourceLanguage.KOTLIN,
    ".kts": SourceLanguage.KOTLIN,
    ".swift": SourceLanguage.SWIFT,
    ".dart": SourceLanguage.DART,
    ".lua": SourceLanguage.LUA,
    ".pl": SourceLanguage.PERL,
    ".pm": SourceLanguage.PERL,
    ".r": SourceLanguage.R,
    ".jl": SourceLanguage.JULIA,
    ".sh": SourceLanguage.SHELL,
    ".bash": SourceLanguage.SHELL,
    ".zsh": SourceLanguage.SHELL,
    ".ps1": SourceLanguage.POWERSHELL,
}

TOOL_LANGUAGES = {
    "python": SourceLanguage.PYTHON,
    "python3": SourceLanguage.PYTHON,
    "py": SourceLanguage.PYTHON,
    "node": SourceLanguage.JAVASCRIPT,
    "nodejs": SourceLanguage.JAVASCRIPT,
    "bun": SourceLanguage.JAVASCRIPT,
    "deno": SourceLanguage.JAVASCRIPT,
    "javac": SourceLanguage.JAVA,
    "java": SourceLanguage.JAVA,
    "gcc": SourceLanguage.C,
    "cc": SourceLanguage.C,
    "clang": SourceLanguage.C,
    "g++": SourceLanguage.CPP,
    "c++": SourceLanguage.CPP,
    "clang++": SourceLanguage.CPP,
    "csc": SourceLanguage.CSHARP,
    "dotnet": SourceLanguage.CSHARP,
    "go": SourceLanguage.GO,
    "rustc": SourceLanguage.RUST,
    "cargo": SourceLanguage.RUST,
    "ruby": SourceLanguage.RUBY,
    "php": SourceLanguage.PHP,
    "kotlinc": SourceLanguage.KOTLIN,
    "kotlin": SourceLanguage.KOTLIN,
    "swift": SourceLanguage.SWIFT,
    "swiftc": SourceLanguage.SWIFT,
    "dart": SourceLanguage.DART,
    "lua": SourceLanguage.LUA,
    "perl": SourceLanguage.PERL,
    "rscript": SourceLanguage.R,
    "julia": SourceLanguage.JULIA,
    "bash": SourceLanguage.SHELL,
    "sh": SourceLanguage.SHELL,
    "zsh": SourceLanguage.SHELL,
    "pwsh": SourceLanguage.POWERSHELL,
    "powershell": SourceLanguage.POWERSHELL,
}

PROJECT_MARKERS = {
    "pyproject.toml": SourceLanguage.PYTHON,
    "setup.py": SourceLanguage.PYTHON,
    "package.json": SourceLanguage.JAVASCRIPT,
    "pom.xml": SourceLanguage.JAVA,
    "build.gradle": SourceLanguage.JAVA,
    "cargo.toml": SourceLanguage.RUST,
    "go.mod": SourceLanguage.GO,
}

PROFILE_KEYWORDS = {
    SourceLanguage.PYTHON: frozenset(keyword.kwlist),
    SourceLanguage.JAVASCRIPT: frozenset(
        "await break case catch class const continue debugger default delete do else "
        "export extends finally for function if import in instanceof let new return "
        "static super switch this throw try typeof var void while with yield async of"
        .split()
    ),
    SourceLanguage.JAVA: frozenset(
        "abstract assert boolean break byte case catch char class const continue "
        "default do double else enum extends final finally float for goto if "
        "implements import instanceof int interface long native new package private "
        "protected public return short static strictfp super switch synchronized this "
        "throw throws transient try void volatile while record sealed permits var"
        .split()
    ),
    SourceLanguage.C: frozenset(
        "auto break case char const continue default do double else enum extern float "
        "for goto if inline int long register restrict return short signed sizeof "
        "static struct switch typedef union unsigned void volatile while _Alignas "
        "_Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn _Static_assert "
        "_Thread_local"
        .split()
    ),
    SourceLanguage.CPP: frozenset(
        "alignas alignof and and_eq asm auto bitand bitor bool break case catch char "
        "char8_t char16_t char32_t class compl concept const consteval constexpr "
        "constinit const_cast continue co_await co_return co_yield decltype default "
        "delete do double dynamic_cast else enum explicit export extern false float "
        "for friend goto if inline int long mutable namespace new noexcept not not_eq "
        "nullptr operator or or_eq private protected public register reinterpret_cast "
        "requires return short signed sizeof static static_assert static_cast struct "
        "switch template this thread_local throw true try typedef typeid typename union "
        "unsigned using virtual void volatile wchar_t while xor xor_eq"
        .split()
    ),
}

PROFILE_BUILTINS = {
    SourceLanguage.PYTHON: frozenset(dir(builtins)),
    SourceLanguage.JAVASCRIPT: frozenset(
        "Array BigInt Boolean Date Error Function JSON Map Math Number Object Promise "
        "RegExp Set String Symbol WeakMap WeakSet console decodeURI encodeURI eval "
        "fetch isFinite isNaN parseFloat parseInt queueMicrotask setInterval setTimeout"
        .split()
    ),
}

PROFILE_STANDARD_LIBRARY = {
    SourceLanguage.JAVA: frozenset(
        "Boolean Byte Character Class Double Enum Exception Float Integer Long Math "
        "Object Runtime Short String StringBuilder System Thread Throwable Void"
        .split()
    ),
    SourceLanguage.C: frozenset(
        "calloc fclose feof ferror fflush fgets fopen fprintf fputs fread free fscanf "
        "fseek fwrite getchar gets malloc memcpy memmove memset perror printf putchar "
        "puts realloc scanf snprintf sprintf stderr stdin stdout strcat strchr strcmp "
        "strcpy strlen strncmp strncpy strstr strtol"
        .split()
    ),
    SourceLanguage.CPP: frozenset(
        "array cerr cin clog cout deque endl forward_list list make_pair map move pair "
        "priority_queue queue set shared_ptr sort stack string swap tuple unique_ptr "
        "unordered_map unordered_set vector weak_ptr"
        .split()
    ),
}


@dataclass(frozen=True)
class ExecutableCandidate:
    """An executable name proven to exist in a local PATH directory."""

    name: str
    filename: str
    path: str


@dataclass(frozen=True)
class Correction:
    """A proof-backed replacement for the executable token."""

    original_token: str
    suggested_token: str
    original_arguments: tuple[str, ...]
    score: int
    label: MatchLabel
    reason: str
    evidence: str
    executable_path: str

    @property
    def original_argv(self) -> tuple[str, ...]:
        return (self.original_token, *self.original_arguments)

    @property
    def suggested_argv(self) -> tuple[str, ...]:
        return (self.suggested_token, *self.original_arguments)


@dataclass(frozen=True)
class PathCorrection:
    """A proof-backed replacement for one file or directory argument."""

    token_index: int
    original_token: str
    suggested_token: str
    original_component: str
    suggested_component: str
    category: str
    score: int
    label: MatchLabel
    reason: str
    evidence: str
    resolved_path: str


@dataclass(frozen=True)
class ErrorAnalysis:
    """Structured, sanitized evidence extracted from an error message."""

    category: ErrorCategory
    extracted_token: str | None
    token_index: int | None
    suggestion: str | None
    score: int | None
    label: MatchLabel | None
    reason: str
    evidence_source: str
    resolved_evidence: str | None = None

    @property
    def has_correction(self) -> bool:
        return self.suggestion is not None


@dataclass(frozen=True)
class SafetyFinding:
    """One deterministic rule that contributed to a safety decision."""

    level: RiskLevel
    rule: str
    reason: str
    evidence: str


@dataclass(frozen=True)
class SafetyAssessment:
    """A read-only safety classification for an argument vector."""

    level: RiskLevel
    findings: tuple[SafetyFinding, ...]

    @property
    def blocked(self) -> bool:
        return self.level is RiskLevel.HIGH


@dataclass(frozen=True)
class LanguageContext:
    """A deterministic source-language decision and the evidence behind it."""

    shell: str
    tool: str | None
    language: SourceLanguage
    source_files: tuple[str, ...]
    evidence: tuple[str, ...]
    confidence: DetectionConfidence
    deep_profile: bool


@dataclass(frozen=True)
class CodeSymbol:
    """One identifier proven by a built-in profile or explicit source file."""

    name: str
    kind: SymbolKind
    source: str
    line: int | None = None


@dataclass(frozen=True)
class LanguageUnderstanding:
    """Language context plus symbols obtained without executing source code."""

    context: LanguageContext
    symbols: tuple[CodeSymbol, ...]


@dataclass(frozen=True)
class IdentifierCorrection:
    """A profile- or source-backed correction for one code identifier."""

    original: str
    suggested: str
    kind: SymbolKind
    score: int
    label: MatchLabel
    reason: str
    evidence: str


@dataclass(frozen=True)
class PreflightAnalysis:
    """Structured correction and safety evidence prepared before execution."""

    original_argv: tuple[str, ...]
    suggested_argv: tuple[str, ...]
    resolved_executable: str | None
    executable_correction: Correction | None
    path_corrections: tuple[PathCorrection, ...]
    unresolved_paths: tuple[tuple[int, str], ...]
    original_safety: SafetyAssessment
    suggested_safety: SafetyAssessment
    risk_increased: bool
    language_understanding: LanguageUnderstanding

    @property
    def has_correction(self) -> bool:
        return self.original_argv != self.suggested_argv

    @property
    def correction_count(self) -> int:
        return int(self.executable_correction is not None) + len(self.path_corrections)

    @property
    def match_label(self) -> MatchLabel | None:
        labels = [correction.label for correction in self.path_corrections]
        if self.executable_correction is not None:
            labels.append(self.executable_correction.label)
        if not labels:
            return None
        return MatchLabel.MEDIUM if MatchLabel.MEDIUM in labels else MatchLabel.HIGH


@dataclass(frozen=True)
class ExecutionResult:
    """Captured result of one shell-free child-process attempt."""

    returncode: int
    stdout: str
    stderr: str
    launch_error: str | None = None


def selected_environment(env: dict[str, str] | None) -> dict[str, str]:
    """Return the supplied environment without treating an empty dict as absent."""

    return dict(os.environ) if env is None else dict(env)


def environment_value(env: dict[str, str], name: str) -> str | None:
    """Read an environment value while tolerating Windows key casing."""

    if name in env:
        return env[name]
    folded_name = name.casefold()
    for key, value in env.items():
        if key.casefold() == folded_name:
            return value
    return None


def language_from_name(value: str | SourceLanguage) -> SourceLanguage:
    """Parse an explicit language name or raise a useful validation error."""

    if isinstance(value, SourceLanguage):
        return value
    language = LANGUAGE_ALIASES.get(str(value).strip().casefold())
    if language is None:
        supported = ", ".join(sorted(LANGUAGE_ALIASES))
        raise ValueError(f"unsupported language {value!r}; choose one of: {supported}")
    return language


def normalized_tool_name(token: str) -> str:
    """Return an executable basename suitable for deterministic tool matching."""

    name = Path(str(token).replace("\\", "/")).name.casefold()
    for suffix in WINDOWS_EXECUTABLE_EXTENSIONS:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def detect_shell(
    env: dict[str, str] | None = None,
    *,
    explicit_shell: str | None = None,
) -> str:
    """Describe the outer shell using an override or conservative environment clues."""

    if explicit_shell and explicit_shell.strip():
        return explicit_shell.strip()
    environment = selected_environment(env)
    shell_value = environment_value(environment, "SHELL")
    if shell_value:
        return Path(shell_value.replace("\\", "/")).name or shell_value
    if environment_value(environment, "PSModulePath"):
        return "PowerShell-compatible environment"
    command_shell = environment_value(environment, "COMSPEC")
    if command_shell:
        return Path(command_shell.replace("\\", "/")).name or command_shell
    return "Unknown"


def bounded_source_text(path: Path) -> str | None:
    """Read at most one small, explicit source file; never execute or modify it."""

    try:
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def language_from_shebang(path: Path) -> SourceLanguage | None:
    """Infer a language from the first line of an explicitly named local file."""

    text = bounded_source_text(path)
    if text is None:
        return None
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if not first_line.startswith("#!"):
        return None
    lowered = first_line.casefold()
    if re.search(r"(?:^|[/\s])python(?:\d+(?:\.\d+)*)?(?:\s|$)", lowered):
        return SourceLanguage.PYTHON
    shebang_names = (
        ("node", SourceLanguage.JAVASCRIPT),
        ("deno", SourceLanguage.JAVASCRIPT),
        ("ruby", SourceLanguage.RUBY),
        ("php", SourceLanguage.PHP),
        ("perl", SourceLanguage.PERL),
        ("julia", SourceLanguage.JULIA),
        ("pwsh", SourceLanguage.POWERSHELL),
        ("powershell", SourceLanguage.POWERSHELL),
        ("bash", SourceLanguage.SHELL),
        ("zsh", SourceLanguage.SHELL),
        ("sh", SourceLanguage.SHELL),
    )
    for name, language in shebang_names:
        if re.search(rf"(?:^|[/\s]){re.escape(name)}(?:\s|$)", lowered):
            return language
    return None


def explicit_source_files(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: str | os.PathLike[str] | Path | None = None,
) -> tuple[tuple[str, SourceLanguage | None], ...]:
    """Find bounded source-file arguments without recursively scanning a project."""

    root = Path.cwd() if cwd is None else Path(cwd)
    found: list[tuple[str, SourceLanguage | None]] = []
    seen: set[str] = set()
    for token in argv:
        if len(found) >= MAX_SOURCE_FILES:
            break
        value = str(token)
        if not value or value.startswith("-") or "://" in value:
            continue
        try:
            path = Path(value)
            resolved = path if path.is_absolute() else root / path
            extension_language = SOURCE_EXTENSIONS.get(path.suffix.casefold())
            shebang_language = None
            if extension_language is None and resolved.is_file():
                shebang_language = language_from_shebang(resolved)
            language = extension_language or shebang_language
            if language is None:
                continue
            display_path = str(resolved.resolve(strict=False))
        except (OSError, ValueError):
            continue
        key = display_path.casefold() if os.name == "nt" else display_path
        if key in seen:
            continue
        seen.add(key)
        found.append((display_path, language))
    return tuple(found)


def language_from_error(error_text: str | None) -> SourceLanguage | None:
    """Recognize language-specific compiler/runtime error shapes."""

    if not error_text:
        return None
    sample = error_text[-65536:]
    rules = (
        (r"Traceback \(most recent call last\)|\b(?:NameError|SyntaxError):", SourceLanguage.PYTHON),
        (r"\b(?:javac|java):|\.java:\d+: error:", SourceLanguage.JAVA),
        (r"\b(?:rustc|cargo):|error\[E\d{4}\]", SourceLanguage.RUST),
        (r"\bnode(?:\.exe)?:|\bReferenceError:|\bTypeError:.*\bat ", SourceLanguage.JAVASCRIPT),
        (r"\.cpp:\d+(?::\d+)?: (?:error|warning):", SourceLanguage.CPP),
        (r"\.c:\d+(?::\d+)?: (?:error|warning):", SourceLanguage.C),
    )
    for pattern, language in rules:
        if re.search(pattern, sample, re.IGNORECASE | re.MULTILINE):
            return language
    return None


def language_from_project_marker(root: Path) -> tuple[SourceLanguage | None, str | None]:
    """Inspect only direct project markers in the working directory."""

    matches: list[tuple[SourceLanguage, str]] = []
    for marker, language in PROJECT_MARKERS.items():
        try:
            if (root / marker).is_file():
                matches.append((language, marker))
        except OSError:
            continue
    try:
        for path in sorted(root.glob("*.csproj"), key=lambda item: item.name.casefold()):
            if path.is_file():
                matches.append((SourceLanguage.CSHARP, path.name))
                break
    except OSError:
        pass
    languages = {language for language, _marker in matches}
    if len(languages) != 1:
        return None, None
    language = next(iter(languages))
    marker = sorted(marker for item_language, marker in matches if item_language is language)[0]
    return language, marker


def compatible_language_decision(
    tool_language: SourceLanguage,
    source_language: SourceLanguage,
) -> SourceLanguage | None:
    """Resolve known compiler-family combinations and reject real conflicts."""

    if tool_language is source_language:
        return source_language
    if tool_language is SourceLanguage.JAVASCRIPT and source_language is SourceLanguage.TYPESCRIPT:
        return SourceLanguage.TYPESCRIPT
    if tool_language is SourceLanguage.C and source_language is SourceLanguage.CPP:
        return SourceLanguage.CPP
    if tool_language is SourceLanguage.CPP and source_language is SourceLanguage.C:
        return SourceLanguage.CPP
    return None


def detect_language_context(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: str | os.PathLike[str] | Path | None = None,
    env: dict[str, str] | None = None,
    explicit_language: str | SourceLanguage | None = None,
    explicit_shell: str | None = None,
    error_text: str | None = None,
) -> LanguageContext:
    """Detect source context from bounded local evidence, never from execution."""

    command = tuple(str(token) for token in argv)
    root = Path.cwd() if cwd is None else Path(cwd)
    tool = normalized_tool_name(command[0]) if command else None
    tool_language = TOOL_LANGUAGES.get(tool or "")
    source_records = explicit_source_files(command, cwd=root)
    source_languages = {language for _path, language in source_records if language}
    evidence: list[str] = []

    if tool_language is not None:
        evidence.append(f"tool {tool!r} is associated with {tool_language.value}")
    for path, language in source_records:
        assert language is not None
        suffix = Path(path).suffix or "shebang"
        evidence.append(f"source {Path(path).name!r} indicates {language.value} ({suffix})")

    if explicit_language is not None:
        selected = language_from_name(explicit_language)
        evidence.insert(0, f"explicit language override selected {selected.value}")
        return LanguageContext(
            shell=detect_shell(env, explicit_shell=explicit_shell),
            tool=tool,
            language=selected,
            source_files=tuple(path for path, _language in source_records),
            evidence=tuple(evidence),
            confidence=DetectionConfidence.HIGH,
            deep_profile=selected in DEEP_PROFILE_LANGUAGES,
        )

    source_language = next(iter(source_languages)) if len(source_languages) == 1 else None
    if len(source_languages) > 1:
        names = ", ".join(sorted(language.value for language in source_languages))
        evidence.append(f"conflicting source languages were present: {names}")
        selected = SourceLanguage.UNKNOWN
        confidence = DetectionConfidence.LOW
    elif tool_language is not None and source_language is not None:
        compatible = compatible_language_decision(tool_language, source_language)
        if compatible is None:
            evidence.append("tool and source language evidence conflict")
            selected = SourceLanguage.UNKNOWN
            confidence = DetectionConfidence.LOW
        else:
            selected = compatible
            confidence = DetectionConfidence.HIGH
    elif source_language is not None:
        selected = source_language
        confidence = DetectionConfidence.HIGH
    elif tool_language is not None:
        selected = tool_language
        confidence = DetectionConfidence.MEDIUM
    else:
        error_language = language_from_error(error_text)
        if error_language is not None:
            selected = error_language
            confidence = DetectionConfidence.MEDIUM
            evidence.append(f"recognized {error_language.value} compiler/runtime error shape")
        else:
            marker_language, marker = language_from_project_marker(root)
            if marker_language is not None and marker is not None:
                selected = marker_language
                confidence = DetectionConfidence.LOW
                evidence.append(f"project marker {marker!r} indicates {marker_language.value}")
            else:
                selected = SourceLanguage.UNKNOWN
                confidence = DetectionConfidence.LOW
                evidence.append("no reliable language evidence was found")

    return LanguageContext(
        shell=detect_shell(env, explicit_shell=explicit_shell),
        tool=tool,
        language=selected,
        source_files=tuple(path for path, _language in source_records),
        evidence=tuple(evidence),
        confidence=confidence,
        deep_profile=selected in DEEP_PROFILE_LANGUAGES,
    )


def symbol_line(text: str, offset: int) -> int:
    """Return a stable one-based line number for a regex match offset."""

    return text.count("\n", 0, offset) + 1


def mask_c_like_comments_and_strings(text: str) -> str:
    """Mask C-family comments and strings while preserving offsets and newlines."""

    result = list(text)
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if character == "/" and following == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "line-comment"
                continue
            if character == "/" and following == "*":
                result[index] = result[index + 1] = " "
                index += 2
                state = "block-comment"
                continue
            if character in {'"', "'", "`"}:
                quote = character
                result[index] = " "
                index += 1
                state = "string"
                continue
        elif state == "line-comment":
            if character == "\n":
                state = "code"
            else:
                result[index] = " "
        elif state == "block-comment":
            if character == "*" and following == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "code"
                continue
            if character != "\n":
                result[index] = " "
        else:
            if character == "\\" and following:
                result[index] = " "
                if following != "\n":
                    result[index + 1] = " "
                index += 2
                continue
            if character == quote:
                result[index] = " "
                state = "code"
            elif character != "\n":
                result[index] = " "
        index += 1
    return "".join(result)


def python_source_symbols(text: str, source: str) -> tuple[CodeSymbol, ...]:
    """Extract Python declarations with the standard-library AST only."""

    try:
        tree = ast.parse(text, filename=source)
    except (SyntaxError, ValueError, TypeError):
        return ()
    symbols: list[CodeSymbol] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(CodeSymbol(node.name, SymbolKind.LOCAL_FUNCTION, source, node.lineno))
        elif isinstance(node, ast.ClassDef):
            symbols.append(CodeSymbol(node.name, SymbolKind.LOCAL_CLASS, source, node.lineno))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            symbols.append(CodeSymbol(node.id, SymbolKind.LOCAL_VARIABLE, source, node.lineno))
        elif isinstance(node, ast.arg):
            symbols.append(CodeSymbol(node.arg, SymbolKind.LOCAL_VARIABLE, source, node.lineno))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                symbols.append(CodeSymbol(name, SymbolKind.IMPORT, source, node.lineno))
    return tuple(symbols)


def javascript_source_symbols(text: str, source: str) -> tuple[CodeSymbol, ...]:
    """Extract conservative JavaScript declarations from masked source."""

    masked = mask_c_like_comments_and_strings(text)
    symbols: list[CodeSymbol] = []
    arrow_names: set[str] = set()
    patterns = (
        (r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", SymbolKind.LOCAL_FUNCTION),
        (r"\bclass\s+([A-Za-z_$][\w$]*)\b", SymbolKind.LOCAL_CLASS),
        (
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
            SymbolKind.LOCAL_FUNCTION,
        ),
    )
    for pattern, kind in patterns:
        for match in re.finditer(pattern, masked, re.MULTILINE):
            name = match.group(1)
            if kind is SymbolKind.LOCAL_FUNCTION and "=>" in match.group(0):
                arrow_names.add(name)
            symbols.append(CodeSymbol(name, kind, source, symbol_line(masked, match.start(1))))
    for match in re.finditer(
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b",
        masked,
        re.MULTILINE,
    ):
        name = match.group(1)
        if name not in arrow_names:
            symbols.append(
                CodeSymbol(name, SymbolKind.LOCAL_VARIABLE, source, symbol_line(masked, match.start(1)))
            )
    for match in re.finditer(
        r"\bimport\s+(?:\{\s*)?([A-Za-z_$][\w$]*)",
        masked,
        re.MULTILINE,
    ):
        symbols.append(
            CodeSymbol(match.group(1), SymbolKind.IMPORT, source, symbol_line(masked, match.start(1)))
        )
    return tuple(symbols)


def java_source_symbols(text: str, source: str) -> tuple[CodeSymbol, ...]:
    """Extract Java type and method declarations conservatively."""

    masked = mask_c_like_comments_and_strings(text)
    symbols: list[CodeSymbol] = []
    class_names: set[str] = set()
    for match in re.finditer(
        r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)\b",
        masked,
    ):
        class_names.add(match.group(1))
        symbols.append(
            CodeSymbol(match.group(1), SymbolKind.LOCAL_CLASS, source, symbol_line(masked, match.start(1)))
        )
    method_pattern = re.compile(
        r"(?m)^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default|strictfp)\s+)*"
        r"(?:<[^>]+>\s*)?(?:[A-Za-z_$][\w$]*(?:\s*<[^;{}()]+>)?(?:\[\])?\s+)+"
        r"([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[^{}]+)?(?:\{|;)",
    )
    for match in method_pattern.finditer(masked):
        name = match.group(1)
        symbols.append(CodeSymbol(name, SymbolKind.LOCAL_FUNCTION, source, symbol_line(masked, match.start(1))))
    for class_name in class_names:
        constructor = re.compile(
            rf"(?m)^\s*(?:(?:public|protected|private)\s+)?({re.escape(class_name)})\s*\([^;{{}}]*\)\s*{{"
        )
        for match in constructor.finditer(masked):
            symbols.append(
                CodeSymbol(match.group(1), SymbolKind.LOCAL_FUNCTION, source, symbol_line(masked, match.start(1)))
            )
    return tuple(symbols)


def c_family_source_symbols(
    text: str,
    source: str,
    *,
    cpp: bool,
) -> tuple[CodeSymbol, ...]:
    """Extract C/C++ functions and declared types from masked source."""

    masked = mask_c_like_comments_and_strings(text)
    symbols: list[CodeSymbol] = []
    type_words = "class|struct|enum|union" if cpp else "struct|enum|union"
    for match in re.finditer(rf"\b(?:{type_words})\s+([A-Za-z_]\w*)\b", masked):
        symbols.append(
            CodeSymbol(match.group(1), SymbolKind.LOCAL_CLASS, source, symbol_line(masked, match.start(1)))
        )
    function_pattern = re.compile(
        r"(?m)^\s*(?:(?:static|extern|inline|constexpr|virtual|friend|explicit|consteval)\s+)*"
        r"(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?(?:\s*<[^;{}()]+>)?[\s*&]+)+"
        r"(?:[A-Za-z_]\w*::)*([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:\{|;)",
    )
    controls = {"if", "for", "while", "switch", "catch", "return", "sizeof"}
    for match in function_pattern.finditer(masked):
        name = match.group(1)
        if name not in controls:
            symbols.append(
                CodeSymbol(name, SymbolKind.LOCAL_FUNCTION, source, symbol_line(masked, match.start(1)))
            )
    return tuple(symbols)


def unique_symbols(symbols: list[CodeSymbol] | tuple[CodeSymbol, ...]) -> tuple[CodeSymbol, ...]:
    """Deduplicate and sort symbols so the same evidence always gives the same result."""

    unique: dict[tuple[str, SymbolKind, str, int | None], CodeSymbol] = {}
    for symbol in symbols:
        unique[(symbol.name, symbol.kind, symbol.source, symbol.line)] = symbol
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.name.casefold(),
                item.name,
                item.kind.value,
                item.source.casefold(),
                item.line or 0,
            ),
        )
    )


def inspect_code_symbols(context: LanguageContext) -> tuple[CodeSymbol, ...]:
    """Inspect only explicitly named, bounded source files for supported languages."""

    if not context.deep_profile:
        return ()
    symbols: list[CodeSymbol] = []
    for source in context.source_files[:MAX_SOURCE_FILES]:
        path = Path(source)
        text = bounded_source_text(path)
        if text is None:
            continue
        if context.language is SourceLanguage.PYTHON:
            symbols.extend(python_source_symbols(text, source))
        elif context.language is SourceLanguage.JAVASCRIPT:
            symbols.extend(javascript_source_symbols(text, source))
        elif context.language is SourceLanguage.JAVA:
            symbols.extend(java_source_symbols(text, source))
        elif context.language in {SourceLanguage.C, SourceLanguage.CPP}:
            symbols.extend(
                c_family_source_symbols(
                    text,
                    source,
                    cpp=context.language is SourceLanguage.CPP,
                )
            )
    return unique_symbols(symbols)


def understand_language_context(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: str | os.PathLike[str] | Path | None = None,
    env: dict[str, str] | None = None,
    explicit_language: str | SourceLanguage | None = None,
    explicit_shell: str | None = None,
    error_text: str | None = None,
) -> LanguageUnderstanding:
    """Build a read-only language context and its bounded local symbol table."""

    context = detect_language_context(
        argv,
        cwd=cwd,
        env=env,
        explicit_language=explicit_language,
        explicit_shell=explicit_shell,
        error_text=error_text,
    )
    return LanguageUnderstanding(context, inspect_code_symbols(context))


def classify_identifier(
    identifier: str,
    understanding: LanguageUnderstanding,
) -> CodeSymbol:
    """Classify an exact identifier using language and explicit-source evidence."""

    language = understanding.context.language
    if identifier in PROFILE_KEYWORDS.get(language, frozenset()):
        return CodeSymbol(identifier, SymbolKind.KEYWORD, f"{language.value} keyword profile")
    local_priority = {
        SymbolKind.LOCAL_FUNCTION: 1,
        SymbolKind.LOCAL_CLASS: 2,
        SymbolKind.LOCAL_VARIABLE: 3,
        SymbolKind.IMPORT: 4,
    }
    local_matches = [symbol for symbol in understanding.symbols if symbol.name == identifier]
    if local_matches:
        return sorted(
            local_matches,
            key=lambda item: (local_priority.get(item.kind, 99), item.source.casefold(), item.line or 0),
        )[0]
    if identifier in PROFILE_BUILTINS.get(language, frozenset()):
        return CodeSymbol(identifier, SymbolKind.BUILTIN, f"{language.value} built-in profile")
    if identifier in PROFILE_STANDARD_LIBRARY.get(language, frozenset()):
        return CodeSymbol(
            identifier,
            SymbolKind.STANDARD_LIBRARY,
            f"{language.value} standard-library profile",
        )
    return CodeSymbol(identifier, SymbolKind.UNKNOWN, "no matching profile or local declaration")


def identifier_candidates(understanding: LanguageUnderstanding) -> tuple[CodeSymbol, ...]:
    """Return deterministic correction candidates backed by profiles or source."""

    language = understanding.context.language
    candidates = list(understanding.symbols)
    candidates.extend(
        CodeSymbol(name, SymbolKind.KEYWORD, f"{language.value} keyword profile")
        for name in PROFILE_KEYWORDS.get(language, frozenset())
    )
    candidates.extend(
        CodeSymbol(name, SymbolKind.BUILTIN, f"{language.value} built-in profile")
        for name in PROFILE_BUILTINS.get(language, frozenset())
    )
    candidates.extend(
        CodeSymbol(name, SymbolKind.STANDARD_LIBRARY, f"{language.value} standard-library profile")
        for name in PROFILE_STANDARD_LIBRARY.get(language, frozenset())
    )
    best_by_name: dict[str, CodeSymbol] = {}
    priority = {
        SymbolKind.LOCAL_FUNCTION: 1,
        SymbolKind.LOCAL_CLASS: 2,
        SymbolKind.LOCAL_VARIABLE: 3,
        SymbolKind.IMPORT: 4,
        SymbolKind.KEYWORD: 5,
        SymbolKind.BUILTIN: 6,
        SymbolKind.STANDARD_LIBRARY: 7,
    }
    for candidate in candidates:
        current = best_by_name.get(candidate.name)
        if current is None or priority.get(candidate.kind, 99) < priority.get(current.kind, 99):
            best_by_name[candidate.name] = candidate
    return tuple(sorted(best_by_name.values(), key=lambda item: (item.name.casefold(), item.name)))


def find_identifier_correction(
    identifier: str,
    understanding: LanguageUnderstanding,
) -> IdentifierCorrection | None:
    """Suggest one reliable identifier correction from language/project evidence."""

    if not re.fullmatch(r"[A-Za-z_$][\w$]*", identifier, re.UNICODE):
        return None
    if classify_identifier(identifier, understanding).kind is not SymbolKind.UNKNOWN:
        return None
    ranked: list[tuple[int, int, str, str, CodeSymbol, str]] = []
    kind_priority = {
        SymbolKind.LOCAL_FUNCTION: 1,
        SymbolKind.LOCAL_CLASS: 2,
        SymbolKind.LOCAL_VARIABLE: 3,
        SymbolKind.IMPORT: 4,
        SymbolKind.KEYWORD: 5,
        SymbolKind.BUILTIN: 6,
        SymbolKind.STANDARD_LIBRARY: 7,
    }
    for candidate in identifier_candidates(understanding):
        if candidate.name.casefold() == identifier.casefold():
            continue
        score, reason = similarity_score(identifier, candidate.name)
        if score < MINIMUM_MATCH_SCORE:
            continue
        ranked.append(
            (
                -score,
                kind_priority.get(candidate.kind, 99),
                candidate.name.casefold(),
                candidate.name,
                candidate,
                reason,
            )
        )
    if not ranked:
        return None
    _negative_score, _priority, _folded, _name, candidate, reason = sorted(ranked)[0]
    score = -_negative_score
    line = f" line {candidate.line}" if candidate.line is not None else ""
    source = (
        Path(candidate.source).name
        if candidate.kind
        in {
            SymbolKind.LOCAL_FUNCTION,
            SymbolKind.LOCAL_CLASS,
            SymbolKind.LOCAL_VARIABLE,
            SymbolKind.IMPORT,
        }
        else candidate.source
    )
    evidence = f"{candidate.kind.value} from {source}{line}"
    return IdentifierCorrection(
        original=identifier,
        suggested=candidate.name,
        kind=candidate.kind,
        score=score,
        label=match_label(score),
        reason=reason,
        evidence=evidence,
    )


def identifiers_from_code_error(error_text: str | None) -> tuple[str, ...]:
    """Extract identifier-shaped tokens from recognized compiler/runtime errors."""

    if not error_text:
        return ()
    sample = error_text[-65536:]
    patterns = (
        r"(?:NameError:\s*)?name\s+['\"‘’]([A-Za-z_$][\w$]*)['\"‘’]\s+is not defined",
        r"\b([A-Za-z_$][\w$]*)\s+is not defined\b",
        r"(?:use of )?undeclared identifier\s+['\"‘’]([A-Za-z_$][\w$]*)['\"‘’]",
        r"['\"‘’]([A-Za-z_$][\w$]*)['\"‘’]\s+(?:was not declared|undeclared)",
        r"implicit declaration of function\s+['\"‘’]([A-Za-z_$][\w$]*)['\"‘’]",
        r"cannot find symbol[\s\S]{0,240}?symbol:\s+(?:method|variable|class)\s+([A-Za-z_$][\w$]*)",
    )
    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, sample, re.IGNORECASE | re.MULTILINE):
            identifier = match.group(1)
            key = identifier.casefold()
            if key not in seen:
                seen.add(key)
                found.append(identifier)
    return tuple(found)


def render_code_error_diagnostic(
    understanding: LanguageUnderstanding,
    identifiers: tuple[str, ...],
    *,
    indicator: str = "[i]",
) -> str | None:
    """Render only useful, evidence-backed identifier findings after a failure."""

    findings: list[str] = []
    for identifier in identifiers:
        classification = classify_identifier(identifier, understanding)
        if classification.kind is not SymbolKind.UNKNOWN:
            findings.append(f"  {identifier}: {classification.kind.value}")
            continue
        correction = find_identifier_correction(identifier, understanding)
        if correction is not None:
            findings.extend(
                (
                    f"  {identifier} -> {correction.suggested}  {indicator}",
                    f"  Kind: {correction.kind.value} | Match: "
                    f"{correction.score}/100 ({correction.label.value})",
                    f"  Evidence: {correction.evidence}",
                )
            )
    if not findings:
        return None
    return "\n".join(
        (
            "Code diagnostic:",
            f"  Language: {understanding.context.language.value}",
            *findings,
            "  Source was not modified; apply the change in your editor.",
        )
    )


def windows_extensions(env: dict[str, str]) -> tuple[str, ...]:
    """Return normalized PATHEXT entries in deterministic order."""

    raw = environment_value(env, "PATHEXT")
    if raw is None:
        raw = ";".join(WINDOWS_EXECUTABLE_EXTENSIONS)
    extensions: set[str] = set()
    for value in raw.split(";"):
        value = value.strip().casefold()
        if not value:
            continue
        extensions.add(value if value.startswith(".") else "." + value)
    return tuple(sorted(extensions))


def discover_executables(
    env: dict[str, str] | None = None,
    *,
    windows: bool | None = None,
) -> tuple[ExecutableCandidate, ...]:
    """Discover real executable candidates from PATH.

    Broken, missing and unreadable PATH entries are ignored. PATH order decides
    which duplicate command wins, while directory entries are sorted so the
    result remains deterministic.
    """

    environment = selected_environment(env)
    is_windows = os.name == "nt" if windows is None else windows
    extensions = set(windows_extensions(environment)) if is_windows else set()

    path_value = environment_value(environment, "PATH")
    search_directories = [] if not path_value else path_value.split(os.pathsep)

    discovered: dict[str, ExecutableCandidate] = {}
    visited_directories: set[str] = set()

    for raw_directory in search_directories:
        directory = raw_directory or os.curdir
        directory_key = os.path.normcase(os.path.abspath(directory))
        if directory_key in visited_directories:
            continue
        visited_directories.add(directory_key)

        try:
            with os.scandir(directory) as directory_entries:
                entries = sorted(
                    directory_entries,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except (OSError, ValueError):
            continue

        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if not is_windows and not os.access(entry.path, os.X_OK):
                    continue
            except OSError:
                continue

            filename = entry.name
            if is_windows:
                suffix = Path(filename).suffix.casefold()
                if suffix not in extensions:
                    continue
                command_name = filename[: -len(suffix)]
            else:
                command_name = filename

            if not command_name:
                continue
            key = command_name.casefold() if is_windows else command_name
            discovered.setdefault(
                key,
                ExecutableCandidate(command_name, filename, str(Path(directory) / filename)),
            )

    return tuple(
        sorted(
            discovered.values(),
            key=lambda candidate: (
                candidate.name.casefold(),
                candidate.name,
                candidate.path.casefold(),
            ),
        )
    )


def contains_path_separator(command: str) -> bool:
    """Return whether the token is a path rather than a PATH command name."""

    return "/" in command or "\\" in command


def command_exists(command: str, env: dict[str, str] | None = None) -> str | None:
    """Return the resolved executable path, or None when it is not available."""

    if not command:
        return None

    if contains_path_separator(command):
        candidate = Path(command)
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate)
        return None

    environment = selected_environment(env)
    path_value = environment_value(environment, "PATH")
    if not path_value:
        return None
    return shutil.which(command, path=path_value)


def adjacent_transposition(left: str, right: str) -> bool:
    """Detect exactly one swap of neighboring characters."""

    left = left.casefold()
    right = right.casefold()
    if len(left) != len(right):
        return False
    differences = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
    return (
        len(differences) == 2
        and differences[1] == differences[0] + 1
        and left[differences[0]] == right[differences[1]]
        and left[differences[1]] == right[differences[0]]
    )


def similarity_score(left: str, right: str) -> tuple[int, str]:
    """Calculate a deterministic similarity score and its primary reason."""

    score = round(
        difflib.SequenceMatcher(None, left.casefold(), right.casefold()).ratio() * 100
    )
    reason = "character similarity"
    if adjacent_transposition(left, right):
        score = max(score, 92)
        reason = "one adjacent-character transposition"
    return score, reason


def match_label(score: int) -> MatchLabel:
    """Convert an accepted score to a human-readable label."""

    return MatchLabel.HIGH if score >= HIGH_MATCH_SCORE else MatchLabel.MEDIUM


def command_stem(command: str, env: dict[str, str], windows: bool) -> tuple[str, bool]:
    """Return the name used for matching and whether an extension was supplied."""

    if not windows:
        return command, False
    suffix = Path(command).suffix.casefold()
    if suffix in set(windows_extensions(env)):
        return command[: -len(suffix)], True
    return command, False


def find_correction(
    argv: list[str] | tuple[str, ...],
    env: dict[str, str] | None = None,
    *,
    windows: bool | None = None,
    candidates: tuple[ExecutableCandidate, ...] | None = None,
) -> Correction | None:
    """Return the strongest reliable PATH-backed correction, if one exists."""

    if not argv:
        return None

    original = str(argv[0])
    if contains_path_separator(original):
        return None

    environment = selected_environment(env)
    is_windows = os.name == "nt" if windows is None else windows
    target, supplied_extension = command_stem(original, environment, is_windows)
    available = (
        discover_executables(environment, windows=is_windows)
        if candidates is None
        else candidates
    )

    ranked: list[tuple[int, str, str, ExecutableCandidate, str]] = []
    for candidate in available:
        if candidate.name.casefold() == target.casefold():
            continue
        score, reason = similarity_score(target, candidate.name)
        if score < MINIMUM_MATCH_SCORE:
            continue
        ranked.append(
            (
                -score,
                candidate.name.casefold(),
                candidate.path.casefold(),
                candidate,
                reason,
            )
        )

    if not ranked:
        return None

    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    negative_score, _, _, candidate, reason = ranked[0]
    score = -negative_score
    suggested = candidate.filename if supplied_extension else candidate.name
    return Correction(
        original_token=original,
        suggested_token=suggested,
        original_arguments=tuple(str(item) for item in argv[1:]),
        score=score,
        label=match_label(score),
        reason=reason,
        evidence=f"{candidate.filename!r} was discovered in a local PATH directory",
        executable_path=candidate.path,
    )


def safe_path_exists(path: Path) -> bool:
    """Check path existence without allowing malformed or inaccessible paths to crash."""

    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def safe_is_directory(path: Path) -> bool:
    """Check whether a path is a directory while containing filesystem errors."""

    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def local_path(token: str, cwd: Path) -> Path:
    """Resolve a user token against the inspected working directory."""

    candidate = Path(token)
    return candidate if candidate.is_absolute() else cwd / candidate


def looks_like_path(token: str, cwd: Path) -> bool:
    """Conservatively identify arguments that can safely be treated as paths."""

    if not token or token.startswith("-"):
        return False
    if "://" in token or any(character in token for character in "*?[]"):
        return False

    try:
        candidate = Path(token)
        if candidate.drive and not candidate.is_absolute():
            return False
        return (
            contains_path_separator(token)
            or bool(candidate.suffix)
            or token in (".", "..")
            or safe_path_exists(local_path(token, cwd))
        )
    except (OSError, ValueError):
        return False


def restore_path_style(original: str, rebuilt: Path) -> str:
    """Keep the user's slash style and explicit current-directory prefix."""

    suggested = str(rebuilt)
    if "/" in original and "\\" not in original:
        suggested = suggested.replace("\\", "/")

    if original.startswith("./") and not suggested.startswith("./"):
        suggested = "./" + suggested
    elif original.startswith(".\\") and not suggested.startswith(".\\"):
        suggested = ".\\" + suggested

    if original.endswith("/") and not suggested.endswith("/"):
        suggested += "/"
    elif original.endswith("\\") and not suggested.endswith("\\"):
        suggested += "\\"
    return suggested


def find_path_correction(
    token: str,
    token_index: int,
    *,
    cwd: str | os.PathLike[str] | Path | None = None,
    assume_path: bool = False,
) -> PathCorrection | None:
    """Correct at most one path component and prove the final path exists."""

    working_directory = Path.cwd() if cwd is None else Path(cwd)
    if not assume_path and not looks_like_path(token, working_directory):
        return None

    try:
        original_path = Path(token)
        if safe_path_exists(local_path(token, working_directory)):
            return None

        original_parts = list(original_path.parts)
        if original_path.is_absolute():
            base = Path(original_path.anchor)
            components = original_parts[1:]
        else:
            base = working_directory
            components = original_parts

        if not components:
            return None

        suggested_components: list[str] = []
        corrected: tuple[int, str, str, int, str] | None = None

        for component_index, component in enumerate(components):
            is_last = component_index == len(components) - 1
            exact_path = base / component
            if safe_path_exists(exact_path) and (is_last or safe_is_directory(exact_path)):
                suggested_components.append(component)
                base = exact_path
                continue

            if corrected is not None or not safe_is_directory(base):
                return None

            ranked: list[tuple[int, str, str, str, Path, str]] = []
            try:
                with os.scandir(base) as directory_entries:
                    entries = sorted(
                        directory_entries,
                        key=lambda entry: (entry.name.casefold(), entry.name),
                    )
            except (OSError, ValueError):
                return None

            for entry in entries:
                try:
                    if is_last:
                        if not (entry.is_file() or entry.is_dir()):
                            continue
                    elif not entry.is_dir():
                        continue
                except OSError:
                    continue

                score, reason = similarity_score(component, entry.name)
                if score < MINIMUM_MATCH_SCORE:
                    continue
                ranked.append(
                    (
                        -score,
                        entry.name.casefold(),
                        entry.name,
                        str(entry.path).casefold(),
                        Path(entry.path),
                        reason,
                    )
                )

            if not ranked:
                return None

            ranked.sort(key=lambda item: item[:4])
            negative_score, _, candidate_name, _, candidate_path, reason = ranked[0]
            score = -negative_score
            corrected = (component_index, component, candidate_name, score, reason)
            suggested_components.append(candidate_name)
            base = candidate_path

        if corrected is None or not safe_path_exists(base):
            return None

        component_index, original_component, suggested_component, score, reason = corrected
        if original_path.is_absolute():
            rebuilt = Path(original_path.anchor, *suggested_components)
        else:
            rebuilt = Path(*suggested_components)
        suggested_token = restore_path_style(token, rebuilt)

        if component_index < len(components) - 1:
            category = "directory path component"
        elif safe_is_directory(base):
            category = "directory path"
        else:
            category = "file path"

        try:
            resolved_path = str(base.resolve(strict=True))
        except (OSError, ValueError):
            resolved_path = str(base)

        return PathCorrection(
            token_index=token_index,
            original_token=token,
            suggested_token=suggested_token,
            original_component=original_component,
            suggested_component=suggested_component,
            category=category,
            score=score,
            label=match_label(score),
            reason=reason,
            evidence=f"{suggested_token!r} exists on the local filesystem",
            resolved_path=resolved_path,
        )
    except (OSError, ValueError):
        return None


def inspect_path_arguments(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: str | os.PathLike[str] | Path | None = None,
) -> tuple[tuple[PathCorrection, ...], tuple[tuple[int, str], ...]]:
    """Inspect path-like arguments without changing or executing them."""

    working_directory = Path.cwd() if cwd is None else Path(cwd)
    corrections: list[PathCorrection] = []
    unresolved: list[tuple[int, str]] = []

    for token_index, raw_token in enumerate(argv[1:], start=1):
        token = str(raw_token)
        if not looks_like_path(token, working_directory):
            continue
        if safe_path_exists(local_path(token, working_directory)):
            continue

        correction = find_path_correction(token, token_index, cwd=working_directory)
        if correction is None:
            unresolved.append((token_index, token))
        else:
            corrections.append(correction)

    return tuple(corrections), tuple(unresolved)


def locate_token(argv: list[str] | tuple[str, ...], token: str) -> int | None:
    """Locate an error token in the original argument vector deterministically."""

    target = token.casefold()
    for index, argument in enumerate(argv):
        if str(argument).casefold() == target:
            return index

    try:
        target_name = Path(token).name.casefold()
        for index, argument in enumerate(argv):
            if Path(str(argument)).name.casefold() == target_name:
                return index
    except (OSError, ValueError):
        return None
    return None


def rank_text_candidates(
    original: str,
    candidates: tuple[str, ...] | list[str],
) -> tuple[str, int, str] | None:
    """Select the strongest deterministic candidate from trusted text evidence."""

    ranked: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for raw_candidate in candidates:
        candidate = str(raw_candidate).strip()
        key = candidate.casefold()
        if not candidate or key in seen or key == original.casefold():
            continue
        seen.add(key)
        score, reason = similarity_score(original, candidate)
        if score < MINIMUM_MATCH_SCORE:
            continue
        ranked.append((-score, key, candidate, reason))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    negative_score, _, suggestion, reason = ranked[0]
    return suggestion, -negative_score, reason


def explicit_suggestions(message: str) -> tuple[str, ...]:
    """Extract candidates that the program explicitly offered in stderr."""

    suggestions: list[str] = []
    patterns = (
        r"did you mean(?: one of)?\s*[:?]?\s*['\"]?(?P<value>[-\w.]+)",
        r"most similar commands?\s+(?:is|are)\s*['\"]?(?P<value>[-\w.]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, message, re.IGNORECASE):
            suggestions.append(match.group("value"))
    return tuple(suggestions)


def allowed_choices(message: str) -> tuple[str, ...]:
    """Extract argparse-style choices that were explicitly listed in stderr."""

    match = re.search(r"choose from\s+(?P<choices>[^\r\n)]+)", message, re.IGNORECASE)
    if match is None:
        return ()
    return tuple(re.findall(r"['\"]([^'\"]+)['\"]", match.group("choices")))


def discover_module_candidates(cwd: Path) -> tuple[tuple[str, str], ...]:
    """Discover standard-library and neighboring local Python module names."""

    candidates = {
        name: "Python standard library"
        for name in getattr(sys, "stdlib_module_names", frozenset())
    }
    try:
        with os.scandir(cwd) as directory_entries:
            entries = sorted(
                directory_entries,
                key=lambda entry: (entry.name.casefold(), entry.name),
            )
    except (OSError, ValueError):
        entries = []

    for entry in entries:
        try:
            if entry.is_file() and Path(entry.name).suffix.casefold() == ".py":
                name = Path(entry.name).stem
                candidates.setdefault(name, f"local module {entry.name!r}")
            elif entry.is_dir() and safe_path_exists(Path(entry.path) / "__init__.py"):
                candidates.setdefault(entry.name, f"local package {entry.name!r}")
        except OSError:
            continue

    return tuple(
        sorted(
            candidates.items(),
            key=lambda item: (item[0].casefold(), item[0], item[1]),
        )
    )


def find_module_correction(
    module: str,
    cwd: Path,
) -> tuple[str, int, str, str] | None:
    """Suggest a proven standard-library or neighboring local module."""

    head, separator, remainder = module.partition(".")
    candidate_sources = discover_module_candidates(cwd)
    ranked = rank_text_candidates(head, [name for name, _ in candidate_sources])
    if ranked is None:
        return None

    candidate, score, reason = ranked
    sources = dict(candidate_sources)
    suggestion = candidate + (separator + remainder if separator else "")
    return suggestion, score, reason, sources[candidate]


def sanitized_error_token(token: str | None) -> str | None:
    """Redact obvious credentials and bound extracted-token display length."""

    if token is None:
        return None
    safe = re.sub(
        r"(?i)(password|passwd|token|api[_-]?key|authorization)=([^\s]+)",
        r"\1=<redacted>",
        token,
    )
    safe = re.sub(r"(?i)(://)[^/@:\s]+:[^/@\s]+@", r"\1<redacted>@", safe)
    return safe[:512]


def sanitized_argv(argv: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Redact values associated with common sensitive command-line flags."""

    sensitive_flags = {
        "--api-key",
        "--apikey",
        "--authorization",
        "--auth-token",
        "--password",
        "--passwd",
        "--secret",
        "--token",
    }
    sanitized: list[str] = []
    redact_next = False
    for raw_token in argv:
        token = str(raw_token)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue

        safe = sanitized_error_token(token) or ""
        sanitized.append(safe)
        flag = token.split("=", 1)[0].casefold()
        if flag in sensitive_flags and "=" not in token:
            redact_next = True
    return tuple(sanitized)


def analyze_error_message(
    error_text: str,
    argv: list[str] | tuple[str, ...],
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    cwd: str | os.PathLike[str] | Path | None = None,
) -> ErrorAnalysis:
    """Recognize one useful stderr pattern without executing or trusting its text."""

    message = str(error_text)[:65536]
    working_directory = Path.cwd() if cwd is None else Path(cwd)

    invalid = re.search(
        r"invalid choice:\s*['\"](?P<token>[^'\"]+)['\"]",
        message,
        re.IGNORECASE,
    )
    if invalid is not None:
        token = invalid.group("token")
        choices = (*allowed_choices(message), *explicit_suggestions(message))
        ranked = rank_text_candidates(token, list(choices))
        return ErrorAnalysis(
            category=ErrorCategory.INVALID_CHOICE,
            extracted_token=sanitized_error_token(token),
            token_index=locate_token(argv, token),
            suggestion=None if ranked is None else ranked[0],
            score=None if ranked is None else ranked[1],
            label=None if ranked is None else match_label(ranked[1]),
            reason=(
                "program rejected an invalid choice"
                if ranked is None
                else f"{ranked[2]}; candidate was explicitly listed by the program"
            ),
            evidence_source="recognized stderr invalid-choice rule",
            resolved_evidence=(
                None if ranked is None else "stderr explicitly listed the allowed choice"
            ),
        )

    git_subcommand = re.search(
        r"git:\s*['\"](?P<token>[^'\"]+)['\"]\s+is not a git command",
        message,
        re.IGNORECASE,
    )
    if git_subcommand is not None:
        token = git_subcommand.group("token")
        ranked = rank_text_candidates(token, list(explicit_suggestions(message)))
        return ErrorAnalysis(
            category=ErrorCategory.INVALID_CHOICE,
            extracted_token=sanitized_error_token(token),
            token_index=locate_token(argv, token),
            suggestion=None if ranked is None else ranked[0],
            score=None if ranked is None else ranked[1],
            label=None if ranked is None else match_label(ranked[1]),
            reason=(
                "Git rejected the subcommand"
                if ranked is None
                else f"{ranked[2]}; Git explicitly suggested this subcommand"
            ),
            evidence_source="recognized Git stderr rule",
            resolved_evidence=(
                None if ranked is None else "stderr explicitly suggested the subcommand"
            ),
        )

    module_match = re.search(
        r"(?:ModuleNotFoundError:\s*)?No module named\s+['\"](?P<token>[^'\"]+)['\"]",
        message,
        re.IGNORECASE,
    )
    if module_match is not None:
        token = module_match.group("token")
        correction = find_module_correction(token, working_directory)
        return ErrorAnalysis(
            category=ErrorCategory.MODULE_NOT_FOUND,
            extracted_token=sanitized_error_token(token),
            token_index=locate_token(argv, token),
            suggestion=None if correction is None else correction[0],
            score=None if correction is None else correction[1],
            label=None if correction is None else match_label(correction[1]),
            reason=(
                "Python reported a missing module"
                if correction is None
                else f"{correction[2]}; candidate is a proven module name"
            ),
            evidence_source="recognized Python module-error rule",
            resolved_evidence=None if correction is None else correction[3],
        )

    file_patterns = (
        r"can't open file\s+['\"](?P<token>[^'\"]+)['\"]",
        r"No such file or directory:\s*['\"](?P<token>[^'\"]+)['\"]",
        r"cannot find the file specified:\s*['\"](?P<token>[^'\"]+)['\"]",
    )
    file_match = next(
        (
            match
            for pattern in file_patterns
            if (match := re.search(pattern, message, re.IGNORECASE)) is not None
        ),
        None,
    )
    if file_match is not None:
        token = file_match.group("token")
        token_index = locate_token(argv, token)
        correction = find_path_correction(
            token,
            1 if token_index is None else token_index,
            cwd=working_directory,
            assume_path=True,
        )
        return ErrorAnalysis(
            category=ErrorCategory.FILE_NOT_FOUND,
            extracted_token=sanitized_error_token(token),
            token_index=token_index,
            suggestion=None if correction is None else correction.suggested_token,
            score=None if correction is None else correction.score,
            label=None if correction is None else correction.label,
            reason=(
                "program reported a missing file or directory"
                if correction is None
                else f"{correction.reason}; suggested path exists locally"
            ),
            evidence_source="recognized missing-file stderr rule",
            resolved_evidence=None if correction is None else correction.resolved_path,
        )

    command_patterns = (
        r"(?mi)^\s*(?P<token>[^\s:]+):\s*command not found\s*$",
        r"(?i)['\"](?P<token>[^'\"]+)['\"]\s+is not recognized as an internal or external command",
        r"(?mi)^\s*(?P<token>[^\s:]+)\s*:\s*The term\s+['\"][^'\"]+['\"]\s+is not recognized",
        r"(?i)executable\s+['\"](?P<token>[^'\"]+)['\"]\s+"
        r"(?:was\s+)?not (?:found|recognized)",
    )
    command_match = next(
        (
            match
            for pattern in command_patterns
            if (match := re.search(pattern, message)) is not None
        ),
        None,
    )
    if command_match is not None:
        token = command_match.group("token")
        explicit = rank_text_candidates(token, list(explicit_suggestions(message)))
        executable = None if explicit is not None else find_correction(
            [token],
            env,
            windows=windows,
        )
        if explicit is not None:
            suggestion, score, reason = explicit
            resolved = "stderr explicitly suggested the executable"
        elif executable is not None:
            suggestion = executable.suggested_token
            score = executable.score
            reason = executable.reason
            resolved = executable.executable_path
        else:
            suggestion = None
            score = None
            reason = "shell reported that the executable was not found"
            resolved = None
        return ErrorAnalysis(
            category=ErrorCategory.COMMAND_NOT_FOUND,
            extracted_token=sanitized_error_token(token),
            token_index=locate_token(argv, token),
            suggestion=suggestion,
            score=score,
            label=None if score is None else match_label(score),
            reason=reason,
            evidence_source="recognized command-not-found stderr rule",
            resolved_evidence=resolved,
        )

    unrecognized = re.search(
        r"unrecognized arguments?:\s*(?P<token>\S+)",
        message,
        re.IGNORECASE,
    )
    if unrecognized is not None:
        token = unrecognized.group("token")
        ranked = rank_text_candidates(token, list(explicit_suggestions(message)))
        return ErrorAnalysis(
            category=ErrorCategory.UNRECOGNIZED_ARGUMENT,
            extracted_token=sanitized_error_token(token),
            token_index=locate_token(argv, token),
            suggestion=None if ranked is None else ranked[0],
            score=None if ranked is None else ranked[1],
            label=None if ranked is None else match_label(ranked[1]),
            reason=(
                "program rejected an unrecognized argument"
                if ranked is None
                else f"{ranked[2]}; program explicitly suggested this argument"
            ),
            evidence_source="recognized unrecognized-argument stderr rule",
            resolved_evidence=(
                None if ranked is None else "stderr explicitly suggested the argument"
            ),
        )

    permission = re.search(
        r"(?:PermissionError:.*?)?"
        r"(?:permission denied|access is denied)"
        r"(?:\s*:\s*['\"](?P<token>[^'\"]+)['\"])?",
        message,
        re.IGNORECASE,
    )
    if permission is not None:
        token = permission.groupdict().get("token")
        return ErrorAnalysis(
            category=ErrorCategory.PERMISSION_DENIED,
            extracted_token=sanitized_error_token(token),
            token_index=None if token is None else locate_token(argv, token),
            suggestion=None,
            score=None,
            label=None,
            reason="permission failures are diagnoses, not spelling corrections",
            evidence_source="recognized permission-error stderr rule",
        )

    return ErrorAnalysis(
        category=ErrorCategory.UNKNOWN,
        extracted_token=None,
        token_index=None,
        suggestion=None,
        score=None,
        label=None,
        reason="no supported error pattern matched",
        evidence_source="sanitized stderr pattern analysis",
    )


HIGH_RISK_COMMANDS = {
    "cfdisk": "can modify disk partitions",
    "clc": "clears file content through the PowerShell Clear-Content alias",
    "clear-content": "removes file content",
    "dd": "can overwrite disks or files byte-for-byte",
    "del": "deletes files",
    "diskpart": "can modify disks and partitions",
    "erase": "deletes files",
    "fdisk": "can modify disk partitions",
    "format": "formats a storage volume",
    "gdisk": "can modify disk partitions",
    "mkfs": "creates a filesystem and can erase existing data",
    "parted": "can modify disk partitions",
    "rd": "deletes directories",
    "reboot": "restarts the computer",
    "ri": "deletes items through the PowerShell Remove-Item alias",
    "remove-item": "deletes filesystem or registry items",
    "restart-computer": "restarts the computer",
    "rmdir": "deletes directories",
    "rm": "deletes files or directories",
    "sfdisk": "can modify disk partitions",
    "shred": "overwrites file content",
    "shutdown": "stops or restarts the computer",
    "stop-computer": "stops the computer",
    "truncate": "can discard existing file content",
    "unlink": "deletes a filesystem entry",
    "wipefs": "removes filesystem signatures",
}

READ_ONLY_COMMANDS = {
    "dir",
    "echo",
    "get-childitem",
    "get-date",
    "get-location",
    "get-process",
    "gci",
    "hostname",
    "ls",
    "printf",
    "ps",
    "pwd",
    "systeminfo",
    "tasklist",
    "type",
    "where",
    "which",
    "whoami",
}

CODE_EXECUTORS = {
    "bash",
    "cmd",
    "node",
    "perl",
    "powershell",
    "pwsh",
    "py",
    "python",
    "python3",
    "ruby",
    "sh",
    "wsl",
}

COMMAND_WRAPPERS = {"command", "doas", "env", "nohup", "sudo"}

KNOWN_MUTATING_COMMANDS = {
    "add-content",
    "chmod",
    "chown",
    "copy",
    "cp",
    "install",
    "md",
    "mkdir",
    "move",
    "move-item",
    "mv",
    "new-item",
    "npm",
    "pip",
    "pip3",
    "rename-item",
    "set-content",
    "touch",
}

FORCE_FLAGS = {
    "--force",
    "--force-with-lease",
    "-f",
    "-force",
    "/f",
}

RECURSIVE_FLAGS = {
    "--recursive",
    "-r",
    "-recurse",
    "/s",
}

EXECUTION_BLOCKING_RULES = {
    "append-redirection",
    "compound-shell-syntax",
    "shell-command-text",
}

GIT_READ_ONLY_SUBCOMMANDS = {
    "diff",
    "grep",
    "help",
    "log",
    "ls-files",
    "ls-tree",
    "rev-parse",
    "show",
    "status",
    "version",
}


def normalized_command_name(token: str) -> str:
    """Return a case-insensitive command basename without launcher suffixes."""

    name = token.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in (".exe", ".com", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def git_subcommand(argv: tuple[str, ...]) -> tuple[str | None, tuple[str, ...]]:
    """Find a Git subcommand while skipping common global options."""

    options_with_values = {
        "-c",
        "-C",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    }
    index = 1
    while index < len(argv):
        token = argv[index]
        folded = token.casefold()
        if token in options_with_values:
            index += 2
            continue
        if any(
            folded.startswith(option.casefold() + "=")
            for option in options_with_values
            if option.startswith("--")
        ):
            index += 1
            continue
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            index += 1
            continue
        return folded, tuple(argv[index + 1 :])
    if index < len(argv):
        return argv[index].casefold(), tuple(argv[index + 1 :])
    return None, ()


def is_broad_target(token: str) -> bool:
    """Recognize filesystem roots and home-wide target expressions."""

    value = token.strip().strip("\"'").casefold()
    if "=" in value:
        value = value.rsplit("=", 1)[-1]
    if value in {
        "/",
        "\\",
        "~",
        "~/",
        "~\\",
        "$home",
        "${home}",
        "%homepath%",
        "%userprofile%",
    }:
        return True
    return re.fullmatch(r"[a-z]:[\\/]?", value) is not None


def is_device_target(token: str) -> bool:
    """Recognize raw disk and device paths conservatively."""

    value = token.strip().strip("\"'").casefold().replace("\\", "/")
    if "=" in value:
        value = value.rsplit("=", 1)[-1]
    return bool(
        value.startswith("//./physicaldrive")
        or value.startswith("/dev/sd")
        or value.startswith("/dev/nvme")
        or value.startswith("/dev/mmcblk")
        or value in {"/dev/mem", "/dev/kmem"}
    )


def shell_payload(argv: tuple[str, ...], command: str) -> str | None:
    """Return explicit shell command text when a supported launcher uses it."""

    flags = {
        "bash": {"-c"},
        "cmd": {"/c", "/k"},
        "powershell": {"-c", "-command"},
        "pwsh": {"-c", "-command"},
        "sh": {"-c"},
    }.get(command)
    if flags is None:
        return None
    for index, token in enumerate(argv[1:], start=1):
        if token.casefold() in flags and index + 1 < len(argv):
            return " ".join(argv[index + 1 :]).strip()
    return None


def wrapped_command(argv: tuple[str, ...], command: str) -> tuple[str, ...] | None:
    """Return the command passed through a recognized command wrapper."""

    if command not in COMMAND_WRAPPERS:
        return None
    index = 1
    options_with_values = {
        "env": {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"},
        "sudo": {
            "-C",
            "-D",
            "-g",
            "-h",
            "-p",
            "-R",
            "-T",
            "-u",
            "--chdir",
            "--group",
            "--host",
            "--prompt",
            "--user",
        },
        "doas": {"-C", "-u"},
    }.get(command, set())
    joined_value_prefixes = {
        "env": ("-C", "-S", "-u"),
        "sudo": ("-C", "-D", "-g", "-h", "-p", "-R", "-T", "-u"),
        "doas": ("-C", "-u"),
    }.get(command, ())
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        if token in options_with_values:
            index += 2
            continue
        if any(
            token.startswith(option + "=")
            for option in options_with_values
            if option.startswith("--")
        ) or any(
            token.startswith(prefix) and token != prefix
            for prefix in joined_value_prefixes
        ):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        if command == "env" and "=" in token and not token.startswith("="):
            index += 1
            continue
        break
    return tuple(argv[index:]) if index < len(argv) else None


def nested_destructive_command(payload: str) -> str | None:
    """Recognize a destructive command at a shell command boundary."""

    names = sorted(HIGH_RISK_COMMANDS, key=len, reverse=True)
    alternatives = "|".join(re.escape(name) for name in names)
    match = re.search(
        rf"(?:^|[;&|]\s*)[\"']?\s*({alternatives})(?:\.exe|\.com|\.cmd|\.bat|\.ps1)?(?:\s|$)",
        payload,
        flags=re.IGNORECASE,
    )
    return None if match is None else match.group(1).casefold()


def assess_command_safety(argv: list[str] | tuple[str, ...]) -> SafetyAssessment:
    """Classify a command without executing, resolving or modifying anything."""

    command = tuple(str(token) for token in argv)
    findings: list[SafetyFinding] = []
    seen: set[tuple[str, str]] = set()

    def add(level: RiskLevel, rule: str, reason: str, evidence: str) -> None:
        key = (rule, evidence)
        if key not in seen:
            findings.append(SafetyFinding(level, rule, reason, evidence))
            seen.add(key)

    if not command:
        add(
            RiskLevel.MEDIUM,
            "empty-command",
            "an empty command cannot be proven read-only",
            "no executable token was supplied",
        )
        return SafetyAssessment(RiskLevel.MEDIUM, tuple(findings))

    executable = normalized_command_name(command[0])
    folded_arguments = tuple(token.casefold() for token in command[1:])

    destructive_reason = HIGH_RISK_COMMANDS.get(executable)
    if destructive_reason is not None:
        add(
            RiskLevel.HIGH,
            "destructive-command",
            destructive_reason,
            f"executable token: {executable}",
        )
    elif executable.startswith("mkfs."):
        add(
            RiskLevel.HIGH,
            "filesystem-format",
            "creates a filesystem and can erase existing data",
            f"executable token: {executable}",
        )

    if executable == "git":
        subcommand, git_arguments = git_subcommand(command)
        folded_git_arguments = tuple(token.casefold() for token in git_arguments)
        help_only = "--help" in folded_arguments or "-h" in folded_git_arguments
        if help_only:
            add(
                RiskLevel.LOW,
                "git-help",
                "Git help only displays documentation",
                "Git help flag",
            )
        elif subcommand == "clean":
            add(
                RiskLevel.HIGH,
                "git-clean",
                "git clean can permanently remove untracked files",
                "Git subcommand: clean",
            )
        elif subcommand == "reset" and "--hard" in folded_git_arguments:
            add(
                RiskLevel.HIGH,
                "git-reset-hard",
                "git reset --hard can discard uncommitted work",
                "Git operation: reset --hard",
            )
        elif subcommand == "restore" and git_arguments:
            add(
                RiskLevel.HIGH,
                "git-restore",
                "git restore can discard working-tree changes",
                "Git subcommand: restore",
            )
        elif subcommand == "checkout" and "--" in folded_git_arguments:
            add(
                RiskLevel.HIGH,
                "git-checkout-path",
                "git checkout -- <path> can discard working-tree changes",
                "Git checkout contains the path separator --",
            )
        elif subcommand == "branch" and any(
            token in {"-d", "--delete"} for token in folded_git_arguments
        ):
            add(
                RiskLevel.HIGH,
                "git-branch-delete",
                "deleting a branch can remove an otherwise unreferenced history tip",
                "Git branch delete flag",
            )
        elif subcommand == "push" and any(
            token in {"-f", "--force", "--force-with-lease", "--delete"}
            for token in folded_git_arguments
        ):
            add(
                RiskLevel.HIGH,
                "git-destructive-push",
                "forced or deleting pushes can rewrite remote repository state",
                "Git push force/delete flag",
            )
        elif subcommand == "stash" and any(
            token in {"clear", "drop"} for token in folded_git_arguments
        ):
            add(
                RiskLevel.HIGH,
                "git-stash-delete",
                "dropping or clearing stashes can remove saved work",
                "Git stash drop/clear operation",
            )
        elif subcommand in GIT_READ_ONLY_SUBCOMMANDS:
            add(
                RiskLevel.LOW,
                "git-read-only",
                "the recognized Git subcommand only reads repository information",
                f"Git subcommand: {subcommand}",
            )
        else:
            add(
                RiskLevel.MEDIUM,
                "git-state-change-possible",
                "this Git operation is not proven read-only",
                f"Git subcommand: {subcommand or '(none)'}",
            )

    payload = shell_payload(command, executable)
    if payload is not None:
        nested = nested_destructive_command(payload)
        if nested is not None:
            add(
                RiskLevel.HIGH,
                "nested-destructive-command",
                "explicit shell command text launches a destructive command",
                f"nested executable token: {nested}",
            )
        else:
            add(
                RiskLevel.MEDIUM,
                "shell-command-text",
                "shell command text can perform actions that static token checks "
                "cannot prove safe",
                f"launcher: {executable}",
            )

    wrapped = wrapped_command(command, executable)
    if wrapped is not None:
        add(
            RiskLevel.MEDIUM,
            "command-wrapper",
            "a command wrapper can alter privileges or execution context",
            f"wrapper executable: {executable}",
        )
        wrapped_assessment = assess_command_safety(wrapped)
        for finding in wrapped_assessment.findings:
            add(
                finding.level,
                "wrapped-" + finding.rule,
                finding.reason,
                "wrapped command: " + finding.evidence,
            )

    for token in command[1:]:
        stripped = token.strip()
        if re.fullmatch(r"(?:\d+|&|\*)?>>.*", stripped):
            add(
                RiskLevel.MEDIUM,
                "append-redirection",
                "append redirection changes a file or device destination",
                "append redirection operator: >>",
            )
        elif re.fullmatch(r"(?:\d+|&|\*)?>(?!>).*", stripped):
            add(
                RiskLevel.HIGH,
                "overwrite-redirection",
                "overwrite redirection can replace existing file or device content",
                "overwrite redirection operator: >",
            )
        elif stripped in {"&&", ";", "|", "||"}:
            add(
                RiskLevel.MEDIUM,
                "compound-shell-syntax",
                "compound shell syntax can launch additional commands",
                f"shell operator: {stripped}",
            )

    destructive_executable = (
        executable in HIGH_RISK_COMMANDS or executable.startswith("mkfs.")
    )
    combined_force = destructive_executable and any(
        token.startswith("-")
        and not token.startswith("--")
        and "f" in token[1:].casefold()
        for token in command[1:]
    )
    combined_recursive = destructive_executable and any(
        token.startswith("-")
        and not token.startswith("--")
        and "r" in token[1:].casefold()
        for token in command[1:]
    )
    if executable not in READ_ONLY_COMMANDS and (
        any(token in FORCE_FLAGS for token in folded_arguments) or combined_force
    ):
        add(
            RiskLevel.MEDIUM,
            "force-flag",
            "a force flag may bypass normal safeguards",
            "force option present",
        )
    if executable not in READ_ONLY_COMMANDS and (
        any(token in RECURSIVE_FLAGS for token in folded_arguments)
        or combined_recursive
    ):
        add(
            RiskLevel.MEDIUM,
            "recursive-flag",
            "a recursive flag can widen the affected scope",
            "recursive option present",
        )

    destructive_context = any(
        finding.level is RiskLevel.HIGH
        and finding.rule
        in {
            "destructive-command",
            "filesystem-format",
            "nested-destructive-command",
            "overwrite-redirection",
        }
        for finding in findings
    )
    if destructive_context:
        if any(is_broad_target(token) for token in command[1:]):
            add(
                RiskLevel.HIGH,
                "broad-target",
                "a destructive operation targets a filesystem root or home-wide expression",
                "broad target detected",
            )
        if any(is_device_target(token) for token in command[1:]):
            add(
                RiskLevel.HIGH,
                "device-target",
                "a destructive operation targets a raw disk or device",
                "raw device target detected",
            )

    if not findings:
        if executable in READ_ONLY_COMMANDS:
            add(
                RiskLevel.LOW,
                "recognized-read-only-command",
                "the recognized command only displays information",
                f"executable token: {executable}",
            )
        elif executable in {"py", "python", "python3"} and command[1:] in {
            ("--help",),
            ("--version",),
            ("-h",),
            ("-V",),
            ("-VV",),
        }:
            add(
                RiskLevel.LOW,
                "python-information-only",
                "this Python option only displays interpreter information",
                f"Python option: {command[1]}",
            )
        elif executable in CODE_EXECUTORS:
            add(
                RiskLevel.MEDIUM,
                "code-execution-possible",
                "the interpreter or shell can execute user-supplied code",
                f"executable token: {executable}",
            )
        elif executable in KNOWN_MUTATING_COMMANDS:
            add(
                RiskLevel.MEDIUM,
                "state-change-possible",
                "the recognized command can create or modify local state",
                f"executable token: {executable}",
            )
        else:
            add(
                RiskLevel.MEDIUM,
                "unknown-command",
                "unknown commands are never assumed to be read-only",
                f"executable token: {executable or '(empty)'}",
            )

    level = max(findings, key=lambda finding: RISK_PRIORITY[finding.level]).level
    return SafetyAssessment(level, tuple(findings))


def correction_preserves_risk(
    original_argv: list[str] | tuple[str, ...],
    suggested_argv: list[str] | tuple[str, ...],
) -> tuple[bool, SafetyAssessment, SafetyAssessment]:
    """Reject corrections that increase the conservative safety level."""

    original = assess_command_safety(original_argv)
    suggested = assess_command_safety(suggested_argv)
    allowed = RISK_PRIORITY[suggested.level] <= RISK_PRIORITY[original.level]
    return allowed, original, suggested


def execution_blocking_findings(
    assessment: SafetyAssessment,
) -> tuple[SafetyFinding, ...]:
    """Return findings that forbid execution even when risk is not High."""

    return tuple(
        finding
        for finding in assessment.findings
        if finding.level is RiskLevel.HIGH
        or any(
            finding.rule == rule or finding.rule.endswith("-" + rule)
            for rule in EXECUTION_BLOCKING_RULES
        )
    )


def display_token(token: str) -> str:
    """Render one argument unambiguously for display only."""

    if token and not any(character.isspace() or character in "\"'" for character in token):
        return token
    return json.dumps(token, ensure_ascii=False)


def display_argv(argv: tuple[str, ...] | list[str]) -> str:
    """Render an argument vector for review; the result is never executed."""

    return " ".join(display_token(token) for token in argv)


def render_safety_assessment(
    assessment: SafetyAssessment,
    *,
    footer: str = "Nothing was executed.",
) -> str:
    """Render a safety label and the exact deterministic evidence behind it."""

    decision = (
        "BLOCKED - future execution must not proceed."
        if assessment.blocked
        else "REVIEW - this classification does not execute or approve the command."
    )
    lines = [
        "Safety assessment:",
        f"  Risk: {assessment.level.value}",
        f"  Decision: {decision}",
        "  Evidence:",
    ]
    for finding in assessment.findings:
        lines.extend(
            (
                f"    - [{finding.rule}] {finding.reason}",
                f"      Evidence: {finding.evidence}",
            )
        )
    lines.extend(
        (
            "  Note: a Low label is conservative evidence, not a guarantee.",
            footer,
        )
    )
    return "\n".join(lines)


def render_blocked_correction(
    original: SafetyAssessment,
    suggested: SafetyAssessment,
) -> str:
    """Explain why a risk-increasing correction was withheld."""

    blocking_reasons = tuple(
        finding for finding in suggested.findings if finding.level is suggested.level
    )
    lines = [
        "Correction blocked:",
        "  A proposed correction was withheld because it increased command risk.",
        f"  Original risk: {original.level.value}",
        f"  Proposed risk: {suggested.level.value}",
        "  Blocking evidence:",
    ]
    for finding in blocking_reasons:
        lines.extend(
            (
                f"    - [{finding.rule}] {finding.reason}",
                f"      Evidence: {finding.evidence}",
            )
        )
    lines.extend(("No runnable correction was returned.", "Nothing was executed."))
    return "\n".join(lines)


def corrected_argv(
    argv: list[str] | tuple[str, ...],
    executable_correction: Correction | None,
    path_corrections: tuple[PathCorrection, ...],
) -> tuple[str, ...]:
    """Apply proven token replacements to an in-memory argument vector."""

    suggested = list(str(token) for token in argv)
    if executable_correction is not None:
        suggested[0] = executable_correction.suggested_token
    for correction in path_corrections:
        suggested[correction.token_index] = correction.suggested_token
    return tuple(suggested)


def prepare_preflight(
    argv: list[str] | tuple[str, ...],
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    cwd: str | os.PathLike[str] | Path | None = None,
    explicit_language: str | SourceLanguage | None = None,
    explicit_shell: str | None = None,
    error_text: str | None = None,
) -> PreflightAnalysis:
    """Prepare proof-backed corrections and safety evidence without execution."""

    original = tuple(str(token) for token in argv)
    language_understanding = understand_language_context(
        original,
        cwd=cwd,
        env=env,
        explicit_language=explicit_language,
        explicit_shell=explicit_shell,
        error_text=error_text,
    )
    resolved = command_exists(original[0], env) if original else None
    executable_correction = None
    if original and resolved is None:
        executable_correction = find_correction(original, env, windows=windows)
    path_corrections, unresolved_paths = inspect_path_arguments(original, cwd=cwd)
    suggestion = corrected_argv(original, executable_correction, path_corrections)
    original_safety = assess_command_safety(original)
    suggested_safety = assess_command_safety(suggestion)
    risk_increased = (
        RISK_PRIORITY[suggested_safety.level]
        > RISK_PRIORITY[original_safety.level]
    )
    return PreflightAnalysis(
        original_argv=original,
        suggested_argv=suggestion,
        resolved_executable=resolved,
        executable_correction=executable_correction,
        path_corrections=path_corrections,
        unresolved_paths=unresolved_paths,
        original_safety=original_safety,
        suggested_safety=suggested_safety,
        risk_increased=risk_increased,
        language_understanding=language_understanding,
    )


def render_compact_correction(
    original: tuple[str, ...] | list[str],
    suggested: tuple[str, ...] | list[str],
    *,
    correction_count: int,
    label: MatchLabel,
    safety: SafetyAssessment,
    indicator: str = "[i]",
) -> str:
    """Render the small approval view used before corrected execution."""

    noun = "correction" if correction_count == 1 else "corrections"
    return "\n".join(
        (
            "Original:",
            f"  {display_argv(sanitized_argv(original))}",
            "",
            "Suggestion:",
            f"  {display_argv(sanitized_argv(suggested))}  {indicator}",
            "",
            f"{correction_count} {noun} | Match: {label.value} | "
            f"Risk: {safety.level.value}",
        )
    )


def information_indicator(output: object = sys.stdout) -> str:
    """Use the circled information mark only when the output encoding supports it."""

    encoding = getattr(output, "encoding", None) or "utf-8"
    try:
        "ⓘ".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "[i]"
    return "ⓘ"


def render_token_diff(
    original: tuple[str, ...] | list[str],
    suggested: tuple[str, ...] | list[str],
) -> str:
    """Render changed argument positions without constructing shell text."""

    safe_original = sanitized_argv(original)
    safe_suggested = sanitized_argv(suggested)
    lines = ["Token diff:"]
    width = max(len(safe_original), len(safe_suggested))
    changes = 0
    for index in range(width):
        before = safe_original[index] if index < len(safe_original) else "(missing)"
        after = safe_suggested[index] if index < len(safe_suggested) else "(missing)"
        if before != after:
            lines.append(
                f"  [{index}] {display_token(before)} -> {display_token(after)}"
            )
            changes += 1
    if changes == 0:
        lines.append("  No token changes.")
    return "\n".join(lines)


def render_execution_block(
    assessment: SafetyAssessment,
    blockers: tuple[SafetyFinding, ...],
) -> str:
    """Render exact reasons why the run action refused a command."""

    lines = [
        "Execution blocked:",
        f"  Risk: {assessment.level.value}",
        "  Blocking evidence:",
    ]
    for finding in blockers:
        lines.extend(
            (
                f"    - [{finding.rule}] {finding.reason}",
                f"      Evidence: {finding.evidence}",
            )
        )
    lines.append("Nothing was executed.")
    return "\n".join(lines)


def request_correction_approval(
    original: tuple[str, ...] | list[str],
    suggested: tuple[str, ...] | list[str],
    *,
    correction_count: int,
    label: MatchLabel,
    safety: SafetyAssessment,
    explanation: str,
    input_fn: object | None = None,
    interactive: bool | None = None,
    output: object = sys.stdout,
) -> bool:
    """Require an explicit y while offering explanation and token diff views."""

    print(
        render_compact_correction(
            original,
            suggested,
            correction_count=correction_count,
            label=label,
            safety=safety,
            indicator=information_indicator(output),
        ),
        file=output,
    )
    can_prompt = (
        input_fn is not None or sys.stdin.isatty()
        if interactive is None
        else interactive
    )
    if not can_prompt:
        print("Cancelled: interactive approval is required.", file=output)
        return False

    reader = input if input_fn is None else input_fn
    while True:
        print("", file=output)
        print("[y] Run   [e] Explain   [d] Diff   [Enter/n] Cancel", file=output)
        try:
            choice = reader("Choice: ").strip().casefold()
        except EOFError:
            choice = ""
        if choice in {"y", "yes"}:
            return True
        if choice in {"", "n", "no"}:
            print("Cancelled. Nothing was executed.", file=output)
            return False
        if choice in {"e", "explain"}:
            print("", file=output)
            print(explanation, file=output)
            continue
        if choice in {"d", "diff"}:
            print("", file=output)
            print(render_token_diff(original, suggested), file=output)
            continue
        print("Choose y, e, d, n, or press Enter to cancel.", file=output)


def render_error_analysis(
    analysis: ErrorAnalysis,
    argv: list[str] | tuple[str, ...],
    *,
    footer: str = "Nothing was executed.",
) -> str:
    """Render sanitized error evidence without repeating the raw stderr text."""

    original = sanitized_argv(argv)
    lines = [
        "Error analysis:",
        f"  Category: {analysis.category.value}",
        f"  Extracted token: {analysis.extracted_token or '(none)'}",
        f"  Evidence source: {analysis.evidence_source}",
        f"  Reason: {analysis.reason}",
    ]

    if analysis.has_correction:
        assert analysis.suggestion is not None
        lines.extend(("", "Original:", f"  {display_argv(original)}"))
        if analysis.token_index is not None and analysis.token_index < len(original):
            suggested = list(original)
            suggested[analysis.token_index] = analysis.suggestion
            lines.extend(("", "Suggestion:", f"  {display_argv(suggested)}"))
        else:
            lines.extend(("", "Suggested token:", f"  {display_token(analysis.suggestion)}"))

        lines.extend(("", "Correction evidence:"))
        if analysis.resolved_evidence is not None:
            lines.append(f"  - {analysis.resolved_evidence}")
        if analysis.score is not None and analysis.label is not None:
            lines.append(
                f"  - Match: {analysis.score}/100 ({analysis.label.value})"
            )
    else:
        lines.extend(("", "No reliable correction found."))

    lines.extend(("", "Raw stderr was not repeated.", footer))
    return "\n".join(lines)


def render_command_corrections(
    argv: list[str] | tuple[str, ...],
    executable_correction: Correction | None,
    path_corrections: tuple[PathCorrection, ...],
    *,
    unresolved: tuple[str, ...] = (),
) -> str:
    """Render executable and path corrections as one auditable suggestion."""

    original = tuple(str(token) for token in argv)
    suggested = list(original)
    evidence: list[str] = []

    if executable_correction is not None:
        suggested[0] = executable_correction.suggested_token
        evidence.extend(
            (
                "  - Token 0 (executable): "
                f"{executable_correction.original_token} -> "
                f"{executable_correction.suggested_token}",
                f"    Source: {executable_correction.evidence}",
                f"    Resolved path: {executable_correction.executable_path}",
                f"    Reason: {executable_correction.reason}",
                "    Match: "
                f"{executable_correction.score}/100 "
                f"({executable_correction.label.value})",
            )
        )

    for correction in path_corrections:
        suggested[correction.token_index] = correction.suggested_token
        evidence.extend(
            (
                f"  - Token {correction.token_index} ({correction.category}): "
                f"{correction.original_token} -> {correction.suggested_token}",
                f"    Source: {correction.evidence}",
                f"    Resolved path: {correction.resolved_path}",
                "    Corrected component: "
                f"{correction.original_component} -> {correction.suggested_component}",
                f"    Reason: {correction.reason}",
                f"    Match: {correction.score}/100 ({correction.label.value})",
            )
        )

    display_original = sanitized_argv(original)
    display_suggested = sanitized_argv(suggested)
    lines = [
        "Original:",
        f"  {display_argv(display_original)}",
        "",
        "Suggestion:",
        f"  {display_argv(display_suggested)}",
        "",
        "Evidence:",
        *evidence,
    ]
    if unresolved:
        lines.extend(("", "Unresolved:", *(f"  - {item}" for item in unresolved)))
    lines.extend(("", "Nothing was executed."))
    return "\n".join(lines)


def render_correction(correction: Correction) -> str:
    """Render one executable correction through the combined renderer."""

    return render_command_corrections(correction.original_argv, correction, ())


def check_command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    cwd: str | os.PathLike[str] | Path | None = None,
    error_text: str | None = None,
    output: object = sys.stdout,
) -> int:
    """Inspect executable and path tokens without executing the command."""

    if not argv:
        print("termfix: no command was supplied", file=sys.stderr)
        return EXIT_USAGE

    if error_text is not None:
        if not error_text.strip():
            print("termfix: --error-text cannot be empty", file=sys.stderr)
            return EXIT_USAGE
        analysis = analyze_error_message(
            error_text,
            argv,
            env=env,
            windows=windows,
            cwd=cwd,
        )
        if (
            analysis.has_correction
            and analysis.token_index is not None
            and analysis.suggestion is not None
            and analysis.token_index < len(argv)
        ):
            suggestion = list(argv)
            suggestion[analysis.token_index] = analysis.suggestion
            allowed, original_safety, suggested_safety = correction_preserves_risk(
                argv,
                suggestion,
            )
            if not allowed:
                print(
                    render_blocked_correction(original_safety, suggested_safety),
                    file=output,
                )
                return EXIT_BLOCKED
            print(render_error_analysis(analysis, argv), file=output)
            print("", file=output)
            print(render_safety_assessment(suggested_safety), file=output)
            return EXIT_BLOCKED if suggested_safety.blocked else EXIT_CORRECTION

        print(render_error_analysis(analysis, argv), file=output)
        return EXIT_CORRECTION if analysis.has_correction else EXIT_NOT_FOUND

    original = argv[0]
    resolved = command_exists(original, env)
    executable_correction = None
    if resolved is None:
        executable_correction = find_correction(argv, env, windows=windows)

    path_corrections, unresolved_paths = inspect_path_arguments(argv, cwd=cwd)
    if executable_correction is not None or path_corrections:
        suggestion = corrected_argv(argv, executable_correction, path_corrections)
        allowed, original_safety, suggested_safety = correction_preserves_risk(
            argv,
            suggestion,
        )
        if not allowed:
            print(
                render_blocked_correction(original_safety, suggested_safety),
                file=output,
            )
            return EXIT_BLOCKED

        unresolved_messages = []
        if resolved is None and executable_correction is None:
            unresolved_messages.append(
                f"Executable {original!r} was not found and has no reliable correction."
            )
        unresolved_messages.extend(
            f"Path token {index} {token!r} was not found and has no reliable correction."
            for index, token in unresolved_paths
        )
        print(
            render_command_corrections(
                argv,
                executable_correction,
                path_corrections,
                unresolved=tuple(unresolved_messages),
            ),
            file=output,
        )
        print("", file=output)
        print(render_safety_assessment(suggested_safety), file=output)
        return EXIT_BLOCKED if suggested_safety.blocked else EXIT_CORRECTION

    if resolved is None:
        safety = assess_command_safety(argv)
        print(f"Executable not found: {original}", file=output)
        print("No reliable correction found. Nothing was executed.", file=output)
        print("", file=output)
        print(render_safety_assessment(safety), file=output)
        return EXIT_BLOCKED if safety.blocked else EXIT_NOT_FOUND

    if unresolved_paths:
        safety = assess_command_safety(argv)
        for index, token in unresolved_paths:
            print(f"Path not found at token {index}: {token}", file=output)
        print("No reliable path correction found. Nothing was executed.", file=output)
        print("", file=output)
        print(render_safety_assessment(safety), file=output)
        return EXIT_BLOCKED if safety.blocked else EXIT_NOT_FOUND

    safety = assess_command_safety(argv)
    print(f"Command exists: {original}", file=output)
    print(f"Resolved executable: {resolved}", file=output)
    print("No correction is required. Nothing was executed.", file=output)
    print("", file=output)
    print(render_safety_assessment(safety), file=output)
    return EXIT_BLOCKED if safety.blocked else EXIT_OK


def safety_command(
    argv: list[str],
    *,
    output: object = sys.stdout,
) -> int:
    """Display a read-only safety classification for a supplied command."""

    if not argv:
        print("termfix: no command was supplied", file=sys.stderr)
        return EXIT_USAGE
    assessment = assess_command_safety(argv)
    print("Command:", file=output)
    print(f"  {display_argv(sanitized_argv(argv))}", file=output)
    print("", file=output)
    print(render_safety_assessment(assessment), file=output)
    return EXIT_BLOCKED if assessment.blocked else EXIT_OK


def execute_command(
    argv: tuple[str, ...] | list[str],
    *,
    cwd: str | os.PathLike[str] | Path | None = None,
    env: dict[str, str] | None = None,
    runner: object | None = None,
) -> ExecutionResult:
    """Execute one argument vector with shell=False and capture its output."""

    active_runner = subprocess.run if runner is None else runner
    try:
        completed = active_runner(
            list(argv),
            shell=False,
            cwd=None if cwd is None else str(cwd),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, PermissionError) as error:
        filename = sanitized_error_token(getattr(error, "filename", None))
        detail = filename or "the executable could not be launched"
        message = f"{type(error).__name__}: {detail}"
        return ExecutionResult(1, "", "", message)
    except OSError as error:
        detail = f"OS error {error.errno}" if error.errno is not None else "OS error"
        return ExecutionResult(1, "", "", f"{type(error).__name__}: {detail}")

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    return ExecutionResult(int(completed.returncode), stdout, stderr)


def emit_captured_output(
    result: ExecutionResult,
    *,
    output: object,
    error_output: object,
) -> None:
    """Forward captured child output once, preserving its stream."""

    if result.stdout:
        print(
            result.stdout,
            end="" if result.stdout.endswith("\n") else "\n",
            file=output,
        )
    if result.stderr:
        print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=error_output,
        )


def run_command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    cwd: str | os.PathLike[str] | Path | None = None,
    input_fn: object | None = None,
    interactive: bool | None = None,
    runner: object | None = None,
    output: object = sys.stdout,
    error_output: object = sys.stderr,
) -> int:
    """Preflight, optionally approve, and execute one shell-free command."""

    if not argv:
        print("termfix: no command was supplied", file=error_output)
        return EXIT_USAGE

    preflight = prepare_preflight(
        argv,
        env=env,
        windows=windows,
        cwd=cwd,
    )
    if preflight.risk_increased:
        print(
            render_blocked_correction(
                preflight.original_safety,
                preflight.suggested_safety,
            ),
            file=output,
        )
        return EXIT_BLOCKED

    blockers = execution_blocking_findings(preflight.suggested_safety)
    if blockers:
        print(
            render_execution_block(preflight.suggested_safety, blockers),
            file=output,
        )
        return EXIT_BLOCKED

    if (
        preflight.resolved_executable is None
        and preflight.executable_correction is None
    ):
        print(f"Executable not found: {preflight.original_argv[0]}", file=error_output)
        print("No reliable correction found. Nothing was executed.", file=output)
        return EXIT_NOT_FOUND

    if preflight.unresolved_paths:
        for index, token in preflight.unresolved_paths:
            print(f"Path not found at token {index}: {token}", file=error_output)
        print(
            "Run cancelled because a path could not be proven or corrected. "
            "Nothing was executed.",
            file=output,
        )
        return EXIT_NOT_FOUND

    command_to_run = preflight.suggested_argv
    if preflight.has_correction:
        assert preflight.match_label is not None
        explanation = "\n\n".join(
            (
                render_command_corrections(
                    preflight.original_argv,
                    preflight.executable_correction,
                    preflight.path_corrections,
                ),
                render_safety_assessment(preflight.suggested_safety),
            )
        )
        approved = request_correction_approval(
            preflight.original_argv,
            preflight.suggested_argv,
            correction_count=preflight.correction_count,
            label=preflight.match_label,
            safety=preflight.suggested_safety,
            explanation=explanation,
            input_fn=input_fn,
            interactive=interactive,
            output=output,
        )
        if not approved:
            return EXIT_CANCELLED

    result = execute_command(
        command_to_run,
        cwd=cwd,
        env=env,
        runner=runner,
    )
    emit_captured_output(result, output=output, error_output=error_output)
    if result.launch_error is not None:
        print(f"termfix: launch failed: {result.launch_error}", file=error_output)
        return EXIT_NOT_FOUND
    if result.returncode == 0:
        return EXIT_OK

    print(
        f"TermFix: command failed with exit code {result.returncode}.",
        file=error_output,
    )
    error_sample = result.stderr[-65536:]
    if not error_sample.strip():
        print("No stderr evidence was available for correction.", file=output)
        return EXIT_NOT_FOUND
    code_error_identifiers = identifiers_from_code_error(error_sample)
    if code_error_identifiers:
        failure_understanding = understand_language_context(
            command_to_run,
            cwd=cwd,
            env=env,
            error_text=error_sample,
        )
        code_diagnostic = render_code_error_diagnostic(
            failure_understanding,
            code_error_identifiers,
            indicator=information_indicator(output),
        )
        if code_diagnostic is not None:
            print(code_diagnostic, file=output)
            print("", file=output)
    analysis = analyze_error_message(
        error_sample,
        command_to_run,
        env=env,
        windows=windows,
        cwd=cwd,
    )
    if (
        not analysis.has_correction
        or analysis.token_index is None
        or analysis.suggestion is None
        or analysis.token_index >= len(command_to_run)
    ):
        print(
            f"No reliable post-failure correction found ({analysis.category.value}).",
            file=output,
        )
        return EXIT_NOT_FOUND

    retry_argv = list(command_to_run)
    retry_argv[analysis.token_index] = analysis.suggestion
    allowed, original_safety, retry_safety = correction_preserves_risk(
        command_to_run,
        retry_argv,
    )
    if not allowed:
        print(render_blocked_correction(original_safety, retry_safety), file=output)
        return EXIT_BLOCKED
    retry_blockers = execution_blocking_findings(retry_safety)
    if retry_blockers:
        print(render_execution_block(retry_safety, retry_blockers), file=output)
        return EXIT_BLOCKED

    retry_label = analysis.label or MatchLabel.MEDIUM
    retry_explanation = "\n\n".join(
        (
            render_error_analysis(
                analysis,
                command_to_run,
                footer="No retry was executed during analysis.",
            ),
            render_safety_assessment(
                retry_safety,
                footer="No retry was executed during safety review.",
            ),
        )
    )
    approved = request_correction_approval(
        command_to_run,
        retry_argv,
        correction_count=1,
        label=retry_label,
        safety=retry_safety,
        explanation=retry_explanation,
        input_fn=input_fn,
        interactive=interactive,
        output=output,
    )
    if not approved:
        return EXIT_CANCELLED

    retry_result = execute_command(
        retry_argv,
        cwd=cwd,
        env=env,
        runner=runner,
    )
    emit_captured_output(retry_result, output=output, error_output=error_output)
    if retry_result.launch_error is not None:
        print(
            f"termfix: corrected launch failed: {retry_result.launch_error}",
            file=error_output,
        )
        return EXIT_NOT_FOUND
    if retry_result.returncode == 0:
        return EXIT_OK
    print(
        f"TermFix: corrected command failed with exit code {retry_result.returncode}.",
        file=error_output,
    )
    return EXIT_NOT_FOUND


def strip_separator(command: list[str]) -> list[str]:
    """Remove a literal argparse remainder separator when present."""

    return command[1:] if command and command[0] == "--" else command


def render_language_understanding(
    understanding: LanguageUnderstanding,
    *,
    identifiers: tuple[str, ...] = (),
) -> str:
    """Render an opt-in diagnostic view while keeping ordinary correction UI small."""

    context = understanding.context
    lines = [
        "Language context:",
        f"  Shell: {context.shell}",
        f"  Tool: {context.tool or 'Unknown'}",
        f"  Language: {context.language.value}",
        f"  Confidence: {context.confidence.value}",
        f"  Deep profile: {'yes' if context.deep_profile else 'no'}",
        "  Source files:",
    ]
    if context.source_files:
        lines.extend(f"    - {Path(path).name}" for path in context.source_files)
    else:
        lines.append("    - none explicitly named")
    lines.append("  Evidence:")
    lines.extend(f"    - {item}" for item in context.evidence)

    local_symbols = tuple(
        symbol
        for symbol in understanding.symbols
        if symbol.kind
        in {
            SymbolKind.LOCAL_FUNCTION,
            SymbolKind.LOCAL_CLASS,
            SymbolKind.LOCAL_VARIABLE,
            SymbolKind.IMPORT,
        }
    )
    lines.append("Local declarations:")
    if local_symbols:
        display_priority = {
            SymbolKind.LOCAL_FUNCTION: 1,
            SymbolKind.LOCAL_CLASS: 2,
            SymbolKind.IMPORT: 3,
            SymbolKind.LOCAL_VARIABLE: 4,
        }
        ordered_symbols = tuple(
            sorted(
                local_symbols,
                key=lambda item: (
                    display_priority.get(item.kind, 99),
                    item.name.casefold(),
                    item.name,
                    item.line or 0,
                ),
            )
        )
        display_limit = 12
        for symbol in ordered_symbols[:display_limit]:
            location = f"{Path(symbol.source).name}:{symbol.line}" if symbol.line else Path(symbol.source).name
            lines.append(f"  - {symbol.name}: {symbol.kind.value} ({location})")
        if len(ordered_symbols) > display_limit:
            lines.append(
                f"  - ... {len(ordered_symbols) - display_limit} more available to the backend"
            )
    else:
        lines.append("  - none discovered")

    if identifiers:
        lines.append("Identifier checks:")
        for identifier in identifiers:
            classification = classify_identifier(identifier, understanding)
            if classification.kind is not SymbolKind.UNKNOWN:
                location = (
                    f" line {classification.line}" if classification.line is not None else ""
                )
                source = (
                    Path(classification.source).name
                    if classification.kind
                    in {
                        SymbolKind.LOCAL_FUNCTION,
                        SymbolKind.LOCAL_CLASS,
                        SymbolKind.LOCAL_VARIABLE,
                        SymbolKind.IMPORT,
                    }
                    else classification.source
                )
                lines.append(
                    f"  - {identifier}: {classification.kind.value} "
                    f"({source}{location})"
                )
                continue
            correction = find_identifier_correction(identifier, understanding)
            if correction is None:
                lines.append(f"  - {identifier}: unknown; no reliable correction")
            else:
                lines.append(
                    f"  - {identifier} -> {correction.suggested}: "
                    f"{correction.kind.value}, {correction.score}/100 "
                    f"({correction.label.value})"
                )
                lines.append(f"    Evidence: {correction.evidence}")
    lines.extend(
        (
            "Scope: only explicitly named source files were inspected; no recursive scan.",
            "Nothing was executed or modified.",
        )
    )
    return "\n".join(lines)


def context_command(
    argv: list[str],
    *,
    identifiers: tuple[str, ...] = (),
    explicit_language: str | SourceLanguage | None = None,
    explicit_shell: str | None = None,
    error_text: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | Path | None = None,
    output: object = sys.stdout,
    error_output: object = sys.stderr,
) -> int:
    """Inspect language and identifier context without executing a command."""

    if not argv:
        print("termfix: no command was supplied", file=error_output)
        return EXIT_USAGE
    try:
        understanding = understand_language_context(
            argv,
            cwd=cwd,
            env=env,
            explicit_language=explicit_language,
            explicit_shell=explicit_shell,
            error_text=error_text,
        )
    except ValueError as error:
        print(f"termfix: {error}", file=error_output)
        return EXIT_USAGE
    automatic_identifiers = identifiers_from_code_error(error_text)
    combined_identifiers = tuple(
        dict.fromkeys(
            (
                *(str(item) for item in identifiers),
                *automatic_identifiers,
            )
        )
    )
    print(
        render_language_understanding(
            understanding,
            identifiers=combined_identifiers,
        ),
        file=output,
    )
    return EXIT_OK


def split_windows_interactive_command(line: str) -> tuple[str, ...]:
    """Split a Windows-style line while preserving ordinary path backslashes."""

    tokens: list[str] = []
    token: list[str] = []
    active_quote: str | None = None
    token_started = False
    index = 0
    while index < len(line):
        character = line[index]
        if character.isspace() and active_quote is None:
            if token_started:
                tokens.append("".join(token))
                token = []
                token_started = False
            index += 1
            continue

        if character == "'":
            token_started = True
            if active_quote is None:
                active_quote = "'"
            elif active_quote == "'":
                active_quote = None
            else:
                token.append(character)
            index += 1
            continue

        if character == "\\":
            start = index
            while index < len(line) and line[index] == "\\":
                index += 1
            count = index - start
            if index < len(line) and line[index] == '"' and active_quote != "'":
                token.extend("\\" * (count // 2))
                token_started = True
                if count % 2:
                    token.append('"')
                elif active_quote == '"':
                    active_quote = None
                else:
                    active_quote = '"'
                index += 1
            else:
                token.extend("\\" * count)
                token_started = True
            continue

        if character == '"':
            token_started = True
            if active_quote is None:
                active_quote = '"'
            elif active_quote == '"':
                active_quote = None
            else:
                token.append(character)
            index += 1
            continue

        token.append(character)
        token_started = True
        index += 1

    if active_quote is not None:
        raise ValueError(f"No closing quotation for {active_quote}")
    if token_started:
        tokens.append("".join(token))
    return tuple(tokens)


def split_interactive_command(
    line: str,
    *,
    windows: bool | None = None,
) -> tuple[str, ...]:
    """Split one prompt line into arguments without invoking any system shell."""

    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        return split_windows_interactive_command(line)
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return tuple(lexer)


def render_interactive_help() -> str:
    """Return the small built-in command guide for the interactive prompt."""

    return "\n".join(
        (
            "TermFix interactive commands:",
            "  cd PATH   Change this TermFix session's current directory.",
            "  pwd       Show the current directory.",
            "  clear     Clear the terminal display.",
            "  help      Show this guide.",
            "  exit      Return to the normal terminal.",
            "  Any other input is checked and run through TermFix.",
            "Corrected commands require y; high-risk commands remain blocked.",
        )
    )


def clear_interactive_screen(output: object = sys.stdout) -> None:
    """Clear with a terminal control sequence instead of spawning a shell command."""

    print("\033[2J\033[H", end="", file=output, flush=True)


def change_interactive_directory(
    argv: tuple[str, ...],
    current_directory: Path,
    *,
    input_fn: object,
    output: object,
    error_output: object,
) -> Path:
    """Validate and optionally correct a cd target without changing process cwd."""

    if len(argv) != 2:
        print("Usage: cd PATH", file=error_output)
        return current_directory

    original_target = argv[1]
    target = local_path(original_target, current_directory)
    if safe_is_directory(target):
        try:
            return target.resolve(strict=True)
        except (OSError, ValueError):
            print(f"cd: cannot resolve directory: {original_target}", file=error_output)
            return current_directory

    correction = find_path_correction(
        original_target,
        1,
        cwd=current_directory,
        assume_path=True,
    )
    if correction is None:
        print(f"cd: directory not found: {original_target}", file=error_output)
        return current_directory

    corrected_target = local_path(correction.suggested_token, current_directory)
    if not safe_is_directory(corrected_target):
        print(f"cd: directory not found: {original_target}", file=error_output)
        return current_directory

    approved = request_correction_approval(
        ("cd", original_target),
        ("cd", correction.suggested_token),
        correction_count=1,
        label=correction.label,
        safety=SafetyAssessment(
            RiskLevel.LOW,
            (
                SafetyFinding(
                    RiskLevel.LOW,
                    "interactive-cd",
                    "the built-in changes only the current TermFix session directory",
                    f"local directory: {Path(correction.resolved_path).name}",
                ),
            ),
        ),
        explanation=render_command_corrections(
            ("cd", original_target),
            None,
            (correction,),
        ),
        input_fn=input_fn,
        interactive=True,
        output=output,
    )
    if not approved:
        return current_directory
    try:
        return corrected_target.resolve(strict=True)
    except (OSError, ValueError):
        print(
            f"cd: corrected directory became unavailable: {correction.suggested_token}",
            file=error_output,
        )
        return current_directory


def interactive_shell(
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    cwd: str | os.PathLike[str] | Path | None = None,
    input_fn: object | None = None,
    runner: object | None = None,
    output: object = sys.stdout,
    error_output: object = sys.stderr,
) -> int:
    """Run a continuous safe prompt while keeping all session state in memory."""

    reader = input if input_fn is None else input_fn
    initial_directory = Path.cwd() if cwd is None else Path(cwd)
    try:
        current_directory = initial_directory.resolve(strict=True)
    except (OSError, ValueError):
        print(f"termfix: starting directory is unavailable: {initial_directory}", file=error_output)
        return EXIT_USAGE
    if not safe_is_directory(current_directory):
        print(f"termfix: starting path is not a directory: {initial_directory}", file=error_output)
        return EXIT_USAGE

    print(f"TermFix interactive terminal {VERSION}", file=output)
    print("Type help for commands or exit to return to the normal terminal.", file=output)
    print("Commands are parsed as arguments; no system shell is opened.", file=output)

    while True:
        try:
            print("termfix> ", end="", file=output, flush=True)
            line = reader("")
        except EOFError:
            print("", file=output)
            print("Leaving TermFix.", file=output)
            return EXIT_OK
        except KeyboardInterrupt:
            print("", file=output)
            print("Input cancelled. Type exit to leave TermFix.", file=output)
            continue

        if not isinstance(line, str):
            print("Input error: the prompt reader did not return text.", file=error_output)
            continue
        if not line.strip():
            continue
        try:
            command = split_interactive_command(line, windows=windows)
        except ValueError as error:
            print(f"Input error: {error}", file=error_output)
            continue
        if not command:
            continue

        builtin = command[0].casefold()
        if builtin == "exit":
            if len(command) != 1:
                print("Usage: exit", file=error_output)
                continue
            print("Leaving TermFix.", file=output)
            return EXIT_OK
        if builtin == "help":
            if len(command) != 1:
                print("Usage: help", file=error_output)
            else:
                print(render_interactive_help(), file=output)
            continue
        if builtin == "pwd":
            if len(command) != 1:
                print("Usage: pwd", file=error_output)
            else:
                print(str(current_directory), file=output)
            continue
        if builtin == "clear":
            if len(command) != 1:
                print("Usage: clear", file=error_output)
            else:
                clear_interactive_screen(output)
            continue
        if builtin == "cd":
            current_directory = change_interactive_directory(
                command,
                current_directory,
                input_fn=reader,
                output=output,
                error_output=error_output,
            )
            continue

        try:
            run_command(
                list(command),
                env=env,
                windows=windows,
                cwd=current_directory,
                input_fn=reader,
                interactive=True,
                runner=runner,
                output=output,
                error_output=error_output,
            )
        except KeyboardInterrupt:
            print("", file=output)
            print("Command interrupted. TermFix is still open.", file=output)


def make_parser() -> argparse.ArgumentParser:
    """Create the TermFix command-line parser."""

    parser = argparse.ArgumentParser(
        prog="termfix.py",
        description=(
            "Proof-backed command correction, deterministic safety, and "
            "guarded shell-free execution using only Python's standard library."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    actions = parser.add_subparsers(dest="action", required=True)

    check = actions.add_parser("check", help="inspect a command without executing it")
    check.add_argument(
        "--error-text",
        metavar="TEXT",
        help="analyze supplied stderr text without executing the command",
    )
    check.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    safety = actions.add_parser(
        "safety",
        help="classify command risk without executing it",
    )
    safety.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    run = actions.add_parser(
        "run",
        help="preflight and safely run a command",
    )
    run.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    context = actions.add_parser(
        "context",
        help="inspect language and local symbols without executing anything",
    )
    context.add_argument(
        "--language",
        help="explicit language override, such as python, js, c, cpp, or java",
    )
    context.add_argument(
        "--shell",
        help="explicit outer-shell label for diagnostics",
    )
    context.add_argument(
        "--error-text",
        metavar="TEXT",
        help="optional compiler/runtime error evidence",
    )
    context.add_argument(
        "--identifier",
        action="append",
        default=[],
        metavar="NAME",
        help="classify or correct an identifier; may be repeated",
    )
    context.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    shell = actions.add_parser(
        "shell",
        help="open the continuous interactive TermFix terminal",
    )
    shell.add_argument(
        "--cwd",
        metavar="DIRECTORY",
        help="start in this directory instead of the current directory",
    )

    actions.add_parser("self-test", help="run the embedded standard-library tests")
    return parser


def run_tests() -> int:
    """Run the embedded test suite without third-party tooling."""

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TermFixTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return EXIT_OK if result.wasSuccessful() else EXIT_NOT_FOUND


def cli(argv: list[str] | None = None) -> int:
    """Run the TermFix CLI."""

    args = make_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.action == "self-test":
        return run_tests()
    if args.action == "check":
        return check_command(
            strip_separator(args.command),
            error_text=args.error_text,
        )
    if args.action == "safety":
        return safety_command(strip_separator(args.command))
    if args.action == "run":
        return run_command(strip_separator(args.command))
    if args.action == "context":
        return context_command(
            strip_separator(args.command),
            identifiers=tuple(args.identifier),
            explicit_language=args.language,
            explicit_shell=args.shell,
            error_text=args.error_text,
        )
    if args.action == "shell":
        return interactive_shell(cwd=args.cwd)
    return EXIT_USAGE


class TermFixTests(unittest.TestCase):
    """Acceptance and unit tests for proof-backed local correction."""

    def test_adjacent_transposition(self) -> None:
        self.assertTrue(adjacent_transposition("pyhton", "python"))
        self.assertFalse(adjacent_transposition("python", "python"))
        self.assertFalse(adjacent_transposition("pythn", "python"))

    def test_transposition_receives_high_score(self) -> None:
        score, reason = similarity_score("pyhton", "python")
        self.assertEqual(score, 92)
        self.assertEqual(reason, "one adjacent-character transposition")

    def test_windows_extensions_and_case_insensitive_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Python.EXE").write_text("", encoding="utf-8")
            (root / "python.cmd").write_text("", encoding="utf-8")
            (root / "notes.txt").write_text("", encoding="utf-8")
            env = {"PATH": str(root), "PATHEXT": ".EXE;.CMD;.BAT;.COM"}
            candidates = discover_executables(env, windows=True)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name.casefold(), "python")

    def test_all_required_windows_extensions_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = set()
            for extension in (".EXE", ".CMD", ".BAT", ".COM"):
                name = "tool" + extension[1:].casefold()
                expected.add(name)
                (root / f"{name}{extension}").write_text("", encoding="utf-8")
            env = {"PATH": str(root), "PATHEXT": ".EXE;.CMD;.BAT;.COM"}
            candidates = discover_executables(env, windows=True)

        self.assertEqual({candidate.name for candidate in candidates}, expected)

    def test_missing_and_duplicate_path_directories_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "hello.EXE").write_text("", encoding="utf-8")
            path = os.pathsep.join((str(root / "missing"), str(root), str(root)))
            candidates = discover_executables(
                {"PATH": path, "PATHEXT": ".EXE"},
                windows=True,
            )

        self.assertEqual([candidate.name for candidate in candidates], ["hello"])

    def test_empty_environment_does_not_use_real_path(self) -> None:
        self.assertIsNone(command_exists("python", {}))
        self.assertEqual(discover_executables({}, windows=True), ())

    def test_windows_environment_key_casing_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Python.EXE").write_text("", encoding="utf-8")
            env = {"Path": str(root), "PathExt": ".EXE"}
            candidates = discover_executables(env, windows=True)

        self.assertEqual([candidate.name for candidate in candidates], ["Python"])

    def test_path_like_executable_is_not_corrected_from_path(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        self.assertIsNone(find_correction(["./pyhton"], {}, windows=True, candidates=(candidate,)))

    def test_pyhton_suggests_python_and_preserves_arguments(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        correction = find_correction(
            ["pyhton", "app.py", "--verbose"],
            {},
            windows=True,
            candidates=(candidate,),
        )

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_argv, ("python", "app.py", "--verbose"))
        self.assertEqual(correction.label, MatchLabel.HIGH)
        self.assertIn("PATH", correction.evidence)

    def test_windows_matching_is_case_insensitive(self) -> None:
        candidate = ExecutableCandidate("Python", "Python.EXE", "C:/Python/Python.EXE")
        correction = find_correction(
            ["PYHTON"],
            {},
            windows=True,
            candidates=(candidate,),
        )

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "Python")

    def test_extension_is_preserved_using_real_candidate_filename(self) -> None:
        candidate = ExecutableCandidate("python", "python.EXE", "C:/Python/python.EXE")
        correction = find_correction(
            ["pyhton.exe", "app.py"],
            {"PATHEXT": ".EXE"},
            windows=True,
            candidates=(candidate,),
        )

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "python.EXE")

    def test_unrelated_name_is_rejected(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        correction = find_correction(
            ["completely-unrelated"],
            {},
            windows=True,
            candidates=(candidate,),
        )
        self.assertIsNone(correction)

    def test_candidate_ordering_is_deterministic(self) -> None:
        candidates = (
            ExecutableCandidate("cutx", "cutx.exe", "C:/B/cutx.exe"),
            ExecutableCandidate("catx", "catx.exe", "C:/A/catx.exe"),
        )
        first = find_correction(["cotx"], {}, windows=True, candidates=candidates)
        second = find_correction(["cotx"], {}, windows=True, candidates=tuple(reversed(candidates)))

        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.suggested_token, "catx")

    def test_empty_command_returns_usage(self) -> None:
        self.assertEqual(check_command([], output=self._output()), EXIT_USAGE)

    def test_existing_command_returns_zero(self) -> None:
        output = self._output()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app.py").write_text("", encoding="utf-8")
            with mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Python/python.exe",
            ):
                code = check_command(["python", "app.py"], cwd=root, output=output)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("No correction is required", output.getvalue())

    def test_correction_returns_three_and_does_not_change_arguments(self) -> None:
        output = self._output()
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = check_command(["pyhton", "app.py"], env={}, windows=True, output=output)
        self.assertEqual(code, EXIT_CORRECTION)
        self.assertIn("python app.py", output.getvalue())
        self.assertIn("Nothing was executed", output.getvalue())

    def test_no_match_returns_one(self) -> None:
        output = self._output()
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=()),
        ):
            code = check_command(["unknown-command"], env={}, windows=True, output=output)
        self.assertEqual(code, EXIT_NOT_FOUND)
        self.assertIn("No reliable correction found", output.getvalue())

    def test_misspelled_file_is_corrected_from_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "main.py"
            expected.write_text("print('safe')", encoding="utf-8")
            correction = find_path_correction("mian.py", 1, cwd=root)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "main.py")
        self.assertEqual(correction.category, "file path")
        self.assertEqual(correction.score, 92)
        self.assertEqual(Path(correction.resolved_path), expected)

    def test_nested_file_is_corrected_and_forward_slashes_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "src"
            source.mkdir()
            (source / "main.py").write_text("", encoding="utf-8")
            correction = find_path_correction("src/mian.py", 1, cwd=root)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "src/main.py")

    def test_misspelled_directory_component_is_corrected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "src"
            source.mkdir()
            (source / "main.py").write_text("", encoding="utf-8")
            correction = find_path_correction("scr/main.py", 1, cwd=root)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "src/main.py")
        self.assertEqual(correction.category, "directory path component")
        self.assertEqual(correction.original_component, "scr")
        self.assertEqual(correction.suggested_component, "src")

    def test_directory_argument_is_corrected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs").mkdir()
            correction = find_path_correction("docz/", 1, cwd=root)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "docs/")
        self.assertEqual(correction.category, "directory path")

    def test_file_extension_typo_is_corrected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.py").write_text("", encoding="utf-8")
            correction = find_path_correction("main.pye", 1, cwd=root)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested_token, "main.py")

    def test_unrelated_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.py").write_text("", encoding="utf-8")
            correction = find_path_correction(
                "completely-unrelated.txt",
                1,
                cwd=root,
            )

        self.assertIsNone(correction)

    def test_two_misspelled_path_components_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "src"
            source.mkdir()
            (source / "main.py").write_text("", encoding="utf-8")
            correction = find_path_correction("scr/mian.py", 1, cwd=root)

        self.assertIsNone(correction)

    def test_path_candidate_ordering_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "cutx.py").write_text("", encoding="utf-8")
            (root / "catx.py").write_text("", encoding="utf-8")
            first = find_path_correction("cotx.py", 1, cwd=root)
            second = find_path_correction("cotx.py", 1, cwd=root)

        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.suggested_token, "catx.py")

    def test_options_urls_and_globs_are_not_treated_as_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for token in ("--config=main.py", "https://example.com/a.py", "*.py"):
                self.assertFalse(looks_like_path(token, root))

    def test_missing_path_directory_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            correction = find_path_correction("missing/mian.py", 1, cwd=root)
        self.assertIsNone(correction)

    def test_path_correction_preserves_all_other_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.py").write_text("", encoding="utf-8")
            argv = ["python", "mian.py", "--verbose", "value with spaces"]
            corrections, unresolved = inspect_path_arguments(argv, cwd=root)
            rendered = render_command_corrections(argv, None, corrections)

        self.assertEqual(unresolved, ())
        self.assertEqual(len(corrections), 1)
        self.assertIn('python main.py --verbose "value with spaces"', rendered)

    def test_path_correction_returns_three_and_proves_resolved_path(self) -> None:
        output = self._output()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "main.py"
            expected.write_text("unchanged", encoding="utf-8")
            with mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Python/python.exe",
            ):
                code = check_command(["python", "mian.py"], cwd=root, output=output)
            content_after_check = expected.read_text(encoding="utf-8")

        self.assertEqual(code, EXIT_CORRECTION)
        self.assertEqual(content_after_check, "unchanged")
        self.assertIn("python main.py", output.getvalue())
        self.assertIn(str(expected), output.getvalue())
        self.assertIn("Nothing was executed", output.getvalue())

    def test_existing_path_does_not_produce_a_correction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.py").write_text("", encoding="utf-8")
            corrections, unresolved = inspect_path_arguments(
                ["python", "main.py"],
                cwd=root,
            )

        self.assertEqual(corrections, ())
        self.assertEqual(unresolved, ())

    def test_missing_path_without_match_returns_one(self) -> None:
        output = self._output()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Python/python.exe",
            ):
                code = check_command(
                    ["python", "unrelated-file.py"],
                    cwd=root,
                    output=output,
                )

        self.assertEqual(code, EXIT_NOT_FOUND)
        self.assertIn("No reliable path correction found", output.getvalue())

    def test_argparse_invalid_choice_uses_explicit_allowed_choice(self) -> None:
        error = (
            "argument action: invalid choice: 'instal' "
            "(choose from 'install', 'remove', 'update')"
        )
        analysis = analyze_error_message(error, ["tool", "instal"])

        self.assertEqual(analysis.category, ErrorCategory.INVALID_CHOICE)
        self.assertEqual(analysis.extracted_token, "instal")
        self.assertEqual(analysis.token_index, 1)
        self.assertEqual(analysis.suggestion, "install")
        self.assertEqual(analysis.label, MatchLabel.HIGH)

    def test_invalid_choice_rejects_weak_allowed_choices(self) -> None:
        error = "invalid choice: 'unrelated' (choose from 'start', 'stop')"
        analysis = analyze_error_message(error, ["tool", "unrelated"])

        self.assertEqual(analysis.category, ErrorCategory.INVALID_CHOICE)
        self.assertFalse(analysis.has_correction)

    def test_git_explicit_subcommand_suggestion_is_recognized(self) -> None:
        error = (
            "git: 'statsu' is not a git command. See 'git --help'.\n\n"
            "The most similar command is\n\tstatus"
        )
        analysis = analyze_error_message(error, ["git", "statsu"])

        self.assertEqual(analysis.category, ErrorCategory.INVALID_CHOICE)
        self.assertEqual(analysis.suggestion, "status")
        self.assertEqual(analysis.token_index, 1)

    def test_bash_command_not_found_uses_real_path_candidate(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        with mock.patch(
            __name__ + ".discover_executables",
            return_value=(candidate,),
        ):
            analysis = analyze_error_message(
                "pyhton: command not found",
                ["pyhton", "app.py"],
                env={},
                windows=True,
            )

        self.assertEqual(analysis.category, ErrorCategory.COMMAND_NOT_FOUND)
        self.assertEqual(analysis.suggestion, "python")
        self.assertEqual(analysis.resolved_evidence, "C:/Python/python.exe")

    def test_powershell_not_recognized_is_categorized(self) -> None:
        error = (
            "pyhton : The term 'pyhton' is not recognized as the name of a "
            "cmdlet, function, script file, or operable program."
        )
        analysis = analyze_error_message(error, ["pyhton"], env={})

        self.assertEqual(analysis.category, ErrorCategory.COMMAND_NOT_FOUND)
        self.assertEqual(analysis.extracted_token, "pyhton")

    def test_cmd_not_recognized_is_categorized(self) -> None:
        error = (
            "'pyhton' is not recognized as an internal or external command, "
            "operable program or batch file."
        )
        analysis = analyze_error_message(error, ["pyhton"], env={})

        self.assertEqual(analysis.category, ErrorCategory.COMMAND_NOT_FOUND)

    def test_generic_executable_not_recognized_is_categorized(self) -> None:
        analysis = analyze_error_message(
            "executable 'pyhton' was not recognized",
            ["pyhton"],
            env={},
        )

        self.assertEqual(analysis.category, ErrorCategory.COMMAND_NOT_FOUND)

    def test_python_cannot_open_file_uses_local_path_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "main.py"
            expected.write_text("unchanged", encoding="utf-8")
            analysis = analyze_error_message(
                "python: can't open file 'mian.py': [Errno 2] No such file or directory",
                ["python", "mian.py"],
                cwd=root,
            )
            content_after = expected.read_text(encoding="utf-8")

        self.assertEqual(analysis.category, ErrorCategory.FILE_NOT_FOUND)
        self.assertEqual(analysis.suggestion, "main.py")
        self.assertEqual(Path(analysis.resolved_evidence or ""), expected)
        self.assertEqual(content_after, "unchanged")

    def test_file_not_found_error_pattern_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "settings.json").write_text("{}", encoding="utf-8")
            analysis = analyze_error_message(
                "FileNotFoundError: [Errno 2] No such file or directory: 'setings.json'",
                ["python", "setings.json"],
                cwd=root,
            )

        self.assertEqual(analysis.category, ErrorCategory.FILE_NOT_FOUND)
        self.assertEqual(analysis.suggestion, "settings.json")

    def test_missing_file_without_neighbor_has_no_correction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            analysis = analyze_error_message(
                "No such file or directory: 'unrelated-file.xyz'",
                ["tool", "unrelated-file.xyz"],
                cwd=folder,
            )

        self.assertEqual(analysis.category, ErrorCategory.FILE_NOT_FOUND)
        self.assertFalse(analysis.has_correction)

    def test_module_not_found_uses_standard_library_names(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            analysis = analyze_error_message(
                "ModuleNotFoundError: No module named 'josn'",
                ["python", "-m", "josn"],
                cwd=folder,
            )

        self.assertEqual(analysis.category, ErrorCategory.MODULE_NOT_FOUND)
        self.assertEqual(analysis.suggestion, "json")
        self.assertEqual(analysis.resolved_evidence, "Python standard library")

    def test_module_not_found_uses_neighboring_local_module(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "helpers.py").write_text("", encoding="utf-8")
            analysis = analyze_error_message(
                "No module named 'hlepers'",
                ["python", "-m", "hlepers"],
                cwd=root,
            )

        self.assertEqual(analysis.category, ErrorCategory.MODULE_NOT_FOUND)
        self.assertEqual(analysis.suggestion, "helpers")
        self.assertIn("local module", analysis.resolved_evidence or "")

    def test_unrecognized_argument_without_evidence_is_not_guessed(self) -> None:
        analysis = analyze_error_message(
            "tool: error: unrecognized arguments: --verison",
            ["tool", "--verison"],
        )

        self.assertEqual(analysis.category, ErrorCategory.UNRECOGNIZED_ARGUMENT)
        self.assertFalse(analysis.has_correction)

    def test_unrecognized_argument_accepts_explicit_suggestion(self) -> None:
        analysis = analyze_error_message(
            "unrecognized argument: --verison. Did you mean --version?",
            ["tool", "--verison"],
        )

        self.assertEqual(analysis.category, ErrorCategory.UNRECOGNIZED_ARGUMENT)
        self.assertEqual(analysis.suggestion, "--version")

    def test_permission_denied_is_diagnosed_without_spelling_guess(self) -> None:
        analysis = analyze_error_message(
            "PermissionError: [Errno 13] Permission denied: 'private.txt'",
            ["tool", "private.txt"],
        )

        self.assertEqual(analysis.category, ErrorCategory.PERMISSION_DENIED)
        self.assertEqual(analysis.extracted_token, "private.txt")
        self.assertFalse(analysis.has_correction)
        self.assertIn("diagnoses", analysis.reason)

    def test_unknown_error_is_returned_without_guessing(self) -> None:
        analysis = analyze_error_message("something unusual happened", ["tool"])

        self.assertEqual(analysis.category, ErrorCategory.UNKNOWN)
        self.assertFalse(analysis.has_correction)

    def test_rendered_error_does_not_repeat_raw_secret(self) -> None:
        raw_error = "unrecognized arguments: --token=super-secret-value"
        analysis = analyze_error_message(
            raw_error,
            ["tool", "--token=super-secret-value"],
        )
        rendered = render_error_analysis(
            analysis,
            ["tool", "--token=super-secret-value"],
        )

        self.assertNotIn("super-secret-value", analysis.extracted_token or "")
        self.assertNotIn(raw_error, rendered)
        self.assertIn("<redacted>", rendered)
        self.assertIn("Raw stderr was not repeated", rendered)

    def test_corrected_error_output_redacts_separate_sensitive_value(self) -> None:
        error = "invalid choice: 'instal' (choose from 'install', 'remove')"
        argv = ["tool", "instal", "--token", "super-secret-value"]
        analysis = analyze_error_message(error, argv)
        rendered = render_error_analysis(analysis, argv)

        self.assertTrue(analysis.has_correction)
        self.assertNotIn("super-secret-value", rendered)
        self.assertIn("--token <redacted>", rendered)

    def test_error_check_returns_three_without_preflight_execution(self) -> None:
        output = self._output()
        error = "invalid choice: 'instal' (choose from 'install', 'remove')"
        with mock.patch(
            __name__ + ".command_exists",
            side_effect=AssertionError("preflight must not run"),
        ):
            code = check_command(
                ["tool", "instal"],
                error_text=error,
                output=output,
            )

        self.assertEqual(code, EXIT_CORRECTION)
        self.assertIn("tool install", output.getvalue())
        self.assertIn("Nothing was executed", output.getvalue())

    def test_recognized_error_without_correction_returns_one(self) -> None:
        output = self._output()
        code = check_command(
            ["tool", "--unknown"],
            error_text="unrecognized arguments: --unknown",
            output=output,
        )

        self.assertEqual(code, EXIT_NOT_FOUND)
        self.assertIn("No reliable correction found", output.getvalue())

    def test_empty_error_text_returns_usage(self) -> None:
        self.assertEqual(
            check_command(["tool"], error_text="  ", output=self._output()),
            EXIT_USAGE,
        )

    def test_python_version_is_low_risk(self) -> None:
        assessment = assess_command_safety(["python.exe", "--version"])

        self.assertEqual(assessment.level, RiskLevel.LOW)
        self.assertEqual(assessment.findings[0].rule, "python-information-only")

    def test_python_lowercase_verbose_flag_is_not_treated_as_version(self) -> None:
        assessment = assess_command_safety(["python", "-v"])

        self.assertEqual(assessment.level, RiskLevel.MEDIUM)

    def test_git_status_is_low_risk(self) -> None:
        assessment = assess_command_safety(["git", "status", "--short"])

        self.assertEqual(assessment.level, RiskLevel.LOW)
        self.assertEqual(assessment.findings[0].rule, "git-read-only")

    def test_python_script_is_medium_risk(self) -> None:
        assessment = assess_command_safety(["python", "app.py"])

        self.assertEqual(assessment.level, RiskLevel.MEDIUM)
        self.assertEqual(assessment.findings[0].rule, "code-execution-possible")

    def test_unknown_command_is_never_assumed_low_risk(self) -> None:
        assessment = assess_command_safety(["custom-tool", "inspect"])

        self.assertEqual(assessment.level, RiskLevel.MEDIUM)
        self.assertEqual(assessment.findings[0].rule, "unknown-command")

    def test_delete_command_and_broad_target_are_high_risk(self) -> None:
        assessment = assess_command_safety(["rm", "-rf", "/"])
        rules = {finding.rule for finding in assessment.findings}

        self.assertEqual(assessment.level, RiskLevel.HIGH)
        self.assertIn("destructive-command", rules)
        self.assertIn("broad-target", rules)
        self.assertIn("force-flag", rules)
        self.assertIn("recursive-flag", rules)

    def test_windows_remove_item_is_high_risk(self) -> None:
        assessment = assess_command_safety(
            ["Remove-Item", "-Recurse", "-Force", "C:\\temp"]
        )

        self.assertEqual(assessment.level, RiskLevel.HIGH)
        self.assertTrue(assessment.blocked)

    def test_format_shutdown_and_disk_tools_are_high_risk(self) -> None:
        for argv in (
            ["format.com", "D:"],
            ["shutdown", "/s"],
            ["diskpart"],
            ["mkfs.ext4", "/dev/sda1"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(
                    assess_command_safety(argv).level,
                    RiskLevel.HIGH,
                )

    def test_git_clean_and_reset_hard_are_high_risk(self) -> None:
        for argv in (["git", "clean", "-fd"], ["git", "reset", "--hard"]):
            with self.subTest(argv=argv):
                self.assertEqual(
                    assess_command_safety(argv).level,
                    RiskLevel.HIGH,
                )

    def test_other_destructive_git_operations_are_high_risk(self) -> None:
        commands = (
            ["git", "branch", "-D", "old-work"],
            ["git", "checkout", "--", "changed.txt"],
            ["git", "push", "--force", "origin", "main"],
            ["git", "stash", "clear"],
        )
        for argv in commands:
            with self.subTest(argv=argv):
                self.assertEqual(
                    assess_command_safety(argv).level,
                    RiskLevel.HIGH,
                )

    def test_forced_git_operation_carries_force_evidence(self) -> None:
        assessment = assess_command_safety(["git", "fetch", "--force"])
        rules = {finding.rule for finding in assessment.findings}

        self.assertEqual(assessment.level, RiskLevel.MEDIUM)
        self.assertIn("force-flag", rules)

    def test_overwrite_redirection_is_high_risk(self) -> None:
        assessment = assess_command_safety(["echo", "replacement", ">", "file.txt"])

        self.assertEqual(assessment.level, RiskLevel.HIGH)
        self.assertEqual(assessment.findings[0].rule, "overwrite-redirection")

    def test_raw_device_target_is_reported(self) -> None:
        assessment = assess_command_safety(["dd", "if=image", "of=/dev/sda"])
        rules = {finding.rule for finding in assessment.findings}

        self.assertEqual(assessment.level, RiskLevel.HIGH)
        self.assertIn("device-target", rules)

    def test_nested_shell_deletion_is_high_risk(self) -> None:
        assessment = assess_command_safety(
            ["powershell", "-Command", "Remove-Item C:\\temp"]
        )

        self.assertEqual(assessment.level, RiskLevel.HIGH)
        self.assertEqual(assessment.findings[0].rule, "nested-destructive-command")

    def test_privilege_wrapper_cannot_hide_deletion(self) -> None:
        assessment = assess_command_safety(
            ["sudo", "--user=root", "rm", "-rf", "/"]
        )
        rules = {finding.rule for finding in assessment.findings}

        self.assertEqual(assessment.level, RiskLevel.HIGH)
        self.assertIn("wrapped-destructive-command", rules)
        self.assertIn("wrapped-broad-target", rules)

    def test_harmless_text_that_mentions_rm_is_not_high_risk(self) -> None:
        assessment = assess_command_safety(["echo", "rm -rf /"])

        self.assertEqual(assessment.level, RiskLevel.LOW)

    def test_safety_classification_is_deterministic_and_non_mutating(self) -> None:
        argv = ["git", "status", "--short"]
        original = list(argv)

        self.assertEqual(
            assess_command_safety(argv),
            assess_command_safety(argv),
        )
        self.assertEqual(argv, original)

    def test_risk_increasing_executable_correction_is_withheld(self) -> None:
        output = self._output()
        candidate = ExecutableCandidate("rm", "rm.exe", "C:/Tools/rm.exe")
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = check_command(
                ["rmm", "file.txt"],
                env={},
                windows=True,
                output=output,
            )

        self.assertEqual(code, EXIT_BLOCKED)
        self.assertIn("Correction blocked", output.getvalue())
        self.assertIn("No runnable correction was returned", output.getvalue())

    def test_error_correction_cannot_turn_git_typo_into_git_clean(self) -> None:
        output = self._output()
        error = (
            "git: 'clea' is not a git command. See 'git --help'.\n\n"
            "The most similar command is\n\tclean"
        )
        code = check_command(
            ["git", "clea"],
            error_text=error,
            output=output,
        )

        self.assertEqual(code, EXIT_BLOCKED)
        self.assertIn("Proposed risk: High", output.getvalue())
        self.assertNotIn("Suggestion:", output.getvalue())

    def test_normal_correction_keeps_safety_evidence(self) -> None:
        output = self._output()
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = check_command(
                ["pyhton", "app.py"],
                env={},
                windows=True,
                output=output,
            )

        self.assertEqual(code, EXIT_CORRECTION)
        self.assertIn("Safety assessment", output.getvalue())
        self.assertIn("Risk: Medium", output.getvalue())

    def test_safety_command_returns_blocked_for_high_risk(self) -> None:
        output = self._output()
        code = safety_command(["git", "reset", "--hard"], output=output)

        self.assertEqual(code, EXIT_BLOCKED)
        self.assertIn("BLOCKED", output.getvalue())
        self.assertIn("Nothing was executed", output.getvalue())

    def test_check_returns_blocked_for_existing_high_risk_command(self) -> None:
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Program Files/Git/cmd/git.exe",
        ):
            code = check_command(["git", "reset", "--hard"], output=output)

        self.assertEqual(code, EXIT_BLOCKED)
        self.assertIn("Risk: High", output.getvalue())

    def test_safety_command_returns_usage_without_command(self) -> None:
        self.assertEqual(safety_command([], output=self._output()), EXIT_USAGE)

    def test_keyboard_interrupt_uses_cancelled_exit_code(self) -> None:
        error_output = self._output()
        with (
            mock.patch(__name__ + ".cli", side_effect=KeyboardInterrupt),
            mock.patch(__name__ + ".sys.stderr", error_output),
        ):
            code = main()

        self.assertEqual(code, EXIT_CANCELLED)
        self.assertIn("cancelled", error_output.getvalue())

    def test_check_and_safety_never_call_executor(self) -> None:
        check_output = self._output()
        safety_output = self._output()
        with (
            mock.patch(__name__ + ".execute_command") as executor,
            mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Python/python.exe",
            ),
        ):
            check_code = check_command(
                ["python", "--version"],
                output=check_output,
            )
            safety_code = safety_command(
                ["python", "--version"],
                output=safety_output,
            )

        self.assertEqual(check_code, EXIT_OK)
        self.assertEqual(safety_code, EXIT_OK)
        executor.assert_not_called()

    def test_execute_command_uses_argument_list_and_shell_false(self) -> None:
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                ["tool", "value with spaces"],
                0,
                stdout="done\n",
                stderr="",
            )
        )

        result = execute_command(["tool", "value with spaces"], runner=runner)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "done\n")
        positional, keywords = runner.call_args
        self.assertEqual(positional[0], ["tool", "value with spaces"])
        self.assertIs(keywords["shell"], False)
        self.assertIs(keywords["capture_output"], True)
        self.assertIs(keywords["check"], False)

    def test_execute_command_handles_expected_launch_error(self) -> None:
        runner = mock.Mock(
            side_effect=FileNotFoundError(2, "not found", "missing-tool")
        )

        result = execute_command(["missing-tool"], runner=runner)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FileNotFoundError", result.launch_error or "")
        self.assertNotIn("not found", result.launch_error or "")

    def test_run_executes_valid_original_without_correction(self) -> None:
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                ["echo", "hello"],
                0,
                stdout="hello\n",
                stderr="",
            )
        )
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Windows/System32/echo.exe",
        ):
            code = run_command(
                ["echo", "hello"],
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("hello", output.getvalue())

    def test_run_without_command_returns_usage(self) -> None:
        runner = mock.Mock()

        code = run_command(
            [],
            runner=runner,
            output=self._output(),
            error_output=self._output(),
        )

        self.assertEqual(code, EXIT_USAGE)
        runner.assert_not_called()

    def test_run_refuses_unresolved_path_without_execution(self) -> None:
        runner = mock.Mock()
        output = self._output()
        errors = self._output()
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Python/python.exe",
            ),
        ):
            code = run_command(
                ["python", "missing-script.py"],
                cwd=folder,
                runner=runner,
                output=output,
                error_output=errors,
            )

        self.assertEqual(code, EXIT_NOT_FOUND)
        runner.assert_not_called()
        self.assertIn("could not be proven or corrected", output.getvalue())
        self.assertIn("missing-script.py", errors.getvalue())

    def test_corrected_run_cancels_on_enter_without_execution(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        runner = mock.Mock()
        output = self._output()
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = run_command(
                ["pyhton", "--version"],
                env={},
                windows=True,
                input_fn=lambda _prompt: "",
                interactive=True,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_CANCELLED)
        runner.assert_not_called()
        self.assertIn("Cancelled. Nothing was executed", output.getvalue())

    def test_corrected_run_requires_explicit_y_and_uses_suggestion(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                ["python", "--version"],
                0,
                stdout="Python 3.13\n",
                stderr="",
            )
        )
        output = self._output()
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = run_command(
                ["pyhton", "--version"],
                env={},
                windows=True,
                input_fn=lambda _prompt: "y",
                interactive=True,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(runner.call_args.args[0], ["python", "--version"])
        self.assertIn("1 correction | Match: High | Risk: Low", output.getvalue())

    def test_correction_prompt_supports_explain_and_diff(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        choices = iter(("e", "d", "y"))
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                ["python", "--version"],
                0,
                stdout="",
                stderr="",
            )
        )
        output = self._output()
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = run_command(
                ["pyhton", "--version"],
                env={},
                windows=True,
                input_fn=lambda _prompt: next(choices),
                interactive=True,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("Resolved path: C:/Python/python.exe", output.getvalue())
        self.assertIn("Token diff:", output.getvalue())
        self.assertIn("[0] pyhton -> python", output.getvalue())

    def test_noninteractive_corrected_run_cancels(self) -> None:
        candidate = ExecutableCandidate("python", "python.exe", "C:/Python/python.exe")
        runner = mock.Mock()
        output = self._output()
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = run_command(
                ["pyhton", "--version"],
                env={},
                windows=True,
                input_fn=lambda _prompt: "y",
                interactive=False,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_CANCELLED)
        runner.assert_not_called()
        self.assertIn("interactive approval is required", output.getvalue())

    def test_run_blocks_high_risk_command_without_calling_runner(self) -> None:
        runner = mock.Mock()
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Program Files/Git/cmd/git.exe",
        ):
            code = run_command(
                ["git", "reset", "--hard"],
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_BLOCKED)
        runner.assert_not_called()
        self.assertIn("git reset --hard can discard", output.getvalue())

    def test_run_blocks_risk_increasing_preflight_correction(self) -> None:
        candidate = ExecutableCandidate("rm", "rm.exe", "C:/Tools/rm.exe")
        runner = mock.Mock()
        output = self._output()
        with (
            mock.patch(__name__ + ".command_exists", return_value=None),
            mock.patch(__name__ + ".discover_executables", return_value=(candidate,)),
        ):
            code = run_command(
                ["rmm", "ordinary-name"],
                env={},
                windows=True,
                input_fn=lambda _prompt: "y",
                interactive=True,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_BLOCKED)
        runner.assert_not_called()
        self.assertIn("No runnable correction was returned", output.getvalue())

    def test_run_blocks_complex_shell_operator_without_execution(self) -> None:
        runner = mock.Mock()
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Windows/System32/echo.exe",
        ):
            code = run_command(
                ["echo", "hello", "|", "other-tool"],
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_BLOCKED)
        runner.assert_not_called()
        self.assertIn("compound shell syntax", output.getvalue())

    def test_run_blocks_explicit_shell_command_text(self) -> None:
        runner = mock.Mock()
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        ):
            code = run_command(
                ["powershell", "-Command", "Get-Date"],
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_BLOCKED)
        runner.assert_not_called()
        self.assertIn("static token checks cannot prove safe", output.getvalue())

    def test_failed_run_can_offer_one_error_based_retry(self) -> None:
        first = subprocess.CompletedProcess(
            ["tool", "instal"],
            2,
            stdout="",
            stderr=(
                "argument action: invalid choice: 'instal' "
                "(choose from 'install', 'remove')"
            ),
        )
        second = subprocess.CompletedProcess(
            ["tool", "install"],
            0,
            stdout="installed\n",
            stderr="",
        )
        runner = mock.Mock(side_effect=(first, second))
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Tools/tool.exe",
        ):
            code = run_command(
                ["tool", "instal"],
                input_fn=lambda _prompt: "y",
                interactive=True,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[1].args[0], ["tool", "install"])
        self.assertIn("installed", output.getvalue())

    def test_failed_run_without_reliable_correction_returns_one(self) -> None:
        failure = subprocess.CompletedProcess(
            ["tool"],
            7,
            stdout="",
            stderr="something unusual happened",
        )
        runner = mock.Mock(return_value=failure)
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Tools/tool.exe",
        ):
            code = run_command(
                ["tool"],
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_NOT_FOUND)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("unknown error", output.getvalue())

    def test_user_can_cancel_post_failure_retry(self) -> None:
        failure = subprocess.CompletedProcess(
            ["tool", "instal"],
            2,
            stdout="",
            stderr=(
                "invalid choice: 'instal' "
                "(choose from 'install', 'remove')"
            ),
        )
        runner = mock.Mock(return_value=failure)
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Tools/tool.exe",
        ):
            code = run_command(
                ["tool", "instal"],
                input_fn=lambda _prompt: "n",
                interactive=True,
                runner=runner,
                output=self._output(),
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_CANCELLED)
        self.assertEqual(runner.call_count, 1)

    def test_corrected_retry_is_attempted_only_once(self) -> None:
        first = subprocess.CompletedProcess(
            ["tool", "instal"],
            2,
            stdout="",
            stderr="invalid choice: 'instal' (choose from 'install')",
        )
        second = subprocess.CompletedProcess(
            ["tool", "install"],
            9,
            stdout="",
            stderr="invalid choice: 'install' (choose from 'remove')",
        )
        runner = mock.Mock(side_effect=(first, second))
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Tools/tool.exe",
        ):
            code = run_command(
                ["tool", "instal"],
                input_fn=lambda _prompt: "y",
                interactive=True,
                runner=runner,
                output=self._output(),
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_NOT_FOUND)
        self.assertEqual(runner.call_count, 2)

    def test_failed_run_blocks_risk_increasing_retry(self) -> None:
        failure = subprocess.CompletedProcess(
            ["git", "clea"],
            1,
            stdout="",
            stderr=(
                "git: 'clea' is not a git command. See 'git --help'.\n\n"
                "The most similar command is\n\tclean"
            ),
        )
        runner = mock.Mock(return_value=failure)
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Program Files/Git/cmd/git.exe",
        ):
            code = run_command(
                ["git", "clea"],
                input_fn=lambda _prompt: "y",
                interactive=True,
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_BLOCKED)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("Proposed risk: High", output.getvalue())

    def test_compact_correction_redacts_sensitive_values(self) -> None:
        rendered = render_compact_correction(
            ["tool", "instal", "--token", "super-secret"],
            ["tool", "install", "--token", "super-secret"],
            correction_count=1,
            label=MatchLabel.HIGH,
            safety=assess_command_safety(["tool", "install"]),
        )

        self.assertNotIn("super-secret", rendered)
        self.assertIn("--token <redacted>", rendered)

    def test_detailed_correction_redacts_sensitive_values(self) -> None:
        correction = Correction(
            original_token="pyhton",
            suggested_token="python",
            original_arguments=("--token", "super-secret"),
            score=92,
            label=MatchLabel.HIGH,
            reason="one adjacent-character transposition",
            evidence="PATH evidence",
            executable_path="C:/Python/python.exe",
        )

        rendered = render_correction(correction)

        self.assertNotIn("super-secret", rendered)
        self.assertIn("--token <redacted>", rendered)

    def test_language_detection_for_five_deep_profiles(self) -> None:
        cases = (
            (["python", "main.py"], SourceLanguage.PYTHON),
            (["node", "app.js"], SourceLanguage.JAVASCRIPT),
            (["gcc", "main.c"], SourceLanguage.C),
            (["g++", "main.cpp"], SourceLanguage.CPP),
            (["javac", "Main.java"], SourceLanguage.JAVA),
        )
        with tempfile.TemporaryDirectory() as folder:
            for argv, expected in cases:
                context = detect_language_context(argv, cwd=folder, env={})
                self.assertEqual(context.language, expected)
                self.assertTrue(context.deep_profile)
                self.assertEqual(context.confidence, DetectionConfidence.HIGH)

    def test_declared_additional_language_extensions_are_detectable(self) -> None:
        cases = (
            ("app.ts", SourceLanguage.TYPESCRIPT),
            ("Program.cs", SourceLanguage.CSHARP),
            ("main.go", SourceLanguage.GO),
            ("main.rs", SourceLanguage.RUST),
            ("app.rb", SourceLanguage.RUBY),
            ("index.php", SourceLanguage.PHP),
            ("Main.kt", SourceLanguage.KOTLIN),
            ("main.swift", SourceLanguage.SWIFT),
            ("main.dart", SourceLanguage.DART),
            ("main.lua", SourceLanguage.LUA),
            ("main.pl", SourceLanguage.PERL),
            ("analysis.r", SourceLanguage.R),
            ("main.jl", SourceLanguage.JULIA),
            ("build.sh", SourceLanguage.SHELL),
            ("build.ps1", SourceLanguage.POWERSHELL),
        )
        with tempfile.TemporaryDirectory() as folder:
            for filename, expected in cases:
                context = detect_language_context(["custom", filename], cwd=folder, env={})
                self.assertEqual(context.language, expected)
                self.assertFalse(context.deep_profile)

    def test_language_tool_and_source_conflict_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            context = detect_language_context(
                ["python", "main.js"],
                cwd=folder,
                env={},
            )

        self.assertEqual(context.language, SourceLanguage.UNKNOWN)
        self.assertIn("conflict", " ".join(context.evidence))

    def test_explicit_language_override_wins_and_is_recorded(self) -> None:
        context = detect_language_context(
            ["mystery", "main.unknown"],
            env={},
            explicit_language="cpp",
        )

        self.assertEqual(context.language, SourceLanguage.CPP)
        self.assertEqual(context.confidence, DetectionConfidence.HIGH)
        self.assertIn("explicit language override", context.evidence[0])

    def test_invalid_explicit_language_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            detect_language_context(["tool"], env={}, explicit_language="braincode")

    def test_extensionless_python_shebang_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "worker"
            script.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
            context = detect_language_context([str(script)], cwd=folder, env={})

        self.assertEqual(context.language, SourceLanguage.PYTHON)
        self.assertEqual(context.source_files, (str(script.resolve()),))

    def test_compiler_error_shape_can_supply_language_evidence(self) -> None:
        context = detect_language_context(
            ["unknown-tool"],
            env={},
            error_text="main.c:12:5: error: expected ';' before '}' token",
        )

        self.assertEqual(context.language, SourceLanguage.C)
        self.assertEqual(context.confidence, DetectionConfidence.MEDIUM)

    def test_direct_project_marker_is_low_confidence_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "package.json").write_text("{}", encoding="utf-8")
            context = detect_language_context(["unknown-tool"], cwd=folder, env={})

        self.assertEqual(context.language, SourceLanguage.JAVASCRIPT)
        self.assertEqual(context.confidence, DetectionConfidence.LOW)

    def test_shell_override_and_environment_detection(self) -> None:
        self.assertEqual(detect_shell({}, explicit_shell="fish"), "fish")
        self.assertEqual(detect_shell({"SHELL": "/bin/zsh"}), "zsh")
        self.assertEqual(
            detect_shell({"PSModulePath": "C:/modules", "COMSPEC": "cmd.exe"}),
            "PowerShell-compatible environment",
        )

    def test_preflight_contains_language_understanding(self) -> None:
        with mock.patch(__name__ + ".command_exists", return_value="C:/Python/python.exe"):
            preflight = prepare_preflight(["python", "main.py"], env={}, cwd=".")

        self.assertEqual(
            preflight.language_understanding.context.language,
            SourceLanguage.PYTHON,
        )

    def test_python_ast_extracts_and_classifies_local_symbols(self) -> None:
        source_text = (
            "import math as maths\n"
            "total = 0\n"
            "def calculate(value):\n    return value + total\n"
            "class Engine:\n    pass\n"
            "# def phantom(): pass\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.py"
            source.write_text(source_text, encoding="utf-8")
            understanding = understand_language_context(
                ["python", str(source)],
                cwd=folder,
                env={},
            )

        kinds = {(symbol.name, symbol.kind) for symbol in understanding.symbols}
        self.assertIn(("calculate", SymbolKind.LOCAL_FUNCTION), kinds)
        self.assertIn(("Engine", SymbolKind.LOCAL_CLASS), kinds)
        self.assertIn(("total", SymbolKind.LOCAL_VARIABLE), kinds)
        self.assertIn(("maths", SymbolKind.IMPORT), kinds)
        self.assertNotIn("phantom", {symbol.name for symbol in understanding.symbols})
        self.assertEqual(classify_identifier("while", understanding).kind, SymbolKind.KEYWORD)
        self.assertEqual(classify_identifier("print", understanding).kind, SymbolKind.BUILTIN)

    def test_javascript_extractor_ignores_comments_and_strings(self) -> None:
        source_text = (
            "// function phantom() {}\n"
            "const text = 'class Fake {}';\n"
            "function calculate(value) { return value; }\n"
            "const transform = (value) => value;\n"
            "class Engine {}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "app.js"
            source.write_text(source_text, encoding="utf-8")
            understanding = understand_language_context(["node", str(source)], cwd=folder, env={})

        kinds = {(symbol.name, symbol.kind) for symbol in understanding.symbols}
        self.assertIn(("calculate", SymbolKind.LOCAL_FUNCTION), kinds)
        self.assertIn(("transform", SymbolKind.LOCAL_FUNCTION), kinds)
        self.assertIn(("Engine", SymbolKind.LOCAL_CLASS), kinds)
        self.assertNotIn("phantom", {symbol.name for symbol in understanding.symbols})
        self.assertNotIn("Fake", {symbol.name for symbol in understanding.symbols})

    def test_java_extractor_finds_class_method_and_constructor(self) -> None:
        source_text = (
            "public class Calculator {\n"
            "  public Calculator() {}\n"
            "  public static int calculate(int value) { return value; }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "Calculator.java"
            source.write_text(source_text, encoding="utf-8")
            understanding = understand_language_context(["javac", str(source)], cwd=folder, env={})

        kinds = {(symbol.name, symbol.kind) for symbol in understanding.symbols}
        self.assertIn(("Calculator", SymbolKind.LOCAL_CLASS), kinds)
        self.assertIn(("Calculator", SymbolKind.LOCAL_FUNCTION), kinds)
        self.assertIn(("calculate", SymbolKind.LOCAL_FUNCTION), kinds)
        self.assertEqual(
            classify_identifier("System", understanding).kind,
            SymbolKind.STANDARD_LIBRARY,
        )

    def test_c_extractor_and_standard_library_classification(self) -> None:
        source_text = (
            "#include <stdio.h>\n"
            "int calculate(int value) { return value; }\n"
            "int main(void) { printf(\"ok\"); return calculate(1); }\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            source.write_text(source_text, encoding="utf-8")
            understanding = understand_language_context(["gcc", str(source)], cwd=folder, env={})

        self.assertEqual(
            classify_identifier("calculate", understanding).kind,
            SymbolKind.LOCAL_FUNCTION,
        )
        self.assertEqual(
            classify_identifier("printf", understanding).kind,
            SymbolKind.STANDARD_LIBRARY,
        )
        self.assertEqual(classify_identifier("while", understanding).kind, SymbolKind.KEYWORD)

    def test_cpp_extractor_finds_class_and_qualified_method(self) -> None:
        source_text = (
            "class Calculator { public: int calculate(int value); };\n"
            "int Calculator::calculate(int value) { return value; }\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.cpp"
            source.write_text(source_text, encoding="utf-8")
            understanding = understand_language_context(["g++", str(source)], cwd=folder, env={})

        kinds = {(symbol.name, symbol.kind) for symbol in understanding.symbols}
        self.assertIn(("Calculator", SymbolKind.LOCAL_CLASS), kinds)
        self.assertIn(("calculate", SymbolKind.LOCAL_FUNCTION), kinds)

    def test_malformed_python_source_does_not_crash_or_invent_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "broken.py"
            source.write_text("def calculate(:\n", encoding="utf-8")
            understanding = understand_language_context(["python", str(source)], cwd=folder, env={})

        self.assertEqual(understanding.symbols, ())

    def test_oversized_source_is_not_scanned_for_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "large.py"
            source.write_text("x" * (MAX_SOURCE_BYTES + 1), encoding="utf-8")
            understanding = understand_language_context(["python", str(source)], cwd=folder, env={})

        self.assertEqual(understanding.context.language, SourceLanguage.PYTHON)
        self.assertEqual(understanding.symbols, ())

    def test_only_explicit_source_file_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            named = root / "main.py"
            hidden = root / "nested" / "other.py"
            hidden.parent.mkdir()
            named.write_text("def visible(): pass\n", encoding="utf-8")
            hidden.write_text("def hidden_symbol(): pass\n", encoding="utf-8")
            understanding = understand_language_context(["python", str(named)], cwd=root, env={})

        names = {symbol.name for symbol in understanding.symbols}
        self.assertIn("visible", names)
        self.assertNotIn("hidden_symbol", names)

    def test_local_function_typo_gets_source_backed_correction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            source.write_text("int calculate(int x) { return x; }\n", encoding="utf-8")
            understanding = understand_language_context(["gcc", str(source)], cwd=folder, env={})
            correction = find_identifier_correction("calcluate", understanding)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested, "calculate")
        self.assertEqual(correction.kind, SymbolKind.LOCAL_FUNCTION)
        self.assertEqual(correction.label, MatchLabel.HIGH)

    def test_keyword_typo_uses_current_language_profile(self) -> None:
        understanding = understand_language_context(["gcc", "main.c"], env={})
        correction = find_identifier_correction("whlie", understanding)

        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.suggested, "while")
        self.assertEqual(correction.kind, SymbolKind.KEYWORD)

    def test_unrelated_identifier_is_not_guessed(self) -> None:
        understanding = understand_language_context(["gcc", "main.c"], env={})
        self.assertIsNone(find_identifier_correction("totallyDifferentName", understanding))

    def test_identifier_candidate_ranking_is_deterministic(self) -> None:
        understanding = understand_language_context(["gcc", "main.c"], env={})
        first = find_identifier_correction("whlie", understanding)
        second = find_identifier_correction("whlie", understanding)
        self.assertEqual(first, second)

    def test_context_command_is_read_only_and_explains_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            source.write_text("int calculate(int x) { return x; }\n", encoding="utf-8")
            output = self._output()
            error_output = self._output()
            with mock.patch(__name__ + ".subprocess.run") as runner:
                code = context_command(
                    ["gcc", str(source)],
                    identifiers=("calcluate", "while", "printf"),
                    cwd=folder,
                    env={},
                    output=output,
                    error_output=error_output,
                )

        rendered = output.getvalue()
        self.assertEqual(code, EXIT_OK)
        runner.assert_not_called()
        self.assertIn("Language: C", rendered)
        self.assertIn("calcluate -> calculate", rendered)
        self.assertIn("while: keyword", rendered)
        self.assertIn("printf: standard-library symbol", rendered)
        self.assertIn("Nothing was executed or modified.", rendered)

    def test_context_command_rejects_invalid_override(self) -> None:
        error_output = self._output()
        code = context_command(
            ["tool"],
            explicit_language="not-a-language",
            env={},
            output=self._output(),
            error_output=error_output,
        )

        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("unsupported language", error_output.getvalue())

    def test_code_error_identifier_patterns_are_bounded_and_deterministic(self) -> None:
        samples = (
            ("NameError: name 'calcluate' is not defined", ("calcluate",)),
            ("ReferenceError: calcluate is not defined", ("calcluate",)),
            ("error: use of undeclared identifier 'calcluate'", ("calcluate",)),
            ("error: ‘calcluate’ was not declared in this scope", ("calcluate",)),
            (
                "error: cannot find symbol\n  symbol: method calcluate(int)",
                ("calcluate",),
            ),
        )
        for error_text, expected in samples:
            self.assertEqual(identifiers_from_code_error(error_text), expected)

    def test_context_automatically_checks_identifier_from_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            source.write_text("int calculate(int x) { return x; }\n", encoding="utf-8")
            output = self._output()
            code = context_command(
                ["gcc", str(source)],
                error_text="main.c:3: error: ‘calcluate’ undeclared",
                cwd=folder,
                env={},
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("calcluate -> calculate", output.getvalue())

    def test_failed_compile_reports_code_typo_without_editing_or_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            original_source = "int calculate(int x) { return x; }\n"
            source.write_text(original_source, encoding="utf-8")
            failure = subprocess.CompletedProcess(
                ["gcc", str(source)],
                1,
                stdout="",
                stderr="main.c:3: error: ‘calcluate’ undeclared",
            )
            runner = mock.Mock(return_value=failure)
            output = self._output()
            with mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Tools/gcc.exe",
            ):
                code = run_command(
                    ["gcc", str(source)],
                    cwd=folder,
                    env={},
                    runner=runner,
                    output=output,
                    error_output=self._output(),
                )
            unchanged_source = source.read_text(encoding="utf-8")

        self.assertEqual(code, EXIT_NOT_FOUND)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(unchanged_source, original_source)
        self.assertIn("Code diagnostic:", output.getvalue())
        self.assertIn("calcluate -> calculate", output.getvalue())
        self.assertIn("Source was not modified", output.getvalue())

    def test_interactive_parser_handles_posix_quotes_and_comments_as_arguments(self) -> None:
        parsed = split_interactive_command(
            'python "my file.py" --label "hello world" #literal',
            windows=False,
        )

        self.assertEqual(
            parsed,
            ("python", "my file.py", "--label", "hello world", "#literal"),
        )

    def test_interactive_parser_preserves_windows_backslashes_and_quoted_paths(self) -> None:
        parsed = split_interactive_command(
            'python "C:\\Program Files\\main.py" C:\\Users\\M\\app.py '
            '--label="hello world" ""',
            windows=True,
        )

        self.assertEqual(
            parsed,
            (
                "python",
                "C:\\Program Files\\main.py",
                "C:\\Users\\M\\app.py",
                "--label=hello world",
                "",
            ),
        )

    def test_interactive_parser_rejects_unclosed_quote(self) -> None:
        with self.assertRaises(ValueError):
            split_interactive_command('python "main.py', windows=False)
        with self.assertRaises(ValueError):
            split_interactive_command('python "main.py', windows=True)

    def test_information_indicator_uses_unicode_or_ascii_fallback(self) -> None:
        class EncodingOnly:
            def __init__(self, encoding: str) -> None:
                self.encoding = encoding

        self.assertEqual(information_indicator(EncodingOnly("utf-8")), "ⓘ")
        self.assertEqual(information_indicator(EncodingOnly("ascii")), "[i]")

    def test_compact_renderer_accepts_unicode_indicator(self) -> None:
        rendered = render_compact_correction(
            ["pyhton", "main.py"],
            ["python", "main.py"],
            correction_count=1,
            label=MatchLabel.HIGH,
            safety=assess_command_safety(["python", "main.py"]),
            indicator="ⓘ",
        )

        self.assertIn("python main.py  ⓘ", rendered)

    def test_interactive_help_pwd_clear_and_exit_are_internal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = self._output()
            error_output = self._output()
            reader = mock.Mock(side_effect=("pwd", "help", "clear", "exit"))
            runner = mock.Mock()
            with mock.patch(__name__ + ".subprocess.run") as subprocess_runner:
                code = interactive_shell(
                    cwd=folder,
                    input_fn=reader,
                    runner=runner,
                    output=output,
                    error_output=error_output,
                )

        rendered = output.getvalue()
        self.assertEqual(code, EXIT_OK)
        self.assertIn(str(Path(folder).resolve()), rendered)
        self.assertIn("TermFix interactive commands:", rendered)
        self.assertIn("\033[2J\033[H", rendered)
        self.assertIn("Leaving TermFix.", rendered)
        runner.assert_not_called()
        subprocess_runner.assert_not_called()

    def test_interactive_cd_changes_only_session_directory(self) -> None:
        process_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child = root / "child"
            child.mkdir()
            output = self._output()
            code = interactive_shell(
                cwd=root,
                input_fn=mock.Mock(side_effect=("cd child", "pwd", "exit")),
                runner=mock.Mock(),
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertIn(str(child.resolve()), output.getvalue())
        self.assertEqual(Path.cwd(), process_directory)

    def test_interactive_cd_can_approve_proven_directory_correction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "source"
            expected.mkdir()
            output = self._output()
            code = interactive_shell(
                cwd=root,
                input_fn=mock.Mock(
                    side_effect=("cd soruce", "y", "pwd", "exit"),
                ),
                runner=mock.Mock(),
                output=output,
                error_output=self._output(),
            )

        rendered = output.getvalue()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("cd soruce", rendered)
        self.assertIn("cd source", rendered)
        self.assertIn(str(expected.resolve()), rendered)

    def test_interactive_cd_correction_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source").mkdir()
            output = self._output()
            code = interactive_shell(
                cwd=root,
                input_fn=mock.Mock(
                    side_effect=("cd soruce", "n", "pwd", "exit"),
                ),
                runner=mock.Mock(),
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertIn(str(root.resolve()), output.getvalue())
        self.assertIn("Cancelled", output.getvalue())

    def test_interactive_cd_rejects_file_and_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "note.txt").write_text("data", encoding="utf-8")
            errors = self._output()
            code = interactive_shell(
                cwd=root,
                input_fn=mock.Mock(
                    side_effect=("cd note.txt", "cd missing", "cd", "exit"),
                ),
                runner=mock.Mock(),
                output=self._output(),
                error_output=errors,
            )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("directory not found: note.txt", errors.getvalue())
        self.assertIn("directory not found: missing", errors.getvalue())
        self.assertIn("Usage: cd PATH", errors.getvalue())

    def test_interactive_external_command_uses_run_pipeline_and_session_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runner = mock.Mock(
                return_value=subprocess.CompletedProcess(
                    ["python", "--version"],
                    0,
                    stdout="Python test\n",
                    stderr="",
                )
            )
            output = self._output()
            with mock.patch(
                __name__ + ".command_exists",
                return_value="C:/Python/python.exe",
            ):
                code = interactive_shell(
                    cwd=folder,
                    input_fn=mock.Mock(side_effect=("python --version", "exit")),
                    runner=runner,
                    output=output,
                    error_output=self._output(),
                )

        self.assertEqual(code, EXIT_OK)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], ["python", "--version"])
        self.assertFalse(runner.call_args.kwargs["shell"])
        self.assertEqual(runner.call_args.kwargs["cwd"], str(Path(folder).resolve()))
        self.assertIn("Python test", output.getvalue())

    def test_interactive_corrected_command_requires_approval(self) -> None:
        candidate = ExecutableCandidate(
            "python",
            "python.exe",
            "C:/Python/python.exe",
        )
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                ["python", "--version"],
                0,
                stdout="Python test\n",
                stderr="",
            )
        )
        output = self._output()
        with mock.patch(__name__ + ".command_exists", return_value=None), mock.patch(
            __name__ + ".discover_executables",
            return_value=(candidate,),
        ):
            code = interactive_shell(
                input_fn=mock.Mock(
                    side_effect=("pyhton --version", "y", "exit"),
                ),
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(runner.call_args.args[0], ["python", "--version"])
        self.assertIn("Suggestion:", output.getvalue())
        self.assertIn("python --version", output.getvalue())

    def test_interactive_high_risk_command_is_blocked_and_prompt_continues(self) -> None:
        runner = mock.Mock()
        output = self._output()
        with mock.patch(__name__ + ".command_exists", return_value="C:/Tools/rm.exe"):
            code = interactive_shell(
                input_fn=mock.Mock(side_effect=("rm -rf /", "exit")),
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        runner.assert_not_called()
        self.assertIn("Execution blocked:", output.getvalue())
        self.assertGreaterEqual(output.getvalue().count("termfix> "), 2)

    def test_interactive_shell_operator_is_blocked_without_system_shell(self) -> None:
        runner = mock.Mock()
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Python/python.exe",
        ):
            code = interactive_shell(
                input_fn=mock.Mock(
                    side_effect=("python --version | more", "exit"),
                ),
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        runner.assert_not_called()
        self.assertIn("compound-shell-syntax", output.getvalue())

    def test_interactive_blank_input_and_bad_builtin_usage_recover(self) -> None:
        errors = self._output()
        code = interactive_shell(
            input_fn=mock.Mock(
                side_effect=("", "   ", "pwd extra", "help extra", "clear extra", "exit extra", "exit"),
            ),
            runner=mock.Mock(),
            output=self._output(),
            error_output=errors,
        )

        self.assertEqual(code, EXIT_OK)
        rendered = errors.getvalue()
        self.assertIn("Usage: pwd", rendered)
        self.assertIn("Usage: help", rendered)
        self.assertIn("Usage: clear", rendered)
        self.assertIn("Usage: exit", rendered)

    def test_interactive_invalid_quote_reports_error_and_recovers(self) -> None:
        errors = self._output()
        code = interactive_shell(
            windows=False,
            input_fn=mock.Mock(side_effect=('python "main.py', "exit")),
            runner=mock.Mock(),
            output=self._output(),
            error_output=errors,
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("Input error:", errors.getvalue())

    def test_interactive_ctrl_c_cancels_input_without_closing(self) -> None:
        output = self._output()
        code = interactive_shell(
            input_fn=mock.Mock(side_effect=(KeyboardInterrupt(), "exit")),
            runner=mock.Mock(),
            output=output,
            error_output=self._output(),
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("Input cancelled", output.getvalue())
        self.assertIn("Leaving TermFix", output.getvalue())

    def test_interactive_ctrl_d_exits_cleanly(self) -> None:
        output = self._output()
        code = interactive_shell(
            input_fn=mock.Mock(side_effect=EOFError()),
            runner=mock.Mock(),
            output=output,
            error_output=self._output(),
        )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("Leaving TermFix", output.getvalue())

    def test_interactive_ctrl_c_during_command_keeps_prompt_open(self) -> None:
        runner = mock.Mock(side_effect=KeyboardInterrupt())
        output = self._output()
        with mock.patch(
            __name__ + ".command_exists",
            return_value="C:/Python/python.exe",
        ):
            code = interactive_shell(
                input_fn=mock.Mock(side_effect=("python --version", "exit")),
                runner=runner,
                output=output,
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_OK)
        self.assertIn("Command interrupted", output.getvalue())
        self.assertIn("Leaving TermFix", output.getvalue())

    def test_interactive_rejects_unavailable_start_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing"
            code = interactive_shell(
                cwd=missing,
                input_fn=mock.Mock(side_effect=EOFError()),
                output=self._output(),
                error_output=self._output(),
            )

        self.assertEqual(code, EXIT_USAGE)

    def test_cli_routes_shell_action(self) -> None:
        with mock.patch(__name__ + ".interactive_shell", return_value=EXIT_OK) as shell:
            code = cli(["shell", "--cwd", "."])

        self.assertEqual(code, EXIT_OK)
        shell.assert_called_once_with(cwd=".")

    @staticmethod
    def _output():
        import io

        return io.StringIO()


def main() -> int:
    """Return a documented exit code and keep unexpected failures readable."""

    try:
        return cli()
    except KeyboardInterrupt:
        print("termfix: cancelled", file=sys.stderr)
        return EXIT_CANCELLED
    except SystemExit:
        raise
    except Exception as error:
        print(f"termfix: internal failure: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
