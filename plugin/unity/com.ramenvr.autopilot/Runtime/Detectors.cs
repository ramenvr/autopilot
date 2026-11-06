// In-engine detectors (docs/protocol.md, Findings): cheap checks every frame or so, so glitches between the brain's
// steps are caught too. log_error (errors, exceptions and failed asserts in the log), nan (a body's transform not
// finite), out_of_world (a body far below the level or beyond any sane distance), long_fall (the player falling for
// 8 s), hitch (a frame longer than 0.75 s), hud_fraction (on-screen text a/b with a > b > 0), and as Unreal's:
// teleport (a character moving farther in one frame than its speed allows), actor_growth (the objects of one kind
// growing for 2 minutes: a leak), clamp (a game script's number outside its [Range] or [Min]) and over_max (a number
// field above its sibling maximum: health above maxHealth).

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using UnityEngine;

namespace Autopilot.Playtest
{
    public static class LogCapture
    {
        public struct Line
        {
            public string Verbosity;
            public string Category;
            public string Message;
        }

        static readonly ConcurrentQueue<Line> lines = new ConcurrentQueue<Line>();
        static bool installed;
        [ThreadStatic] static int quiet;

        public static void Install()
        {
            if (installed) return;
            installed = true;
            Application.logMessageReceivedThreaded += OnLog;
        }

        public static IDisposable Quiet()
        {
            return new QuietScope();
        }

        sealed class QuietScope : IDisposable
        {
            public QuietScope()
            {
                ++quiet;
            }

            public void Dispose()
            {
                --quiet;
            }
        }

        static void OnLog(string condition, string stack, LogType type)
        {
            if (quiet > 0) return;
            if (type != LogType.Error && type != LogType.Exception && type != LogType.Assert) return;
            if (condition != null && condition.StartsWith("[Autopilot]", StringComparison.Ordinal)) return;
            if (lines.Count >= 500) return;
            var message = condition ?? "";
            if (type == LogType.Exception && !string.IsNullOrEmpty(stack))
            {
                // The first frames say where it was thrown
                var frames = stack.Split('\n');
                message += "\n" + string.Join("\n", frames, 0, Math.Min(4, frames.Length)).TrimEnd();
            }
            lines.Enqueue(new Line { Verbosity = "Error", Category = type.ToString(), Message = message });
        }

        public static List<Line> Drain()
        {
            var list = new List<Line>();
            while (lines.TryDequeue(out var l)) list.Add(l);
            return list;
        }
    }

    public sealed class Detectors
    {
        readonly PlaytestClient client;
        bool enabled = true;
        double longFallSeconds = 8.0;
        double hitchSeconds = 0.75;
        double hudEvery = 1.0;
        double bodyEvery = 0.5;
        double? killY;  // native metres; else worked out from the level
        double farAway = 10000.0;
        double teleportFactor = 3.0;  // a frame's move may be this many times what its speed covers in it
        double teleportMin = 1000.0;  // kit units (cm): a move this short is never a teleport
        double growthSeconds = 120.0;
        double propertyEvery = 0.5;

        /// <summary>The frame the bridge last moved or called something in the game (rc): a teleport then is the
        /// agent's own (a verification's setup), not the game's.</summary>
        public static int BridgeActedFrame = -10;

        sealed class Track
        {
            public Vector3 At;
            public int Frames;
        }

        readonly Dictionary<GameObject, Track> tracks = new Dictionary<GameObject, Track>();
        readonly List<Dictionary<string, int>> growth = new List<Dictionary<string, int>>();
        double nextGrowth, nextProperties;

        readonly Dictionary<string, int> counts = new Dictionary<string, int>();
        double sessionStart;
        double nextHud, nextBodies;
        double fallingSince = -1;
        double levelBottom = double.NaN;

        static readonly Regex Fraction = new Regex(@"(?<![\d.])(\d{1,9})\s*/\s*(\d{1,9})(?![\d.])", RegexOptions.Compiled);

        public Detectors(PlaytestClient client)
        {
            this.client = client;
        }

        public static IDisposable Quiet()
        {
            return LogCapture.Quiet();
        }

        public void Configure(Dictionary<string, object> args)
        {
            enabled = Json.Bool(args, "enabled", enabled);
            longFallSeconds = Json.Num(args, "long_fall_seconds", longFallSeconds);
            hitchSeconds = Json.Num(args, "hitch_seconds", hitchSeconds);
            hudEvery = Json.Num(args, "hud_every", hudEvery);
            if (Json.Has(args, "kill_y")) killY = Json.Num(args, "kill_y", 0);
            teleportFactor = Json.Num(args, "teleport_factor", teleportFactor);
            teleportMin = Json.Num(args, "teleport_min", teleportMin);
            growthSeconds = Json.Num(args, "growth_seconds", growthSeconds);
            propertyEvery = Json.Num(args, "property_every", propertyEvery);
        }

        public void Reset()
        {
            counts.Clear();
            sessionStart = PlaytestClient.Now;
            nextHud = nextBodies = 0;
            fallingSince = -1;
            levelBottom = double.NaN;
            tracks.Clear();
            growth.Clear();
            nextGrowth = nextProperties = 0;
        }

        void Report(string detector, string severity, string summary, string signature, GameObject actor = null,
            Dictionary<string, object> details = null)
        {
            counts.TryGetValue(signature, out var count);
            counts[signature] = ++count;
            if (count != 1 && count != 10 && count != 100 && count != 1000) return;
            var finding = new Dictionary<string, object>
            {
                ["detector"] = detector,
                ["severity"] = severity,
                ["summary"] = summary,
                ["signature"] = signature,
                ["t"] = client.GameTime,
                ["details"] = details ?? new Dictionary<string, object>(),
                ["count"] = count,
                ["source"] = "engine",
            };
            if (actor != null)
            {
                finding["actor"] = Paths.Of(actor);
                finding["where"] = Frame.Point(actor.transform.position);
            }
            client.QueueEvent(new Dictionary<string, object> { ["kind"] = "finding", ["t"] = client.GameTime, ["finding"] = finding });
        }

        static string Normalize(string message)
        {
            var first = message.Split('\n')[0];
            var sb = new StringBuilder(first.Length);
            var inNumber = false;
            foreach (var c in first)
            {
                if (char.IsDigit(c))
                {
                    if (!inNumber) sb.Append('#');
                    inNumber = true;
                    continue;
                }
                inNumber = false;
                sb.Append(c);
            }
            var s = sb.ToString();
            return s.Length > 160 ? s.Substring(0, 160) : s;
        }

        public void OnLogLines(List<LogCapture.Line> lines)
        {
            if (!enabled) return;
            foreach (var line in lines)
            {
                var first = line.Message.Split('\n')[0];
                Report("log_error", "error", (line.Category == "Exception" ? "Exception: " : line.Category == "Assert" ? "Assert failed: " : "Error logged: ") + Trim(first, 300),
                    "log_error:" + Normalize(line.Message), null,
                    new Dictionary<string, object> { ["type"] = line.Category, ["message"] = Trim(line.Message, 2000) });
            }
        }

        static string Trim(string s, int n)
        {
            return s.Length > n ? s.Substring(0, n) : s;
        }

        public void Tick()
        {
            if (!enabled || !Application.isPlaying) return;
            var now = PlaytestClient.Now;
            var settled = now - sessionStart > 2.0;
            // hitch: a frame that took too long while the game plays
            if (settled && !Ops.IsPaused && Time.unscaledDeltaTime > hitchSeconds)
            {
                Report("hitch", "warning", $"A frame took {Time.unscaledDeltaTime:0.00} s (hitch)", "hitch", null,
                    new Dictionary<string, object> { ["seconds"] = Json.Round(Time.unscaledDeltaTime, 3) });
            }
            if (now >= nextBodies)
            {
                nextBodies = now + bodyEvery;
                CheckBodies(now);
            }
            if (now >= nextHud && hudEvery > 0)
            {
                nextHud = now + hudEvery;
                CheckHud();
            }
            if (Time.deltaTime > 0) CheckTeleports();
            var gameTime = Convert.ToDouble(client.GameTime);
            if (gameTime < nextGrowth - 10.0) nextGrowth = 0;  // (game time starts over with a Stop and a Play)
            if (gameTime >= nextGrowth)
            {
                nextGrowth = gameTime + 10.0;
                CheckGrowth();
            }
            if (now >= nextProperties && propertyEvery > 0)
            {
                nextProperties = now + propertyEvery;
                CheckProperties();
            }
        }

        // -- teleport: a character moving farther in one frame than its speed allows ------------------------------

        /// <summary>The characters: the player, and whatever has a CharacterController or a NavMeshAgent.</summary>
        static IEnumerable<GameObject> Characters()
        {
            var player = Player.Find();
            if (player != null) yield return player;
#if AUTOPILOT_PHYSICS
            foreach (var cc in Loaded.All<CharacterController>(FindObjectsInactive.Exclude))
                if (cc != null && cc.gameObject != player)
                    yield return cc.gameObject;
#endif
        }

        void CheckTeleports()
        {
            var alive = new HashSet<GameObject>();
            var bridgeMoved = Time.frameCount - BridgeActedFrame <= 2;
            foreach (var go in Characters())
            {
                if (go == null || !alive.Add(go)) continue;
                if (!tracks.TryGetValue(go, out var track)) tracks[go] = track = new Track { At = go.transform.position };
                var at = go.transform.position;
                if (track.Frames > 5 && !bridgeMoved)
                {
                    var speed = (double)Player.Velocity(go).magnitude;
                    speed = Math.Max(speed, Player.WalkSpeed(go));
                    var allowed = Math.Max(teleportMin, teleportFactor * speed * Frame.Scale * Time.deltaTime);
                    var moved = (at - track.At).magnitude * Frame.Scale;
                    if (moved > allowed)
                    {
                        var label = Label(go);
                        Report("teleport", "suspicion", $"{label} moved {moved:0} units in one frame (at most {allowed:0} expected)", "teleport:" + label, go,
                            new Dictionary<string, object>
                            {
                                ["from"] = Frame.Point(track.At, 0), ["to"] = Frame.Point(at, 0),
                                ["moved"] = Math.Round(moved), ["allowed"] = Math.Round(allowed),
                            });
                    }
                }
                track.At = at;
                ++track.Frames;
            }
            foreach (var gone in tracks.Keys.Where(k => k == null || !alive.Contains(k)).ToList()) tracks.Remove(gone);
        }

        // -- actor_growth: the objects of one kind growing all the time (a leak) -------------------------------------

        void CheckGrowth()
        {
            var counts = new Dictionary<string, int>();
            foreach (var t in Loaded.All<Transform>(FindObjectsInactive.Exclude))
            {
                if (t == null || Paths.Hidden(t.gameObject)) continue;
                var label = Label(t.gameObject);
                counts.TryGetValue(label, out var n);
                counts[label] = n + 1;
            }
            growth.Add(counts);
            var needed = Math.Max(2, (int)Math.Round(growthSeconds / 10.0) + 1);
            while (growth.Count > needed) growth.RemoveAt(0);
            if (growth.Count < needed) return;
            foreach (var last in growth[growth.Count - 1])
            {
                growth[0].TryGetValue(last.Key, out var first);
                if (last.Value < 2 * first || last.Value - first < 50) continue;
                var rising = true;
                for (var i = 1; i < growth.Count && rising; ++i)
                {
                    growth[i].TryGetValue(last.Key, out var now);
                    growth[i - 1].TryGetValue(last.Key, out var before);
                    rising = now >= before;
                }
                if (!rising) continue;
                Report("actor_growth", "warning", $"{last.Key} actors kept growing from {first} to {last.Value} in {growthSeconds:0} s (a possible leak)",
                    "actor_growth:" + last.Key, null,
                    new Dictionary<string, object> { ["from"] = first, ["to"] = last.Value, ["seconds"] = growthSeconds });
            }
        }

        // -- clamp and over_max: a game script's numbers out of their range ------------------------------------------

        internal sealed class Checked
        {
            public FieldInfo Field;
            public string Name;
            public double? Min, Max;
            public FieldInfo MaxField;
        }

        static readonly Dictionary<Type, List<Checked>> checkedFields = new Dictionary<Type, List<Checked>>();

        static bool Numeric(Type t) => t == typeof(float) || t == typeof(double) || t == typeof(int) || t == typeof(long) || t == typeof(short)
                                       || t == typeof(byte) || t == typeof(uint) || t == typeof(ushort);

        /// <summary>A game script's number fields that have a range to keep ([Range], [Min]) or a sibling maximum (health
        /// and maxHealth, ammo and ammoMax): found once per type.</summary>
        internal static List<Checked> FieldsOf(Type type)
        {
            if (checkedFields.TryGetValue(type, out var list)) return list;
            list = new List<Checked>();
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var all = new List<FieldInfo>();
            for (var t = type; t != null && t != typeof(MonoBehaviour) && Reflect.IsGameType(t); t = t.BaseType)
                all.AddRange(t.GetFields(flags | BindingFlags.DeclaredOnly).Where(f => Numeric(f.FieldType) && !f.IsDefined(typeof(System.Runtime.CompilerServices.CompilerGeneratedAttribute), false)));
            var byName = new Dictionary<string, FieldInfo>();
            foreach (var f in all)
                if (!byName.ContainsKey(f.Name))
                    byName[f.Name] = f;
            foreach (var f in all)
            {
                var c = new Checked { Field = f, Name = NameOf(f) };
                var range = f.GetCustomAttribute<RangeAttribute>();
                if (range != null)
                {
                    c.Min = range.min;
                    c.Max = range.max;
                }
                var min = f.GetCustomAttribute<MinAttribute>();
                if (min != null) c.Min = min.min;
                var core = f.Name;
                var prefix = "";
                foreach (var p in new[] { "m_", "_" })
                {
                    if (!core.StartsWith(p, StringComparison.Ordinal) || core.Length <= p.Length) continue;
                    prefix = p;
                    core = core.Substring(p.Length);
                    break;
                }
                var isMax = core.StartsWith("max", StringComparison.OrdinalIgnoreCase) || core.EndsWith("Max", StringComparison.Ordinal);
                if (!isMax)
                {
                    var cap = char.ToUpperInvariant(core[0]) + core.Substring(1);
                    foreach (var candidate in new[] { prefix + "max" + cap, prefix + "Max" + cap, prefix + core + "Max" })
                    {
                        if (byName.TryGetValue(candidate, out var maxField) && maxField != f)
                        {
                            c.MaxField = maxField;
                            break;
                        }
                    }
                }
                if (c.Min != null || c.Max != null || c.MaxField != null) list.Add(c);
            }
            checkedFields[type] = list;
            return list;
        }

        /// <summary>A field's name as the Inspector shows it: without m_ or _ (health, maxHealth).</summary>
        static string NameOf(FieldInfo f)
        {
            var n = f.Name;
            if (n.StartsWith("m_", StringComparison.Ordinal) && n.Length > 2) return n.Substring(2);
            if (n.StartsWith("_", StringComparison.Ordinal) && n.Length > 1) return n.Substring(1);
            return n;
        }

        static string Number(double v) => Json.Write(Math.Round(v * 1000) / 1000);

        void CheckProperties()
        {
            foreach (var mb in Loaded.All<MonoBehaviour>(FindObjectsInactive.Exclude))
            {
                if (mb == null || !mb.isActiveAndEnabled || !Reflect.IsGameType(mb.GetType())) continue;
                var fields = FieldsOf(mb.GetType());
                if (fields.Count == 0) continue;
                var go = mb.gameObject;
                var label = Label(go);
                var type = mb.GetType().Name;
                var where = label == type ? label : label + "." + type;
                foreach (var c in fields)
                {
                    double value;
                    try
                    {
                        value = Convert.ToDouble(c.Field.GetValue(mb));
                    }
                    catch (Exception)
                    {
                        continue;
                    }
                    var below = c.Min != null && value < c.Min.Value - 1e-4;
                    var above = c.Max != null && value > c.Max.Value + 1e-4;
                    if (below || above)
                    {
                        var min = c.Min != null ? Number(c.Min.Value) : "";
                        var max = c.Max != null ? Number(c.Max.Value) : "";
                        Report("clamp", "warning", $"{where} {c.Name} is {Number(value)}, outside its allowed range {min}..{max}", $"clamp:{type}.{c.Name}", go,
                            new Dictionary<string, object> { ["property"] = c.Name, ["value"] = Math.Round(value * 1000) / 1000, ["clamp"] = $"[{min}, {max}]" });
                    }
                    if (c.MaxField == null) continue;
                    double maxValue;
                    try
                    {
                        maxValue = Convert.ToDouble(c.MaxField.GetValue(mb));
                    }
                    catch (Exception)
                    {
                        continue;
                    }
                    if (maxValue > 0 && value > maxValue + 1e-4)
                    {
                        var maxName = NameOf(c.MaxField);
                        Report("over_max", "warning", $"{where} {c.Name} is {Number(value)}, above {maxName} {Number(maxValue)}", $"over_max:{type}.{c.Name}", go,
                            new Dictionary<string, object>
                            {
                                ["property"] = c.Name, ["value"] = Math.Round(value * 1000) / 1000, ["max_property"] = maxName, ["max"] = Math.Round(maxValue * 1000) / 1000,
                            });
                    }
                }
            }
        }

        double Bottom()
        {
            if (killY.HasValue) return killY.Value;
            if (double.IsNaN(levelBottom))
            {
                // The lowest solid thing of the level, and 100 m below it
                var lowest = double.PositiveInfinity;
#if AUTOPILOT_PHYSICS
                foreach (var c in Loaded.All<Collider>(FindObjectsInactive.Exclude))
                {
                    if (c == null || c.isTrigger || c.attachedRigidbody != null && !c.attachedRigidbody.isKinematic) continue;
                    lowest = Math.Min(lowest, c.bounds.min.y);
                }
#endif
                levelBottom = double.IsInfinity(lowest) ? -1000.0 : lowest - 100.0;
            }
            return levelBottom;
        }

        void CheckBodies(double now)
        {
            var bodies = new List<GameObject>();
            var player = Player.Find();
            if (player != null) bodies.Add(player);
#if AUTOPILOT_PHYSICS
            foreach (var rb in Loaded.All<Rigidbody>(FindObjectsInactive.Exclude))
                if (rb != null && !rb.isKinematic && bodies.Count < 2000) bodies.Add(rb.gameObject);
#endif
            var bottom = Bottom();
            foreach (var go in bodies)
            {
                if (go == null) continue;
                var t = go.transform;
                var label = Label(go);
                if (!Frame.Finite(t.position) || !Frame.Finite(t.localScale) || float.IsNaN(t.rotation.x))
                {
                    Report("nan", "error", $"{label} has an invalid (NaN or infinite) transform", "nan:" + label, null);
                    continue;
                }
                var p = t.position;
                if (p.y < bottom)
                    Report("out_of_world", "warning", $"{label} is {bottom - p.y:0} m below the level (y {p.y:0.0})", "out_of_world:" + label, go,
                        new Dictionary<string, object> { ["y"] = Json.Round(p.y, 2), ["bottom"] = Json.Round(bottom, 2) });
                else if (Math.Abs(p.x) > farAway || Math.Abs(p.y) > farAway || Math.Abs(p.z) > farAway)
                    Report("out_of_world", "warning", $"{label} is beyond the world bounds ({p.magnitude:0} m)", "out_of_world:" + label, go);
            }
            // long_fall: the player falling for a long time (fell through the floor or off the level)
            if (player != null)
            {
                var pose = Player.Grounded(player, out var velocity);
                if (pose == false && velocity.y < -0.5f)
                {
                    if (fallingSince < 0) fallingSince = now;
                    else if (now - fallingSince > longFallSeconds)
                        Report("long_fall", "warning", $"{Label(player)} has been falling for {now - fallingSince:0} s (fell through the floor or off the level?)",
                            "long_fall:" + Label(player), player);
                }
                else fallingSince = -1;
            }
        }

        static string Label(GameObject go)
        {
            // A class-like label: the game's own script on it, else its name without a number
            foreach (var c in go.GetComponents<MonoBehaviour>())
                if (c != null && Reflect.IsGameType(c.GetType()))
                    return c.GetType().Name;
            return Regex.Replace(go.name, @"\s*\(\d+\)$", "");
        }

        void CheckHud()
        {
            Dictionary<string, string> texts;
            try
            {
                texts = ScreenText.Texts();
            }
            catch
            {
                return;
            }
            foreach (var pair in texts)
            {
                foreach (Match m in Fraction.Matches(pair.Value))
                {
                    if (!long.TryParse(m.Groups[1].Value, out var a) || !long.TryParse(m.Groups[2].Value, out var b)) continue;
                    if (a > b && b > 0)
                    {
                        Report("hud_fraction", "warning",
                            $"On-screen text {pair.Key} shows \"{Trim(pair.Value, 80)}\": {a} is more than its stated maximum {b}",
                            "hud_fraction:" + pair.Key, null,
                            new Dictionary<string, object> { ["text"] = pair.Value, ["value"] = a, ["max"] = b, ["key"] = pair.Key });
                    }
                }
            }
        }
    }
}
