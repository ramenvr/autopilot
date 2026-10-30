// The agent service, started by the plugin (docs/protocol.md, "Started by the plugin"): Autopilot finds the `autopilot`
// command (the window's "Agent service command" setting, else `autopilot` on PATH, else where the installer puts it),
// starts `autopilot serve --port 0 --exit-with <this editor's pid>` with no window, its output appended to
// Autopilot/logs/agent-service-<time>.log, and waits for it to say where it listens in Autopilot/brain.json. One
// already announced and alive is used instead: several editors share one service, and it ends with the editor that
// started it (the next connect then starts another). The installer the package carries (AgentService~/, put there by
// tools/package_release.py; Unity skips folders ending in ~) installs the command once per computer, and again when the
// one it installed (Autopilot/agent-service.json) is another than the package carries (AgentService~/agent-service.json).
//
// A package that carries its agent service (a download: AgentService~/ with the installer and an agent-service.json
// that names a commit or sha256) always runs that one, unless the "Agent service command" setting says otherwise: the
// command is the installer's (`autopilot` on PATH is not used), and one running from another commit (brain.json's
// "commit") is stopped and replaced, once per editor session (Decide). A package without one (a repository checkout)
// does as before: the setting, PATH, the installer's place, and whatever runs is used.
//
// Nothing here waits on the editor's main thread: processes are started and polled from EditorApplication.update.
// Their output goes to the log through the shell (cmd.exe /c ... >> log, sh's exec ... >> log) rather than through
// pipes this editor reads, so a script reload (which ends every managed reader) never stalls or breaks them; what is
// going is kept across reloads in SessionState.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using UnityEditor;
using UnityEngine;
using Debug = UnityEngine.Debug;

namespace Autopilot.Playtest.Editor
{
    [InitializeOnLoad]
    public static class AgentService
    {
        public enum Phase
        {
            /// <summary>Not running here, nothing going.</summary>
            Idle,
            /// <summary>One is announced and alive (Url).</summary>
            Running,
            /// <summary>Started; waiting for it to announce itself.</summary>
            Starting,
            Installing,
            /// <summary>No command found; the package carries the installer.</summary>
            NotInstalled,
            /// <summary>No command found and no installer (a repository checkout).</summary>
            NoInstaller,
            InstallFailed,
            /// <summary>It stopped before it announced itself, or couldn't be started (Problem, LogPath).</summary>
            Failed,
        }

        /// <summary>How to start the agent service: a command line prefix, ready for the shell ("serve ..." goes after
        /// it).</summary>
        public sealed class Command
        {
            public string Text;
            /// <summary>setting | path | installed</summary>
            public string Source;
            /// <summary>The executable found (path, installed); null for the setting's command line.</summary>
            public string Exe;
            public override string ToString() => Text;
        }

        public const double StartTimeout = 30.0;
        public const double PollSeconds = 0.25;
        public const int KeepLogs = 5;
        public const string ServiceLogPrefix = "agent-service";
        public const string InstallLogPrefix = "install";
        public const string InstallerFolder = "AgentService~";
        /// <summary>Which agent service: beside the installer (the package's), and in Autopilot/ (the installed one's,
        /// kept by the installer).</summary>
        public const string StampFile = "agent-service.json";

        const string StateKey = "Autopilot.AgentService";
        const string StartedKey = "Autopilot.AgentService.StartedUrl";
        const string UpdateTriedKey = "Autopilot.AgentService.UpdateTried";  // (once per editor session)
        const string UpdatingKey = "Autopilot.AgentService.Updating";  // (the install going is an update)
        const string ReplaceTriedKey = "Autopilot.AgentService.ReplaceTried";  // (once per editor session)

        public static Phase Current { get; private set; } = Phase.Idle;
        /// <summary>The address it listens on (Running).</summary>
        public static string Url { get; private set; } = "";
        /// <summary>What went wrong, in plain words (Failed, InstallFailed).</summary>
        public static string Problem { get; private set; } = "";
        /// <summary>The log of the last start or install.</summary>
        public static string LogPath { get; private set; } = "";
        /// <summary>Changes whenever any of the above does.</summary>
        public static int Version { get; private set; }
        /// <summary>The one running is the one this editor started (not one started elsewhere: a terminal, another
        /// editor).</summary>
        public static bool StartedHere => Current == Phase.Running && Url.Length > 0 && Url == SessionState.GetString(StartedKey, "");
        /// <summary>It became reachable (a start finished, or one was found running).</summary>
        public static event Action Ready;

        static int pid;           // the process started (the shell that runs it)
        static Process process;   // the same, while this script domain lives
        static double since;      // when it was started (editor time)
        static double nextPoll;

        static AgentService()
        {
            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += Save;
            Restore();
        }

        // -- finding, and the command lines (pure: the tests give their own folders and PATH) ----------------------

        static bool IsWindows => RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        static string Home => Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

        /// <summary>Where the `autopilot` command is, in order: the setting's command line (as written), `autopilot` on
        /// PATH, the installer's place (%USERPROFILE%\.local\bin\autopilot.exe, ~/.local/bin/autopilot). Null when none.</summary>
        public static Command Find(string setting, string pathEnv, string home, bool windows, Func<string, bool> exists)
            => Find(setting, pathEnv, home, windows, exists, false);

        /// <summary>As Find; <paramref name="carried"/>: the package carries its own agent service, so PATH is skipped
        /// (the setting, else only the installer's place).</summary>
        public static Command Find(string setting, string pathEnv, string home, bool windows, Func<string, bool> exists, bool carried)
        {
            if (!string.IsNullOrWhiteSpace(setting)) return new Command { Text = setting.Trim(), Source = "setting" };
            var names = windows ? new[] { "autopilot.exe", "autopilot.cmd", "autopilot.bat" } : new[] { "autopilot" };
            foreach (var dir in carried ? new string[0] : (pathEnv ?? "").Split(windows ? ';' : ':'))
            {
                var d = dir.Trim().Trim('"');
                if (d.Length == 0) continue;
                foreach (var name in names)
                {
                    var full = Join(d, name, windows);
                    if (exists(full)) return new Command { Text = QuoteExe(full, windows), Source = "path", Exe = full };
                }
            }
            if (!string.IsNullOrEmpty(home))
            {
                var installed = InstalledExe(home, windows);
                if (exists(installed)) return new Command { Text = QuoteExe(installed, windows), Source = "installed", Exe = installed };
            }
            return null;
        }

        /// <summary>Where the installer puts the command: %USERPROFILE%\.local\bin\autopilot.exe, ~/.local/bin/autopilot.</summary>
        public static string InstalledExe(string home, bool windows)
        {
            return windows ? Join(home, @".local\bin\autopilot.exe", true) : Join(home, ".local/bin/autopilot", false);
        }

        /// <summary>Whether the installer the package carries should run before the agent service starts: the package
        /// says which agent service it carries (AgentService~/agent-service.json, <paramref name="shippedJson"/>), the
        /// installer kept which one it installed (Autopilot/agent-service.json, <paramref name="installedJson"/>;
        /// null when there is none), they differ, and the command found is the installer's own (never the setting's,
        /// nor another `autopilot` on PATH: those are someone's own).</summary>
        public static bool NeedsUpdate(string shippedJson, string installedJson, Command command, string home, bool windows)
        {
            var shipped = Sha256Of(shippedJson);
            if (shipped.Length == 0 || command == null || command.Source == "setting" || string.IsNullOrEmpty(command.Exe)
                || string.IsNullOrEmpty(home))
                return false;
            var ours = InstalledExe(home, windows);
            var same = windows
                ? string.Equals(command.Exe.Replace('/', '\\'), ours.Replace('/', '\\'), StringComparison.OrdinalIgnoreCase)
                : command.Exe == ours;
            return same && !string.Equals(Sha256Of(installedJson), shipped, StringComparison.OrdinalIgnoreCase);
        }

        static string Sha256Of(string json) => Field(json, "sha256");

        /// <summary>A string field of a JSON object ("" when absent, not a string, or the text unreadable).</summary>
        static string Field(string json, string key)
        {
            try
            {
                if (string.IsNullOrEmpty(json) || !(Json.Parse(json) is Dictionary<string, object> o)) return "";
                return o.TryGetValue(key, out var v) && v is string s ? s.Trim() : "";
            }
            catch (Exception)
            {
                return "";  // (unreadable: none)
            }
        }

        // -- which agent service: the decisions (pure: the tests give their own folders, files and processes) ----------

        /// <summary>Whether the package carries its own agent service: the installer is there, and its
        /// agent-service.json (<paramref name="shippedJson"/>) names a commit or a sha256.</summary>
        public static bool Carries(string shippedJson, bool hasInstaller)
        {
            return hasInstaller && (Field(shippedJson, "commit").Length > 0 || Sha256Of(shippedJson).Length > 0);
        }

        /// <summary>Whether the agent service announced (brain.json's text) is the one the package carries: the same
        /// "commit". One that names none (older than announcing it, or a repository checkout's "") isn't. When the package
        /// doesn't say its commit there is nothing to tell them apart by: it counts as the same.</summary>
        public static bool SameCommit(string shippedJson, string brainJson)
        {
            var shipped = Field(shippedJson, "commit");
            if (shipped.Length == 0) return true;
            return string.Equals(Field(brainJson, "commit"), shipped, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>What Start does next.</summary>
        public enum Next
        {
            /// <summary>Use the one announced and running.</summary>
            UseRunning,
            /// <summary>Use the one running although it is older: replacing it was tried this session already.</summary>
            KeepOlder,
            /// <summary>Stop the older one running (its pid), then go on as if none ran.</summary>
            Replace,
            /// <summary>Start the command found.</summary>
            Start,
            /// <summary>Install the package's own (the installer's place is empty, though `autopilot` is on PATH), then start it.</summary>
            Install,
            /// <summary>Install the package's own over another the installer put in, then start it (that one if it fails).</summary>
            Update,
            /// <summary>Nothing installed; the package carries the installer (the window offers Install).</summary>
            NotInstalled,
            /// <summary>Nothing found and no installer (a repository checkout).</summary>
            NoInstaller,
        }

        /// <summary>Everything Decide looks at.</summary>
        public sealed class Situation
        {
            /// <summary>The "Agent service command" setting.</summary>
            public string Setting = "";
            public string PathEnv = "";
            public string Home = "";
            public bool Windows;
            public Func<string, bool> Exists = _ => false;
            /// <summary>The package's AgentService~/agent-service.json (null: none).</summary>
            public string Shipped;
            /// <summary>The package has AgentService~/ with its installer.</summary>
            public bool HasInstaller;
            /// <summary>The installer's Autopilot/agent-service.json (null: none).</summary>
            public string Installed;
            /// <summary>brain.json's text (null: none).</summary>
            public string Announcement;
            /// <summary>Whether a process runs.</summary>
            public Func<int, bool> Alive = _ => false;
            /// <summary>This editor is connected to an agent service (left alone).</summary>
            public bool Connected;
            /// <summary>Once per editor session each.</summary>
            public bool ReplaceTried, UpdateTried;
        }

        /// <summary>
        /// Which agent service to use or start. One announced and running is used, unless the package carries its own
        /// (Carries), no setting names a command, this editor isn't connected, replacing wasn't tried this session, and
        /// it runs another commit (SameCommit): then it is replaced (<paramref name="announcedPid"/>). Else the command:
        /// the setting's; for a package that carries its own only the installer's place (installed first when empty
        /// although PATH has one, or updated when the installer put in another than the package carries); otherwise
        /// PATH, then the installer's place, as before.
        /// </summary>
        public static Next Decide(Situation s, out Command command, out int announcedPid)
        {
            command = null;
            var carried = Carries(s.Shipped, s.HasInstaller);
            var setting = !string.IsNullOrWhiteSpace(s.Setting);
            var url = Aura.ParseAnnouncement(s.Announcement, out announcedPid);
            if (url != null && s.Alive(announcedPid))
            {
                if (!carried || setting || s.Connected || SameCommit(s.Shipped, s.Announcement)) return Next.UseRunning;
                return s.ReplaceTried ? Next.KeepOlder : Next.Replace;
            }
            announcedPid = 0;
            command = Find(s.Setting, s.PathEnv, s.Home, s.Windows, s.Exists, carried);
            if (command == null)
            {
                if (carried && !s.UpdateTried && Find(s.Setting, s.PathEnv, s.Home, s.Windows, s.Exists, false) != null) return Next.Install;
                return s.HasInstaller ? Next.NotInstalled : Next.NoInstaller;
            }
            if (s.HasInstaller && !s.UpdateTried && NeedsUpdate(s.Shipped, s.Installed, command, s.Home, s.Windows)) return Next.Update;
            return Next.Start;
        }

        /// <summary>Whether a process of this name (or path) may be an agent service, so may be stopped: autopilot, or
        /// the Python it runs under (python, pythonw, python3, python3.N; .exe or not). Anything else is never stopped.</summary>
        public static bool IsAgentServiceExe(string nameOrPath)
        {
            if (string.IsNullOrWhiteSpace(nameOrPath)) return false;
            var name = nameOrPath.Trim();
            var slash = Math.Max(name.LastIndexOf('/'), name.LastIndexOf('\\'));
            if (slash >= 0) name = name.Substring(slash + 1);
            name = name.ToLowerInvariant();
            if (name.EndsWith(".exe", StringComparison.Ordinal)) name = name.Substring(0, name.Length - 4);
            return name == "autopilot" || name == "python" || name == "pythonw" || name == "python3"
                   || System.Text.RegularExpressions.Regex.IsMatch(name, @"^python3\.\d+$");
        }

        /// <summary>How long after brain.json's "started" the process it names may have started and still count as the
        /// one that wrote it (clocks, rounding).</summary>
        public const double OwnerSlack = 5.0;

        /// <summary>When brain.json says the agent service announced itself ("started": seconds since the epoch); null
        /// when it doesn't say, or the text can't be read.</summary>
        public static double? AnnouncedAt(string brainJson)
        {
            try
            {
                if (!(Json.Parse(brainJson ?? "") is Dictionary<string, object> o) || !o.TryGetValue("started", out var v) || v == null) return null;
                if (v is string || v is bool) return null;
                var at = Convert.ToDouble(v, System.Globalization.CultureInfo.InvariantCulture);
                return double.IsNaN(at) || double.IsInfinity(at) || at <= 0 ? (double?)null : at;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// Whether the process brain.json names may be stopped: its executable is an agent service's (IsAgentServiceExe),
        /// and it is the process that wrote the announcement: it started (<paramref name="processStartUtc"/>) no later
        /// than brain.json's "started" + OwnerSlack. A process id reused after the agent service ended belongs to a
        /// process started after that. Not when "started" is missing or unreadable, or the start time couldn't be read
        /// (null): then it is left alone, with <paramref name="why"/>.
        /// </summary>
        public static bool MayStop(string processName, DateTime? processStartUtc, string brainJson, out string why)
        {
            why = "";
            if (!IsAgentServiceExe(processName))
            {
                why = $"its process is {processName}, not an agent service";
                return false;
            }
            var announced = AnnouncedAt(brainJson);
            if (announced == null)
            {
                why = "brain.json doesn't say when it started";
                return false;
            }
            if (processStartUtc == null)
            {
                why = "its process's start time can't be read";
                return false;
            }
            var startedAt = (processStartUtc.Value.ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
            if (startedAt > announced.Value + OwnerSlack)
            {
                why = "its process started after brain.json was written: another process now has that id";
                return false;
            }
            return true;
        }

        /// <summary>Whether brain.json's text names the process <paramref name="pid"/>.</summary>
        public static bool NamesPid(string brainJson, int pid)
        {
            try
            {
                return pid > 0 && Json.Parse(brainJson ?? "") is Dictionary<string, object> o && (int)Json.Num(o, "pid", 0) == pid;
            }
            catch (Exception)
            {
                return false;
            }
        }

        static string Join(string dir, string name, bool windows)
        {
            return windows ? dir.TrimEnd('\\', '/') + "\\" + name : dir.TrimEnd('/') + "/" + name;
        }

        static string QuoteExe(string path, bool windows) => windows ? "\"" + path + "\"" : ShQuote(path);

        /// <summary>The command found on this computer now, from the settings (null: not installed).</summary>
        public static Command FindHere()
        {
            return Find(PlaytestSettings.Get().ServiceCommand, Environment.GetEnvironmentVariable("PATH"), Home, IsWindows, File.Exists,
                Carries(ShippedStamp(), HasInstaller));
        }

        /// <summary>The situation on this computer now (Decide's input).</summary>
        static Situation Here()
        {
            var client = EditorHost.Client;
            return new Situation
            {
                Setting = PlaytestSettings.Get().ServiceCommand,
                PathEnv = Environment.GetEnvironmentVariable("PATH"),
                Home = Home,
                Windows = IsWindows,
                Exists = File.Exists,
                Shipped = ShippedStamp(),
                HasInstaller = HasInstaller,
                Installed = ReadText(Path.Combine(Aura.Folder(), StampFile)),
                Announcement = ReadBrainJson(out var text) ? text : null,
                Alive = Aura.Running,
                Connected = client != null && client.Connected,
                ReplaceTried = SessionState.GetBool(ReplaceTriedKey, false),
                UpdateTried = SessionState.GetBool(UpdateTriedKey, false),
            };
        }

        /// <summary>The package's AgentService~/agent-service.json (null: none).</summary>
        static string ShippedStamp()
        {
            var dir = InstallerDir();
            return dir == null ? null : ReadText(Path.Combine(dir, StampFile));
        }

        /// <summary>The agent service's command line: serve on a free port, ending with the editor.</summary>
        public static string ServeLine(Command command, int editorPid)
        {
            return $"{command.Text} serve --port 0 --exit-with {editorPid}";
        }

        /// <summary>The installer's command line: powershell on Windows, sh elsewhere.</summary>
        public static string InstallLine(string installerDir, bool windows)
        {
            return windows
                ? "powershell -NoProfile -ExecutionPolicy Bypass -File \"" + Join(installerDir, "install.ps1", true) + "\""
                : "sh " + ShQuote(installerDir.TrimEnd('/') + "/install.sh");
        }

        /// <summary>The process to start for a command line whose output is appended to <paramref name="log"/>:
        /// on Windows cmd.exe /d /s /c "line >> "log" 2>&1"; elsewhere /bin/sh running <paramref name="script"/>,
        /// whose text is UnixScript (written first by the caller).</summary>
        public static (string file, string args) Redirected(string line, string log, bool windows, string script = null)
        {
            if (windows) return ("cmd.exe", "/d /s /c \"" + line + " >> \"" + log + "\" 2>&1\"");
            return ("/bin/sh", "\"" + script + "\"");
        }

        /// <summary>The sh script that runs a command line in place (exec: the same process), its output appended to
        /// the log.</summary>
        public static string UnixScript(string line, string log)
        {
            return "#!/bin/sh\n# Written by Autopilot: runs the agent service or its installer, its output to the log\nexec " + line + " >> " + ShQuote(log) + " 2>&1\n";
        }

        /// <summary>Single quotes for sh (a ' inside written as '\'').</summary>
        public static string ShQuote(string s) => "'" + (s ?? "").Replace("'", "'\\''") + "'";

        /// <summary>A log's name: prefix-yyyyMMdd-HHmmss.log.</summary>
        public static string LogName(string prefix, DateTime when) => $"{prefix}-{when:yyyyMMdd-HHmmss}.log";

        /// <summary>Delete all but the <paramref name="keep"/> newest prefix-*.log in dir (their names sort by time).</summary>
        public static void PruneLogs(string dir, string prefix, int keep)
        {
            try
            {
                if (!Directory.Exists(dir)) return;
                var old = Directory.GetFiles(dir, prefix + "-*.log")
                    .Where(f => IsStamped(Path.GetFileName(f), prefix))
                    .OrderByDescending(f => Path.GetFileName(f), StringComparer.Ordinal)
                    .Skip(Math.Max(0, keep));
                foreach (var f in old)
                {
                    try
                    {
                        File.Delete(f);
                    }
                    catch (Exception)
                    {
                        // (open elsewhere: next time)
                    }
                }
            }
            catch (Exception)
            {
                // (the folder can't be listed: nothing pruned)
            }
        }

        static bool IsStamped(string name, string prefix)
        {
            // prefix-yyyyMMdd-HHmmss.log, nothing else (another prefix that starts the same, e.g. "install-agent")
            var rest = name.Substring(prefix.Length + 1);
            return rest.Length == "yyyyMMdd-HHmmss.log".Length && rest[8] == '-' && rest.EndsWith(".log", StringComparison.Ordinal)
                   && rest.Take(8).All(char.IsDigit) && rest.Skip(9).Take(6).All(char.IsDigit);
        }

        /// <summary>The url brain.json names when the process it names is <paramref name="startedPid"/> or one it
        /// started (on Windows the started shell, autopilot.exe's launcher and the venv's python.exe each start the next).
        /// Null otherwise, or when that process has ended.</summary>
        public static string AnnouncedBy(string brainJson, int startedPid, Func<int, int> parentOf, Func<int, bool> alive)
        {
            var url = Aura.ParseAnnouncement(brainJson, out var announced);
            if (url == null || !alive(announced)) return null;
            return Descends(announced, startedPid, parentOf) ? url : null;
        }

        /// <summary>Whether <paramref name="pid"/> is <paramref name="ancestor"/> or one of its descendants (a few
        /// levels at most).</summary>
        public static bool Descends(int pid, int ancestor, Func<int, int> parentOf, int depth = 6)
        {
            if (ancestor <= 0) return false;
            for (var i = 0; i <= depth && pid > 0; ++i)
            {
                if (pid == ancestor) return true;
                var parent = parentOf(pid);
                if (parent == pid) return false;
                pid = parent;
            }
            return false;
        }

        // -- the folders --------------------------------------------------------------------------------------

        public static string LogsFolder => Path.Combine(Aura.Folder(), "logs");

        /// <summary>The package's AgentService~ folder with an installer in it; null in a repository checkout.</summary>
        public static string InstallerDir()
        {
            try
            {
                if (packageRoot == null)
                {
                    var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(AgentService).Assembly);
                    packageRoot = info == null || string.IsNullOrEmpty(info.resolvedPath) ? "" : info.resolvedPath;
                }
                if (packageRoot.Length == 0) return null;
                var dir = Path.Combine(packageRoot, InstallerFolder);
                return File.Exists(Path.Combine(dir, IsWindows ? "install.ps1" : "install.sh")) ? dir : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        static string packageRoot;  // (the package's folder: the same until scripts reload)

        public static bool HasInstaller => InstallerDir() != null;

        // -- what the window starts -------------------------------------------------------------------------------

        /// <summary>Whether connecting starts it: Use with Aura is off (the Aura app runs its own), the URL is left at
        /// the default (one on this machine), and not in batch mode.</summary>
        public static bool StartsWithConnect(PlaytestSettings s)
        {
            return !s.UseWithAura && !Application.isBatchMode
                   && (string.IsNullOrEmpty(s.Url) || s.Url == PlaytestClient.DefaultUrl);
        }

        /// <summary>The one announced in brain.json and alive, now (null: none).</summary>
        public static string RunningUrl() => Aura.AnnouncedUrl(Aura.OwnBrainFile);

        /// <summary>Make sure it runs: use the one announced (replacing it first when it is older than the one the
        /// package carries, see Decide), else start it (when found, installing the package's first when needed), else
        /// say it isn't installed. Never starts a second while one is starting or announced.</summary>
        public static void Start()
        {
            if (Current == Phase.Starting || Current == Phase.Installing) return;
            if (Uninstall.Holds) return;  // (Autopilot is being removed from this computer, or was: Uninstall.cs)
            var next = Decide(Here(), out var command, out var announced);
            switch (next)
            {
                case Next.UseRunning:
                case Next.KeepOlder:
                {
                    var url = RunningUrl();
                    if (url == null) break;  // (it ended meanwhile: as if none ran)
                    if (next == Next.KeepOlder && !keptOlderLogged)
                    {
                        keptOlderLogged = true;
                        Debug.Log($"[Autopilot] An older agent service is running (pid {announced}) and replacing it was tried already: using it");
                    }
                    SetRunning(url);
                    return;
                }
                case Next.Replace:
                    SessionState.SetBool(ReplaceTriedKey, true);
                    if (!StopOlder(announced, out var why))
                    {
                        Debug.Log($"[Autopilot] An older agent service is running (pid {announced}), not replaced ({why}): using it");
                        var url = RunningUrl();
                        if (url != null)
                        {
                            SetRunning(url);
                            return;
                        }
                        break;  // (it ended meanwhile)
                    }
                    Debug.Log($"[Autopilot] An older agent service was running (pid {announced}): replacing it with this plugin's");
                    replacing = announced;
                    since = EditorApplication.timeSinceStartup;
                    nextPoll = since + PollSeconds;
                    pid = 0;
                    Problem = "";
                    Set(Phase.Starting);  // (TickStarting waits for it to end, then starts this package's)
                    if (!Aura.Running(replacing)) Replaced();
                    return;
                default:
                    Begin(next, command);
                    return;
            }
            // (the one announced ended meanwhile: decide again without it)
            Begin(Decide(Here(), out command, out _), command);
        }

        /// <summary>Start or install, as Decide said (nothing running to use).</summary>
        static void Begin(Next next, Command command)
        {
            switch (next)
            {
                case Next.Install:
                    // (the installer's place is empty: this package's goes in, rather than `autopilot` on PATH; once a session)
                    Debug.Log("[Autopilot] Installing this plugin's agent service (an autopilot command on PATH isn't used while the plugin carries its own)");
                    SessionState.SetBool(UpdateTriedKey, true);
                    Install();
                    return;
                case Next.Update:
                    // (an older agent service from an earlier download: this package's goes in first, once a session)
                    Debug.Log("[Autopilot] The agent service installed is older than this plugin's: installing this one");
                    SessionState.SetBool(UpdateTriedKey, true);
                    Install(true);
                    return;
                case Next.Start when command != null:
                    Spawn(ServeLine(command, Process.GetCurrentProcess().Id), ServiceLogPrefix, Phase.Starting);
                    return;
                case Next.NoInstaller:
                    Set(Phase.NoInstaller);
                    return;
                default:
                    Set(HasInstaller ? Phase.NotInstalled : Phase.NoInstaller);
                    return;
            }
        }

        static bool keptOlderLogged;
        static int replacing;  // (the older agent service stopped: Starting waits for it to end)
        public const double ReplaceTimeout = 5.0;

        /// <summary>Stop the older agent service <paramref name="olderPid"/>: only when it still runs, its executable is an
        /// agent service's and it is the process that wrote brain.json (MayStop). False, with why, when it wasn't stopped.</summary>
        static bool StopOlder(int olderPid, out string why)
        {
            why = "";
            try
            {
                using (var p = Process.GetProcessById(olderPid))
                {
                    if (p.HasExited) return true;
                    DateTime? started = null;
                    try
                    {
                        started = p.StartTime.ToUniversalTime();
                    }
                    catch (Exception)
                    {
                        // (not ours to ask: left alone)
                    }
                    var brain = ReadBrainJson(out var text) ? text : null;
                    if (!NamesPid(brain, olderPid))
                    {
                        why = "brain.json no longer names it";
                        return false;
                    }
                    if (!MayStop(p.ProcessName, started, brain, out why)) return false;
                    p.Kill();
                    try
                    {
                        p.WaitForExit(1000);  // (usually at once; else TickStarting waits on)
                    }
                    catch (Exception)
                    {
                        // (TickStarting waits on)
                    }
                    return true;
                }
            }
            catch (ArgumentException)
            {
                return true;  // (no such process: it ended already)
            }
            catch (Exception e)
            {
                why = "stopping it failed: " + e.Message;
                return false;
            }
        }

        /// <summary>The older one ended: its brain.json goes (when it still names it), then start as if none ran.</summary>
        static void Replaced()
        {
            var gone = replacing;
            replacing = 0;
            try
            {
                if (ReadBrainJson(out var text) && NamesPid(text, gone)) File.Delete(Path.Combine(Aura.Folder(), Aura.OwnBrainFile));
            }
            catch (Exception e)
            {
                Debug.LogWarning("[Autopilot] Couldn't remove the older agent service's brain.json: " + e.Message);
            }
            Set(Phase.Idle);
            Start();
        }

        /// <summary>Run the installer the package carries, then start it.</summary>
        public static void Install() => Install(false);

        /// <summary>As Install(); update: an agent service is installed already (an older one): when the install fails,
        /// that one starts instead.</summary>
        static void Install(bool update)
        {
            if (Current == Phase.Starting || Current == Phase.Installing) return;
            if (Uninstall.Holds) return;
            var dir = InstallerDir();
            if (dir == null)
            {
                Set(Phase.NoInstaller);
                return;
            }
            SessionState.SetBool(UpdatingKey, update);
            Spawn(InstallLine(dir, IsWindows), InstallLogPrefix, Phase.Installing);
            if (update && Current == Phase.InstallFailed) UpdateFailed();
        }

        /// <summary>The update didn't install: the agent service installed already starts (it works, only older).</summary>
        static void UpdateFailed()
        {
            SessionState.SetBool(UpdatingKey, false);
            Debug.LogWarning($"[Autopilot] Couldn't update the agent service (see {LogPath}): starting the one installed");
            Problem = "";
            Set(Phase.Idle);
            Start();
        }

        static string ReadText(string path)
        {
            try
            {
                return File.Exists(path) ? File.ReadAllText(path) : null;
            }
            catch (Exception)
            {
                return null;  // (unreadable: as if there were none)
            }
        }

        /// <summary>After a problem: install again when that failed, else start again.</summary>
        public static void TryAgain()
        {
            if (Current == Phase.InstallFailed) Install();
            else if (Current == Phase.Failed && Alive())
            {
                // (the one started is still there, unannounced: wait for it again rather than start a second)
                since = EditorApplication.timeSinceStartup;
                Problem = "";
                Set(Phase.Starting);
            }
            else
            {
                if (Current == Phase.Failed || Current == Phase.NotInstalled || Current == Phase.NoInstaller) Set(Phase.Idle);
                Start();
            }
        }

        /// <summary>Open the last log in the system's viewer.</summary>
        public static void OpenLog()
        {
            if (!string.IsNullOrEmpty(LogPath) && File.Exists(LogPath)) EditorUtility.OpenWithDefaultApp(LogPath);
            else if (Directory.Exists(LogsFolder)) EditorUtility.RevealInFinder(LogsFolder);
        }

        static void Spawn(string line, string logPrefix, Phase phase)
        {
            var windows = IsWindows;
            string log = "";
            try
            {
                Directory.CreateDirectory(LogsFolder);
                PruneLogs(LogsFolder, logPrefix, KeepLogs - 1);
                log = Path.Combine(LogsFolder, LogName(logPrefix, DateTime.Now));
                File.AppendAllText(log, $"[Autopilot] {DateTime.Now:yyyy-MM-dd HH:mm:ss} {line}\n", new UTF8Encoding(false));
                string script = null;
                if (!windows)
                {
                    script = Path.Combine(LogsFolder, logPrefix + ".sh");
                    File.WriteAllText(script, UnixScript(line, log), new UTF8Encoding(false));
                }
                var (file, args) = Redirected(line, log, windows, script);
                var info = new ProcessStartInfo(file, args)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = Aura.Folder(),
                };
                // (Python writes to the log as it goes, in UTF-8 whatever the system's code page)
                info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                process = Process.Start(info);
                if (process == null) throw new InvalidOperationException("the process didn't start");
                pid = process.Id;
                since = EditorApplication.timeSinceStartup;
                nextPoll = since + PollSeconds;
                LogPath = log;
                Problem = "";
                Set(phase);
            }
            catch (Exception e)
            {
                process = null;
                pid = 0;
                LogPath = log;
                Problem = phase == Phase.Installing
                    ? $"Couldn’t install the agent service ({e.Message})"
                    : $"Couldn’t start the agent service ({e.Message})";
                Set(phase == Phase.Installing ? Phase.InstallFailed : Phase.Failed);
            }
        }

        // -- following it ------------------------------------------------------------------------------------------

        static void Set(Phase phase)
        {
            if (phase != Phase.Running) Url = "";
            Current = phase;
            ++Version;
        }

        static void SetRunning(string url)
        {
            var was = Current;
            Url = url;
            Current = Phase.Running;
            Problem = "";
            ++Version;
            if (was != Phase.Running) Ready?.Invoke();
        }

        static void Tick()
        {
            var now = EditorApplication.timeSinceStartup;
            if (now < nextPoll) return;
            var going = Current == Phase.Starting || Current == Phase.Installing;
            nextPoll = now + (going ? PollSeconds : 2.0);
            try
            {
                switch (Current)
                {
                    case Phase.Starting:
                        TickStarting(now);
                        break;
                    case Phase.Installing:
                        TickInstalling();
                        break;
                    default:
                    {
                        // Started or ended elsewhere (another editor, a terminal): follow brain.json. An older one that
                        // connecting replaces (Decide) doesn't count as running: the next connect (or the setup) starts ours
                        var url = RunningUrl();
                        if (url != null && Current != Phase.Running && StartsWithConnect(PlaytestSettings.Get())
                            && Decide(Here(), out _, out _) == Next.Replace)
                            url = null;
                        if (url != null && (Current != Phase.Running || url != Url)) SetRunning(url);
                        else if (url == null && Current == Phase.Running) Set(Phase.Idle);
                        break;
                    }
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning("[Autopilot] Following the agent service failed: " + e.Message);
            }
        }

        static void TickStarting(double now)
        {
            if (replacing > 0)
            {
                if (!Aura.Running(replacing))
                {
                    Replaced();
                    return;
                }
                if (now - since <= ReplaceTimeout) return;
                // (it didn't end: use what runs)
                Debug.Log($"[Autopilot] The older agent service (pid {replacing}) didn't stop: using it");
                replacing = 0;
                Set(Phase.Idle);
                Start();
                return;
            }
            var url = ReadBrainJson(out var text) ? AnnouncedBy(text, pid, ParentOf, Aura.Running) : null;
            if (url != null)
            {
                Forget();
                SessionState.SetString(StartedKey, url);
                SetRunning(url);
                return;
            }
            if (!Alive())
            {
                Forget();
                // (another editor's may have come up meanwhile: use it)
                var other = RunningUrl();
                if (other != null)
                {
                    SetRunning(other);
                    return;
                }
                Problem = $"The agent service stopped (see {LogPath})";
                Set(Phase.Failed);
                return;
            }
            if (now - since > StartTimeout)
            {
                // Left running (it may still come up: Try again waits for it rather than starting a second)
                Problem = $"The agent service didn’t start within {StartTimeout:0} s (see {LogPath})";
                Set(Phase.Failed);
            }
        }

        static void TickInstalling()
        {
            if (Alive()) return;
            var code = ExitCode();
            Forget();
            var update = SessionState.GetBool(UpdatingKey, false);
            if (update && code != 0 && code != null)
            {
                UpdateFailed();
                return;
            }
            SessionState.SetBool(UpdatingKey, false);
            if (code == 0 || (code == null && FindHere() != null))
            {
                Set(Phase.Idle);
                if (FindHere() != null)
                {
                    Start();
                    return;
                }
            }
            Problem = $"Couldn’t install the agent service (see {LogPath})";
            Set(Phase.InstallFailed);
        }

        static bool ReadBrainJson(out string text)
        {
            text = null;
            try
            {
                var path = Path.Combine(Aura.Folder(), Aura.OwnBrainFile);
                if (!File.Exists(path)) return false;
                text = File.ReadAllText(path);
                return true;
            }
            catch (Exception)
            {
                return false;  // (being replaced: next poll)
            }
        }

        static bool Alive()
        {
            if (pid <= 0) return false;
            try
            {
                if (process != null) return !process.HasExited;
            }
            catch (Exception)
            {
                // (no longer ours to ask)
            }
            return Aura.Running(pid);
        }

        static int? ExitCode()
        {
            try
            {
                return process != null && process.HasExited ? process.ExitCode : (int?)null;
            }
            catch (Exception)
            {
                return null;  // (started before a script reload: unknown)
            }
        }

        static void Forget()
        {
            try
            {
                process?.Dispose();
            }
            catch (Exception)
            {
                // (gone)
            }
            process = null;
            // (pid stays while Starting timed out: Try again goes on waiting for it)
        }

        // -- the process tree ----------------------------------------------------------------------------------------

        /// <summary>The process that started <paramref name="child"/> (0 when unknown).</summary>
        public static int ParentOf(int child)
        {
            try
            {
                if (IsWindows) return WindowsParents().TryGetValue(child, out var parent) ? parent : 0;
                var stat = $"/proc/{child}/stat";
                if (File.Exists(stat))
                {
                    // pid (comm) state ppid ...: after the last ')'
                    var text = File.ReadAllText(stat);
                    var fields = text.Substring(text.LastIndexOf(')') + 1).Trim().Split(' ');
                    return fields.Length > 1 && int.TryParse(fields[1], out var ppid) ? ppid : 0;
                }
            }
            catch (Exception)
            {
                // (unknown)
            }
            return 0;  // (macOS: sh's exec keeps the started process's id, so it matches without its parents)
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct ProcessEntry
        {
            public uint dwSize;
            public uint cntUsage;
            public uint th32ProcessID;
            public IntPtr th32DefaultHeapID;
            public uint th32ModuleID;
            public uint cntThreads;
            public uint th32ParentProcessID;
            public int pcPriClassBase;
            public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool Process32FirstW(IntPtr snapshot, ref ProcessEntry entry);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool Process32NextW(IntPtr snapshot, ref ProcessEntry entry);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        static Dictionary<int, int> parents;
        static double parentsAt = -1;

        static Dictionary<int, int> WindowsParents()
        {
            var now = EditorApplication.timeSinceStartup;
            if (parents != null && now - parentsAt < 0.2) return parents;
            var map = new Dictionary<int, int>();
            var snap = CreateToolhelp32Snapshot(0x2, 0);  // TH32CS_SNAPPROCESS
            if (snap == IntPtr.Zero || snap == new IntPtr(-1)) return map;
            try
            {
                var e = new ProcessEntry { dwSize = (uint)Marshal.SizeOf(typeof(ProcessEntry)) };
                for (var ok = Process32FirstW(snap, ref e); ok; ok = Process32NextW(snap, ref e))
                    map[(int)e.th32ProcessID] = (int)e.th32ParentProcessID;
            }
            finally
            {
                CloseHandle(snap);
            }
            parents = map;
            parentsAt = now;
            return map;
        }

        /// <summary>Tests: show a state without starting anything (the next start or poll replaces it).</summary>
        internal static void TestState(Phase phase, string url = "", string problem = "", string log = "")
        {
            Current = phase;
            Url = phase == Phase.Running ? url ?? "" : "";
            Problem = problem ?? "";
            LogPath = log ?? "";
            ++Version;
        }

        // -- across script reloads ----------------------------------------------------------------------------------

        static void Save()
        {
            if (Current != Phase.Starting && Current != Phase.Installing && (Current != Phase.Failed || pid <= 0))
            {
                SessionState.EraseString(StateKey);
                return;
            }
            SessionState.SetString(StateKey, Json.Write(new Dictionary<string, object>
            {
                ["phase"] = Current.ToString(),
                ["pid"] = pid,
                ["replacing"] = replacing,
                ["since"] = since,
                ["log"] = LogPath,
                ["problem"] = Problem,
            }));
        }

        static void Restore()
        {
            var saved = SessionState.GetString(StateKey, "");
            SessionState.EraseString(StateKey);
            if (string.IsNullOrEmpty(saved)) return;
            try
            {
                var o = Json.ParseObject(saved);
                if (!Enum.TryParse(Json.Str(o, "phase"), out Phase phase)) return;
                pid = (int)Json.Num(o, "pid", 0);
                replacing = (int)Json.Num(o, "replacing", 0);
                since = Json.Num(o, "since", EditorApplication.timeSinceStartup);
                LogPath = Json.Str(o, "log");
                Problem = Json.Str(o, "problem");
                Current = phase;
                ++Version;
            }
            catch (Exception)
            {
                // (unreadable: start afresh)
            }
        }
    }
}
