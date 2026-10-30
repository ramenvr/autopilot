// The agent service the plugin starts (Editor/AgentService.cs): finding the autopilot command (the setting, PATH, the
// installer's place), the command lines it starts and installs with, brain.json's pid matched to the process started,
// and the logs kept. Pure functions only: nothing is started, no real settings folder is touched.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autopilot.Playtest.Editor;
using NUnit.Framework;

namespace Autopilot.Playtest.Tests
{
    public class AgentServiceTests
    {
        static Func<string, bool> Only(params string[] paths)
        {
            var set = new HashSet<string>(paths, StringComparer.Ordinal);
            return set.Contains;
        }

        [Test]
        public void TheSettingsCommandComesFirst()
        {
            var c = AgentService.Find(@"  C:\autopilot\.venv\Scripts\python.exe -m autopilot ", @"C:\bin", @"C:\Users\me", true, _ => true);
            Assert.AreEqual(@"C:\autopilot\.venv\Scripts\python.exe -m autopilot", c.Text);
            Assert.AreEqual("setting", c.Source);
        }

        [Test]
        public void AutopilotOnPathOnWindows()
        {
            var exists = Only(@"C:\b\autopilot.exe", @"C:\Users\me\.local\bin\autopilot.exe");
            var c = AgentService.Find("", @"C:\a;;""C:\b\"";C:\c", @"C:\Users\me", true, exists);
            Assert.AreEqual("\"C:\\b\\autopilot.exe\"", c.Text);
            Assert.AreEqual("path", c.Source);

            // a .cmd shim counts too
            c = AgentService.Find(null, @"C:\shims", @"C:\Users\me", true, Only(@"C:\shims\autopilot.cmd"));
            Assert.AreEqual("\"C:\\shims\\autopilot.cmd\"", c.Text);
        }

        [Test]
        public void TheInstallersPlaceWhenNotOnPath()
        {
            var c = AgentService.Find("", @"C:\Windows;C:\Windows\System32", @"C:\Users\me", true, Only(@"C:\Users\me\.local\bin\autopilot.exe"));
            Assert.AreEqual("\"C:\\Users\\me\\.local\\bin\\autopilot.exe\"", c.Text);
            Assert.AreEqual("installed", c.Source);

            c = AgentService.Find("", "/usr/bin:/bin", "/Users/me", false, Only("/Users/me/.local/bin/autopilot"));
            Assert.AreEqual("'/Users/me/.local/bin/autopilot'", c.Text);
            Assert.AreEqual("installed", c.Source);
        }

        [Test]
        public void AutopilotOnPathElsewhere()
        {
            var c = AgentService.Find("", "/usr/bin:/opt/it's here", "/home/me", false, Only("/opt/it's here/autopilot", "/home/me/.local/bin/autopilot"));
            Assert.AreEqual("'/opt/it'\\''s here/autopilot'", c.Text);
            Assert.AreEqual("path", c.Source);
        }

        // -- an older agent service, installed from an earlier download: this package's goes in before it starts --

        const string Shipped = "{\"sha256\": \"ab12\", \"commit\": \"36a6fec\", \"wheel\": \"autopilot-0.1.0-py3-none-any.whl\"}";

        static AgentService.Command Installers(bool windows) => windows
            ? AgentService.Find("", @"C:\Users\me\.local\bin", @"C:\Users\me", true, Only(@"C:\Users\me\.local\bin\autopilot.exe"))
            : AgentService.Find("", "/usr/bin", "/Users/me", false, Only("/Users/me/.local/bin/autopilot"));

        [Test]
        public void TheSameAgentServiceIsntInstalledAgain()
        {
            Assert.AreEqual("path", Installers(true).Source);  // (the installer puts its folder on PATH)
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, Shipped, Installers(true), @"C:\Users\me", true));
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, "{\"sha256\": \"AB12\"}", Installers(true), @"C:\Users\me", true));
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, Shipped, Installers(false), "/Users/me", false));
        }

        [Test]
        public void AnotherOrAnUnknownOneIsReplaced()
        {
            Assert.IsTrue(AgentService.NeedsUpdate(Shipped, "{\"sha256\": \"cd34\"}", Installers(true), @"C:\Users\me", true));
            Assert.IsTrue(AgentService.NeedsUpdate(Shipped, "{\"sha256\": \"cd34\"}", Installers(false), "/Users/me", false));
            // (installed before installers kept which one: none kept, or unreadable)
            Assert.IsTrue(AgentService.NeedsUpdate(Shipped, null, Installers(true), @"C:\Users\me", true));
            Assert.IsTrue(AgentService.NeedsUpdate(Shipped, "{not json", Installers(false), "/Users/me", false));
            var installedOnly = AgentService.Find("", @"C:\Windows", @"C:\Users\me", true, Only(@"C:\Users\me\.local\bin\autopilot.exe"));
            Assert.AreEqual("installed", installedOnly.Source);
            Assert.IsTrue(AgentService.NeedsUpdate(Shipped, null, installedOnly, @"C:\Users\me", true));
        }

        [Test]
        public void OnWindowsThePathsCaseDoesntMatter()
        {
            var c = AgentService.Find("", @"c:\users\ME\.LOCAL\bin", @"C:\Users\me", true, Only(@"c:\users\ME\.LOCAL\bin\autopilot.exe"));
            Assert.IsTrue(AgentService.NeedsUpdate(Shipped, null, c, @"C:\Users\me", true));
            c = AgentService.Find("", "/Users/ME/.local/bin", "/Users/me", false, Only("/Users/ME/.local/bin/autopilot"));
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, null, c, "/Users/me", false));  // (elsewhere it does: another file)
        }

        [Test]
        public void NothingIsInstalledWithoutTheDownloadsWord()
        {
            // (a repository checkout, or a download from before downloads said which agent service they carry)
            Assert.IsFalse(AgentService.NeedsUpdate(null, null, Installers(true), @"C:\Users\me", true));
            Assert.IsFalse(AgentService.NeedsUpdate("{}", "{\"sha256\": \"cd34\"}", Installers(true), @"C:\Users\me", true));
            Assert.IsFalse(AgentService.NeedsUpdate("{\"sha256\": \"\"}", null, Installers(false), "/Users/me", false));
        }

        [Test]
        public void SomeonesOwnAgentServiceIsLeftAlone()
        {
            var setting = AgentService.Find(@"C:\Users\me\.local\bin\autopilot.exe", "", @"C:\Users\me", true, _ => true);
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, null, setting, @"C:\Users\me", true));
            var venv = AgentService.Find("", @"C:\autopilot\.venv\Scripts;C:\Users\me\.local\bin", @"C:\Users\me", true,
                Only(@"C:\autopilot\.venv\Scripts\autopilot.exe", @"C:\Users\me\.local\bin\autopilot.exe"));
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, null, venv, @"C:\Users\me", true));
            var elsewhere = AgentService.Find("", "/opt/autopilot/bin", "/home/me", false, Only("/opt/autopilot/bin/autopilot"));
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, null, elsewhere, "/home/me", false));
            Assert.IsFalse(AgentService.NeedsUpdate(Shipped, null, null, "/home/me", false));
        }

        [Test]
        public void NothingFoundIsNotInstalled()
        {
            Assert.IsNull(AgentService.Find("", @"C:\a;C:\b", @"C:\Users\me", true, _ => false));
            Assert.IsNull(AgentService.Find("   ", "", "", false, _ => false));
            Assert.IsNull(AgentService.Find(null, null, null, true, _ => false));
        }

        [Test]
        public void TheServeCommandLine()
        {
            var c = new AgentService.Command { Text = "\"C:\\Users\\me\\.local\\bin\\autopilot.exe\"", Source = "installed" };
            var line = AgentService.ServeLine(c, 4242);
            Assert.AreEqual("\"C:\\Users\\me\\.local\\bin\\autopilot.exe\" serve --port 0 --exit-with 4242", line);

            var (file, args) = AgentService.Redirected(line, @"C:\Users\me\AppData\Local\Autopilot\logs\agent-service-20261013-101500.log", true);
            Assert.AreEqual("cmd.exe", file);
            // cmd /s takes off the outer quotes and runs the rest as written
            Assert.AreEqual("/d /s /c \"\"C:\\Users\\me\\.local\\bin\\autopilot.exe\" serve --port 0 --exit-with 4242 >> \"C:\\Users\\me\\AppData\\Local\\Autopilot\\logs\\agent-service-20261013-101500.log\" 2>&1\"", args);

            var setting = new AgentService.Command { Text = @"C:\autopilot\.venv\Scripts\python.exe -m autopilot", Source = "setting" };
            Assert.AreEqual(@"C:\autopilot\.venv\Scripts\python.exe -m autopilot serve --port 0 --exit-with 7", AgentService.ServeLine(setting, 7));
        }

        [Test]
        public void TheServeCommandLineElsewhere()
        {
            var c = new AgentService.Command { Text = "'/Users/me/.local/bin/autopilot'", Source = "installed" };
            var line = AgentService.ServeLine(c, 99);
            var log = "/Users/me/Library/Application Support/Autopilot/logs/agent-service-20261013-101500.log";
            var script = AgentService.UnixScript(line, log);
            StringAssert.StartsWith("#!/bin/sh\n", script);
            // exec: the started process becomes the agent service (its pid is the one brain.json names)
            StringAssert.EndsWith("\nexec '/Users/me/.local/bin/autopilot' serve --port 0 --exit-with 99 >> '/Users/me/Library/Application Support/Autopilot/logs/agent-service-20261013-101500.log' 2>&1\n", script);
            var (file, args) = AgentService.Redirected(line, log, false, "/Users/me/Library/Application Support/Autopilot/logs/agent-service.sh");
            Assert.AreEqual("/bin/sh", file);
            Assert.AreEqual("\"/Users/me/Library/Application Support/Autopilot/logs/agent-service.sh\"", args);
        }

        [Test]
        public void TheInstallerCommandLine()
        {
            Assert.AreEqual("powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\Proj\\Library\\PackageCache\\com.ramenvr.autopilot@abc\\AgentService~\\install.ps1\"",
                AgentService.InstallLine(@"C:\Proj\Library\PackageCache\com.ramenvr.autopilot@abc\AgentService~", true));
            Assert.AreEqual("powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\pkg\\AgentService~\\install.ps1\"",
                AgentService.InstallLine(@"C:\pkg\AgentService~\", true));
            Assert.AreEqual("sh '/Users/me/Proj/Library/PackageCache/com.ramenvr.autopilot@abc/AgentService~/install.sh'",
                AgentService.InstallLine("/Users/me/Proj/Library/PackageCache/com.ramenvr.autopilot@abc/AgentService~/", false));
            var (file, args) = AgentService.Redirected(AgentService.InstallLine(@"C:\pkg\AgentService~", true), @"C:\l\install-20261013-101500.log", true);
            Assert.AreEqual("cmd.exe", file);
            Assert.AreEqual("/d /s /c \"powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\pkg\\AgentService~\\install.ps1\" >> \"C:\\l\\install-20261013-101500.log\" 2>&1\"", args);
        }

        [Test]
        public void ShQuoting()
        {
            Assert.AreEqual("'plain'", AgentService.ShQuote("plain"));
            Assert.AreEqual("'it'\\''s'", AgentService.ShQuote("it's"));
            Assert.AreEqual("''", AgentService.ShQuote(null));
        }

        // -- which agent service: a package that carries its own always runs that one (Decide) ----------------------

        const string Home = @"C:\Users\me";
        const string Ours = @"C:\Users\me\.local\bin\autopilot.exe";
        const string OnPath = @"C:\tools\autopilot.exe";
        const string ShippedStamp = "{\"sha256\": \"ab12\", \"commit\": \"36a6fec\", \"wheel\": \"autopilot-0.1.0-py3-none-any.whl\"}";
        const int OlderPid = 4321;

        static string Announcing(string commit, int pid = OlderPid)
        {
            var c = commit == null ? "" : ", \"commit\": \"" + commit + "\"";
            return "{\"url\": \"ws://127.0.0.1:51234/v1/bridge\", \"pid\": " + pid + ", \"started\": 1791900000.5" + c + "}";
        }

        /// <summary>A download (carries its own), nothing installed, nothing running; PATH has C:\tools.</summary>
        static AgentService.Situation Download(params string[] files) => new AgentService.Situation
        {
            PathEnv = @"C:\Windows;C:\tools",
            Home = Home,
            Windows = true,
            Exists = Only(files),
            Shipped = ShippedStamp,
            HasInstaller = true,
        };

        [Test]
        public void ADownloadCarriesItsOwn()
        {
            Assert.IsTrue(AgentService.Carries(ShippedStamp, true));
            Assert.IsTrue(AgentService.Carries("{\"commit\": \"36a6fec\"}", true));
            Assert.IsTrue(AgentService.Carries("{\"sha256\": \"ab12\"}", true));
            Assert.IsFalse(AgentService.Carries(ShippedStamp, false), "no installer: nothing to install it with");
            Assert.IsFalse(AgentService.Carries(null, true));
            Assert.IsFalse(AgentService.Carries("{}", true));
            Assert.IsFalse(AgentService.Carries("{\"commit\": \"\", \"sha256\": \"\"}", true));
            Assert.IsFalse(AgentService.Carries("{not json", true));
        }

        [Test]
        public void TheSettingsCommandIsNeverReplacedNorInstalledOver()
        {
            var s = Download(Ours, OnPath);
            s.Setting = @"C:\autopilot\.venv\Scripts\python.exe -m autopilot";
            s.Installed = "{\"sha256\": \"cd34\"}";
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(s, out var c, out _));
            Assert.AreEqual("setting", c.Source);
            // one running from another commit (or none said) is used as it is
            s.Announcement = Announcing("0000000");
            s.Alive = _ => true;
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
            s.Announcement = Announcing(null);
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
        }

        [Test]
        public void CarriedAndNothingInstalledItsOwnGoesInNotPaths()
        {
            var s = Download(OnPath);
            Assert.AreEqual(AgentService.Next.Install, AgentService.Decide(s, out var c, out _));
            Assert.IsNull(c, "autopilot on PATH isn't used");
            s.UpdateTried = true;  // (tried this session and failed: the window says so, PATH still unused)
            Assert.AreEqual(AgentService.Next.NotInstalled, AgentService.Decide(s, out c, out _));
            Assert.IsNull(c);
            // nothing anywhere: the first-run setup's Install, as before
            Assert.AreEqual(AgentService.Next.NotInstalled, AgentService.Decide(Download(), out _, out _));
        }

        [Test]
        public void CarriedAndAnotherInstalledItsOwnGoesIn()
        {
            var s = Download(Ours, OnPath);
            s.Installed = "{\"sha256\": \"cd34\", \"commit\": \"1111111\"}";
            Assert.AreEqual(AgentService.Next.Update, AgentService.Decide(s, out var c, out _));
            Assert.AreEqual(Ours, c.Exe, "the installer's, never PATH's");
            s.Installed = null;  // (installed before installers kept which one)
            Assert.AreEqual(AgentService.Next.Update, AgentService.Decide(s, out _, out _));
            s.UpdateTried = true;  // (once a session: then the one installed starts)
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(s, out c, out _));
            Assert.AreEqual(Ours, c.Exe);
        }

        [Test]
        public void CarriedAndTheSameInstalledItStarts()
        {
            var s = Download(Ours, OnPath);
            s.Installed = ShippedStamp;
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(s, out var c, out _));
            Assert.AreEqual("installed", c.Source);
            Assert.AreEqual("\"" + Ours + "\"", c.Text);

            var unix = new AgentService.Situation
            {
                PathEnv = "/usr/local/bin", Home = "/Users/me", Exists = Only("/usr/local/bin/autopilot", "/Users/me/.local/bin/autopilot"),
                Shipped = ShippedStamp, HasInstaller = true, Installed = ShippedStamp,
            };
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(unix, out c, out _));
            Assert.AreEqual("/Users/me/.local/bin/autopilot", c.Exe);
        }

        [Test]
        public void TheSameCommitRunningIsUsed()
        {
            var s = Download(Ours);
            s.Installed = ShippedStamp;
            s.Announcement = Announcing("36a6fec");
            s.Alive = pid => pid == OlderPid;
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out var c, out var pid));
            Assert.IsNull(c);
            Assert.AreEqual(OlderPid, pid);
            s.Announcement = Announcing("36A6FEC");
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
        }

        [Test]
        public void AnotherCommitRunningIsReplaced()
        {
            var s = Download(Ours);
            s.Installed = ShippedStamp;
            s.Alive = pid => pid == OlderPid;
            foreach (var older in new[] { Announcing("1111111"), Announcing(""), Announcing(null) })
            {
                s.Announcement = older;
                Assert.AreEqual(AgentService.Next.Replace, AgentService.Decide(s, out var c, out var pid), older);
                Assert.AreEqual(OlderPid, pid);
                Assert.IsNull(c);
            }

            // once a session: if it comes back, or stopping failed, it is used
            s.ReplaceTried = true;
            Assert.AreEqual(AgentService.Next.KeepOlder, AgentService.Decide(s, out _, out _));
            // never while this editor is connected to it
            s.ReplaceTried = false;
            s.Connected = true;
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
            // one announced but ended: as if none ran (ours starts)
            s.Connected = false;
            s.Alive = _ => false;
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(s, out var started, out var none));
            Assert.AreEqual(Ours, started.Exe);
            Assert.AreEqual(0, none);
        }

        [Test]
        public void ADownloadThatDoesntSayItsCommitReplacesNothing()
        {
            var s = Download(Ours);
            s.Shipped = "{\"sha256\": \"ab12\"}";
            s.Installed = s.Shipped;
            s.Announcement = Announcing(null);
            s.Alive = _ => true;
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
        }

        [Test]
        public void NotCarriedIsAsBefore()
        {
            // a repository checkout: PATH first, whatever runs is used, nothing installed or replaced
            var s = new AgentService.Situation
            {
                PathEnv = @"C:\tools", Home = Home, Windows = true, Exists = Only(Ours, OnPath),
                Shipped = null, HasInstaller = false, Installed = "{\"sha256\": \"cd34\"}",
            };
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(s, out var c, out _));
            Assert.AreEqual("path", c.Source);
            Assert.AreEqual(OnPath, c.Exe);
            s.Announcement = Announcing("1111111");
            s.Alive = _ => true;
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
            s.Announcement = Announcing(null);
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
            s.Announcement = null;
            s.Exists = Only();
            Assert.AreEqual(AgentService.Next.NoInstaller, AgentService.Decide(s, out c, out _));
            Assert.IsNull(c);

            // the installer but a download from before downloads said which agent service they carry: as before too
            s = new AgentService.Situation
            {
                PathEnv = @"C:\tools", Home = Home, Windows = true, Exists = Only(Ours, OnPath), Shipped = "{}", HasInstaller = true,
                Announcement = Announcing(null), Alive = _ => true,
            };
            Assert.AreEqual(AgentService.Next.UseRunning, AgentService.Decide(s, out _, out _));
            s.Announcement = null;
            Assert.AreEqual(AgentService.Next.Start, AgentService.Decide(s, out c, out _));
            Assert.AreEqual(OnPath, c.Exe);
            s.Exists = Only();
            Assert.AreEqual(AgentService.Next.NotInstalled, AgentService.Decide(s, out _, out _));
        }

        [Test]
        public void CarriedFindSkipsPath()
        {
            var c = AgentService.Find("", @"C:\tools", Home, true, Only(Ours, OnPath), true);
            Assert.AreEqual(Ours, c.Exe);
            Assert.AreEqual("installed", c.Source);
            Assert.IsNull(AgentService.Find("", @"C:\tools", Home, true, Only(OnPath), true));
            Assert.AreEqual("setting", AgentService.Find("x -m autopilot", @"C:\tools", Home, true, Only(OnPath), true).Source);
        }

        [Test]
        public void OnlyAnAgentServicesProcessIsStopped()
        {
            foreach (var name in new[] { "autopilot", "autopilot.exe", "AUTOPILOT.EXE", "python", "python.exe", "pythonw", "pythonw.exe",
                         "python3", "python3.exe", "python3.12", @"C:\Users\me\.local\bin\autopilot.exe",
                         @"C:\Users\me\AppData\Roaming\uv\python\cpython-3.12\python.exe", "/Users/me/.local/share/uv/tools/autopilot/bin/python3" })
                Assert.IsTrue(AgentService.IsAgentServiceExe(name), name);
            foreach (var name in new[] { "Unity", "Unity.exe", "UnrealEditor.exe", "explorer.exe", "cmd.exe", "node", "python2", "pythonista",
                         "autopilot-helper.exe", "notpython.exe", "", null, "  " })
                Assert.IsFalse(AgentService.IsAgentServiceExe(name), name ?? "null");
        }

        // -- only the process that wrote brain.json is stopped (a reused pid belongs to a process started after it) ----

        static readonly DateTime Epoch = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
        const double Announced = 1791900000.5;

        static string StartedAt(string started) =>
            "{\"url\": \"ws://127.0.0.1:51234/v1/bridge\", \"pid\": " + OlderPid + (started == null ? "" : ", \"started\": " + started) + "}";

        [Test]
        public void TheProcessThatWroteBrainJsonMayBeStopped()
        {
            var brain = StartedAt("1791900000.5");
            Assert.AreEqual(Announced, AgentService.AnnouncedAt(brain));
            // started before it announced itself (the usual: a few seconds before), or within the slack after
            Assert.IsTrue(AgentService.MayStop("python", Epoch.AddSeconds(Announced - 3), brain, out var why), why);
            Assert.IsTrue(AgentService.MayStop("autopilot.exe", Epoch.AddSeconds(Announced - 86400), brain, out why), why);
            Assert.IsTrue(AgentService.MayStop("python", Epoch.AddSeconds(Announced + AgentService.OwnerSlack - 0.5), brain, out why), why);
            // a local time works the same as UTC
            Assert.IsTrue(AgentService.MayStop("python", Epoch.AddSeconds(Announced - 3).ToLocalTime(), brain, out why), why);
            Assert.IsTrue(AgentService.MayStop("python", Epoch.AddSeconds(Announced - 3), "{\"pid\": 1, \"started\": 1791900000}", out why), why);
        }

        [Test]
        public void AProcessStartedAfterTheAnnouncementIsNeverStopped()
        {
            var brain = StartedAt("1791900000.5");
            Assert.IsFalse(AgentService.MayStop("python", Epoch.AddSeconds(Announced + AgentService.OwnerSlack + 1), brain, out var why));
            StringAssert.Contains("started after brain.json was written", why);
            Assert.IsFalse(AgentService.MayStop("python.exe", Epoch.AddSeconds(Announced + 3600), brain, out _));
        }

        [Test]
        public void NoStartedOrNoStartTimeNeverStops()
        {
            var at = Epoch.AddSeconds(Announced - 3);
            foreach (var brain in new[] { StartedAt(null), StartedAt("null"), StartedAt("\"1791900000.5\""), StartedAt("0"), StartedAt("-5"), StartedAt("true"), "{oops", "", null })
            {
                Assert.IsNull(AgentService.AnnouncedAt(brain), brain ?? "null");
                Assert.IsFalse(AgentService.MayStop("python", at, brain, out var why), brain ?? "null");
                StringAssert.Contains("doesn't say when it started", why);
            }
            Assert.IsFalse(AgentService.MayStop("python", null, StartedAt("1791900000.5"), out var unread));
            StringAssert.Contains("start time can't be read", unread);
        }

        [Test]
        public void ANonAgentServiceIsNeverStoppedWhenEverItStarted()
        {
            var brain = StartedAt("1791900000.5");
            foreach (var name in new[] { "Unity", "node", "cmd.exe", "", null })
                Assert.IsFalse(AgentService.MayStop(name, Epoch.AddSeconds(Announced - 3), brain, out _), name ?? "null");
        }

        [Test]
        public void BrainJsonNamingThePid()
        {
            Assert.IsTrue(AgentService.NamesPid(Announcing("1"), OlderPid));
            Assert.IsFalse(AgentService.NamesPid(Announcing("1", 99), OlderPid), "another took its place: kept");
            Assert.IsFalse(AgentService.NamesPid(null, OlderPid));
            Assert.IsFalse(AgentService.NamesPid("{oops", OlderPid));
            Assert.IsFalse(AgentService.NamesPid(Announcing("1"), 0));
        }

        [Test]
        public void TheCommitsCompared()
        {
            Assert.IsTrue(AgentService.SameCommit(ShippedStamp, Announcing("36a6fec")));
            Assert.IsFalse(AgentService.SameCommit(ShippedStamp, Announcing("36a6fed")));
            Assert.IsFalse(AgentService.SameCommit(ShippedStamp, Announcing(null)));
            Assert.IsFalse(AgentService.SameCommit(ShippedStamp, Announcing("")));
            Assert.IsFalse(AgentService.SameCommit(ShippedStamp, "{\"commit\": 36}"));
            Assert.IsTrue(AgentService.SameCommit("{\"sha256\": \"ab12\"}", Announcing("x")), "the package doesn't say: nothing to tell apart by");
        }

        // -- brain.json --------------------------------------------------------------------------------------

        static readonly Dictionary<int, int> Tree = new Dictionary<int, int>
        {
            // the editor (1) started cmd.exe (10), which started autopilot.exe (20), whose python.exe (30) started the real
            // python (40); another editor's service is 50
            [10] = 1, [20] = 10, [30] = 20, [40] = 30, [50] = 2,
        };

        static int Parent(int pid) => Tree.TryGetValue(pid, out var p) ? p : 0;

        static string Brain(int pid, string url = "ws://127.0.0.1:51234/v1/bridge") =>
            "{\"url\": \"" + url + "\", \"pid\": " + pid + ", \"started\": 1791900000.5}";

        [Test]
        public void BrainJsonOfTheProcessStarted()
        {
            Assert.AreEqual("ws://127.0.0.1:51234/v1/bridge", AgentService.AnnouncedBy(Brain(10), 10, Parent, _ => true));  // (sh's exec: the same pid)
            Assert.AreEqual("ws://127.0.0.1:51234/v1/bridge", AgentService.AnnouncedBy(Brain(40), 10, Parent, _ => true));  // (a descendant)
        }

        [Test]
        public void BrainJsonOfAnotherProcessIsNotOurs()
        {
            Assert.IsNull(AgentService.AnnouncedBy(Brain(50), 10, Parent, _ => true));
            Assert.IsNull(AgentService.AnnouncedBy(Brain(1), 10, Parent, _ => true));   // (the editor is the parent, not a child)
            Assert.IsNull(AgentService.AnnouncedBy(Brain(40), 0, Parent, _ => true));   // (nothing started)
        }

        [Test]
        public void BrainJsonOfAnEndedOrRemoteProcessIsNothing()
        {
            Assert.IsNull(AgentService.AnnouncedBy(Brain(40), 10, Parent, _ => false));
            Assert.IsNull(AgentService.AnnouncedBy(Brain(40, "ws://10.0.0.5:8765/v1/bridge"), 10, Parent, _ => true));
            Assert.IsNull(AgentService.AnnouncedBy("{\"url\": \"ws://127.0.0.1:1/v1/bridge\"}", 10, Parent, _ => true));
            Assert.IsNull(AgentService.AnnouncedBy("{\"url\": \"ws://127.0.0.1:1", 10, Parent, _ => true));
            Assert.IsNull(AgentService.AnnouncedBy("", 10, Parent, _ => true));
            Assert.IsNull(AgentService.AnnouncedBy(null, 10, Parent, _ => true));
        }

        [Test]
        public void DescendsStopsOnLoopsAndDepth()
        {
            Assert.IsFalse(AgentService.Descends(7, 3, pid => pid));  // (a process its own parent)
            Assert.IsFalse(AgentService.Descends(40, 10, Parent, 1));
            Assert.IsTrue(AgentService.Descends(40, 10, Parent, 3));
            Assert.IsFalse(AgentService.Descends(40, 10, _ => 0));
        }

        // -- logs ------------------------------------------------------------------------------------------------

        [Test]
        public void LogNames()
        {
            Assert.AreEqual("agent-service-20261013-090502.log", AgentService.LogName("agent-service", new DateTime(2026, 10, 13, 9, 5, 2)));
            Assert.AreEqual("install-20261231-235959.log", AgentService.LogName("install", new DateTime(2026, 12, 31, 23, 59, 59)));
        }

        [Test]
        public void PruneKeepsTheNewestOfItsOwn()
        {
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-logs-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                for (var i = 1; i <= 7; ++i)
                    File.WriteAllText(Path.Combine(dir, AgentService.LogName("agent-service", new DateTime(2026, 10, i, 12, 0, 0))), "x");
                File.WriteAllText(Path.Combine(dir, AgentService.LogName("install", new DateTime(2026, 9, 1, 12, 0, 0))), "x");
                File.WriteAllText(Path.Combine(dir, "agent-service.sh"), "x");
                File.WriteAllText(Path.Combine(dir, "agent-service-notes.log"), "x");

                AgentService.PruneLogs(dir, "agent-service", 4);

                var left = Directory.GetFiles(dir).Select(Path.GetFileName).OrderBy(n => n, StringComparer.Ordinal).ToList();
                CollectionAssert.AreEqual(new[]
                {
                    "agent-service-20261004-120000.log", "agent-service-20261005-120000.log", "agent-service-20261006-120000.log",
                    "agent-service-20261007-120000.log", "agent-service-notes.log", "agent-service.sh", "install-20260901-120000.log",
                }, left);

                AgentService.PruneLogs(Path.Combine(dir, "missing"), "install", 5);  // (no folder: nothing)
            }
            finally
            {
                Directory.Delete(dir, true);
            }
        }
    }
}
