"""Windows nativeのlocal file保護境界。

このmoduleはWindowsでだけ呼び出す。locale依存のcommand出力をparseせず、Windows APIから
volume、owner、DACL、ACEを検査する。判定不能な状態は安全と推測せず例外にする。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


class WindowsSecurityError(RuntimeError):
    """Windowsの保護契約を作成・確認できない。"""


_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000

_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_ERROR_INSUFFICIENT_BUFFER = 122

_ACCESS_ALLOWED_ACE_TYPE = 0x00
_OBJECT_INHERIT_ACE = 0x01
_CONTAINER_INHERIT_ACE = 0x02
_INHERIT_ONLY_ACE = 0x08
_INHERITED_ACE = 0x10
_GENERIC_ALL = 0x10000000
_FILE_ALL_ACCESS = 0x001F01FF

_SET_ACCESS = 2
_NO_MULTIPLE_TRUSTEE = 0
_TRUSTEE_IS_SID = 0
_TRUSTEE_IS_UNKNOWN = 0

_DRIVE_FIXED = 3
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"


class _ACL(ctypes.Structure):
    _fields_ = [
        ("AclRevision", wintypes.BYTE),
        ("Sbz1", wintypes.BYTE),
        ("AclSize", wintypes.WORD),
        ("AceCount", wintypes.WORD),
        ("Sbz2", wintypes.WORD),
    ]


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
        ("AceSize", wintypes.WORD),
    ]


class _ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", _ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class _TRUSTEE_W(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", ctypes.c_void_p),
        ("MultipleTrusteeOperation", wintypes.DWORD),
        ("TrusteeForm", wintypes.DWORD),
        ("TrusteeType", wintypes.DWORD),
        ("ptstrName", ctypes.c_void_p),
    ]


class _EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [
        ("grfAccessPermissions", wintypes.DWORD),
        ("grfAccessMode", wintypes.DWORD),
        ("grfInheritance", wintypes.DWORD),
        ("Trustee", _TRUSTEE_W),
    ]


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER_STRUCT(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


def _libraries() -> tuple[Any, Any]:
    if os.name != "nt":
        raise WindowsSecurityError("Windows security APIs are unavailable")
    # typeshedはhost OSに応じてWindows専用属性を隠すため、Mac上のstrict mypyでも検査できる形で取る。
    loader = vars(ctypes)["WinDLL"]
    advapi32 = loader("advapi32", use_last_error=True)
    kernel32 = loader("kernel32", use_last_error=True)

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetFileAttributesW.restype = wintypes.DWORD
    kernel32.GetVolumePathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    kernel32.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.QueryDosDeviceW.restype = wintypes.DWORD

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.SetEntriesInAclW.argtypes = [
        wintypes.ULONG,
        ctypes.POINTER(_EXPLICIT_ACCESS_W),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    return advapi32, kernel32


def _win_error(operation: str, code: int | None = None) -> WindowsSecurityError:
    number = int(vars(ctypes)["get_last_error"]()) if code is None else code
    return WindowsSecurityError(f"{operation} failed with Windows error {number}")


def _sid_to_string(sid: ctypes.c_void_p) -> str:
    advapi32, kernel32 = _libraries()
    rendered = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(rendered)):
        raise _win_error("SID rendering")
    try:
        value = rendered.value
        if value is None:
            raise WindowsSecurityError("SID rendering returned an empty value")
        return value
    finally:
        kernel32.LocalFree(rendered)


def _string_to_sid(value: str) -> ctypes.c_void_p:
    advapi32, _ = _libraries()
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(value, ctypes.byref(sid)):
        raise _win_error("SID parsing")
    return sid


def current_user_sid() -> str:
    """process tokenのcurrent user SIDを文字列で返す。"""
    advapi32, kernel32 = _libraries()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _win_error("process token inspection")
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        if (
            int(vars(ctypes)["get_last_error"]()) != _ERROR_INSUFFICIENT_BUFFER
            or required.value == 0
        ):
            raise _win_error("current user SID sizing")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_USER,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise _win_error("current user SID inspection")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER_STRUCT)).contents
        return _sid_to_string(ctypes.c_void_p(token_user.User.Sid))
    finally:
        kernel32.CloseHandle(token)


def _get_attributes(path: Path) -> int:
    _, kernel32 = _libraries()
    attributes = int(kernel32.GetFileAttributesW(str(path)))
    if attributes == _INVALID_FILE_ATTRIBUTES:
        raise _win_error("file attribute inspection")
    return attributes


def require_no_reparse_points(path: Path) -> None:
    """既存pathとその親にreparse pointがないことを要求する。"""
    candidate = path.absolute()
    while candidate.parent != candidate:
        if candidate.exists() and _get_attributes(candidate) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise WindowsSecurityError("managed path must not contain a reparse point")
        candidate = candidate.parent


def require_local_fixed_ntfs(path: Path) -> None:
    """pathがlocal fixed NTFS volume上にあることを要求する。"""
    _, kernel32 = _libraries()
    absolute = path.absolute()
    if absolute.drive.startswith("\\\\"):
        raise WindowsSecurityError("managed path must not use a UNC path")
    existing = absolute
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    drive = existing.drive
    if len(drive) == 2 and drive[1] == ":":
        mapping = ctypes.create_unicode_buffer(32768)
        if not kernel32.QueryDosDeviceW(drive, mapping, len(mapping)):
            raise _win_error("drive mapping inspection")
        if mapping.value.startswith("\\??\\"):
            raise WindowsSecurityError("managed path must not use a substituted drive")
    volume = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetVolumePathNameW(str(existing), volume, len(volume)):
        raise _win_error("volume path inspection")
    if int(kernel32.GetDriveTypeW(volume.value)) != _DRIVE_FIXED:
        raise WindowsSecurityError("managed path must be on a local fixed drive")

    filesystem = ctypes.create_unicode_buffer(256)
    if not kernel32.GetVolumeInformationW(
        volume.value,
        None,
        0,
        None,
        None,
        None,
        filesystem,
        len(filesystem),
    ):
        raise _win_error("filesystem inspection")
    if filesystem.value.upper() != "NTFS":
        raise WindowsSecurityError("managed path must be on NTFS")

    require_no_reparse_points(path)


def apply_private_dacl(path: Path, *, directory: bool) -> None:
    """ADR-0021のprotected DACLを既存pathへ設定する。"""
    advapi32, kernel32 = _libraries()
    sid_values = (current_user_sid(), _SYSTEM_SID, _ADMINISTRATORS_SID)
    sid_buffers = [_string_to_sid(value) for value in sid_values]
    entries = (_EXPLICIT_ACCESS_W * len(sid_buffers))()
    inheritance = _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE if directory else 0
    acl = ctypes.c_void_p()
    try:
        for index, sid in enumerate(sid_buffers):
            entries[index].grfAccessPermissions = _GENERIC_ALL
            entries[index].grfAccessMode = _SET_ACCESS
            entries[index].grfInheritance = inheritance
            entries[index].Trustee = _TRUSTEE_W(
                None,
                _NO_MULTIPLE_TRUSTEE,
                _TRUSTEE_IS_SID,
                _TRUSTEE_IS_UNKNOWN,
                sid,
            )
        result = int(
            advapi32.SetEntriesInAclW(
                len(entries), entries, None, ctypes.byref(acl)
            )
        )
        if result != 0:
            raise _win_error("private DACL construction", result)
        result = int(
            advapi32.SetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                acl,
                None,
            )
        )
        if result != 0:
            raise _win_error("private DACL installation", result)
    finally:
        if acl:
            kernel32.LocalFree(acl)
        for sid in sid_buffers:
            kernel32.LocalFree(sid)


def require_private_dacl(path: Path, *, directory: bool) -> None:
    """ownerとDACLがADR-0021のcontractへ一致することを検査する。"""
    advapi32, kernel32 = _libraries()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = int(
        advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if result != 0:
        raise _win_error("owner and DACL inspection", result)
    try:
        if not owner or _sid_to_string(owner) != current_user_sid():
            raise WindowsSecurityError("managed path must be owned by the current user")
        if not dacl:
            raise WindowsSecurityError("managed path must not have a NULL DACL")

        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise _win_error("DACL control inspection")
        if not control.value & _SE_DACL_PROTECTED:
            raise WindowsSecurityError("managed path DACL must be protected from inheritance")

        expected = {current_user_sid(), _SYSTEM_SID, _ADMINISTRATORS_SID}
        direct: set[str] = set()
        inheritable: set[str] = set()
        acl_header = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents
        for index in range(acl_header.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise _win_error("DACL ACE inspection")
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
            if ace.Header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                raise WindowsSecurityError("managed path DACL contains an unsupported ACE")
            if ace.Header.AceFlags & _INHERITED_ACE:
                raise WindowsSecurityError("managed path DACL must not contain inherited ACEs")
            if ace_pointer.value is None:
                raise WindowsSecurityError("DACL ACE inspection returned an empty value")
            sid_address = ace_pointer.value + _ACCESS_ALLOWED_ACE.SidStart.offset
            sid = _sid_to_string(ctypes.c_void_p(sid_address))
            if sid not in expected:
                raise WindowsSecurityError("managed path DACL contains an unexpected principal")
            mask = int(ace.Mask)
            if not (mask & _GENERIC_ALL or mask & _FILE_ALL_ACCESS == _FILE_ALL_ACCESS):
                raise WindowsSecurityError("managed path principal must have Full Control")

            flags = ace.Header.AceFlags & (
                _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE | _INHERIT_ONLY_ACE
            )
            if flags == 0 and sid not in direct:
                direct.add(sid)
            elif (
                directory
                and flags
                == _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE | _INHERIT_ONLY_ACE
                and sid not in inheritable
            ):
                inheritable.add(sid)
            else:
                raise WindowsSecurityError("managed path DACL has unsafe inheritance flags")
        if direct != expected or (directory and inheritable != expected):
            raise WindowsSecurityError("managed path DACL is missing a required principal")
        if not directory and inheritable:
            raise WindowsSecurityError("managed file DACL must not inherit to children")
    finally:
        if descriptor:
            kernel32.LocalFree(descriptor)


def secure_path(path: Path, *, directory: bool) -> None:
    """DACLを適用し、適用後のcontractを同じ実装で確認する。"""
    apply_private_dacl(path, directory=directory)
    require_private_dacl(path, directory=directory)
