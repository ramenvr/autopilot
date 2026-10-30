// Removing Autopilot from this computer (Editor/Uninstall.cs): the uninstall's command line, what its exit code (or its
// log, after a script reload) says, the settings folder emptied here when the command isn't on this computer (the Aura
// app's aura-brain.json kept, memory/ when asked), what the package keeps about the runs in the project deleted, the removed state stopping every start and connect,
// and when the window opens the setup's Agent service step. And, on Windows, the
// uninstall run for real with a stand-in command. Only temporary folders are touched.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using Autopilot.Playtest.Editor;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace Autopilot.Playtest.Tests
{
    public class UninstallTests
    {
        string dir;

        [SetUp]
        public void SetUp()
        {
            dir = Path.Combine(Path.GetTempPath(), "autopilot-uninstall-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            Uninstall.RealRunsBlocked = true;  // (never the real uninstall: only the stand-in in the temporary folder)
        }

        [TearDown]
        public void TearDown()
        {
            Uninstall.TestState(Uninstall.Phase.None);
            Uninstall.RealRunsBlocked = false;
            AgentService.TestState(AgentService.Phase.Idle);
            try
            {
                Directory.Delete(dir, true);
            }
            catch (Exception)
            {
                // (left in the temp folder)
            }
        }

        [Test]
        public void ForgettingTheRunsDeletesWhatThePackageKeepsInTheProject()
        {
            var project = Path.Combine(dir, "Harbor");
            foreach (var rel in new[] { "Logs/Playtest/20261019-120000/result.json", "Library/AutopilotCodex/scripts.txt",
                         "Logs/AutopilotCodexExport.log", "UserSettings/AutopilotRuns/20261019-120000.json", "Logs/Editor.log",
                         "UserSettings/AutopilotPlaytest.json", "Assets/Game.cs" })
            {
                var path = Path.Combine(project, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, "x");
            }
            CollectionAssert.IsEmpty(Uninstall.ForgetProject(project));
            Assert.IsFalse(Directory.Exists(Path.Combine(project, "Logs", "Playtest")));
            Assert.IsFalse(Directory.Exists(Path.Combine(project, "Library", "AutopilotCodex")));
            Assert.IsFalse(File.Exists(Path.Combine(project, "Logs", "AutopilotCodexExport.log")));
            Assert.IsFalse(Directory.Exists(Path.Combine(project, "UserSettings", "AutopilotRuns")));
            foreach (var rel in new[] { "Logs/Editor.log", "UserSettings/AutopilotPlaytest.json", "Assets/Game.cs" })
                Assert.IsTrue(File.Exists(Path.Combine(project, rel)), rel);
            CollectionAssert.IsEmpty(Uninstall.ForgetProject(project), "nothing there: nothing to do");

            var ledger = Path.Combine(project, "UserSettings", "AutopilotUsage.json");
            File.WriteAllText(ledger, "{\"runs\": [{\"id\": \"r1\", \"kind\": \"explore\"}]}");
            AutopilotUsage.LedgerOverride = ledger;
            try
            {
                AutopilotUsage.TestReload();
                Assert.AreEqual(1, AutopilotUsage.Entries.Count);
                AutopilotUsage.Forget();
                Assert.AreEqual(0, AutopilotUsage.Entries.Count);
                Assert.IsFalse(File.Exists(ledger));
            }
            finally
            {
                AutopilotUsage.LedgerOverride = null;
                AutopilotUsage.TestReload();
            }
        }

        [Test]
        public void TheUninstallsCommandLine()
        {
            var installed = new AgentService.Command { Text = "\"C:\\Users\\me\\.local\\bin\\autopilot.exe\"", Source = "installed" };
            Assert.AreEqual("\"C:\\Users\\me\\.local\\bin\\autopilot.exe\" uninstall --yes --keep-memory --log \"C:\\Temp\\u.log\"",
                Uninstall.Line(installed, true, @"C:\Temp\u.log", true));
            var setting = new AgentService.Command { Text = @"C:\autopilot\.venv\Scripts\python.exe -m autopilot", Source = "setting" };
            Assert.AreEqual(@"C:\autopilot\.venv\Scripts\python.exe -m autopilot uninstall --yes --log ""C:\Temp\u.log""",
                Uninstall.Line(setting, false, @"C:\Temp\u.log", true));
            var unix = new AgentService.Command { Text = "'/home/me/.local/bin/autopilot'", Source = "installed" };
            Assert.AreEqual("'/home/me/.local/bin/autopilot' uninstall --yes --log '/tmp/it'\\''s.log'",
                Uninstall.Line(unix, false, "/tmp/it's.log", false));
        }

        [Test]
        public void TheResultIsTheExitCodeElseTheLogsLastWords()
        {
            Assert.AreEqual(Uninstall.Phase.Removed, Uninstall.ResultOf(0, null));
            Assert.AreEqual(Uninstall.Phase.RemovedButSome, Uninstall.ResultOf(3, null));
            Assert.AreEqual(Uninstall.Phase.Failed, Uninstall.ResultOf(1, "Autopilot is removed from this computer."));
            // (started before a script reload: the code is lost, the log says)
            Assert.AreEqual(Uninstall.Phase.Removed, Uninstall.ResultOf(null, "...\nAutopilot is removed from this computer. To remove it from a project too, remove its plugin there.\n"));
            Assert.AreEqual(Uninstall.Phase.RemovedButSome, Uninstall.ResultOf(null, "Autopilot is removed from this computer, but for: C:\\x\\memory\n"));
            Assert.AreEqual(Uninstall.Phase.Failed, Uninstall.ResultOf(null, "Traceback (most recent call last):\n"));
            Assert.AreEqual(Uninstall.Phase.Failed, Uninstall.ResultOf(null, null));
        }

        string Settings()
        {
            var folder = Path.Combine(dir, "Autopilot");
            Directory.CreateDirectory(Path.Combine(folder, "logs"));
            Directory.CreateDirectory(Path.Combine(folder, "memory", "Orchard"));
            File.WriteAllText(Path.Combine(folder, "memory", "Orchard", "notes.md"), "what it learned");
            foreach (var name in new[] { "preferences.json", "autopilot.env", "aura-session.json", "install.json", "brain.json", "aura-brain.json" })
                File.WriteAllText(Path.Combine(folder, name), "{}");
            var log = Path.Combine(folder, "logs", "agent-service-20261019-120000.log");
            File.WriteAllText(log, "log");
            File.SetAttributes(log, FileAttributes.ReadOnly);
            return folder;
        }

        [Test]
        public void WithoutTheCommandTheSettingsFolderIsEmptiedButWhatItLearned()
        {
            var said = new List<string>();
            var folder = Settings();
            // Not asked to keep it: memory/ goes with the rest
            Assert.IsFalse(Uninstall.RemoveFolder(folder, false, said.Add), "kept what wasn't asked to be kept");
            CollectionAssert.AreEquivalent(new[] { "aura-brain.json" }, Names(folder));

            Directory.Delete(folder, true);
            folder = Settings();
            Assert.IsFalse(Uninstall.RemoveFolder(folder, true, said.Add), "kept as asked");
            CollectionAssert.AreEquivalent(new[] { "aura-brain.json", "memory" }, Names(folder));
            Assert.IsTrue(File.Exists(Path.Combine(folder, "memory", "Orchard", "notes.md")));
            Assert.IsTrue(said.Exists(l => l.StartsWith("Kept what Autopilot learned about your games in ", StringComparison.Ordinal)), string.Join("\n", said));

            // Nothing to keep: the folder goes too
            var bare = Path.Combine(dir, "Bare");
            Directory.CreateDirectory(bare);
            File.WriteAllText(Path.Combine(bare, "autopilot.env"), "AUTOPILOT_AGENT=claude\n");
            Assert.IsFalse(Uninstall.RemoveFolder(bare, false, said.Add));
            Assert.IsFalse(Directory.Exists(bare));
            Assert.IsFalse(Uninstall.RemoveFolder(Path.Combine(dir, "none"), false, said.Add), "no folder: nothing to do");
        }

        static string[] Names(string folder)
        {
            return Array.ConvertAll(Directory.GetFileSystemEntries(folder), Path.GetFileName);
        }

        [Test]
        public void WhileRemovedNothingStartsOrConnects()
        {
            var clientWas = EditorHost.Client;
            try
            {
                Uninstall.TestState(Uninstall.Phase.Removed);
                Assert.IsTrue(Uninstall.Holds);
                AgentService.TestState(AgentService.Phase.Idle);
                AgentService.Start();
                Assert.AreEqual(AgentService.Phase.Idle, AgentService.Current, "not started");
                AgentService.Install();
                Assert.AreEqual(AgentService.Phase.Idle, AgentService.Current, "not installed");
                EditorHost.TestClient(null);
                EditorHost.Connect();
                Assert.IsNull(EditorHost.Client, "not connected");

                // Set up again: the window as usual
                Uninstall.Leave();
                Assert.IsFalse(Uninstall.Holds);
                Uninstall.TestState(Uninstall.Phase.Running);
                Uninstall.Leave();
                Assert.IsTrue(Uninstall.Holds, "not while it is going");
            }
            finally
            {
                EditorHost.TestClient(clientWas);
            }
        }

        [Test]
        public void TheSetupsAgentServiceStepWhenTheServiceIsGone()
        {
            Assert.IsTrue(AutopilotView.ServiceGone(true, true, false, false, false, false));
            Assert.IsFalse(AutopilotView.ServiceGone(false, true, false, false, false, false), "not set up: the whole setup shows anyway");
            Assert.IsFalse(AutopilotView.ServiceGone(true, false, false, false, false, false), "started by hand, or with Aura, or another URL");
            Assert.IsFalse(AutopilotView.ServiceGone(true, true, true, false, false, false), "one is running");
            Assert.IsFalse(AutopilotView.ServiceGone(true, true, false, true, false, false), "the command is there");
            Assert.IsFalse(AutopilotView.ServiceGone(true, true, false, false, true, false), "batch mode, a command-line run");
            Assert.IsFalse(AutopilotView.ServiceGone(true, true, false, false, false, true), "removed, the setup open, connected");
        }

        static IEnumerator Until(Func<bool> done, string what, double seconds)
        {
            var end = DateTime.UtcNow.AddSeconds(seconds);
            while (!done())
            {
                if (DateTime.UtcNow > end) Assert.Fail($"Timed out waiting for {what} (now {Uninstall.Current})");
                yield return null;
            }
        }

        /// <summary>While tests run, the uninstall runs only a stand-in in the temporary folder: never the agent
        /// service on this computer (PATH's, the installed one, a setting elsewhere), nor empties the real settings
        /// folder. (Checked without starting anything.)</summary>
        [Test]
        public void TestsNeverRunTheRealUninstall()
        {
            Assert.IsTrue(Uninstall.RealRunsBlocked, "blocked in these tests");
            AgentService.Command C(string text, string source) => new AgentService.Command { Text = text, Source = source };
            var home = Environment.GetEnvironmentVariable("USERPROFILE") ?? Path.Combine("C:", "Users", "someone");
            var venv = Path.Combine(home, "autopilot", ".venv", "Scripts");
            Assert.IsFalse(Uninstall.AllowedNow(C($"\"{Path.Combine(home, ".local", "bin", "autopilot.exe")}\"", "installed")), "the installed one");
            Assert.IsFalse(Uninstall.AllowedNow(C($"\"{Path.Combine(venv, "autopilot.exe")}\"", "path")), "PATH's");
            Assert.IsFalse(Uninstall.AllowedNow(C($"{Path.Combine(venv, "python.exe")} -m autopilot", "setting")), "a setting outside the temporary folder");
            Assert.IsTrue(Uninstall.AllowedNow(C($"\"{Path.Combine(dir, "fake autopilot.cmd")}\"", "setting")), "the stand-in");
            if (Aura.FolderOverride == null) Assert.IsFalse(Uninstall.AllowedNow(null), "the real settings folder never emptied");
            Uninstall.RealRunsBlocked = false;
            Assert.IsTrue(Uninstall.AllowedNow(C($"\"{Path.Combine(venv, "autopilot.exe")}\"", "path")), "outside tests: as before");
            Uninstall.RealRunsBlocked = true;
        }

        /// <summary>The uninstall for real, with a stand-in for the agent service's command (the Agent service command
        /// setting, not saved) that writes what it was given and exits 3: started with no window from the temporary
        /// folder, its output in the log there, its exit code shown.</summary>
        [UnityTest]
        public IEnumerator TheUninstallRunsTheCommandAndShowsHowItWent()
        {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT) Assert.Ignore("the stand-in is a Windows batch file");
            var args = Path.Combine(dir, "args.txt");
            var fake = Path.Combine(dir, "fake autopilot.cmd");
            File.WriteAllText(fake, $"@echo %* > \"{args}\"\r\n@echo Autopilot is removed from this computer, but for: memory\r\n@exit /b 3\r\n");
            var settings = PlaytestSettings.Get();
            var saved = settings.ServiceCommand;
            var clientWas = EditorHost.Client;
            settings.ServiceCommand = $"\"{fake}\"";  // (not saved)
            try
            {
                Uninstall.Start(false);
                Assert.AreEqual(Uninstall.Phase.Running, Uninstall.Current);
                Assert.IsTrue(Uninstall.Holds);
                StringAssert.StartsWith(Path.Combine(Path.GetTempPath(), Uninstall.LogPrefix + "-"), Uninstall.LogPath);
                yield return Until(() => Uninstall.Current != Uninstall.Phase.Running, "the uninstall to end", 30);
                Assert.AreEqual(Uninstall.Phase.RemovedButSome, Uninstall.Current);
                StringAssert.StartsWith("uninstall --yes --log ", File.ReadAllText(args).Trim());
                StringAssert.Contains("Autopilot is removed from this computer, but for", File.ReadAllText(Uninstall.LogPath));
                File.Delete(Uninstall.LogPath);
            }
            finally
            {
                settings.ServiceCommand = saved;
                EditorHost.TestClient(clientWas);
                Uninstall.TestState(Uninstall.Phase.None);
            }
        }
    }
}
