// AgentService.Start for real, with Tests~/fake_autopilot.py as the "Agent service command": the process is started with no
// window, its output appended to a log, its brain.json announcement (written by a descendant: Windows' venv python.exe
// starts the real python) found and taken; a second Start uses it instead of starting another; it ending is noticed. And
// the replace path's stop: only the process that wrote brain.json (it started before brain.json's "started").
// Everything happens in a temporary folder (Aura.FolderOverride), never the user's Autopilot folder. Ignored unless
// RG_PYTHON names a Python to run the fake with.

using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using Autopilot.Playtest.Editor;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace Autopilot.Playtest.Tests
{
    public class AgentServiceLiveTests
    {
        static IEnumerator Until(Func<bool> done, string what, double seconds)
        {
            var end = DateTime.UtcNow.AddSeconds(seconds);
            while (!done())
            {
                if (DateTime.UtcNow > end) Assert.Fail($"Timed out waiting for {what} (now {AgentService.Current}: {AgentService.Problem})");
                yield return null;
            }
        }

        [UnityTest]
        public IEnumerator StartsFindsAndSharesIt()
        {
            var python = Environment.GetEnvironmentVariable("RG_PYTHON");
            if (string.IsNullOrEmpty(python)) Assert.Ignore("RG_PYTHON isn't set");
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(AgentService).Assembly).resolvedPath;
            var fake = Path.Combine(package, "Tests~", "fake_autopilot.py");
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-service-test-" + Guid.NewGuid().ToString("N"));
            var settings = PlaytestSettings.Get();
            var saved = settings.ServiceCommand;
            var announced = 0;
            Aura.FolderOverride = dir;
            Environment.SetEnvironmentVariable("AUTOPILOT_TEST_FOLDER", dir);
            settings.ServiceCommand = $"\"{python}\" \"{fake}\"";  // (not saved)
            try
            {
                if (AgentService.Current != AgentService.Phase.Idle) AgentService.TryAgain();
                else AgentService.Start();
                Assert.AreEqual(AgentService.Phase.Starting, AgentService.Current, AgentService.Problem);
                yield return Until(() => AgentService.Current != AgentService.Phase.Starting, "the announcement", 35);
                Assert.AreEqual(AgentService.Phase.Running, AgentService.Current, AgentService.Problem);
                StringAssert.StartsWith("ws://127.0.0.1:", AgentService.Url);
                Assert.IsNotNull(Aura.Announced(Aura.OwnBrainFile, out announced));

                // the log: in the temporary folder, the command line first, then what the process wrote (UTF-8)
                StringAssert.StartsWith(Path.Combine(dir, "logs", "agent-service-"), AgentService.LogPath);
                var log = "";
                yield return Until(() =>
                {
                    try
                    {
                        using (var f = new FileStream(AgentService.LogPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                        using (var r = new StreamReader(f))
                            log = r.ReadToEnd();
                    }
                    catch (IOException)
                    {
                    }
                    return log.Contains("ready");
                }, "the log", 10);
                StringAssert.Contains("serve --port 0 --exit-with " + Process.GetCurrentProcess().Id, log);
                StringAssert.Contains("✓ ready", log);

                // one announced and alive: used, never a second
                var url = AgentService.Url;
                AgentService.Start();
                Assert.AreEqual(AgentService.Phase.Running, AgentService.Current);
                Assert.AreEqual(url, AgentService.Url);

                // it ends: noticed
                using (var p = Process.GetProcessById(announced)) if (AgentService.IsAgentServiceExe(p.ProcessName)) p.Kill();  // (its id may be another's by now)
                announced = 0;
                yield return Until(() => AgentService.Current == AgentService.Phase.Idle, "the end noticed", 10);
            }
            finally
            {
                if (announced > 0)
                {
                    try
                    {
                        using (var p = Process.GetProcessById(announced)) if (AgentService.IsAgentServiceExe(p.ProcessName)) p.Kill();  // (its id may be another's by now)
                    }
                    catch (Exception)
                    {
                        // (gone)
                    }
                }
                settings.ServiceCommand = saved;
                Aura.FolderOverride = null;
                Environment.SetEnvironmentVariable("AUTOPILOT_TEST_FOLDER", null);
                try
                {
                    Directory.Delete(dir, true);
                }
                catch (Exception)
                {
                    // (a log still open: left in the temporary folder)
                }
            }
        }

        /// <summary>The replace path's stop (AgentService.StopOlder) on a stand-in this test starts itself: a brain.json
        /// whose "started" is long before the stand-in's process started (its pid reused by another process) or missing
        /// leaves it running; its own announcement lets it be stopped.</summary>
        [Test]
        public void OnlyTheProcessThatAnnouncedIsStopped()
        {
            var python = Environment.GetEnvironmentVariable("RG_PYTHON");
            if (string.IsNullOrEmpty(python)) Assert.Ignore("RG_PYTHON isn't set");
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(AgentService).Assembly).resolvedPath;
            var fake = Path.Combine(package, "Tests~", "fake_autopilot.py");
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-owner-test-" + Guid.NewGuid().ToString("N"));
            var stop = typeof(AgentService).GetMethod("StopOlder", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            Assert.IsNotNull(stop);
            Directory.CreateDirectory(dir);
            Aura.FolderOverride = dir;
            Process launched = null, standIn = null;
            var announced = 0;
            try
            {
                var info = new ProcessStartInfo(python, $"\"{fake}\" serve --port 0 --exit-with {Process.GetCurrentProcess().Id}")
                {
                    UseShellExecute = false, CreateNoWindow = true,
                };
                info.EnvironmentVariables["AUTOPILOT_TEST_FOLDER"] = dir;
                // (its "✓ ready" line in UTF-8 whatever the console's code page: else it ends right after announcing)
                info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                launched = Process.Start(info);
                var end = DateTime.UtcNow.AddSeconds(20);
                while (Aura.Announced(Aura.OwnBrainFile, out announced) == null)
                {
                    if (DateTime.UtcNow > end) Assert.Fail("the stand-in didn't announce itself");
                    System.Threading.Thread.Sleep(100);
                }
                var path = Path.Combine(dir, Aura.OwnBrainFile);
                var own = File.ReadAllText(path);
                var o = Json.ParseObject(own);
                var started = Json.Num(o, "started", 0);
                Assert.Greater(started, 0, "the stand-in says when it started");
                System.Threading.Thread.Sleep(500);
                Assert.IsTrue(Aura.Running(announced), "the stand-in keeps running");
                using (var p = Process.GetProcessById(announced))
                {
                    Assert.IsTrue(AgentService.IsAgentServiceExe(p.ProcessName), $"pid {announced} is the stand-in's python, not {p.ProcessName}");
                    standIn = Process.GetProcessById(announced);  // (a handle: cleaning up never reaches a process that reused its id)
                }

                bool Stop(out string why)
                {
                    var args = new object[] { announced, null };
                    var stopped = (bool)stop.Invoke(null, args);
                    why = (string)args[1];
                    return stopped;
                }

                // an announcement from long before the process started: its pid is another process's now
                o["started"] = started - 3600;
                File.WriteAllText(path, Json.Write(o));
                Assert.IsFalse(Stop(out var why1));
                StringAssert.Contains("started after brain.json was written", why1);
                Assert.IsFalse(standIn.HasExited, "left running");

                // an announcement that doesn't say when it started
                o.Remove("started");
                File.WriteAllText(path, Json.Write(o));
                Assert.IsFalse(Stop(out var why2));
                StringAssert.Contains("doesn't say when it started", why2);
                Assert.IsFalse(standIn.HasExited, "left running");

                // its own announcement: stopped
                File.WriteAllText(path, own);
                Assert.IsTrue(Stop(out var why3), why3);
                Assert.IsTrue(standIn.WaitForExit(5000), "stopped");
            }
            finally
            {
                // Only the processes this test started, through their own handles (never by an id another may have now)
                foreach (var p in new[] { standIn, launched })
                {
                    if (p == null) continue;
                    try
                    {
                        if (!p.HasExited) p.Kill();
                    }
                    catch (Exception)
                    {
                        // (gone)
                    }
                    p.Dispose();
                }
                Aura.FolderOverride = null;
                try
                {
                    Directory.Delete(dir, true);
                }
                catch (Exception)
                {
                    // (left in the temporary folder)
                }
            }
        }
    }
}
