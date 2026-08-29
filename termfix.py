#!/usr/bin/env python3
"""TermFix Step 2: proof-backed executable correction.

This version inspects only the executable token. It never executes the command.
Every correction candidate is discovered from the local PATH and ranked with
deterministic standard-library logic.
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
VERSION = "0.1.0"

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
            entries = sorted(
                os.scandir(directory),
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
            key=lambda candidate: (candidate.name.casefold(), candidate.name, candidate.path.casefold()),
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
    available = discover_executables(environment, windows=is_windows) if candidates is None else candidates

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


def display_token(token: str) -> str:
    """Render one argument unambiguously for display only."""

    if token and not any(character.isspace() or character in "\"'" for character in token):
        return token
    return json.dumps(token, ensure_ascii=False)


def display_argv(argv: tuple[str, ...] | list[str]) -> str:
    """Render an argument vector for review; the result is never executed."""

    return " ".join(display_token(token) for token in argv)


def render_correction(correction: Correction) -> str:
    """Render a concise proof-backed correction."""

    return "\n".join(
        (
            "Original:",
            f"  {display_argv(correction.original_argv)}",
            "",
            "Suggestion:",
            f"  {display_argv(correction.suggested_argv)}",
            "",
            "Evidence:",
            f"  - {correction.evidence}",
            f"  - Resolved path: {correction.executable_path}",
            f"  - Reason: {correction.reason}",
            f"  - Match: {correction.score}/100 ({correction.label.value})",
            "",
            "Nothing was executed.",
        )
    )


def check_command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    output: object = sys.stdout,
) -> int:
    """Inspect one command and print the Step 2 result."""

    if not argv:
        print("termfix: no command was supplied", file=sys.stderr)
        return EXIT_USAGE

    original = argv[0]
    resolved = command_exists(original, env)
    if resolved is not None:
        print(f"Command exists: {original}", file=output)
        print(f"Resolved executable: {resolved}", file=output)
        print("No correction is required. Nothing was executed.", file=output)
        return EXIT_OK

    correction = find_correction(argv, env, windows=windows)
    if correction is None:
        print(f"Executable not found: {original}", file=output)
        print("No reliable correction found. Nothing was executed.", file=output)
        return EXIT_NOT_FOUND

    print(render_correction(correction), file=output)
    return EXIT_CORRECTION


def strip_separator(command: list[str]) -> list[str]:
    """Remove a literal argparse remainder separator when present."""

    return command[1:] if command and command[0] == "--" else command


def make_parser() -> argparse.ArgumentParser:
    """Create the Step 2 command-line parser."""

    parser = argparse.ArgumentParser(
        prog="termfix.py",
        description="Proof-backed executable correction using only Python's standard library.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    actions = parser.add_subparsers(dest="action", required=True)

    check = actions.add_parser("check", help="inspect a command without executing it")
    check.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    actions.add_parser("self-test", help="run the embedded Step 2 tests")
    return parser


def run_tests() -> int:
    """Run the embedded test suite without third-party tooling."""

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TermFixStep2Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return EXIT_OK if result.wasSuccessful() else EXIT_NOT_FOUND


def cli(argv: list[str] | None = None) -> int:
    """Run the TermFix Step 2 CLI."""

    args = make_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.action == "self-test":
        return run_tests()
    if args.action == "check":
        return check_command(strip_separator(args.command))
    return EXIT_USAGE


class TermFixStep2Tests(unittest.TestCase):
    """Acceptance and unit tests for proof-backed executable correction."""

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
        with mock.patch(__name__ + ".command_exists", return_value="C:/Python/python.exe"):
            code = check_command(["python", "app.py"], output=output)
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

    def test_step_two_has_no_execution_module(self) -> None:
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
