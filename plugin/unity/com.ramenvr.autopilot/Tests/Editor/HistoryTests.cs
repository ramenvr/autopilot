// The run history kept across editor restarts (UserSettings/AutopilotRuns/): a run's file written and read back, the
// newest 50 kept, a run going when the editor closed shown with no result, runs already here kept newest, and the files
// of runs no longer kept deleted, those of the runs before a memory reset moved aside. Every test of the package reads and writes a temporary folder instead of the project's.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autopilot.Playtest.Editor;
using NUnit.Framework;

namespace Autopilot.Playtest.Tests
{
    /// <summary>Every test of the package: the run history in a temporary folder, never the project's.</summary>
    [SetUpFixture]
    public class HistoryFolder
    {
        string folder;

        [OneTimeSetUp]
        public void SetUp()
        {
            AutopilotSession.SaveHistory();  // (the project's runs so far: in the project's folder)
            folder = Path.Combine(Path.GetTempPath(), "autopilot-runs-" + Guid.NewGuid().ToString("N"));
            AutopilotSession.HistoryDirOverride = folder;
        }

        [OneTimeTearDown]
        public void TearDown()
        {
            AutopilotSession.HistoryDirOverride = null;
            try
            {
                if (Directory.Exists(folder)) Directory.Delete(folder, true);
            }
            catch (Exception)
            {
                // (left in the temp folder)
            }
        }
    }

    public class HistoryTests
    {
        string dir;
        string dirWas;
        string ledger;
        List<AutopilotRun> runsWere;

        [SetUp]
        public void SetUp()
        {
            dir = Path.Combine(Path.GetTempPath(), "autopilot-history-" + Guid.NewGuid().ToString("N"));
            dirWas = AutopilotSession.HistoryDirOverride;
            AutopilotSession.HistoryDirOverride = dir;
            AutopilotUsage.Flush();
            ledger = Path.Combine(dir + "-usage", "AutopilotUsage.json");
            AutopilotUsage.LedgerOverride = ledger;
            AutopilotUsage.TestReload();
            runsWere = AutopilotSession.Runs.ToList();
            AutopilotSession.Runs.Clear();
            AutopilotSession.TestForgetSaved();
        }

        [TearDown]
        public void TearDown()
        {
            AutopilotSession.Runs.Clear();
            AutopilotSession.Runs.AddRange(runsWere);
            AutopilotSession.TestForgetSaved();  // (the editor's runs: written again where they belong at the next save)
            AutopilotSession.HistoryDirOverride = dirWas;
            AutopilotUsage.LedgerOverride = null;
            AutopilotUsage.TestReload();
            foreach (var d in new[] { dir, Path.GetDirectoryName(ledger) })
            {
                try
                {
                    if (Directory.Exists(d)) Directory.Delete(d, true);
                }
                catch (Exception)
                {
                    // (left in the temp folder)
                }
            }
        }

        static AutopilotRun Ended(string id, double started, string verdict = "CONFIRMED")
        {
            var run = new AutopilotRun
            {
                Id = id, Kind = "verify", Prompt = "The gate opens on E", Started = started, StageSince = started, Ended = started + 206,
                Result = new Dictionary<string, object> { ["verdict"] = verdict, ["seconds"] = 206.0, ["attempts"] = 2.0 },
                ReportDir = @"C:\Game\Logs\Playtest\" + id,
            };
            run.Feed.Add(new AutopilotFeedLine { At = 1, Text = "Starting the game", Kind = "stage" });
            return run;
        }

        /// <summary>A new editor: what it knows of the files forgotten, the runs read from disk.</summary>
        static void Restart()
        {
            AutopilotSession.Runs.Clear();
            AutopilotSession.TestForgetSaved();
            AutopilotSession.LoadHistory();
        }

        [Test]
        public void ARunIsWrittenWholeAndReadBack()
        {
            var run = Ended("20261019-132500", 1792000000.25);
            run.Findings.Add(new Dictionary<string, object> { ["signature"] = "s1", ["summary"] = "Fell through the floor", ["source"] = "engine", ["detector"] = "fall", ["count"] = 3.0 });
            run.NotABug.Add("s1");
            run.Usage = new Dictionary<string, object> { ["final"] = true, ["models"] = new List<object>() };
            run.Bill = new Dictionary<string, object> { ["usd"] = 0.42, ["billed"] = true };
            run.NoRecording = "No recording: it runs without rendering";
            run.AddStep(new Dictionary<string, object> { ["call"] = 1.0, ["t"] = 0.5 });
            AutopilotSession.Runs.Add(run);
            AutopilotSession.SaveHistory();

            var file = Path.Combine(dir, "20261019-132500.json");
            Assert.IsTrue(File.Exists(file));
            CollectionAssert.IsEmpty(Directory.GetFiles(dir, "*.tmp"), "the temp file put in place");
            CollectionAssert.IsEmpty(Json.List(Json.ParseObject(File.ReadAllText(file)), "steps"), "no policy steps on disk");

            Restart();
            Assert.AreEqual(1, AutopilotSession.Runs.Count);
            var back = AutopilotSession.Runs[0];
            Assert.AreEqual("20261019-132500", back.Id);
            Assert.AreEqual("verify", back.Kind);
            Assert.AreEqual("The gate opens on E", back.Prompt);
            Assert.AreEqual(1792000000.25, back.Started, 1e-6);
            Assert.AreEqual("CONFIRMED", back.Verdict);
            Assert.AreEqual(206, back.Took, 1e-6);
            Assert.AreEqual(2, back.Attempts);
            Assert.AreEqual(run.ReportDir, back.ReportDir);
            Assert.AreEqual("Fell through the floor", Json.Str(back.Findings.Single(), "summary"));
            CollectionAssert.Contains(back.NotABug, "s1");
            Assert.AreEqual(0.42, Json.Num(back.Bill, "usd", 0), 1e-9);
            Assert.IsTrue(Json.Bool(back.Usage, "final", false));
            Assert.AreEqual(run.NoRecording, back.NoRecording);
            Assert.AreEqual("Starting the game", back.Feed.Single().Text);
            Assert.IsFalse(back.EditorClosed);
            CollectionAssert.IsEmpty(back.Steps);
        }

        [Test]
        public void TheNewest50AreKeptOldestFirst()
        {
            Directory.CreateDirectory(dir);
            // (written in no order: read by the time each started)
            foreach (var i in Enumerable.Range(0, 55).OrderBy(i => (i * 37) % 55))
            {
                AutopilotSession.Runs.Add(Ended($"run{i:00}", 1792000000 + i * 60));
                AutopilotSession.SaveHistory();
                AutopilotSession.Runs.Clear();
                AutopilotSession.TestForgetSaved();
            }
            File.WriteAllText(Path.Combine(dir, "broken.json"), "{not json");
            File.WriteAllText(Path.Combine(dir, "other.json"), Json.Write(Ended("run99", 1792000000).Save(0)));  // (named for another run)

            AutopilotSession.LoadHistory();
            Assert.AreEqual(AutopilotSession.MaxRuns, AutopilotSession.Runs.Count);
            CollectionAssert.AreEqual(Enumerable.Range(5, 50).Select(i => $"run{i:00}"), AutopilotSession.Runs.Select(r => r.Id));
            for (var i = 0; i < 5; ++i) Assert.IsFalse(File.Exists(Path.Combine(dir, $"run{i:00}.json")), $"run{i:00} deleted");
            Assert.IsTrue(File.Exists(Path.Combine(dir, "run05.json")));
            Assert.IsTrue(File.Exists(Path.Combine(dir, "broken.json")), "a file that isn't a run: left alone");
        }

        [Test]
        public void ARunGoingWhenTheEditorClosedHasNoResult()
        {
            var live = new AutopilotRun { Id = "live", Kind = "goal", Prompt = "Reach the gate", Started = AutopilotText.UnixNow - 40, StageSince = AutopilotText.UnixNow - 40 };
            live.Feed.Add(new AutopilotFeedLine { At = 2, Text = "Trying the harness (run 1)", Kind = "attempt" });
            var lost = Ended("lost", AutopilotText.UnixNow - 400);
            lost.Result = null;
            lost.LostConnection = true;
            AutopilotSession.Runs.Add(lost);
            AutopilotSession.Runs.Add(live);
            Assert.AreEqual("LIVE", live.Verdict);
            AutopilotSession.SaveHistory();

            Restart();
            var back = AutopilotSession.Find("live");
            Assert.IsFalse(back.Active);
            Assert.AreEqual("NO RESULT", back.Verdict);
            Assert.IsTrue(back.EditorClosed);
            Assert.AreEqual(AutopilotSession.EditorClosedLine, back.Feed.Last().Text);
            Assert.AreEqual(40, back.Elapsed, 5, "ended when it was last saved");
            Assert.IsNull(AutopilotSession.Current, "no run going");

            var dropped = AutopilotSession.Find("lost");
            Assert.AreEqual("NO RESULT", dropped.Verdict);
            Assert.IsFalse(dropped.LostConnection, "never reopened by the agent service's events now");

            // Saved again with its NO RESULT: the next editor reads it the same, without a second line
            AutopilotSession.SaveHistory();
            Restart();
            back = AutopilotSession.Find("live");
            Assert.AreEqual("NO RESULT", back.Verdict);
            Assert.AreEqual(1, back.Feed.Count(l => l.Text == AutopilotSession.EditorClosedLine));
        }

        [Test]
        public void ARunAlreadyHereStaysNewest()
        {
            AutopilotSession.Runs.Add(Ended("old", 1792000000));
            AutopilotSession.SaveHistory();
            AutopilotSession.Runs.Clear();
            AutopilotSession.TestForgetSaved();
            var now = new AutopilotRun { Id = "now", Kind = "goal", Started = 1700000000, StageSince = 1700000000 };  // (an earlier clock: still newest)
            AutopilotSession.Runs.Add(now);
            AutopilotSession.LoadHistory();
            CollectionAssert.AreEqual(new[] { "old", "now" }, AutopilotSession.Runs.Select(r => r.Id));
            Assert.AreSame(now, AutopilotSession.Current);
        }

        [Test]
        public void ARunNoLongerKeptLosesItsFile()
        {
            var a = Ended("a", 1792000000);
            var b = Ended("b", 1792000060);
            AutopilotSession.Runs.Add(a);
            AutopilotSession.Runs.Add(b);
            AutopilotSession.SaveHistory();
            Assert.IsTrue(File.Exists(Path.Combine(dir, "a.json")));
            AutopilotSession.Runs.Remove(a);
            AutopilotSession.SaveHistory();
            Assert.IsFalse(File.Exists(Path.Combine(dir, "a.json")));
            Assert.IsTrue(File.Exists(Path.Combine(dir, "b.json")));

            // Changed: written again; unchanged: not
            File.WriteAllText(Path.Combine(dir, "b.json"), Json.Write(b.Save(0)).Replace("The gate opens on E", "edited here"));
            AutopilotSession.SaveHistory();
            StringAssert.Contains("edited here", File.ReadAllText(Path.Combine(dir, "b.json")), "unchanged: not written");
            ++b.Version;
            AutopilotSession.SaveHistory();
            StringAssert.DoesNotContain("edited here", File.ReadAllText(Path.Combine(dir, "b.json")));
        }

        [Test]
        public void AMemoryResetSetsTheRunsEndedAside()
        {
            AutopilotSession.Runs.Add(Ended("a", 1792000000));
            AutopilotSession.Runs.Add(Ended("b", 1792000060));
            AutopilotSession.SaveHistory();
            var going = new AutopilotRun { Id = "going", Kind = "goal", Prompt = "Reach the tower", Started = AutopilotText.UnixNow, StageSince = AutopilotText.UnixNow };
            AutopilotSession.Runs.Add(going);
            ++AutopilotSession.Runs[1].Version;  // (changed since it was saved: moved as it is now)
            AutopilotSession.Runs[1].Prompt = "The bridge holds";

            var backup = AutopilotSession.SetRunsAside();
            CollectionAssert.AreEqual(new[] { "going" }, AutopilotSession.Runs.Select(r => r.Id), "the run going stays");
            Assert.AreEqual(Path.Combine(dir, AutopilotSession.ResetBackup), Path.GetDirectoryName(backup));
            CollectionAssert.AreEquivalent(new[] { "a.json", "b.json" }, Directory.GetFiles(backup).Select(f => Path.GetFileName(f)));
            StringAssert.Contains("The bridge holds", File.ReadAllText(Path.Combine(backup, "b.json")));
            CollectionAssert.AreEqual(new[] { "going.json" }, Directory.GetFiles(dir).Select(f => Path.GetFileName(f)));

            // Kept aside: saving leaves them, the next editor doesn't list them, and a second reset makes its own folder
            AutopilotSession.SaveHistory();
            Assert.IsTrue(File.Exists(Path.Combine(backup, "a.json")));
            Restart();
            CollectionAssert.AreEqual(new[] { "going" }, AutopilotSession.Runs.Select(r => r.Id), "(ended now: the editor closed)");
            var second = AutopilotSession.SetRunsAside();
            Assert.AreNotEqual(backup, second);
            CollectionAssert.IsEmpty(AutopilotSession.Runs);
            CollectionAssert.IsEmpty(Directory.GetFiles(dir));
            Assert.IsNull(AutopilotSession.SetRunsAside(), "nothing to set aside");
        }

        [Test]
        public void ForgettingTheRunsDeletesTheirFiles()
        {
            AutopilotSession.Runs.Add(Ended("a", 1792000000));
            AutopilotSession.SaveHistory();
            Assert.IsTrue(Directory.Exists(dir));
            AutopilotSession.Forget();
            CollectionAssert.IsEmpty(AutopilotSession.Runs);
            Assert.IsFalse(Directory.Exists(dir));
            AutopilotSession.LoadHistory();
            CollectionAssert.IsEmpty(AutopilotSession.Runs);
        }
    }
}
