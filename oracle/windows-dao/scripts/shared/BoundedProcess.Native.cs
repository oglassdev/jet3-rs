using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public sealed class Jet3BoundedProcessLaunch : IDisposable
{
    private SafeWaitHandle job;
    private SafeWaitHandle process;
    private FileStream stdout;
    private FileStream stderr;
    private bool disposed;

    internal Jet3BoundedProcessLaunch(
        SafeWaitHandle job,
        SafeWaitHandle process,
        FileStream stdout,
        FileStream stderr
    )
    {
        this.job = job;
        this.process = process;
        this.stdout = stdout;
        this.stderr = stderr;
    }

    private void AssertOpen()
    {
        if (disposed)
        {
            throw new ObjectDisposedException("Jet3BoundedProcessLaunch");
        }
    }

    public Stream StandardOutput
    {
        get
        {
            AssertOpen();
            return stdout;
        }
    }

    public Stream StandardError
    {
        get
        {
            AssertOpen();
            return stderr;
        }
    }

    public bool HasExited
    {
        get
        {
            AssertOpen();
            return Jet3BoundedProcessJobNative.WaitForExit(process, 0);
        }
    }

    public bool WaitForExit(int milliseconds)
    {
        AssertOpen();
        return Jet3BoundedProcessJobNative.WaitForExit(
            process,
            milliseconds
        );
    }

    public int ExitCode
    {
        get
        {
            AssertOpen();
            return Jet3BoundedProcessJobNative.GetExitCode(process);
        }
    }

    public void TerminateOwnedJob()
    {
        AssertOpen();
        Jet3BoundedProcessJobNative.Terminate(job);
    }

    public UInt32 ActiveProcessCount()
    {
        AssertOpen();
        return Jet3BoundedProcessJobNative.ActiveProcessCount(job);
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }
        disposed = true;
        Exception first = null;
        try
        {
            stdout.Dispose();
        }
        catch (Exception error)
        {
            first = error;
        }
        try
        {
            stderr.Dispose();
        }
        catch (Exception error)
        {
            if (first == null)
            {
                first = error;
            }
        }
        process.Dispose();
        job.Dispose();
        if (first != null)
        {
            throw first;
        }
    }
}

public static class Jet3BoundedProcessJobNative
{
    private const uint JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectBasicAccountingInformation = 1;
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;
    private const uint CreateSuspended = 0x00000004;
    private const uint ExtendedStartupInfoPresent = 0x00080000;
    private const uint CreateNoWindow = 0x08000000;
    private const uint StartfUseStdHandles = 0x00000100;
    private const uint HandleFlagInherit = 0x00000001;
    private const long ProcThreadAttributeHandleList = 0x00020002;
    private const uint WaitObject0 = 0x00000000;
    private const uint WaitTimeout = 0x00000102;
    private const uint WaitFailed = 0xffffffff;
    private const uint StillActive = 259;

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes
    {
        public UInt32 Length;
        public IntPtr SecurityDescriptor;
        [MarshalAs(UnmanagedType.Bool)]
        public bool InheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo
    {
        public UInt32 Size;
        public string Reserved;
        public string Desktop;
        public string Title;
        public UInt32 X;
        public UInt32 Y;
        public UInt32 XSize;
        public UInt32 YSize;
        public UInt32 XCountChars;
        public UInt32 YCountChars;
        public UInt32 FillAttribute;
        public UInt32 Flags;
        public UInt16 ShowWindow;
        public UInt16 Reserved2;
        public IntPtr Reserved2Bytes;
        public IntPtr StandardInput;
        public IntPtr StandardOutput;
        public IntPtr StandardError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct StartupInfoEx
    {
        public StartupInfo StartupInfo;
        public IntPtr AttributeList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInformation
    {
        public IntPtr Process;
        public IntPtr Thread;
        public UInt32 ProcessId;
        public UInt32 ThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInformation
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInformation
    {
        public BasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicAccountingInformation
    {
        public Int64 TotalUserTime;
        public Int64 TotalKernelTime;
        public Int64 ThisPeriodTotalUserTime;
        public Int64 ThisPeriodTotalKernelTime;
        public UInt32 TotalPageFaultCount;
        public UInt32 TotalProcesses;
        public UInt32 ActiveProcesses;
        public UInt32 TotalTerminatedProcesses;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(
        IntPtr jobAttributes,
        string name
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        UInt32 informationClass,
        ref ExtendedLimitInformation information,
        UInt32 informationLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(
        IntPtr job,
        IntPtr process
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(
        IntPtr job,
        UInt32 exitCode
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(
        IntPtr job,
        UInt32 informationClass,
        out BasicAccountingInformation information,
        UInt32 informationLength,
        IntPtr returnLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CreatePipe(
        out IntPtr readPipe,
        out IntPtr writePipe,
        ref SecurityAttributes attributes,
        UInt32 size
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetHandleInformation(
        IntPtr handle,
        UInt32 mask,
        UInt32 flags
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool InitializeProcThreadAttributeList(
        IntPtr attributeList,
        int attributeCount,
        UInt32 flags,
        ref IntPtr size
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool UpdateProcThreadAttribute(
        IntPtr attributeList,
        UInt32 flags,
        IntPtr attribute,
        IntPtr value,
        IntPtr size,
        IntPtr previousValue,
        IntPtr returnSize
    );

    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(
        IntPtr attributeList
    );

    [DllImport(
        "kernel32.dll",
        CharSet = CharSet.Unicode,
        SetLastError = true
    )]
    private static extern bool CreateProcess(
        string applicationName,
        StringBuilder commandLine,
        IntPtr processAttributes,
        IntPtr threadAttributes,
        [MarshalAs(UnmanagedType.Bool)] bool inheritHandles,
        UInt32 creationFlags,
        IntPtr environment,
        string currentDirectory,
        ref StartupInfoEx startupInfo,
        out ProcessInformation processInformation
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern UInt32 ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(
        IntPtr process,
        UInt32 exitCode
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern UInt32 WaitForSingleObject(
        IntPtr handle,
        UInt32 milliseconds
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(
        IntPtr process,
        out UInt32 exitCode
    );

    private static void ThrowLastError(string operation)
    {
        throw new Win32Exception(
            Marshal.GetLastWin32Error(),
            operation + " failed"
        );
    }

    private static IntPtr CreateKillOnCloseJob()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
        {
            ThrowLastError("CreateJobObject");
        }
        ExtendedLimitInformation information =
            new ExtendedLimitInformation();
        information.BasicLimitInformation.LimitFlags =
            JobObjectLimitKillOnJobClose;
        if (!SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ref information,
            checked((UInt32)Marshal.SizeOf(
                typeof(ExtendedLimitInformation)
            ))
        ))
        {
            int error = Marshal.GetLastWin32Error();
            CloseHandle(job);
            throw new Win32Exception(
                error,
                "SetInformationJobObject failed"
            );
        }
        return job;
    }

    private static void AssignProcess(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
        {
            ThrowLastError("AssignProcessToJobObject");
        }
    }

    internal static void Terminate(SafeWaitHandle job)
    {
        if (!TerminateJobObject(job.DangerousGetHandle(), 1))
        {
            ThrowLastError("TerminateJobObject");
        }
    }

    internal static UInt32 ActiveProcessCount(SafeWaitHandle job)
    {
        BasicAccountingInformation information =
            new BasicAccountingInformation();
        if (!QueryInformationJobObject(
            job.DangerousGetHandle(),
            JobObjectBasicAccountingInformation,
            out information,
            checked((UInt32)Marshal.SizeOf(
                typeof(BasicAccountingInformation)
            )),
            IntPtr.Zero
        ))
        {
            ThrowLastError("QueryInformationJobObject");
        }
        return information.ActiveProcesses;
    }

    internal static bool WaitForExit(
        SafeWaitHandle process,
        int milliseconds
    )
    {
        UInt32 result = WaitForSingleObject(
            process.DangerousGetHandle(),
            checked((UInt32)milliseconds)
        );
        if (result == WaitObject0)
        {
            return true;
        }
        if (result == WaitTimeout)
        {
            return false;
        }
        if (result == WaitFailed)
        {
            ThrowLastError("WaitForSingleObject");
        }
        throw new InvalidOperationException(
            "WaitForSingleObject returned an unexpected status."
        );
    }

    internal static int GetExitCode(SafeWaitHandle process)
    {
        UInt32 exitCode;
        if (!GetExitCodeProcess(
            process.DangerousGetHandle(),
            out exitCode
        ))
        {
            ThrowLastError("GetExitCodeProcess");
        }
        if (exitCode == StillActive)
        {
            throw new InvalidOperationException(
                "The bounded process has not exited."
            );
        }
        return unchecked((int)exitCode);
    }

    private static void CloseIfPresent(ref IntPtr handle)
    {
        if (handle != IntPtr.Zero)
        {
            CloseHandle(handle);
            handle = IntPtr.Zero;
        }
    }

    private static void CreateInheritedPipe(
        out IntPtr readPipe,
        out IntPtr writePipe
    )
    {
        SecurityAttributes attributes = new SecurityAttributes();
        attributes.Length = checked(
            (UInt32)Marshal.SizeOf(typeof(SecurityAttributes))
        );
        attributes.InheritHandle = true;
        if (!CreatePipe(out readPipe, out writePipe, ref attributes, 0))
        {
            ThrowLastError("CreatePipe");
        }
    }

    private static IntPtr CreateRestrictedHandleList(
        IntPtr stdin,
        IntPtr stdout,
        IntPtr stderr,
        out IntPtr handles
    )
    {
        handles = IntPtr.Zero;
        IntPtr attributeBytes = IntPtr.Zero;
        InitializeProcThreadAttributeList(
            IntPtr.Zero,
            1,
            0,
            ref attributeBytes
        );
        if (attributeBytes == IntPtr.Zero)
        {
            ThrowLastError("InitializeProcThreadAttributeList(size)");
        }
        IntPtr attributeList = Marshal.AllocHGlobal(attributeBytes);
        bool initialized = false;
        try
        {
            if (!InitializeProcThreadAttributeList(
                attributeList,
                1,
                0,
                ref attributeBytes
            ))
            {
                ThrowLastError("InitializeProcThreadAttributeList");
            }
            initialized = true;
            handles = Marshal.AllocHGlobal(IntPtr.Size * 3);
            Marshal.WriteIntPtr(handles, 0, stdin);
            Marshal.WriteIntPtr(handles, IntPtr.Size, stdout);
            Marshal.WriteIntPtr(handles, IntPtr.Size * 2, stderr);
            if (!UpdateProcThreadAttribute(
                attributeList,
                0,
                new IntPtr(ProcThreadAttributeHandleList),
                handles,
                new IntPtr(IntPtr.Size * 3),
                IntPtr.Zero,
                IntPtr.Zero
            ))
            {
                ThrowLastError("UpdateProcThreadAttribute(handle list)");
            }
            return attributeList;
        }
        catch
        {
            if (initialized)
            {
                DeleteProcThreadAttributeList(attributeList);
            }
            Marshal.FreeHGlobal(attributeList);
            if (handles != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(handles);
                handles = IntPtr.Zero;
            }
            throw;
        }
    }

    public static Jet3BoundedProcessLaunch StartSuspendedInJob(
        string executable,
        string commandLine
    )
    {
        IntPtr job = IntPtr.Zero;
        IntPtr process = IntPtr.Zero;
        IntPtr thread = IntPtr.Zero;
        IntPtr stdoutRead = IntPtr.Zero;
        IntPtr stdoutWrite = IntPtr.Zero;
        IntPtr stderrRead = IntPtr.Zero;
        IntPtr stderrWrite = IntPtr.Zero;
        IntPtr stdinRead = IntPtr.Zero;
        IntPtr stdinWrite = IntPtr.Zero;
        IntPtr attributeList = IntPtr.Zero;
        IntPtr attributeHandles = IntPtr.Zero;
        SafeWaitHandle jobOwner = null;
        SafeWaitHandle processOwner = null;
        SafeFileHandle stdoutOwner = null;
        SafeFileHandle stderrOwner = null;
        FileStream stdout = null;
        FileStream stderr = null;
        bool returned = false;
        try
        {
            job = CreateKillOnCloseJob();
            CreateInheritedPipe(out stdoutRead, out stdoutWrite);
            CreateInheritedPipe(out stderrRead, out stderrWrite);
            CreateInheritedPipe(out stdinRead, out stdinWrite);
            if (!SetHandleInformation(
                stdoutRead,
                HandleFlagInherit,
                0
            ) || !SetHandleInformation(
                stderrRead,
                HandleFlagInherit,
                0
            ) || !SetHandleInformation(
                stdinWrite,
                HandleFlagInherit,
                0
            ))
            {
                ThrowLastError("SetHandleInformation");
            }
            attributeList = CreateRestrictedHandleList(
                stdinRead,
                stdoutWrite,
                stderrWrite,
                out attributeHandles
            );

            StartupInfoEx startup = new StartupInfoEx();
            startup.StartupInfo.Size = checked(
                (UInt32)Marshal.SizeOf(typeof(StartupInfoEx))
            );
            startup.StartupInfo.Flags = StartfUseStdHandles;
            startup.StartupInfo.StandardInput = stdinRead;
            startup.StartupInfo.StandardOutput = stdoutWrite;
            startup.StartupInfo.StandardError = stderrWrite;
            startup.AttributeList = attributeList;
            ProcessInformation information;
            if (!CreateProcess(
                executable,
                new StringBuilder(commandLine),
                IntPtr.Zero,
                IntPtr.Zero,
                true,
                CreateSuspended |
                    CreateNoWindow |
                    ExtendedStartupInfoPresent,
                IntPtr.Zero,
                null,
                ref startup,
                out information
            ))
            {
                ThrowLastError("CreateProcess");
            }
            process = information.Process;
            thread = information.Thread;
            CloseIfPresent(ref stdoutWrite);
            CloseIfPresent(ref stderrWrite);
            CloseIfPresent(ref stdinRead);
            CloseIfPresent(ref stdinWrite);

            AssignProcess(job, process);
            UInt32 resumeResult = ResumeThread(thread);
            if (resumeResult == UInt32.MaxValue)
            {
                ThrowLastError("ResumeThread");
            }
            CloseIfPresent(ref thread);

            jobOwner = new SafeWaitHandle(job, true);
            job = IntPtr.Zero;
            processOwner = new SafeWaitHandle(process, true);
            process = IntPtr.Zero;
            stdoutOwner = new SafeFileHandle(stdoutRead, true);
            stdoutRead = IntPtr.Zero;
            stderrOwner = new SafeFileHandle(stderrRead, true);
            stderrRead = IntPtr.Zero;
            stdout = new FileStream(
                stdoutOwner,
                FileAccess.Read,
                4096,
                false
            );
            stdoutOwner = null;
            stderr = new FileStream(
                stderrOwner,
                FileAccess.Read,
                4096,
                false
            );
            stderrOwner = null;
            Jet3BoundedProcessLaunch launch =
                new Jet3BoundedProcessLaunch(
                    jobOwner,
                    processOwner,
                    stdout,
                    stderr
                );
            jobOwner = null;
            processOwner = null;
            stdout = null;
            stderr = null;
            returned = true;
            return launch;
        }
        finally
        {
            if (!returned)
            {
                IntPtr ownedProcess = process;
                if (
                    ownedProcess == IntPtr.Zero &&
                    processOwner != null &&
                    !processOwner.IsInvalid
                )
                {
                    ownedProcess = processOwner.DangerousGetHandle();
                }
                if (ownedProcess != IntPtr.Zero)
                {
                    TerminateProcess(ownedProcess, 1);
                    WaitForSingleObject(ownedProcess, 5000);
                }
                if (stdout != null)
                {
                    stdout.Dispose();
                }
                if (stderr != null)
                {
                    stderr.Dispose();
                }
                if (stdoutOwner != null)
                {
                    stdoutOwner.Dispose();
                }
                if (stderrOwner != null)
                {
                    stderrOwner.Dispose();
                }
                if (processOwner != null)
                {
                    processOwner.Dispose();
                }
                if (jobOwner != null)
                {
                    jobOwner.Dispose();
                }
            }
            CloseIfPresent(ref thread);
            CloseIfPresent(ref process);
            CloseIfPresent(ref stdoutRead);
            CloseIfPresent(ref stdoutWrite);
            CloseIfPresent(ref stderrRead);
            CloseIfPresent(ref stderrWrite);
            CloseIfPresent(ref stdinRead);
            CloseIfPresent(ref stdinWrite);
            if (attributeList != IntPtr.Zero)
            {
                DeleteProcThreadAttributeList(attributeList);
                Marshal.FreeHGlobal(attributeList);
            }
            if (attributeHandles != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(attributeHandles);
            }
            CloseIfPresent(ref job);
        }
    }
}
