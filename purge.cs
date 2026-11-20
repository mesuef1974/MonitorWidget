using System; using System.IO; using System.Diagnostics; using System.Runtime.InteropServices;
public static class P {
  [DllImport("ntdll.dll")] static extern int NtSetSystemInformation(int cls, ref int info, int len);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr h, uint a, out IntPtr t);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool LookupPrivilegeValue(string s, string n, out long luid);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool AdjustTokenPrivileges(IntPtr t, bool d, ref TP n, int l, IntPtr p, IntPtr r);
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  [StructLayout(LayoutKind.Sequential, Pack=1)] struct TP { public int Count; public long Luid; public int Attr; }
  [StructLayout(LayoutKind.Sequential)] struct MS { public uint l, load; public ulong tp, ap, tv, av, a, b, c; }
  [DllImport("kernel32.dll")] static extern bool GlobalMemoryStatusEx(ref MS m);
  static void Priv(string n){ IntPtr t; if(!OpenProcessToken(GetCurrentProcess(),0x28,out t)) return; TP p=new TP(); p.Count=1; p.Attr=2; if(!LookupPrivilegeValue(null,n,out p.Luid)) return; AdjustTokenPrivileges(t,false,ref p,0,IntPtr.Zero,IntPtr.Zero); }
  static int Cmd(int c){ int v=c; return NtSetSystemInformation(80, ref v, 4); }
  static MS Mem(){ MS m=new MS(); m.l=(uint)Marshal.SizeOf(typeof(MS)); GlobalMemoryStatusEx(ref m); return m; }
  // Item 24 of the 19/11 council: one mode only. Purging all standby pages or every working set throws away cache that Windows
  // counts as available anyway and makes it re-read from disk, so the deep and standby modes were removed. The first argument
  // (the old mode) is ignored; the second is the result path, fixed by the task. No file next to the exe is trusted.
  static string ResultPath(string[] a){ return a!=null && a.Length>1 && a[1].Length>0 ? a[1] : Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"purge_result.json"); }
  public static int Main(string[] a){
    Stopwatch sw=Stopwatch.StartNew();
    MS b=Mem(); Priv("SeProfileSingleProcessPrivilege");
    int l=Cmd(5);                     // light: purge only low-priority standby pages; active working sets untouched
    long t1=sw.ElapsedMilliseconds;
    System.Threading.Thread.Sleep(150); MS af=Mem();
    string json="{\"ts\":\""+DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss")+"\",\"mode\":\"light\",\"beforePct\":"+b.load+",\"afterPct\":"+af.load
      +",\"freedMB\":"+(((long)af.ap-(long)b.ap)/1048576)+",\"ms\":"+sw.ElapsedMilliseconds
      +",\"codes\":{\"purgeLow\":"+l+"},\"t\":["+t1+"]}";
    File.WriteAllText(ResultPath(a), json);
    return 0; }
}
