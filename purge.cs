using System; using System.IO; using System.Diagnostics; using System.Runtime.InteropServices;
public static class P {
  [DllImport("ntdll.dll")] static extern int NtSetSystemInformation(int cls, ref int info, int len);
  [DllImport("kernel32.dll")] static extern bool SetSystemFileCacheSize(IntPtr min, IntPtr max, uint flags);
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
  static long StandbyNormalMB(){ try { using(var pc=new System.Diagnostics.PerformanceCounter("Memory","Standby Cache Normal Priority Bytes")){ return (long)(pc.NextValue()/1048576); } } catch { return 0; } }
  // mode comes only from the fixed task argument (light|standby|deep); no file next to the exe is trusted
  static string Mode(string[] a){ string m = a!=null && a.Length>0 ? a[0].Trim().ToLowerInvariant() : "light"; return (m=="deep"||m=="standby") ? m : "light"; }
  static string ResultPath(string[] a){ return a!=null && a.Length>1 && a[1].Length>0 ? a[1] : Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"purge_result.json"); }
  public static int Main(string[] a){
    Stopwatch sw=Stopwatch.StartNew();
    string mode=Mode(a); bool deep = mode=="deep"; bool standby = mode=="standby";
    MS b=Mem(); Priv("SeProfileSingleProcessPrivilege"); Priv("SeIncreaseQuotaPrivilege");
    int e=-1, s=-1, l=-1;
    if(deep){ e=Cmd(2); s=Cmd(4); }   // deep: empty every working set + purge standby (manual only)
    else if(standby){ if(StandbyNormalMB()>=2048) s=Cmd(4); }   // standby: purge all standby only when normal-priority standby >= 2 GB
    else { l=Cmd(5); }                // light: purge only low-priority standby pages; active working sets untouched
    long t1=sw.ElapsedMilliseconds;
    System.Threading.Thread.Sleep(150); MS af=Mem();
    string json="{\"ts\":\""+DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss")+"\",\"mode\":\""+(deep?"deep":standby?"standby":"light")+"\",\"beforePct\":"+b.load+",\"afterPct\":"+af.load
      +",\"freedMB\":"+(((long)af.ap-(long)b.ap)/1048576)+",\"ms\":"+sw.ElapsedMilliseconds
      +",\"codes\":{\"emptyWS\":"+e+",\"purgeStandby\":"+s+",\"purgeLow\":"+l+"},\"t\":["+t1+"]}";
    File.WriteAllText(ResultPath(a), json);
    if(deep){ Cmd(5); Cmd(3); SetSystemFileCacheSize(new IntPtr(-1), new IntPtr(-1), 0); }   // housekeeping after the result is out
    return 0; }
}
