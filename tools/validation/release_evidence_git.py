"""Bounded, noninteractive Git subprocesses for release-evidence validation."""

from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import threading
from pathlib import Path

from .release_evidence_model import ReleaseEvidenceError

DEFAULT_TIMEOUT_SECONDS = 15
STDERR_LIMIT = 64 * 1024
OBJECT_NAME = re.compile(
    r"^[0-9a-f]{40}:"
    r"[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
EXCLUDED_PATHSPEC = re.compile(
    r"^:\(top,exclude\)"
    r"[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*"
    r"(?:/\*\*)?$"
)
GIT_READER_PREFIX = "release-evidence-git-reader"
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operations", ctypes.c_ulonglong),
        ("write_operations", ctypes.c_ulonglong),
        ("other_operations", ctypes.c_ulonglong),
        ("read_bytes", ctypes.c_ulonglong),
        ("write_bytes", ctypes.c_ulonglong),
        ("other_bytes", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time", ctypes.c_longlong),
        ("per_job_user_time", ctypes.c_longlong),
        ("limit_flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimitInformation),
        ("io", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


def _allowed_arguments(arguments: tuple[str, ...]) -> bool:
    if not all(isinstance(argument, str) for argument in arguments):
        return False
    if arguments in {
        ("rev-parse", "HEAD"),
        ("ls-files", "--stage", "-z"),
    }:
        return True
    if len(arguments) == 3 and arguments[:2] in {
        ("cat-file", "-s"),
        ("cat-file", "blob"),
    }:
        return bool(OBJECT_NAME.fullmatch(arguments[2]))
    if len(arguments) < 7 or arguments[:3] != (
        "status",
        "--porcelain=v1",
        "-z",
    ):
        return False
    if arguments[3] not in {"--untracked-files=no", "--untracked-files=all"}:
        return False
    if arguments[4] != "--ignore-submodules=all":
        return False
    if arguments[5:7] != ("--", "."):
        return False
    return all(EXCLUDED_PATHSPEC.fullmatch(argument) for argument in arguments[7:])


WindowsJob = tuple[object, int]


def _windows_job_for(process: subprocess.Popen[bytes]) -> WindowsJob:
    """Own the Windows Git process tree with kill-on-close semantics."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateJobObjectW
    create.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    create.restype = ctypes.c_void_p
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    set_information.restype = ctypes.c_int
    assign = kernel32.AssignProcessToJobObject
    assign.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    assign.restype = ctypes.c_int
    terminate = kernel32.TerminateJobObject
    terminate.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    terminate.restype = ctypes.c_int
    close = kernel32.CloseHandle
    close.argtypes = (ctypes.c_void_p,)
    close.restype = ctypes.c_int
    handle = create(None, None)
    if not handle:
        raise ReleaseEvidenceError(
            f"cannot create Windows Git Job Object: {ctypes.get_last_error()}"
        )
    information = _ExtendedLimitInformation()
    information.basic.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not set_information(
        handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ) or not assign(handle, ctypes.c_void_p(int(process._handle))):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ReleaseEvidenceError(
            f"cannot contain Windows Git process tree: {error}"
        )
    return kernel32, handle


def _terminate_windows_job(job: WindowsJob) -> None:
    kernel32, handle = job
    if not kernel32.TerminateJobObject(handle, 1):
        raise ReleaseEvidenceError(
            f"cannot terminate Windows Git Job Object: {ctypes.get_last_error()}"
        )


def _close_windows_job(job: WindowsJob) -> None:
    kernel32, handle = job
    if not kernel32.CloseHandle(handle):
        raise ReleaseEvidenceError(
            f"cannot close Windows Git Job Object: {ctypes.get_last_error()}"
        )


def _bounded_git(
    repo_root: Path,
    arguments: tuple[str, ...],
    *,
    stdout_limit: int,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Run Git with finite time/output and no credential interaction."""

    if not _allowed_arguments(arguments):
        raise ReleaseEvidenceError("Git command shape is not allowed")
    if (
        isinstance(stdout_limit, bool)
        or not isinstance(stdout_limit, int)
        or not 0 <= stdout_limit <= 64 * 1024 * 1024
    ):
        raise ReleaseEvidenceError("Git stdout limit is outside the safe range")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 60
    ):
        raise ReleaseEvidenceError("Git timeout is outside the safe range")
    inherited = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TMPDIR",
        "TEMP",
        "TMP",
    )
    environment = {
        name: os.environ[name] for name in inherited if name in os.environ
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    try:
        process = subprocess.Popen(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                f"core.excludesFile={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "protocol.allow=never",
                "-c",
                "submodule.recurse=false",
                *arguments,
            ],
            cwd=repo_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=0,
        )
    except OSError as error:
        raise ReleaseEvidenceError(f"cannot start Git: {error}") from error
    assert process.stdout is not None
    assert process.stderr is not None
    windows_job: WindowsJob | None = None
    if os.name == "nt":
        try:
            windows_job = _windows_job_for(process)
        except ReleaseEvidenceError as setup_error:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as termination_error:
                raise ReleaseEvidenceError(
                    "Windows Git Job Object setup failed and the process "
                    "could not be terminated within 2s"
                ) from termination_error
            finally:
                process.stdout.close()
                process.stderr.close()
            raise setup_error
    overflow = threading.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def terminate_process() -> None:
        # The allowlist contains only builtins, with hooks, fsmonitor,
        # submodules, lazy fetch, and protocols disabled. Those shapes have no
        # permitted child process; the POSIX process group is defense in depth.
        try:
            if windows_job is not None:
                _terminate_windows_job(windows_job)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError, ReleaseEvidenceError):
            try:
                process.kill()
            except OSError:
                pass

    def close_windows_job() -> None:
        if windows_job is None:
            return
        _close_windows_job(windows_job)

    def consume(
        stream: object,
        chunks: list[bytes],
        limit: int,
    ) -> None:
        consumed = 0
        while True:
            try:
                chunk = stream.read(min(64 * 1024, limit + 1 - consumed))
            except (OSError, ValueError):
                return
            if not chunk:
                return
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > limit:
                overflow.set()
                terminate_process()
                return

    readers = (
        threading.Thread(
            target=consume,
            args=(process.stdout, stdout_chunks, stdout_limit),
            daemon=True,
            name=f"{GIT_READER_PREFIX}-stdout",
        ),
        threading.Thread(
            target=consume,
            args=(process.stderr, stderr_chunks, STDERR_LIMIT),
            daemon=True,
            name=f"{GIT_READER_PREFIX}-stderr",
        ),
    )
    for reader in readers:
        reader.start()

    def close_pipes() -> None:
        process.stdout.close()
        process.stderr.close()

    def join_readers() -> None:
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            terminate_process()
            close_pipes()
            raise ReleaseEvidenceError(
                f"git {' '.join(arguments)} did not close bounded output pipes"
            )
        close_pipes()

    try:
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            terminate_process()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as termination_error:
                close_pipes()
                raise ReleaseEvidenceError(
                    f"git {' '.join(arguments)} could not be terminated"
                ) from termination_error
            join_readers()
            raise ReleaseEvidenceError(
                f"git {' '.join(arguments)} exceeded {timeout_seconds}s timeout"
            ) from error
        join_readers()
    finally:
        close_windows_job()
    if overflow.is_set():
        raise ReleaseEvidenceError(
            f"git {' '.join(arguments)} exceeded bounded output"
        )
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    if return_code != 0:
        raise ReleaseEvidenceError(
            f"git {' '.join(arguments)} failed: {stderr}"
        )
    return b"".join(stdout_chunks)


def git_head(repo_root: Path) -> str:
    content = _bounded_git(
        repo_root, ("rev-parse", "HEAD"), stdout_limit=128
    )
    try:
        return content.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise ReleaseEvidenceError("Git HEAD output is not ASCII") from error


def git_has_gitlinks(repo_root: Path) -> bool:
    content = _bounded_git(
        repo_root,
        ("ls-files", "--stage", "-z"),
        stdout_limit=8 * 1024 * 1024,
    )
    return any(record.startswith(b"160000 ") for record in content.split(b"\0"))


def git_status_tracked(repo_root: Path) -> bytes:
    return _bounded_git(
        repo_root,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=no",
            "--ignore-submodules=all",
            "--",
            ".",
        ),
        stdout_limit=1024 * 1024,
    )


def git_status_untracked(
    repo_root: Path,
    excluded_paths: tuple[str, ...],
    *,
    output_limit: int = 1024 * 1024,
) -> bytes:
    arguments = (
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=all",
        "--",
        ".",
        *excluded_paths,
    )
    return _bounded_git(repo_root, arguments, stdout_limit=output_limit)


def git_blob_size(repo_root: Path, object_name: str) -> int:
    content = _bounded_git(
        repo_root, ("cat-file", "-s", object_name), stdout_limit=64
    )
    try:
        size = int(content.decode("ascii", errors="strict").strip())
    except (UnicodeDecodeError, ValueError) as error:
        raise ReleaseEvidenceError("Git returned an invalid blob size") from error
    if size < 0:
        raise ReleaseEvidenceError("Git returned a negative blob size")
    return size


def git_blob(repo_root: Path, object_name: str, size: int) -> bytes:
    return _bounded_git(
        repo_root, ("cat-file", "blob", object_name), stdout_limit=size
    )
