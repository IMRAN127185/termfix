#!/usr/bin/env python3
"""TermFix Steps 2-4: proof-backed command, path and error correction.

Executable candidates come from PATH. File and directory candidates come from
the relevant local directory. Error evidence comes from recognized stderr
patterns. Every match is deterministic, and the inspected command is never
executed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
from enum import Enum
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock


APP_NAME = "TermFix"
VERSION = "0.3.0"

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2
EXIT_CORRECTION = 3
EXIT_INTERNAL = 70

MINIMUM_MATCH_SCORE = 70
HIGH_MATCH_SCORE = 85
WINDOWS_EXECUTABLE_EXTENSIONS = (".com", ".exe", ".bat", ".cmd")


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


def display_token(token: str) -> str:
    """Render one argument unambiguously for display only."""

    if token and not any(character.isspace() or character in "\"'" for character in token):
        return token
    return json.dumps(token, ensure_ascii=False)


def display_argv(argv: tuple[str, ...] | list[str]) -> str:
    """Render an argument vector for review; the result is never executed."""

    return " ".join(display_token(token) for token in argv)


def render_error_analysis(
    analysis: ErrorAnalysis,
    argv: list[str] | tuple[str, ...],
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

    lines.extend(("", "Raw stderr was not repeated.", "Nothing was executed."))
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

    lines = [
        "Original:",
        f"  {display_argv(original)}",
        "",
        "Suggestion:",
        f"  {display_argv(suggested)}",
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
        print(render_error_analysis(analysis, argv), file=output)
        return EXIT_CORRECTION if analysis.has_correction else EXIT_NOT_FOUND

    original = argv[0]
    resolved = command_exists(original, env)
    executable_correction = None
    if resolved is None:
        executable_correction = find_correction(argv, env, windows=windows)

    path_corrections, unresolved_paths = inspect_path_arguments(argv, cwd=cwd)
    if executable_correction is not None or path_corrections:
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
        return EXIT_CORRECTION

    if resolved is None:
        print(f"Executable not found: {original}", file=output)
        print("No reliable correction found. Nothing was executed.", file=output)
        return EXIT_NOT_FOUND

    if unresolved_paths:
        for index, token in unresolved_paths:
            print(f"Path not found at token {index}: {token}", file=output)
        print("No reliable path correction found. Nothing was executed.", file=output)
        return EXIT_NOT_FOUND

    print(f"Command exists: {original}", file=output)
    print(f"Resolved executable: {resolved}", file=output)
    print("No correction is required. Nothing was executed.", file=output)
    return EXIT_OK


def strip_separator(command: list[str]) -> list[str]:
    """Remove a literal argparse remainder separator when present."""

    return command[1:] if command and command[0] == "--" else command


def make_parser() -> argparse.ArgumentParser:
    """Create the TermFix command-line parser."""

    parser = argparse.ArgumentParser(
        prog="termfix.py",
        description=(
            "Proof-backed executable and path correction using only "
            "Python's standard library."
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

    def test_check_has_no_execution_module(self) -> None:
        self.assertNotIn("subprocess", globals())

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
        return EXIT_NOT_FOUND
    except SystemExit:
        raise
    except Exception as error:
        print(f"termfix: internal failure: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
