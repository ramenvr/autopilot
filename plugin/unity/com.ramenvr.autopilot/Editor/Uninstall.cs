// Uninstall Autopilot from this computer (the Settings page's Uninstall): the agent service's own
// `autopilot uninstall --yes [--keep-memory] --log <log>` (autopilot/uninstall.py) stops the agent services, sends the
// shared runs not sent yet, signs out of Aura, and removes the settings folder and the `autopilot` command; shared run
// data is never removed. It runs with no window from the system's temporary folder (Windows can't remove a folder that is a
// process's current one), its output appended to <temp>/autopilot-uninstall-<time>.log. When the command isn't on this
// computer, the settings folder's contents are removed here instead, but the Aura app's aura-brain.json (and memory/
// when Keep is checked).
//
// From Remove until Set up again (or the editor's restart) the window is in the removed state (Holds): nothing starts,
// installs or connects to the agent service, the first-run setup isn't shown, and the window shows how the removal went.
// What is going is kept across script reloads in SessionState.

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
    public static class Uninstall
    {
        public enum Phase
        {
            /// <summary>Not removing: the window as usual.</summary>
            None,
            /// <summary>`autopilot uninstall` (or the removal here) is going.</summary>
            Running,
            /// <summary>Removed (exit code 0).</summary>
            Removed,
            /// <summary>Removed but for what the log names (exit code 3).</summary>
            RemovedButSome,
            /// <summary>It failed, or couldn't start.</summary>
            Failed,
        }

        public const int KeptCode = 3;
        public const double Timeout = 600.0;  // (uploads of shared runs not sent yet can take a while)
        public const string LogPrefix = "autopilot-uninstall";
        public const string MemoryFolder = "memory";

        const string StateKey = "Autopilot.Uninstall";

        public static Phase Current { get; private set; } = Phase.None;
        /// <summary>The removal's log.</summary>
        public static string LogPath { get; private set; } = "";
        /// <summary>Keep what Autopilot learned about your games (the confirmation's Delete Autopilot's memories left
        /// unticked; Try again keeps it).</summary>
        public static bool KeepMemory { get; private set; } = true;
        /// <summary>Changes whenever any of the above does.</summary>
        public static int Version { get; private set; }

        /// <summary>The window is in the removed state: nothing starts, installs or connects to the agent service.</summary>
        public static bool Holds => Current != Phase.None;

        static Process process;  // (while this script domain lives)
        static int pid;
        static double since;
        static double nextPoll;

        static Uninstall()
        {
            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += Save;
            Restore();
        }

        static bool IsWindows => RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        // -- the command line and its result (pure: the tests give their own) ---------------------------------------

        /// <summary>The uninstall's command line: the agent service's command (as it is started: AgentService.Find)
        /// with `uninstall --yes [--keep-memory] --log "<log>"` after it.</summary>
        public static string Line(AgentService.Command command, bool keepMemory, string log, bool windows)
        {
            var quoted = windows ? "\"" + log + "\"" : AgentService.ShQuote(log);
            return $"{command.Text} uninstall --yes{(keepMemory ? " --keep-memory" : "")} --log {quoted}";
        }

        /// <summary>What an ended uninstall says: its exit code (0 removed, 3 removed but for what the log names), or,
        /// when it isn't known (started before a script reload), the last words autopilot/uninstall.py wrote in the log.</summary>
        public static Phase ResultOf(int? code, string logText)
        {
            if (code == 0) return Phase.Removed;
            if (code == KeptCode) return Phase.RemovedButSome;
            if (code != null) return Phase.Failed;
            var text = logText ?? "";
            if (text.Contains("Autopilot is removed from this computer, but for")) return Phase.RemovedButSome;
            if (text.Contains("Autopilot is removed from this computer.")) return Phase.Removed;
            return Phase.Failed;
        }

        /// <summary>
        /// The removal without the agent service (its command isn't on this computer): what is in <paramref name="folder"/>
        /// removed but aura-brain.json (the Aura app's) and, when <paramref name="keepMemory"/>, memory/, then the folder
        /// itself when nothing is left. Each line of what was done goes to <paramref name="say"/>. Returns whether
        /// something was kept that the user didn't ask to keep (a file in use).
        /// </summary>
        public static bool RemoveFolder(string folder, bool keepMemory, Action<string> say)
        {
            if (!Directory.Exists(folder)) return false;
            var kept = false;
            foreach (var path in Directory.GetFileSystemEntries(folder).OrderBy(p => p, StringComparer.Ordinal))
            {
                var name = Path.GetFileName(path);
                if (name == Aura.AuraBrainFile) continue;
                if (name == MemoryFolder && keepMemory)
                {
                    say($"Kept what Autopilot learned about your games in {path}");
                    continue;
                }
                if (!Remove(path))
                {
                    say($"Couldn't remove {path} (in use)");
                    kept = true;
                }
            }
            try
            {
                if (!Directory.EnumerateFileSystemEntries(folder).Any()) Directory.Delete(folder);
            }
            catch (Exception)
            {
                // (in use: it stays, empty)
            }
            say(Directory.Exists(folder) ? $"Removed what is in {folder}, but for what is said above" : $"Removed {folder}");
            return kept;
        }

        static bool Remove(string path)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    foreach (var file in Directory.GetFiles(path, "*", SearchOption.AllDirectories))
                        File.SetAttributes(file, FileAttributes.Normal);  // (read-only files can't be deleted as they are)
                    Directory.Delete(path, true);
                }
                else
                {
                    File.SetAttributes(path, FileAttributes.Normal);
                    File.Delete(path);
                }
            }
            catch (Exception)
            {
                // (in use)
            }
            return !Directory.Exists(path) && !File.Exists(path);
        }

        // -- removing -------------------------------------------------------------------------------------------

        /// <summary>What the package keeps about the runs in a project (the agent service's uninstall removes the same
        /// for every project it knows: autopilot/engines/unity.py plugin_files): each run's report files and recording,
        /// the codex export and its log, the run history. Returns the paths that couldn't be deleted.</summary>
        public static List<string> ForgetProject(string projectDir)
        {
            var failed = new List<string>();
            foreach (var rel in new[] { "Logs/Playtest", "Library/AutopilotCodex", "Logs/AutopilotCodexExport.log", "UserSettings/AutopilotRuns" })
            {
                var path = Path.Combine(projectDir, rel.Replace('/', Path.DirectorySeparatorChar));
                if ((File.Exists(path) || Directory.Exists(path)) && !Remove(path)) failed.Add(path);
            }
            return failed;
        }

        /// <summary>Remove Autopilot from this computer: the window goes into the removed state, leaves the agent
        /// service, and runs its uninstall (or, without it, removes the settings folder here).</summary>
        /// <summary>Tests: while set, the uninstall never runs for real. Only a command from the Agent service command
        /// setting inside the temporary folder (a stand-in) is run; the agent service on this computer, PATH's or the
        /// installed one, never is, nor is the settings folder emptied unless it is a temporary one
        /// (Aura.FolderOverride).</summary>
        internal static bool RealRunsBlocked;

        /// <summary>Whether a run with <paramref name="command"/> (null: emptying the settings folder here) is only a
        /// test's stand-in (always true outside tests).</summary>
        internal static bool AllowedNow(AgentService.Command command)
        {
            if (!RealRunsBlocked) return true;
            var temp = Path.GetFullPath(Path.GetTempPath());
            if (command == null) return Path.GetFullPath(Aura.Folder()).StartsWith(temp, StringComparison.OrdinalIgnoreCase);
            return command.Source == "setting" && command.Text.Trim().Trim('"').StartsWith(temp, StringComparison.OrdinalIgnoreCase);
        }

        public static void Start(bool keepMemory)
        {
            if (Current == Phase.Running) return;
            if (!AllowedNow(AgentService.FindHere()))
            {
                Debug.LogWarning("[Autopilot] The uninstall is blocked while tests run: nothing was removed");
                return;
            }
            KeepMemory = keepMemory;
            EditorHost.Disconnect(false);
            var log = Path.Combine(Path.GetTempPath(), AgentService.LogName(LogPrefix, DateTime.Now));
            LogPath = log;
            var command = AgentService.FindHere();
            if (command == null)
            {
                RemoveHere(log, keepMemory);
                return;
            }
            var line = Line(command, keepMemory, log, IsWindows);
            try
            {
                File.AppendAllText(log, $"[Autopilot] {DateTime.Now:yyyy-MM-dd HH:mm:ss} {line}\n", new UTF8Encoding(false));
                string script = null;
                if (!IsWindows)
                {
                    script = Path.Combine(Path.GetTempPath(), LogPrefix + ".sh");
                    File.WriteAllText(script, AgentService.UnixScript(line, log), new UTF8Encoding(false));
                }
                var (file, args) = AgentService.Redirected(line, log, IsWindows, script);
                var info = new ProcessStartInfo(file, args)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = Path.GetTempPath(),
                };
                info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                process = Process.Start(info);
                if (process == null) throw new InvalidOperationException("the process didn't start");
                pid = process.Id;
                since = EditorApplication.timeSinceStartup;
                nextPoll = since + AgentService.PollSeconds;
                Set(Phase.Running);
            }
            catch (Exception e)
            {
                process = null;
                pid = 0;
                Write(log, $"Couldn't start {line}: {e.Message}");
                Set(Phase.Failed);
            }
        }

        /// <summary>The removal without the agent service: the settings folder emptied here.</summary>
        static void RemoveHere(string log, bool keepMemory)
        {
            var kept = false;
            void Say(string text) => Write(log, text);
            Say("The autopilot command isn't on this computer: removing Autopilot's settings folder here");
            kept |= RemoveFolder(Aura.Folder(), keepMemory, Say);
            Say("Shared run data already sent stays where it is.");
            Set(kept ? Phase.RemovedButSome : Phase.Removed);
        }

        static void Write(string log, string text)
        {
            try
            {
                File.AppendAllText(log, text + "\n", new UTF8Encoding(false));
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[Autopilot] Couldn't write {log}: {e.Message}");
            }
        }

        /// <summary>Try again, with the same choice.</summary>
        public static void TryAgain() => Start(KeepMemory);

        /// <summary>Leave the removed state (Set up again, or Cancel after a failure): the window as usual.</summary>
        public static void Leave()
        {
            if (Current == Phase.Running) return;
            LogPath = "";
            Set(Phase.None);
        }

        public static void OpenLog()
        {
            if (!string.IsNullOrEmpty(LogPath) && File.Exists(LogPath)) EditorUtility.OpenWithDefaultApp(LogPath);
            else EditorUtility.RevealInFinder(Path.GetTempPath());
        }

        static void Set(Phase phase)
        {
            Current = phase;
            ++Version;
        }

        // -- following it ------------------------------------------------------------------------------------------

        static void Tick()
        {
            if (Current != Phase.Running) return;
            var now = EditorApplication.timeSinceStartup;
            if (now < nextPoll) return;
            nextPoll = now + AgentService.PollSeconds;
            try
            {
                if (Alive())
                {
                    if (now - since > Timeout)
                    {
                        Write(LogPath, $"[Autopilot] Gave up waiting after {Timeout / 60:0} minutes");
                        Forget();
                        Set(Phase.Failed);
                    }
                    return;
                }
                int? code = null;
                try
                {
                    if (process != null && process.HasExited) code = process.ExitCode;
                }
                catch (Exception)
                {
                    // (started before a script reload: the log says)
                }
                Forget();
                string text = null;
                try
                {
                    if (File.Exists(LogPath)) text = File.ReadAllText(LogPath);
                }
                catch (Exception)
                {
                    // (unreadable: failed, unless the code says)
                }
                Set(ResultOf(code, text));
            }
            catch (Exception e)
            {
                Debug.LogWarning("[Autopilot] Following the removal failed: " + e.Message);
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
            pid = 0;
        }

        /// <summary>Tests: show a state without running anything.</summary>
        internal static void TestState(Phase phase, string log = "")
        {
            Current = phase;
            LogPath = log ?? "";
            ++Version;
        }

        // -- across script reloads ----------------------------------------------------------------------------------

        static void Save()
        {
            if (Current == Phase.None)
            {
                SessionState.EraseString(StateKey);
                return;
            }
            SessionState.SetString(StateKey, Json.Write(new Dictionary<string, object>
            {
                ["phase"] = Current.ToString(),
                ["pid"] = pid,
                ["since"] = since,
                ["log"] = LogPath,
                ["keep"] = KeepMemory,
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
                since = Json.Num(o, "since", EditorApplication.timeSinceStartup);
                LogPath = Json.Str(o, "log");
                KeepMemory = Json.Bool(o, "keep", true);
                Current = phase;
                ++Version;
            }
            catch (Exception)
            {
                // (unreadable: the window as usual)
            }
        }
    }
}
