"""Failure-diagnosis-only tooling for the M4 leaf-alias contract test.

Every function here runs exclusively on the failure path of
``test_platform_short_file_leaf_alias_is_rejected`` in
``test_m4_clone_contract.py``: when Windows accepts an 8.3 leaf alias the
module should have rejected, these probes print what the platform and the
module actually reported so the failure can be diagnosed from CI output alone.

This module assigns no format meaning. It asserts nothing, decides nothing, and
its output is only ever attached to an ``assert*`` message. It is never
imported by production code, and no production behavior depends on it.

The ``ctypes``/``ctypes.wintypes`` imports stay inside the functions because
``ctypes.wintypes`` is unimportable off Windows, and this module must remain
importable on every host that runs the suite's discovery.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable


def native_path_probe(entry: str, path: object) -> dict:
    """Round-trip one kernel32 path converter without judging the answer."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = getattr(kernel32, entry)
    query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    query.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    ctypes.set_last_error(0)
    characters = query(str(path), buffer, len(buffer))
    error = ctypes.get_last_error()
    return {
        "input": str(path),
        "characters": int(characters),
        "value": buffer.value if characters else "",
        "leaf": os.path.basename(buffer.value) if characters else "",
        "last_error": int(error),
    }


def volume_information(path: object) -> dict:
    """Report the filesystem carrying ``path`` so alias support is visible."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GetVolumeInformationW
    query.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    query.restype = wintypes.BOOL
    drive = os.path.splitdrive(str(path))[0]
    root = f"{drive}\\" if drive else str(path)
    label = ctypes.create_unicode_buffer(261)
    system = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    component = wintypes.DWORD()
    flags = wintypes.DWORD()
    ctypes.set_last_error(0)
    accepted = query(
        root,
        label,
        len(label),
        ctypes.byref(serial),
        ctypes.byref(component),
        ctypes.byref(flags),
        system,
        len(system),
    )
    return {
        "root": root,
        "accepted": bool(accepted),
        "filesystem": system.value,
        "label": label.value,
        "serial": serial.value,
        "maximum_component": component.value,
        "flags": hex(flags.value),
        "last_error": int(ctypes.get_last_error()),
    }


def short_name_policy(path: object) -> dict:
    """Read the 8.3 creation policy; GetVolumeInformationW omits it.

    ``fsutil 8dot3name query`` is read-only but may require elevation, so
    every outcome including access denial is returned as evidence text.
    """
    drive = os.path.splitdrive(str(path))[0]
    report: dict = {}
    targets = [("registry", []), ("volume", [f"{drive}\\"] if drive else [])]
    targets.append(("directory", [str(path)]))
    for label, arguments in targets:
        if not arguments and label != "registry":
            report[label] = "no drive component"
            continue
        try:
            query = subprocess.run(
                ["fsutil.exe", "8dot3name", "query", *arguments],
                text=True,
                capture_output=True,
                check=False,
            )
        except Exception as error:  # elevation, absence, anything else
            report[label] = f"error={error!r}"
            continue
        report[label] = {
            "returncode": query.returncode,
            "stdout": query.stdout,
            "stderr": query.stderr,
        }
    return report


def module_alias_probe(
    path: object,
    long_path: object,
    *,
    run_ps: Callable[[str], subprocess.CompletedProcess],
    ps_quote: Callable[[object], str],
) -> str:
    """Print what the module computes for ``path`` without asserting.

    ``long_path`` is the known long-named file the alias stands for. It is
    queried alongside the alias so the FindFirstFileW answers for an alias
    pattern and for a long pattern can be compared side by side.

    ``run_ps`` and ``ps_quote`` are supplied by the calling test so this module
    never needs to know where the PowerShell module under test lives.
    """
    quoted = ps_quote(path)
    body = (
        f"$supplied={quoted};"
        f"$known={ps_quote(long_path)};"
        "$canonical=$null;"
        "$expandleaf=$null;"
        "Write-Output ('psversion=' + $PSVersionTable.PSVersion.ToString());"
        "Write-Output ('supplied=' + $supplied);"
        "Write-Output ('supplied_leaf=' + [IO.Path]::GetFileName($supplied));"
        "try{Write-Output ('getfullpath=' + [IO.Path]::GetFullPath($supplied))}"
        "catch{Write-Output ('getfullpath_error=' + $_.Exception.Message)};"
        "try{Write-Output ('file_exists=' + [IO.File]::Exists($supplied))}"
        "catch{Write-Output ('file_exists_error=' + $_.Exception.Message)};"
        "try{$canonical=Get-M4CloneLocalFullPath -Path $supplied "
        "-Label 'Diagnostic';"
        "Write-Output ('localfullpath=' + $canonical)}"
        "catch{Write-Output ('localfullpath_error=' + $_.Exception.Message)};"
        "try{$expandleaf=Get-M4CloneLocalFullPath -Path $supplied "
        "-Label 'Diagnostic' -ExpandLeafAlias;"
        "Write-Output ('localfullpath_expandleaf=' + $expandleaf)}"
        "catch{Write-Output ("
        "'localfullpath_expandleaf_error=' + $_.Exception.Message)};"
        "Write-Output ('known_long_path=' + $known);"
        "try{Write-Output ('longpath_supplied=' + "
        "(Get-M4CloneLongPathString -Path $supplied "
        "-Failure 'diagnostic supplied expansion failed'))}"
        "catch{Write-Output ("
        "'longpath_supplied_error=' + $_.Exception.Message)};"
        "if($null -ne $canonical){"
        "try{Write-Output ('longpath_canonical=' + "
        "(Get-M4CloneLongPathString -Path $canonical "
        "-Failure 'diagnostic canonical expansion failed'))}"
        "catch{Write-Output ("
        "'longpath_canonical_error=' + $_.Exception.Message)};"
        "try{Assert-M4CloneCanonicalExistingLeaf -Path $canonical "
        "-Label 'Diagnostic';"
        "Write-Output 'assert_canonical_leaf=accepted'}"
        "catch{Write-Output ("
        "'assert_canonical_leaf_threw=' + $_.Exception.Message)}};"
        # The alias pattern, every path the module derived from it, and the
        # known long pattern are all queried so an alias-pattern answer can
        # be compared against a long-pattern answer for the same file.
        "$candidates=@($supplied,$canonical,$expandleaf,$known,"
        r"('\\?\' + $supplied),('\\?\' + $known));"
        "$probes=@();"
        "foreach($candidate in $candidates){"
        "if([string]::IsNullOrEmpty($candidate)){continue};"
        "$seen=$false;"
        "foreach($existing in $probes){"
        "if($existing.Equals("
        "$candidate,[StringComparison]::OrdinalIgnoreCase)){$seen=$true}};"
        "if(-not $seen){$probes+=$candidate}};"
        "foreach($probe in $probes){"
        "$data=New-Object M4Clone.FindData;"
        "$search=$null;"
        "try{$search=[M4Clone.NativeMethods]::FindFirstFile("
        "$probe,[ref]$data);"
        "if($null -eq $search -or $search.IsInvalid){"
        "Write-Output ('find|' + $probe + '|invalid|' + "
        "[Runtime.InteropServices.Marshal]::GetLastWin32Error())}"
        "else{Write-Output ('find|' + $probe + '|cFileName=' + "
        "[string]$data.FileName + '|cAlternateFileName=' + "
        "[string]$data.AlternateFileName)}}"
        "catch{Write-Output ('find|' + $probe + '|error|' + "
        "$_.Exception.Message)}"
        "finally{if($null -ne $search){$search.Dispose()}}}"
    )
    try:
        probe = run_ps(body)
    except Exception as error:  # diagnostics must never mask the assertion
        return f"probe_launch_error={error!r}"
    return (
        f"probe_returncode={probe.returncode!r}\n"
        f"probe_stdout={probe.stdout!r}\n"
        f"probe_stderr={probe.stderr!r}"
    )


def alias_rejection_evidence(
    *,
    root: Path,
    source: Path,
    short_source: Path,
    aliased_leaf: Path,
    result: subprocess.CompletedProcess,
    run_ps: Callable[[str], subprocess.CompletedProcess],
    ps_quote: Callable[[object], str],
) -> str:
    """Build failure-path-only evidence for a leaf alias that was accepted."""
    import platform
    import sys

    lines = ["short 8.3 leaf alias was not rejected; evidence follows"]

    def record(label: str, producer) -> None:
        try:
            lines.append(f"{label}={producer()!r}")
        except Exception as error:  # never let evidence mask the failure
            lines.append(f"{label}_error={error!r}")

    record("python_version", lambda: sys.version)
    record("platform", platform.platform)
    record("root", lambda: str(root))
    record("source", lambda: str(source))
    record("short_source", lambda: str(short_source))
    record("aliased_leaf", lambda: str(aliased_leaf))
    record("aliased_leaf_exists", lambda: os.path.exists(str(aliased_leaf)))
    record(
        "samefile",
        lambda: os.path.samefile(str(source), str(aliased_leaf)),
    )
    record("listdir", lambda: sorted(os.listdir(str(root))))
    record(
        "long_of_aliased_leaf",
        lambda: native_path_probe("GetLongPathNameW", aliased_leaf),
    )
    record(
        "long_of_source",
        lambda: native_path_probe("GetLongPathNameW", source),
    )
    record(
        "long_of_root",
        lambda: native_path_probe("GetLongPathNameW", root),
    )
    record(
        "short_of_source",
        lambda: native_path_probe("GetShortPathNameW", source),
    )
    record(
        "short_of_aliased_leaf",
        lambda: native_path_probe("GetShortPathNameW", aliased_leaf),
    )
    record("volume", lambda: volume_information(root))
    record("short_name_policy", lambda: short_name_policy(root))
    record(
        "dir_slash_x",
        lambda: subprocess.run(
            ["cmd.exe", "/c", "dir", "/x", str(root)],
            text=True,
            capture_output=True,
            check=False,
        ).stdout,
    )
    lines.append(
        module_alias_probe(
            aliased_leaf, source, run_ps=run_ps, ps_quote=ps_quote
        )
    )
    lines.append(f"clone_returncode={result.returncode!r}")
    lines.append(f"clone_stdout={result.stdout!r}")
    lines.append(f"clone_stderr={result.stderr!r}")
    return "\n".join(lines)
