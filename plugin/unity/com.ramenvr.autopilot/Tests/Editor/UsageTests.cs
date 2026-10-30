// The Settings page's model: reading `usage` messages, summing runs, the numbers as shown, the ledger and the run history
// with and without usage; Autopilot's `bill` messages (runs through Aura): read by the client, kept with the run and in the
// ledger, summed, and old records without them still loading.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Autopilot.Playtest.Editor;
using NUnit.Framework;

namespace Autopilot.Playtest.Tests
{
    public class UsageTests
    {
        const string Message = @"{""type"": ""usage"", ""final"": false, ""models"": [
            {""kind"": ""llm"", ""model"": ""claude-opus-5-5"", ""calls"": 12, ""input"": 30120, ""output"": 8211,
             ""cache_read"": 912000, ""cache_write"": 41000},
            {""kind"": ""decision"", ""model"": ""policy-v3"", ""calls"": 2310, ""input"": 1450000, ""output"": 4620}]}";

        const string BillMessage = @"{""type"": ""bill"", ""usd"": 0.593177, ""billed"": true}";

        static Dictionary<string, object> Parse(string text) => Json.ParseObject(text);

        [Test]
        public void RowsReadEveryField()
        {
            var rows = AutopilotUsage.Rows(Parse(Message));
            Assert.AreEqual(2, rows.Count);
            var llm = rows[0];
            Assert.AreEqual("llm", llm.Kind);
            Assert.AreEqual("LLM claude-opus-5-5", llm.Label);
            Assert.AreEqual(12, llm.Calls);
            Assert.AreEqual(30120, llm.Input);
            Assert.AreEqual(8211, llm.Output);
            Assert.AreEqual(912000, llm.CacheRead);
            Assert.AreEqual(41000, llm.CacheWrite);
            var dec = rows[1];
            Assert.IsTrue(dec.IsDecision);
            Assert.AreEqual("Decision Model policy-v3", dec.Label);
            Assert.AreEqual(2310, dec.Calls);
            Assert.AreEqual(0, dec.CacheRead);
        }

        [Test]
        public void RowsTolerateOddInput()
        {
            Assert.AreEqual(0, AutopilotUsage.Rows(null).Count);
            Assert.AreEqual(0, AutopilotUsage.Rows(Parse(@"{""models"": 3}")).Count);
            var rows = AutopilotUsage.Rows(Parse(@"{""models"": [1, ""x"", {""kind"": ""LLM"", ""model"": ""m"", ""input"": 2.0, ""output"": -5}]}"));
            Assert.AreEqual(1, rows.Count);
            Assert.AreEqual("llm", rows[0].Kind);
            Assert.AreEqual(2, rows[0].Input);
            Assert.AreEqual(0, rows[0].Output);
            Assert.AreEqual(0, rows[0].Calls);
        }

        [Test]
        public void SumAddsPerModelLlmFirst()
        {
            var a = AutopilotUsage.Rows(Parse(Message));
            var b = AutopilotUsage.Rows(Parse(@"{""models"": [
                {""kind"": ""decision"", ""model"": ""policy-v3"", ""calls"": 10, ""input"": 100, ""output"": 1},
                {""kind"": ""llm"", ""model"": ""claude-opus-5-5"", ""calls"": 1, ""input"": 1, ""output"": 2, ""cache_read"": 3, ""cache_write"": 4},
                {""kind"": ""llm"", ""model"": ""other"", ""calls"": 2, ""input"": 5, ""output"": 6}]}"));
            var sum = AutopilotUsage.Sum(new IEnumerable<UsageRow>[] { b, a, null });
            Assert.AreEqual(new[] { "LLM claude-opus-5-5", "LLM other", "Decision Model policy-v3" }, sum.Select(r => r.Label).ToArray());
            Assert.AreEqual(13, sum[0].Calls);
            Assert.AreEqual(30121, sum[0].Input);
            Assert.AreEqual(8213, sum[0].Output);
            Assert.AreEqual(912003, sum[0].CacheRead);
            Assert.AreEqual(41004, sum[0].CacheWrite);
            Assert.AreEqual(2320, sum[2].Calls);
            Assert.AreEqual(1450100, sum[2].Input);
            var llm = AutopilotUsage.Tokens(sum, "llm");
            Assert.AreEqual(30126, llm.input);
            Assert.AreEqual(8219, llm.output);
            Assert.AreEqual((1450100L, 4621L), AutopilotUsage.Tokens(sum, "decision"));
        }

        [Test]
        public void NumbersAsShown()
        {
            Assert.AreEqual("0", AutopilotUsage.Thousands(0));
            Assert.AreEqual("912,000", AutopilotUsage.Thousands(912000));
            Assert.AreEqual("1,450,000", AutopilotUsage.Thousands(1450000));
            Assert.AreEqual("999", AutopilotUsage.Compact(999));
            Assert.AreEqual("1.2k", AutopilotUsage.Compact(1234));
            Assert.AreEqual("30k", AutopilotUsage.Compact(30120));
            Assert.AreEqual("912k", AutopilotUsage.Compact(912000));
            Assert.AreEqual("1M", AutopilotUsage.Compact(999_600));
            Assert.AreEqual("1.5M", AutopilotUsage.Compact(1_460_000));
            Assert.AreEqual("3.4B", AutopilotUsage.Compact(3_400_000_000));
        }

        [Test]
        public void KeptDropsTheType()
        {
            var kept = AutopilotUsage.Kept(Parse(Message));
            Assert.IsFalse(kept.ContainsKey("type"));
            Assert.AreEqual(false, kept["final"]);
            Assert.AreEqual(2, AutopilotUsage.Rows(kept).Count);
        }

        [Test]
        public void RunsKeepUsageAndOldRunsStillLoad()
        {
            var run = new AutopilotRun { Id = "20261013-120000", Kind = "verify", Prompt = "Verify that x", Started = 1790000000 };
            run.Usage = AutopilotUsage.Kept(Parse(Message));
            var copy = AutopilotRun.Load(Json.ParseObject(Json.Write(run.Save(100))));
            Assert.AreEqual(2, AutopilotUsage.Rows(copy.Usage).Count);

            var old = AutopilotRun.Load(Json.ParseObject(@"{""id"": ""20250101-000000"", ""kind"": ""goal"", ""prompt"": ""p"", ""started"": 1, ""ended"": 2}"));
            Assert.IsNull(old.Usage);
            Assert.AreEqual("goal", old.Kind);
            Assert.AreEqual(0, AutopilotUsage.Rows(old.Usage).Count);
        }

        [Test]
        public void LedgerRoundTripsAndToleratesBadFiles()
        {
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-usage-test-" + System.Guid.NewGuid().ToString("N"));
            try
            {
                var path = Path.Combine(dir, "UserSettings", "AutopilotUsage.json");
                Assert.AreEqual(0, AutopilotUsage.Load(path).Count);  // (missing)

                var list = new List<UsageEntry>();
                var later = new AutopilotRun { Id = "b", Kind = "explore", Started = 20, Usage = AutopilotUsage.Kept(Parse(Message)) };
                var earlier = new AutopilotRun { Id = "a", Kind = "verify", Prompt = "Verify that y", Started = 10, Usage = AutopilotUsage.Kept(Parse(Message)) };
                AutopilotUsage.Upsert(list, later);
                AutopilotUsage.Upsert(list, earlier);
                Assert.AreEqual(new[] { "a", "b" }, list.Select(e => e.Id).ToArray());
                later.Prompt = "Explore for 5 minutes";
                AutopilotUsage.Upsert(list, later);
                Assert.AreEqual(2, list.Count);
                AutopilotUsage.Save(path, list);

                var back = AutopilotUsage.Load(path);
                Assert.AreEqual(2, back.Count);
                Assert.AreEqual("Explore for 5 minutes", back[1].Prompt);
                Assert.AreEqual("verify", back[0].Kind);
                Assert.AreEqual(10, back[0].Started);
                var total = AutopilotUsage.Sum(back.Select(e => (IEnumerable<UsageRow>)e.Rows));
                Assert.AreEqual(24, total[0].Calls);

                File.WriteAllText(path, "{not json");
                Assert.AreEqual(0, AutopilotUsage.Load(path).Count);
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }
        [Test]
        public void BillMessagesKeptAndRead()
        {
            var kept = Billing.Kept(Parse(BillMessage));
            Assert.IsFalse(kept.ContainsKey("type"));
            Assert.AreEqual(0.593177, Billing.RunUsd(kept).Value, 1e-9);
            Assert.AreEqual("$0.59", Billing.Money(Billing.RunUsd(kept).Value));

            // billed false (Aura bills it later, or didn't): kept with its reason, not shown as billed
            var later = Billing.Kept(Parse(@"{""type"": ""bill"", ""usd"": 0.2, ""billed"": false, ""reason"": ""billed when the token expires""}"));
            Assert.AreEqual("billed when the token expires", later["reason"]);
            Assert.IsNull(Billing.RunUsd(later));
            Assert.IsNull(Billing.RunUsd(null));  // (BYOK runs, and runs from before bills)
            Assert.IsNull(Billing.RunUsd(Parse(@"{""billed"": true}")));
            Assert.IsNull(Billing.RunUsd(Parse(@"{""billed"": true, ""usd"": ""lots""}")));
            Assert.IsNull(Billing.Kept(null));
        }

        [Test]
        public void TheClientTakesABillMessage()
        {
            var client = new PlaytestClient(EditorHost.Instance) { TestWire = _ => { } };
            try
            {
                client.TestConnected(true);
                client.TestReceive(@"{""type"": ""welcome"", ""role"": ""editor"", ""session"": ""usage-test"", ""features"": [""account""]}");
                Dictionary<string, object> got = null;
                client.OnBill += m => got = m;
                client.TestReceive(BillMessage);
                Assert.IsNotNull(got);
                Assert.AreSame(got, client.LastBill);
                Assert.AreEqual(0.593177, Json.Num(got, "usd", 0), 1e-9);
            }
            finally
            {
                client.Disconnect();
                client.TestWire = null;
            }
        }

        [Test]
        public void RunsKeepBillsAndOldRunsStillLoad()
        {
            var run = new AutopilotRun { Id = "20261014-120000", Kind = "goal", Prompt = "Reach the gate", Started = 1790000000 };
            run.Usage = AutopilotUsage.Kept(Parse(Message));
            run.Bill = Billing.Kept(Parse(BillMessage));
            var copy = AutopilotRun.Load(Json.ParseObject(Json.Write(run.Save(100))));
            Assert.AreEqual(0.593177, Billing.RunUsd(copy.Bill).Value, 1e-9);
            Assert.AreEqual(2, AutopilotUsage.Rows(copy.Usage).Count);

            var old = AutopilotRun.Load(Json.ParseObject(@"{""id"": ""20250101-000000"", ""kind"": ""goal"", ""prompt"": ""p"", ""started"": 1, ""ended"": 2,
                ""usage"": {""final"": true, ""models"": []}}"));
            Assert.IsNull(old.Bill);
            Assert.IsNotNull(old.Usage);
        }

        [Test]
        public void TheLedgerKeepsBillsAndOldLedgersLoad()
        {
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-bill-test-" + Guid.NewGuid().ToString("N"));
            try
            {
                var path = Path.Combine(dir, "UserSettings", "AutopilotUsage.json");
                // A ledger written before bills: its runs load, none billed
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, @"{""version"": 1, ""runs"": [{""id"": ""a"", ""started"": 10, ""kind"": ""verify"", ""prompt"": ""Verify that y"",
                    ""usage"": {""final"": true, ""models"": [{""kind"": ""llm"", ""model"": ""m"", ""calls"": 1, ""input"": 10, ""output"": 5}]}}]}");
                var list = AutopilotUsage.Load(path);
                Assert.AreEqual(1, list.Count);
                Assert.IsNull(list[0].Bill);
                Assert.IsNull(list[0].Billed);
                Assert.IsNull(AutopilotUsage.TotalBilled(list));

                // Runs through Aura (one with only its bill so far), a BYOK run, a bill Aura sends later
                var aura = new AutopilotRun { Id = "b", Kind = "goal", Started = 20, Usage = AutopilotUsage.Kept(Parse(Message)), Bill = Billing.Kept(Parse(BillMessage)) };
                var billOnly = new AutopilotRun { Id = "c", Kind = "explore", Started = 30, Bill = Billing.Kept(Parse(@"{""usd"": 0.004, ""billed"": true}")) };
                var byok = new AutopilotRun { Id = "d", Kind = "verify", Started = 40, Usage = AutopilotUsage.Kept(Parse(Message)) };
                var later = new AutopilotRun { Id = "e", Kind = "verify", Started = 50, Bill = Billing.Kept(Parse(@"{""usd"": 9, ""billed"": false, ""reason"": ""later""}")) };
                foreach (var r in new[] { aura, billOnly, byok, later }) AutopilotUsage.Upsert(list, r);
                AutopilotUsage.Save(path, list);

                var back = AutopilotUsage.Load(path);
                Assert.AreEqual(new[] { "a", "b", "c", "d", "e" }, back.Select(e => e.Id).ToArray());
                Assert.AreEqual(0.593177, back[1].Billed.Value, 1e-9);
                Assert.AreEqual("< $0.01", Billing.Money(back[2].Billed.Value));
                Assert.IsNull(back[2].Usage);
                Assert.IsNull(back[3].Billed);
                Assert.IsNull(back[4].Billed);
                Assert.AreEqual("later", Json.Str(back[4].Bill, "reason"));
                Assert.AreEqual(0.597177, AutopilotUsage.TotalBilled(back).Value, 1e-9);
                Assert.AreEqual("$0.60", Billing.Money(AutopilotUsage.TotalBilled(back).Value));
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }

        [Test]
        public void TheSessionKeepsTheBillWithTheRunAndInTheLedger()
        {
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-bill-session-" + Guid.NewGuid().ToString("N"));
            var path = Path.Combine(dir, "AutopilotUsage.json");
            var run = new AutopilotRun { Id = "usage-test-bill", Kind = "goal", Prompt = "Reach the gate", Started = AutopilotText.UnixNow - 30 };
            var onBill = typeof(AutopilotSession).GetMethod("OnBill", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(onBill, "AutopilotSession.OnBill");
            AutopilotUsage.Flush();
            AutopilotUsage.LedgerOverride = path;
            AutopilotUsage.TestReload();
            try
            {
                // (the run going now: the bill comes once it is finished, before its result)
                AutopilotSession.Runs.Add(run);
                onBill.Invoke(null, new object[] { Parse(BillMessage) });
                Assert.AreEqual(0.593177, Billing.RunUsd(run.Bill).Value, 1e-9);
                var entry = AutopilotUsage.Find(run.Id);
                Assert.IsNotNull(entry, "the ledger has the run");
                Assert.AreEqual(0.593177, entry.Billed.Value, 1e-9);
                Assert.IsTrue(File.Exists(path), "a bill is written at once");
                var saved = AutopilotUsage.Load(path).Single(e => e.Id == run.Id);
                Assert.AreEqual(0.593177, saved.Billed.Value, 1e-9);
            }
            finally
            {
                AutopilotSession.Runs.Remove(run);
                AutopilotUsage.LedgerOverride = null;
                AutopilotUsage.TestReload();
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }
    }
}
