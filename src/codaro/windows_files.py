"""Windows source operations with pinned, non-reparse parent handles.

Use Win32 sharing modes to prevent replacing ancestor directories while editing.
File identities, hard links and current bytes are checked before committing.
Imported on Windows only; POSIX retains the descriptor-relative implementation.
"""

from __future__ import annotations

import ctypes
import os
import secrets
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path

from codaro.repository import MAX_FILE_BYTES, RepositoryError


class FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD),
        ("created", w.FILETIME),
        ("accessed", w.FILETIME),
        ("written", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


def api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        w.LPVOID,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
    kernel.GetFileInformationByHandle.restype = w.BOOL
    kernel.ReplaceFileW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, w.LPVOID, w.LPVOID]
    kernel.ReplaceFileW.restype = w.BOOL
    kernel.MoveFileExW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.DWORD]
    kernel.MoveFileExW.restype = w.BOOL
    return kernel


@contextmanager
def handle(path, *, directory=False):
    kernel = api()
    # Ancestors cannot be renamed/deleted while their handles are held. Source
    # files allow deletion for ReplaceFileW, but concurrent writers are denied.
    share = 3 if directory else 5
    value = kernel.CreateFileW(
        str(path), 0x80000000, share, None, 3, 0x00200000 | (0x02000000 if directory else 0), None
    )
    if value == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileInfo()
        if not kernel.GetFileInformationByHandle(value, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise RepositoryError("Links, junctions e arquivos especiais não são permitidos.")
        if not directory and info.links != 1:
            raise RepositoryError("Arquivo não pode ter hard links.")
        yield value, info
    finally:
        kernel.CloseHandle(value)


@contextmanager
def parents(root, relative, *, create=False):
    from contextlib import ExitStack

    with ExitStack() as stack:
        current = Path(root)
        # Pin the whole ancestry, including the project root.
        for parent in reversed([current, *current.parents]):
            stack.enter_context(handle(parent, directory=True))
        for component in relative.parts[:-1]:
            current /= component
            if create:
                current.mkdir(exist_ok=True)
            stack.enter_context(handle(current, directory=True))
        yield current / relative.name


def read(repository, path):
    import msvcrt

    relative = repository._relative(path)
    with parents(repository.root, relative) as target, handle(target) as (native, info):
        if (info.size_high << 32 | info.size_low) > MAX_FILE_BYTES:
            raise RepositoryError("Arquivo maior que 512 KB.")
        # Duplicate ownership: the context remains responsible for the native handle.
        duplicate = w.HANDLE()
        kernel = api()
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.DuplicateHandle.argtypes = [
            w.HANDLE,
            w.HANDLE,
            w.HANDLE,
            ctypes.POINTER(w.HANDLE),
            w.DWORD,
            w.BOOL,
            w.DWORD,
        ]
        process = kernel.GetCurrentProcess()
        if not kernel.DuplicateHandle(
            process, native, process, ctypes.byref(duplicate), 0, False, 2
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES or b"\0" in data:
            raise RepositoryError("Arquivo binário ou maior que 512 KB.")
        return data


def apply(repository, proposal):
    from contextlib import nullcontext

    relative = repository._relative(repository.root / proposal.path)
    repository.resolve_destination(proposal.path)
    with parents(repository.root, relative, create=not proposal.before_exists) as target:
        guard = handle(target) if proposal.before_exists else nullcontext()
        with guard:
            if proposal.before_exists:
                if repository.read_bytes(target) != proposal.before:
                    raise RepositoryError("Arquivo mudou após a proposta.")
            elif target.exists():
                raise RepositoryError("Destino já existe; criação bloqueada.")
            if not proposal.after_exists:
                target.unlink()
                proposal.state = "applied"
                return
            temporary = target.parent / (".codaro-edit-" + secrets.token_hex(12))
            try:
                with temporary.open("xb") as stream:
                    stream.write(proposal.after)
                    stream.flush()
                    os.fsync(stream.fileno())
                if proposal.before_exists:
                    if repository.read_bytes(target) != proposal.before:
                        raise RepositoryError("Arquivo mudou durante a aplicação.")
                    success = api().ReplaceFileW(str(target), str(temporary), None, 0, None, None)
                else:
                    # No REPLACE_EXISTING: a concurrent create must fail, never overwrite.
                    success = api().MoveFileExW(str(temporary), str(target), 8)
                if not success:
                    raise ctypes.WinError(ctypes.get_last_error())
                proposal.state = "applied"
            finally:
                temporary.unlink(missing_ok=True)
