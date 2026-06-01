"""CELL-23: Windows process sandbox using Job Objects for memory limits."""
import sys
import logging

log = logging.getLogger(__name__)

# Only import ctypes on Windows
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    
    # --- Windows API Definitions ---
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    
    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            # Basic limit info fields (JOBOBJECT_BASIC_LIMIT_INFORMATION)
            ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
            ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
            ('LimitFlags', wintypes.DWORD),
            ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t),
            ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ctypes.c_size_t),
            ('PriorityClass', wintypes.DWORD),
            ('SchedulingClass', wintypes.DWORD),
            
            # Extended limit info fields
            ('IoInfo', ctypes.c_size_t * 6), # 6 ULONGLONGs (IO_COUNTERS)
            ('ProcessMemoryLimit', ctypes.c_size_t),
            ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]
        
    JobObjectExtendedLimitInformation = 9
    
    # Flags
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    
    def apply_memory_limit(pid: int, limit_bytes: int) -> None:
        """Create a Job Object, set a memory limit, and assign the process to it."""
        try:
            # 1. Create a new Job Object
            hJob = kernel32.CreateJobObjectW(None, None)
            if not hJob:
                log.warning("win_sandbox: CreateJobObject failed, err=%s", ctypes.get_last_error())
                return
                
            # 2. Configure Limits (Memory limit + Kill children if parent dies)
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            limits.ProcessMemoryLimit = limit_bytes
            
            res = kernel32.SetInformationJobObject(
                hJob,
                JobObjectExtendedLimitInformation,
                ctypes.byref(limits),
                ctypes.sizeof(limits)
            )
            
            if not res:
                log.warning("win_sandbox: SetInformationJobObject failed, err=%s", ctypes.get_last_error())
                kernel32.CloseHandle(hJob)
                return
                
            # 3. Open the target process
            hProcess = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
            if not hProcess:
                log.warning("win_sandbox: OpenProcess failed for pid %d, err=%s", pid, ctypes.get_last_error())
                kernel32.CloseHandle(hJob)
                return
                
            # 4. Assign the process to the Job
            res = kernel32.AssignProcessToJobObject(hJob, hProcess)
            if not res:
                # If the process is already in a job, this will fail. That's usually OK.
                log.debug("win_sandbox: AssignProcessToJobObject failed for pid %d, err=%s", pid, ctypes.get_last_error())
                
            # Note: We intentionally DO NOT close hJob here. 
            # We want the Job handle to live as long as this Python process lives,
            # so the limits remain enforced. If this Python process dies, Windows 
            # automatically closes the handle and the KILL_ON_JOB_CLOSE flag takes down the children.
            
            kernel32.CloseHandle(hProcess)
            
        except Exception:
            log.exception("win_sandbox: unexpected error enforcing memory limit on pid %d", pid)
else:
    def apply_memory_limit(pid: int, limit_bytes: int) -> None:
        pass
