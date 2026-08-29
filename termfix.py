#!/usr/bin/env python3
"""TermFix Steps 2-3: proof-backed executable and path correction.

Executable candidates come from PATH. File and directory candidates come from
the relevant local directory. Every match is deterministic, and the inspected
command is never executed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


APP_NAME = "TermFix"
VERSION = "0.2.0"

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
) -> PathCorrection | None:
    """Correct at most one path component and prove the final path exists."""

    working_directory = Path.cwd() if cwd is None else Path(cwd)
    if not looks_like_path(token, working_directory):
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


def display_token(token: str) -> str:
    """Render one argument unambiguously for display only."""

    if token and not any(character.isspace() or character in "\"'" for character in token):
        return token
    return json.dumps(token, ensure_ascii=False)


def display_argv(argv: tuple[str, ...] | list[str]) -> str:
    """Render an argument vector for review; the result is never executed."""

    return " ".join(display_token(token) for token in argv)


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
    output: object = sys.stdout,
) -> int:
    """Inspect executable and path tokens without executing the command."""

    if not argv:
        print("termfix: no command was supplied", file=sys.stderr)
        return EXIT_USAGE

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
        return check_command(strip_separator(args.command))
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
