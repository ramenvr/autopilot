// The game's code for a brain that can't read this machine's disk (docs/protocol.md: codex_manifest, codex_read).
// Read-only, and only the project's own code and data: Packages/manifest.json, ProjectSettings/*.asset (and
// ProjectVersion.txt), Assets/**/*.cs, *.inputactions and *.asmdef, the same in embedded packages (Packages/<name>/),
// and the text export of scenes, prefabs, data and input that the editor writes to Library/AutopilotCodex/
// (Editor/CodexExport.cs). Folders named Library (but the export), Temp, Logs, obj, Build(s), .git, node_modules, or
// starting with '.' or ending with '~' (folders Unity itself ignores) are skipped anywhere. Walks, hashes and reads run
// off the main thread.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;

namespace Autopilot.Playtest
{
    public static class Codex
    {
        public const string ExportPrefix = "Library/AutopilotCodex";
        const int MaxFiles = 20000;
        const long MaxTotalBytes = 200L * 1024 * 1024;
        const long DefaultReadBytes = 1000000;
        const long MaxReadBytes = 4L * 1024 * 1024;
        const int SyncReadFiles = 3;
        const double ExportWaitSeconds = 14 * 60;
        const string OwnPackage = "com.ramenvr.autopilot";

        static readonly string[] CodeExtensions = { ".cs", ".inputactions", ".asmdef" };
        static readonly string[] AssetExtensions = { ".unity", ".prefab", ".asset", ".inputactions", ".controller", ".overrideController", ".mat", ".uxml", ".uss" };

        static bool SkippedDir(string name)
        {
            return name.Equals("Library", StringComparison.OrdinalIgnoreCase) || name.Equals("Temp", StringComparison.OrdinalIgnoreCase)
                   || name.Equals("Logs", StringComparison.OrdinalIgnoreCase) || name.Equals("obj", StringComparison.OrdinalIgnoreCase)
                   || name.Equals("Build", StringComparison.OrdinalIgnoreCase) || name.Equals("Builds", StringComparison.OrdinalIgnoreCase)
                   || name.Equals("node_modules", StringComparison.OrdinalIgnoreCase) || name.StartsWith(".", StringComparison.Ordinal)
                   || name.EndsWith("~", StringComparison.Ordinal) || name.Equals(OwnPackage, StringComparison.OrdinalIgnoreCase);
        }

        static bool IsCode(string name)
        {
            var ext = Path.GetExtension(name);
            return CodeExtensions.Any(e => e.Equals(ext, StringComparison.OrdinalIgnoreCase));
        }

        /// <summary>The file a path the brain names stands for, or null when the manifest couldn't list it.</summary>
        public static string Resolve(string root, string asked)
        {
            if (string.IsNullOrEmpty(root) || string.IsNullOrEmpty(asked)) return null;
            var path = asked.Replace('\\', '/');
            if (path.StartsWith("/", StringComparison.Ordinal) || path.Contains(":")) return null;
            var parts = path.Split('/');
            var export = parts.Length >= 3 && parts[0].Equals("Library", StringComparison.OrdinalIgnoreCase) && parts[1].Equals("AutopilotCodex", StringComparison.OrdinalIgnoreCase);
            for (var i = 0; i < parts.Length; ++i)
            {
                var p = parts[i];
                if (p.Length == 0 || p == "." || p == "..") return null;
                if (i < parts.Length - 1 && !(export && i < 2) && SkippedDir(p)) return null;
            }
            var name = parts[parts.Length - 1];
            var ok = false;
            if (export) ok = true;
            else if (parts.Length == 2 && parts[0] == "Packages" && name == "manifest.json") ok = true;
            else if (parts.Length == 2 && parts[0] == "ProjectSettings")
                ok = name.EndsWith(".asset", StringComparison.OrdinalIgnoreCase) || name == "ProjectVersion.txt";
            else if (parts.Length >= 2 && parts[0] == "Assets") ok = IsCode(name);
            else if (parts.Length >= 3 && parts[0] == "Packages") ok = IsCode(name);
            if (!ok) return null;
            var full = Path.GetFullPath(Path.Combine(root, path));
            var rootFull = Path.GetFullPath(root).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            return full.StartsWith(rootFull, StringComparison.OrdinalIgnoreCase) ? full : null;
        }

        struct Listed
        {
            public string Path;
            public string Full;
            public long Size;
        }

        static void Walk(string dir, string prefix, Func<string, bool> keep, List<Listed> outList, bool recurse = true)
        {
            if (!Directory.Exists(dir)) return;
            string[] files, dirs;
            try
            {
                files = Directory.GetFiles(dir);
                dirs = recurse ? Directory.GetDirectories(dir) : new string[0];
            }
            catch
            {
                return;
            }
            Array.Sort(files, StringComparer.Ordinal);
            Array.Sort(dirs, StringComparer.Ordinal);
            foreach (var f in files)
            {
                var name = Path.GetFileName(f);
                if (!keep(name)) continue;
                long size;
                try
                {
                    size = new FileInfo(f).Length;
                }
                catch
                {
                    continue;
                }
                outList.Add(new Listed { Path = prefix + "/" + name, Full = f, Size = size });
            }
            foreach (var d in dirs)
            {
                var name = Path.GetFileName(d);
                if (SkippedDir(name)) continue;
                Walk(d, prefix + "/" + name, keep, outList);
            }
        }

        static Dictionary<string, object> BuildManifest(string root, string project)
        {
            var found = new List<Listed>();
            var manifest = Path.Combine(root, "Packages", "manifest.json");
            if (File.Exists(manifest)) found.Add(new Listed { Path = "Packages/manifest.json", Full = manifest, Size = new FileInfo(manifest).Length });
            Walk(Path.Combine(root, "ProjectSettings"), "ProjectSettings", n => n.EndsWith(".asset", StringComparison.OrdinalIgnoreCase) || n == "ProjectVersion.txt", found, false);
            Walk(Path.Combine(root, "Assets"), "Assets", IsCode, found);
            var packages = Path.Combine(root, "Packages");
            if (Directory.Exists(packages))
            {
                foreach (var d in Directory.GetDirectories(packages).OrderBy(x => x, StringComparer.Ordinal))
                {
                    var name = Path.GetFileName(d);
                    if (SkippedDir(name) || !File.Exists(Path.Combine(d, "package.json"))) continue;
                    Walk(d, "Packages/" + name, IsCode, found);
                }
            }
            Walk(Path.Combine(root, "Library", "AutopilotCodex"), ExportPrefix, n => true, found);
            var files = new List<object>();
            long total = 0;
            var truncated = false;
            using (var md5 = MD5.Create())
            {
                foreach (var f in found)
                {
                    if (files.Count >= MaxFiles || total + f.Size > MaxTotalBytes)
                    {
                        truncated = true;
                        break;
                    }
                    string hash;
                    try
                    {
                        using (var s = File.OpenRead(f.Full))
                            hash = BitConverter.ToString(md5.ComputeHash(s)).Replace("-", "").ToLowerInvariant();
                    }
                    catch
                    {
                        continue;
                    }
                    total += f.Size;
                    files.Add(new Dictionary<string, object> { ["path"] = f.Path, ["size"] = f.Size, ["hash"] = hash });
                }
            }
            return new Dictionary<string, object> { ["project"] = project, ["files"] = files, ["truncated"] = truncated };
        }

        /// <summary>Why the export should run ("missing", "stale"), or null when it is up to date.</summary>
        public static string ExportNeeded(string root)
        {
            var summary = Path.Combine(root, "Library", "AutopilotCodex", "summary.txt");
            if (!File.Exists(summary)) return "missing";
            var made = File.GetLastWriteTimeUtc(summary);
            var assets = Path.Combine(root, "Assets");
            return Newer(assets, made) ? "stale" : null;
        }

        static bool Newer(string dir, DateTime than)
        {
            try
            {
                foreach (var f in Directory.GetFiles(dir))
                {
                    var ext = Path.GetExtension(f);
                    if (AssetExtensions.Any(e => e.Equals(ext, StringComparison.OrdinalIgnoreCase)) && File.GetLastWriteTimeUtc(f) > than)
                        return true;
                }
                foreach (var d in Directory.GetDirectories(dir))
                    if (!SkippedDir(Path.GetFileName(d)) && Newer(d, than))
                        return true;
            }
            catch
            {
                // (unreadable: not newer)
            }
            return false;
        }

        // One export at a time: a request while one runs waits for it
        static bool exporting;
        static double exportStarted;
        static string exportReason;
        static int? exportExit;
        static string exportError;
        static double exportSeconds;
        static bool exportDone;

        public static void Manifest(PlaytestClient client, long id, Dictionary<string, object> args)
        {
            var root = client.Host.ProjectDir;
            if (string.IsNullOrEmpty(root) || !Directory.Exists(root))
            {
                client.RespondError(id, "The game's project folder isn't known here (a player build away from its project: give -PlaytestProject=<folder>)");
                return;
            }
            var mode = Json.Str(args, "export", "auto");
            var reason = mode == "skip" ? null : mode == "refresh" ? "refresh" : null;
            var project = Application.productName;
            var started = PlaytestClient.Now;
            Task<string> check = mode == "auto" ? Task.Run(() => ExportNeeded(root)) : Task.FromResult(reason);
            var phase = 0;
            Task<Dictionary<string, object>> build = null;
            var ran = false;
            client.AddPending(new Pending
            {
                Id = id,
                Op = "codex_manifest",
                Tick = (p, now) =>
                {
                    if (phase == 0)
                    {
                        if (!check.IsCompleted) return false;
                        reason = check.IsFaulted ? null : check.Result;
                        if (reason != null && !exporting)
                        {
                            // Start the export
                            exporting = true;
                            exportDone = false;
                            exportStarted = now;
                            exportReason = reason;
                            exportExit = null;
                            exportError = null;
                            var dir = Path.Combine(root, "Library", "AutopilotCodex");
                            var accepted = client.Host.Export(dir, (code, error) =>
                            {
                                exportExit = code;
                                exportError = error;
                                exportSeconds = PlaytestClient.Now - exportStarted;
                                exportDone = true;
                                exporting = false;
                            });
                            if (!accepted)
                            {
                                exportError = "this game can't run the export (no editor to run it with)";
                                exportDone = true;
                                exporting = false;
                            }
                            ran = accepted;
                        }
                        else if (reason != null)
                        {
                            ran = true;  // (waits for the one running)
                            reason = exportReason;
                        }
                        phase = 1;
                    }
                    if (phase == 1)
                    {
                        if (exporting && now - exportStarted < ExportWaitSeconds) return false;
                        build = Task.Run(() => BuildManifest(root, project));
                        phase = 2;
                    }
                    if (phase == 2)
                    {
                        if (!build.IsCompleted) return false;
                        if (build.IsFaulted)
                        {
                            client.RespondError(p.Id, "codex_manifest failed: " + build.Exception?.InnerException?.Message, 500);
                            return true;
                        }
                        var result = build.Result;
                        var export = new Dictionary<string, object>
                        {
                            ["ran"] = ran,
                            ["seconds"] = ran ? Json.Round(exporting ? now - exportStarted : exportSeconds, 1) : 0.0,
                            ["exit_code"] = ran && exportDone ? (object)exportExit : null,
                        };
                        if (ran) export["reason"] = reason;
                        if (ran && exporting) export["error"] = "the export is still running after 14 minutes: these are the files there now";
                        else if (ran && !string.IsNullOrEmpty(exportError)) export["error"] = exportError;
                        result["export"] = export;
                        client.Respond(p.Id, result);
                        return true;
                    }
                    return false;
                },
            });
        }

        public static void Read(PlaytestClient client, long id, Dictionary<string, object> args)
        {
            var root = client.Host.ProjectDir;
            var paths = (Json.List(args, "paths") ?? new List<object>()).Select(o => Convert.ToString(o)).ToList();
            var maxBytes = Math.Max(1, Math.Min(MaxReadBytes, (long)Json.Num(args, "max_bytes", DefaultReadBytes)));
            if (paths.Count <= SyncReadFiles)
            {
                client.Respond(id, ReadFiles(root, paths, maxBytes));
                return;
            }
            var task = Task.Run(() => ReadFiles(root, paths, maxBytes));
            client.AddPending(new Pending
            {
                Id = id,
                Op = "codex_read",
                Tick = (p, now) =>
                {
                    if (!task.IsCompleted) return false;
                    if (task.IsFaulted) client.RespondError(p.Id, "codex_read failed: " + task.Exception?.InnerException?.Message, 500);
                    else client.Respond(p.Id, task.Result);
                    return true;
                },
            });
        }

        static Dictionary<string, object> ReadFiles(string root, List<string> paths, long maxBytes)
        {
            var files = new Dictionary<string, object>();
            var missing = new List<object>();
            var tooBig = new List<object>();
            long total = 0;
            foreach (var asked in paths)
            {
                var full = Resolve(root, asked);
                if (full == null || !File.Exists(full))
                {
                    missing.Add(asked);
                    continue;
                }
                long size;
                try
                {
                    size = new FileInfo(full).Length;
                }
                catch
                {
                    missing.Add(asked);
                    continue;
                }
                if (size > maxBytes)
                {
                    tooBig.Add(asked);
                    continue;
                }
                if (total + size > maxBytes) break;  // (the rest unanswered: the brain asks again)
                try
                {
                    files[asked] = File.ReadAllText(full, Encoding.UTF8);
                    total += size;
                }
                catch
                {
                    missing.Add(asked);
                }
            }
            return new Dictionary<string, object> { ["files"] = files, ["missing"] = missing, ["too_big"] = tooBig };
        }

        // -- the export from a player: the editor run as a child process -----------------------------------------

        /// <summary>Run the editor in batch mode on the project to write the export (a player's way: the editor that
        /// built it, or -PlaytestUnity=). Refused while the project is open in an editor (Unity locks it).</summary>
        public static bool ExportWithEditor(string editor, string project, string dir, Action<int?, string> done)
        {
            if (string.IsNullOrEmpty(editor) || !File.Exists(editor)) return false;
            var lockFile = Path.Combine(project, "Temp", "UnityLockfile");
            if (File.Exists(lockFile))
            {
                try
                {
                    using (File.Open(lockFile, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                    {
                    }
                }
                catch (IOException)
                {
                    done(null, "the project is open in the Unity editor: run it from there (the editor exports the project index before play mode)");
                    return true;
                }
            }
            var log = Path.Combine(project, "Logs", "AutopilotCodexExport.log");
            Directory.CreateDirectory(Path.GetDirectoryName(log));
            var info = new ProcessStartInfo
            {
                FileName = editor,
                Arguments = $"-batchmode -nographics -quit -projectPath \"{project}\" -executeMethod Autopilot.Playtest.Editor.CodexExport.Run -out \"{dir}\" -logFile \"{log}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            Process process;
            try
            {
                process = Process.Start(info);
            }
            catch (Exception e)
            {
                done(null, "couldn't start the editor: " + e.Message);
                return true;
            }
            try
            {
                process.PriorityClass = ProcessPriorityClass.BelowNormal;
            }
            catch
            {
                // (fine)
            }
            Running = process;
            Task.Run(() =>
            {
                process.WaitForExit();
                var code = process.ExitCode;
                Running = null;
                var summary = File.Exists(Path.Combine(dir, "summary.txt"));
                MainThread.Post(() => done(code, code != 0 ? $"the export exited with {code} (see Logs/AutopilotCodexExport.log)" : summary ? null : "the export wrote no summary.txt"));
            });
            return true;
        }

        public static Process Running;

        /// <summary>An export still running when the game quits ends with it.</summary>
        public static void Shutdown()
        {
            try
            {
                if (Running != null && !Running.HasExited) Running.Kill();
            }
            catch
            {
                // (gone)
            }
        }
    }

    /// <summary>Work for the main thread, posted from others (run at the next Tick).</summary>
    public static class MainThread
    {
        static readonly System.Collections.Concurrent.ConcurrentQueue<Action> queue = new System.Collections.Concurrent.ConcurrentQueue<Action>();

        public static void Post(Action a)
        {
            queue.Enqueue(a);
        }

        public static void Run()
        {
            while (queue.TryDequeue(out var a))
            {
                try
                {
                    a();
                }
                catch (Exception e)
                {
                    UnityEngine.Debug.LogWarning("[Autopilot] " + e);
                }
            }
        }
    }
}
