// What the Autopilot window says: the agent service's status lines translated to the words the UI uses (harness, policy step,
// environment), and every other text it shows scrubbed of internal names.

using System;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Autopilot.Playtest.Editor
{
    public static class AutopilotText
    {
        public const string Name = "Autopilot";

        /// <summary>The stage track, in order. Stage 4 is the rollout (its label carries the attempt); after it, the
        /// results are processed until the next rollout or the report (the track follows the lines: an older agent
        /// service went back to the stage the agent went on with).</summary>
        public static readonly string[] Stages = { "Launch", "Analyze", "Probe env", "Build harness", "Rollout", "Process result", "Report" };

        public const int StageRollout = 4;
        public const int StageResults = 5;
        public const int StageReport = 6;

        /// <summary>The Feed tab's heading for each stage (Launch, Analyze, Probe, Build harness, Play, Results, Report).</summary>
        public static readonly string[] StageHeadings = { "Launch", "Analyze", "Probe", "Build harness", "Play", "Results", "Report" };

        /// <summary>The stage an activity frame's `stage` names (analyze, probe, build, play, results, report; -1 for any
        /// other: docs/protocol.md, The activity feed).</summary>
        public static int StageOf(string name)
        {
            switch (name)
            {
                case "analyze": return 1;
                case "probe": return 2;
                case "build": return 3;
                case "play": return StageRollout;
                case "results": return StageResults;
                case "report": return StageReport;
                default: return -1;
            }
        }

        /// <summary>The icon before an activity line's words, by the event's kind, named as every Autopilot UI names them
        /// (AutopilotView draws it: the editor's own icon where it has one of the meaning, a drawn glyph otherwise): a robot
        /// for the agent's words, a magnifying glass for a read, binoculars for a search, a pencil for a write, a flask for
        /// a probe (tried in the game), a gamepad for an episode, an eye for a look, a circular arrow for a restart, a speech
        /// bubble for the user's steer, a flag for the report, a megaphone for an alert, a notebook for the refiner's edit
        /// to the game's memory. "" for a kind this build doesn't know.</summary>
        public static string ActivityIcon(string kind)
        {
            switch (kind)
            {
                case "text": return "robot";
                case "read": return "magnifier";
                case "search": return "binoculars";
                case "write": return "pencil";
                case "probe": return "flask";
                case "episode": return "gamepad";
                case "look": return "eye";
                case "restart": return "circular-arrow";
                case "steer": return "speech-bubble";
                case "report": return "flag";
                case "alert": return "megaphone";
                case "refine": return "notebook";
                default: return "";
            }
        }

        /// <summary>What the icon stands for: the line's tooltip.</summary>
        public static string ActivityTitle(string kind)
        {
            switch (kind)
            {
                case "text": return "What the agent said";
                case "read": return "Read a file";
                case "search": return "Searched the code";
                case "write": return "Wrote a file";
                case "probe": return "Tried something in the game";
                case "episode": return "Played the harness";
                case "look": return "Looked at the picture";
                case "restart": return "Restarted the level";
                case "steer": return "You steered";
                case "report": return "Reported";
                case "alert": return "Announcement";
                case "refine": return "Refined the game's memory";
                default: return "";
            }
        }

        /// <summary>The one letter shown in the icon's place when none can be drawn (? the agent's words, R read, S
        /// searched, W wrote, P tried in the game, E episode, L looked, ! report or alert, > steer, * restart, M memory
        /// refined).</summary>
        public static string ActivityGlyph(string kind)
        {
            switch (kind)
            {
                case "text": return "?";
                case "read": return "R";
                case "search": return "S";
                case "write": return "W";
                case "probe": return "P";
                case "episode": return "E";
                case "look": return "L";
                case "report":
                case "alert": return "!";
                case "steer": return ">";
                case "restart": return "*";
                case "refine": return "M";
                default: return "·";
            }
        }

        /// <summary>The fold control's words: "+ inspect the code" while closed, "– hide the code" while open (the detail's
        /// name stays, so a call's fold and its result's can be told apart).</summary>
        public static string FoldLabel(string label, bool open) => (open ? "– hide " : "+ inspect ") + label;

        /// <summary>The fold's word for an activity event's detail, by its kind.</summary>
        public static string DetailLabel(string kind)
        {
            switch (kind)
            {
                case "probe": return "the code";
                case "write": return "what was written";
                case "episode": return "what was asked";
                case "look": return "the questions";
                case "report": return "the summary";
                case "read": return "the path";
                case "search": return "the pattern";
                case "refine": return "the edit and its evidence";
                default: return "the whole message";
            }
        }

        /// <summary>The fold's word for the detail of the event that ended a call, by the call's kind.</summary>
        public static string ResultLabel(string kind)
        {
            switch (kind)
            {
                case "probe": return "the output";
                case "episode": return "the summary";
                case "look": return "the description";
                default: return "what came back";
            }
        }

        /// <summary>The status line the agent service sends after the agent is done, before the result.</summary>
        public const string SavingMemory = "Saving what it learned to memory";

        /// <summary>The feed's last line when a run's result says it changed what is remembered about the project.</summary>
        public const string MemoryUpdated = "Memory updated";

        /// <summary>The report's line where the recording would be, before the result's `no_recording` (why there is none).</summary>
        public const string NoRecording = "No recording: ";

        /// <summary>The report's line where the recording would be when the result has neither `recording` nor `memory`
        /// (an agent service from before every run was recorded ran it).</summary>
        public const string NoRecordingOlderService = "No recording.";

        static readonly Regex AttemptLine =new Regex(@"^Trying the harness \(run (\d+)\)\s*$", RegexOptions.Compiled);

        static readonly (Regex re, string to)[] Words =
        {
            (new Regex(@"\b[Pp]laytest service\b", RegexOptions.Compiled), "agent service"),
            (new Regex(@"\bPlaytest\b", RegexOptions.Compiled), Name),
            (new Regex(@"\bJev's\b", RegexOptions.Compiled), "the policy's"),
            (new Regex(@"\bJev\b", RegexOptions.Compiled), "the policy"),
            (new Regex(@"\b[Tt]he brain\b", RegexOptions.Compiled), "the agent service"),
            (new Regex(@"\bbrain\b", RegexOptions.Compiled), "agent"),
            (new Regex(@"\bBrain\b", RegexOptions.Compiled), "Agent"),
            (new Regex(@"\bcodex\b", RegexOptions.Compiled), "project index"),
            (new Regex(@"\bCodex\b", RegexOptions.Compiled), "Project index"),
        };

        /// <summary>A status line as shown: the known lines translated, stage (-1: none) and attempt (0: none) read
        /// from it.</summary>
        public static string Status(string text, out int stage, out int attempt)
        {
            stage = -1;
            attempt = 0;
            text = (text ?? "").Trim();
            switch (text)
            {
                case "Starting the game":
                    stage = 0;
                    return "Launching the environment";
                case "Reading the game's code":
                    stage = 1;
                    return "Analyzing the project";
                case "Trying things in the game":
                    stage = 2;
                    return "Probing the environment";
                case "Writing the harness":
                    stage = 3;
                    return "Building the harness";
                case "Looking at the results":
                    stage = StageResults;
                    return "Processing results";
                case SavingMemory:
                case SavingMemory + "...":
                case SavingMemory + "…":
                    // (after the agent is done, before the result: part of the report, never a step back)
                    stage = StageReport;
                    return SavingMemory;
            }
            var m = AttemptLine.Match(text);
            if (m.Success && int.TryParse(m.Groups[1].Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var n))
            {
                stage = StageRollout;
                attempt = n;
                return "Harness " + n;
            }
            if (text.StartsWith("Now: ", StringComparison.Ordinal)) return "Now: " + Scrub(text.Substring(5));
            if (text.StartsWith("Connected to the playtest service", StringComparison.Ordinal)) return "Connected to the agent service";
            if (text.StartsWith("Started ", StringComparison.Ordinal) && text.EndsWith(" run", StringComparison.Ordinal))
                return "Run started";
            return Scrub(text);
        }

        /// <summary>Internal names taken out of any text shown.</summary>
        public static string Scrub(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            foreach (var (re, to) in Words) text = re.Replace(text, to);
            return text;
        }

        /// <summary>A long path or address made to wrap in a narrow window: a zero-width space after each / and \
        /// (a label breaks lines only between words).</summary>
        public static string Breakable(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            var zw = ((char)0x200B).ToString();
            return text.Replace("/", "/" + zw).Replace("\\", "\\" + zw);
        }

        public static string Clock(double seconds)
        {
            if (double.IsNaN(seconds) || seconds < 0) seconds = 0;
            var s = (long)Math.Floor(seconds);
            return s >= 3600 ? $"{s / 3600}:{s / 60 % 60:00}:{s % 60:00}" : $"{s / 60:00}:{s % 60:00}";
        }

        /// <summary>Where a run the window didn't start came from, by its `run_started` message's `by`: "Started from
        /// Claude Code", "Started from Codex", else "Started from your coding agent"; "" for a run the window started
        /// (no `by`).</summary>
        public static string StartedFrom(string by)
        {
            if (string.IsNullOrEmpty(by)) return "";
            if (by == "claude-code") return "Started from Claude Code";
            if (by == "codex") return "Started from Codex";
            return "Started from your coding agent";
        }

        public static string Short(string text, int max)
        {
            text = text ?? "";
            return text.Length <= max ? text : text.Substring(0, Math.Max(0, max - 1)) + "…";
        }

        public static string Num(double v, string format = "0.##")
        {
            return v.ToString(format, CultureInfo.InvariantCulture);
        }

        /// <summary>.46 for 0.46 (probabilities, as the table shows them).</summary>
        public static string Prob(double p)
        {
            if (p >= 0.995) return "1.00";
            return p.ToString(".00", CultureInfo.InvariantCulture);
        }

        public static double UnixNow => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

        public static DateTime Local(double unix)
        {
            return DateTimeOffset.FromUnixTimeMilliseconds((long)(unix * 1000)).LocalDateTime;
        }

        static readonly string[] Months = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };

        /// <summary>When a run started, as every run list and id line shows it: "Oct 08 13:25" (local time).</summary>
        public static string When(double unix)
        {
            var t = Local(unix);
            return $"{Months[Math.Max(0, Math.Min(11, t.Month - 1))]} {t.Day:00} {t.Hour:00}:{t.Minute:00}";
        }
    }
}
