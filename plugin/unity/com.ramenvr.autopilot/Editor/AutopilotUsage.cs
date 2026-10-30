// The Settings page's model: the tokens each run's models used (the brain's `usage` messages, docs/protocol.md), as rows
// per model, summed over the project's runs, and what Autopilot billed for each run through Aura (its `bill` message). A
// run's last usage and its bill are kept with it in the run history (across script reloads) and in the project's usage
// ledger, UserSettings/AutopilotUsage.json (per user, kept across editor restarts), which the page's "This project"
// and run list read.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Autopilot.Playtest.Editor
{
    /// <summary>One model's tokens: kind "llm" (the agent's language model) or "decision" (the model that picks each
    /// action while the harness plays).</summary>
    public sealed class UsageRow
    {
        public string Kind = "";
        public string Model = "";
        public long Calls;
        public long Input;
        public long Output;
        public long CacheRead;
        public long CacheWrite;

        public bool IsDecision => Kind == "decision";

        /// <summary>"LLM <model>" or "Decision Model <model>", the model id as the message gave it.</summary>
        public string Label
        {
            get
            {
                switch (Kind)
                {
                    case "llm": return "LLM " + Model;
                    case "decision": return "Decision Model " + Model;
                    default: return string.IsNullOrEmpty(Kind) ? Model : Kind + " " + Model;
                }
            }
        }
    }

    public sealed class UsageEntry
    {
        public string Id = "";
        public double Started;
        public string Kind = "run";
        public string Prompt = "";
        /// <summary>{"final": bool, "models": [...]} as the last `usage` message said.</summary>
        public Dictionary<string, object> Usage;
        /// <summary>{"usd", "billed", "reason"?} as the `bill` message said (runs through Aura; null otherwise, and in
        /// entries kept before bills).</summary>
        public Dictionary<string, object> Bill;

        public List<UsageRow> Rows => AutopilotUsage.Rows(Usage);

        /// <summary>What Autopilot billed for the run, in dollars (null: not billed, or not through Aura).</summary>
        public double? Billed => Billing.RunUsd(Bill);
    }

    public static class AutopilotUsage
    {
        public const int MaxEntries = 1000;

        // -- reading and summing ----------------------------------------------------------------------------

        static long Count(Dictionary<string, object> o, string key)
        {
            var v = Json.Num(o, key, 0);
            return double.IsNaN(v) || v < 0 ? 0 : (long)Math.Round(v);
        }

        /// <summary>The rows of a `usage` message (or the usage kept with a run): one per model, in its order.</summary>
        public static List<UsageRow> Rows(Dictionary<string, object> usage)
        {
            var rows = new List<UsageRow>();
            foreach (var x in Json.List(usage, "models") ?? new List<object>())
            {
                if (!(x is Dictionary<string, object> m)) continue;
                rows.Add(new UsageRow
                {
                    Kind = Json.Str(m, "kind").Trim().ToLowerInvariant(),
                    Model = Json.Str(m, "model"),
                    Calls = Count(m, "calls"),
                    Input = Count(m, "input"),
                    Output = Count(m, "output"),
                    CacheRead = Count(m, "cache_read"),
                    CacheWrite = Count(m, "cache_write"),
                });
            }
            return rows;
        }

        /// <summary>What a usage message keeps of itself: final and models (not its type).</summary>
        public static Dictionary<string, object> Kept(Dictionary<string, object> message)
        {
            if (message == null) return null;
            return new Dictionary<string, object>
            {
                ["final"] = Json.Bool(message, "final", false),
                ["models"] = Json.List(message, "models") ?? new List<object>(),
            };
        }

        /// <summary>Runs' rows summed per model (LLM rows first, then decision models; each in order of first
        /// appearance).</summary>
        public static List<UsageRow> Sum(IEnumerable<IEnumerable<UsageRow>> runs)
        {
            var sum = new List<UsageRow>();
            var at = new Dictionary<string, UsageRow>();
            foreach (var run in runs)
            {
                if (run == null) continue;
                foreach (var r in run)
                {
                    var key = r.Kind + "\n" + r.Model;
                    if (!at.TryGetValue(key, out var s))
                    {
                        s = new UsageRow { Kind = r.Kind, Model = r.Model };
                        at[key] = s;
                        sum.Add(s);
                    }
                    s.Calls += r.Calls;
                    s.Input += r.Input;
                    s.Output += r.Output;
                    s.CacheRead += r.CacheRead;
                    s.CacheWrite += r.CacheWrite;
                }
            }
            return sum.Where(r => !r.IsDecision).Concat(sum.Where(r => r.IsDecision)).ToList();
        }

        /// <summary>Input and output tokens of the rows of one kind ("llm" or "decision").</summary>
        public static (long input, long output) Tokens(IEnumerable<UsageRow> rows, string kind)
        {
            long i = 0, o = 0;
            foreach (var r in rows ?? Enumerable.Empty<UsageRow>())
            {
                if (r.Kind != kind) continue;
                i += r.Input;
                o += r.Output;
            }
            return (i, o);
        }

        /// <summary>The sum of the runs' bills (null when none of them was billed).</summary>
        public static double? TotalBilled(IEnumerable<UsageEntry> runs)
        {
            double? total = null;
            foreach (var e in runs ?? Enumerable.Empty<UsageEntry>())
            {
                var usd = e?.Billed;
                if (usd != null) total = (total ?? 0) + usd.Value;
            }
            return total;
        }

        /// <summary>12,345,678.</summary>
        public static string Thousands(long v)
        {
            return v.ToString("#,0", CultureInfo.InvariantCulture);
        }

        /// <summary>999, 1.2k, 34k, 3.4M, 1.2B.</summary>
        public static string Compact(long v)
        {
            var a = Math.Abs((double)v);
            string Unit(double x, string unit) => (x < 9.95 ? x.ToString("0.#", CultureInfo.InvariantCulture) : Math.Round(x).ToString("0", CultureInfo.InvariantCulture)) + unit;
            if (a < 1000) return v.ToString(CultureInfo.InvariantCulture);
            var sign = v < 0 ? "-" : "";
            if (a < 999_500) return sign + Unit(a / 1e3, "k");
            if (a < 999_500_000) return sign + Unit(a / 1e6, "M");
            return sign + Unit(a / 1e9, "B");
        }

        // -- the ledger ----------------------------------------------------------------------------------------

        static List<UsageEntry> entries;
        static bool dirty;
        static double lastSave;

        /// <summary>Goes up at every change (the page builds itself again).</summary>
        public static int Version { get; private set; }

        /// <summary>Tests: the ledger read and written instead of the project's (null: the project's).</summary>
        internal static string LedgerOverride;

        static string LedgerPath => LedgerOverride ?? Path.Combine(Directory.GetParent(Application.dataPath).FullName, "UserSettings", "AutopilotUsage.json");

        /// <summary>Tests: forget the ledger read (the next use reads it again, from LedgerOverride when set).</summary>
        internal static void TestReload()
        {
            entries = null;
            dirty = false;
            ++Version;
        }

        /// <summary>The project's runs with usage, oldest first.</summary>
        public static List<UsageEntry> Entries
        {
            get
            {
                if (entries == null) entries = Load(LedgerPath);
                return entries;
            }
        }

        public static List<UsageEntry> Load(string path)
        {
            var list = new List<UsageEntry>();
            try
            {
                if (!File.Exists(path)) return list;
                var o = Json.ParseObject(File.ReadAllText(path));
                foreach (var x in Json.List(o, "runs") ?? new List<object>())
                {
                    if (!(x is Dictionary<string, object> e)) continue;
                    var id = Json.Str(e, "id");
                    if (string.IsNullOrEmpty(id)) continue;
                    list.Add(new UsageEntry
                    {
                        Id = id, Started = Json.Num(e, "started", 0), Kind = Json.Str(e, "kind", "run"), Prompt = Json.Str(e, "prompt"),
                        Usage = Json.Obj(e, "usage"),
                        Bill = Json.Obj(e, "bill"),  // (absent in entries kept before bills)
                    });
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[Autopilot] Couldn't read {path}: {e.Message}");
            }
            return list;
        }

        public static void Save(string path, List<UsageEntry> list)
        {
            var runs = list.Select(e => (object)new Dictionary<string, object>
            {
                ["id"] = e.Id, ["started"] = e.Started, ["kind"] = e.Kind, ["prompt"] = e.Prompt, ["usage"] = e.Usage, ["bill"] = e.Bill,
            }).ToList();
            Files.WriteAtomic(path, Json.Write(new Dictionary<string, object> { ["version"] = 1L, ["runs"] = runs }));
        }

        /// <summary>A run's entry set from it (made when new; the oldest go past MaxEntries).</summary>
        public static UsageEntry Upsert(List<UsageEntry> list, AutopilotRun run)
        {
            var e = list.FirstOrDefault(x => x.Id == run.Id);
            var added = e == null;
            if (added)
            {
                e = new UsageEntry { Id = run.Id };
                list.Add(e);
            }
            e.Started = run.Started;
            e.Kind = run.Kind;
            e.Prompt = run.Prompt ?? "";
            e.Usage = run.Usage;
            e.Bill = run.Bill;
            // (sorted once its start is known: a new entry used to be sorted with no start yet, before the older ones)
            if (added) list.Sort((a, b) => a.Started.CompareTo(b.Started));
            while (list.Count > MaxEntries) list.RemoveAt(0);
            return e;
        }

        /// <summary>A run's usage or bill came (or its result): kept in the ledger (written now when final or billed,
        /// else within 10 s).</summary>
        public static void Record(AutopilotRun run)
        {
            if (run == null || (run.Usage == null && run.Bill == null) || string.IsNullOrEmpty(run.Id)) return;
            Upsert(Entries, run);
            dirty = true;
            ++Version;
            if (Json.Bool(run.Usage, "final", false) || run.Bill != null || !run.Active) Flush();
        }

        /// <summary>The ledger emptied and its file deleted (Autopilot removed from this computer, memories not kept).</summary>
        public static void Forget()
        {
            entries = new List<UsageEntry>();
            dirty = false;
            ++Version;
            try
            {
                if (File.Exists(LedgerPath)) File.Delete(LedgerPath);
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[Autopilot] Couldn't delete {LedgerPath}: {e.Message}");
            }
        }

        public static UsageEntry Find(string id)
        {
            return string.IsNullOrEmpty(id) ? null : Entries.FirstOrDefault(e => e.Id == id);
        }

        /// <summary>Write the ledger when it changed (and at most every 10 s unless forced).</summary>
        public static void Flush(bool onlyIfDue = false)
        {
            if (!dirty || entries == null) return;
            var now = EditorApplication.timeSinceStartup;
            if (onlyIfDue && now - lastSave < 10) return;
            lastSave = now;
            dirty = false;
            try
            {
                Save(LedgerPath, entries);
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[Autopilot] Couldn't save the usage to {LedgerPath}: {e.Message}");
                AutopilotStatus.Say(AutopilotStatus.UsageNotSaved(LedgerPath, e.HResult & 0xFFFF));
            }
        }
    }
}
