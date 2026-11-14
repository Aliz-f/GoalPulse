"""Local operator identity from the OS token, never a CLI/header owner claim."""

import os


def current_local_principal() -> str:
    if os.name != "nt":
        return "uid:" + str(os.geteuid())
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    token, size, sid_text = wintypes.HANDLE(), wintypes.DWORD(), wintypes.LPWSTR()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise PermissionError("Operator OS identity unavailable")
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise PermissionError("Operator OS identity unavailable")
        # TOKEN_USER begins with SID_AND_ATTRIBUTES; the first pointer is the SID.
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID)).contents.value
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise PermissionError("Operator OS identity unavailable")
        return "sid:" + sid_text.value
    finally:
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))
        if token:
            kernel.CloseHandle(token)


def require_local_operator(allowed: str, *, identity_reader=current_local_principal) -> str:
    identities = frozenset(part.strip() for part in allowed.split(",") if part.strip())
    if not identities:
        raise PermissionError("Operator identity required")
    try:
        principal = identity_reader()
    except Exception:
        raise PermissionError("Operator identity required") from None
    if principal not in identities:
        raise PermissionError("Operator identity required")
    return principal
