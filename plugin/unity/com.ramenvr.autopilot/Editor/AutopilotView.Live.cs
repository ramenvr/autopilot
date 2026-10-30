// The Autopilot window's Live and Feed tabs (AutopilotView.cs has the rest of the window).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;
using static Autopilot.Playtest.Editor.AutopilotUI;

namespace Autopilot.Playtest.Editor
{
    public sealed partial class AutopilotView
    {
        // -- live --------------------------------------------------------------------------------------------

        void BuildLive(AutopilotRun run, PlaytestClient client)
        {
            var head = Row("live-head");
            head.Add(DotLabel("LIVE", Accent));
            head.Add(L(Shown(run), "live-prompt grow nowrap"));
            var elapsedName = Lbl("Elapsed");
            head.Add(elapsedName);
            // (the run area under 520 px: the time alone)
            head.RegisterCallback<GeometryChangedEvent>(e => elapsedName.style.display = e.newRect.width < 520 ? DisplayStyle.None : DisplayStyle.Flex);
            var elapsed = L(AutopilotText.Clock(run.Elapsed), "elapsed");
            head.Add(elapsed);
            // (pressed: "Stopping…", disabled, until the run ends)
            var stop = Btn(run.Stopping ? AutopilotSession.StoppingLine : "Stop", () =>
            {
                AutopilotSession.Stop();
                Refresh(true);
            });
            stop.SetEnabled(client != null && client.Connected && client.RunActive && !run.Stopping);
            head.Add(stop);
            content.Add(head);

            var box = PageBox("full live-body stack-16");
            // (a run the user's own Claude Code or Codex started: where it came from, under the request)
            if (run.Origin.Length > 0) box.Add(Para(run.Origin, "small t-sec2 run-origin"));

            // The stages: a 2 px track, lit to the stage going; their names and times under it
            var track = new StageTrack();
            track.Set(run.Stage, false);
            var stages = Box("col");
            stages.Add(track);
            var labels = Box("stage-labels");
            var times = new List<(int stage, Label label)>();
            for (var i = 0; i < AutopilotText.Stages.Length; ++i)
            {
                var name = AutopilotText.Stages[i];
                if (i == AutopilotText.StageRollout && run.Attempt > 0) name += " · H" + run.Attempt;  // (wraps before "· H2", never in it)
                var cell = Box("stage-label col");
                var state = i < run.Stage ? " done" : i == run.Stage ? " now" : "";
                var nameLabel = L(name.ToUpperInvariant(), "stage-name" + state);
                cell.Add(nameLabel);
                // A name is never cut: its cell is at least as wide as its longest word (it wraps between words), and
                // the cells go on more lines when they don't all fit beside each other
                nameLabel.RegisterCallback<GeometryChangedEvent>(_ => FitStageName(cell, nameLabel));
                // (a space when there is no time yet: every cell as tall)
                var time = L(i <= run.Stage ? Mmss(run.StageTime(i)) : " ", "stage-time" + (i == run.Stage ? " now" : ""));
                cell.Add(time);
                times.Add((i, time));
                labels.Add(cell);
            }
            stages.Add(labels);
            box.Add(stages);

            var now = Card("plate now-card");
            now.Add(Lbl(run.NowAttempt > 0 ? $"Now · harness {run.NowAttempt}" : "Now"));
            var nowLine = Row();
            nowLine.style.alignItems = Align.Center;
            nowLine.Add(Box("now-mark"));
            nowLine.Add(L(string.IsNullOrEmpty(run.Now) ? StageWords(run.Stage) : run.Now, "now-text grow"));
            now.Add(nowLine);
            box.Add(now);

            // The harness attempts: a filled accent square each finished, an outlined one the one going, then their
            // count; one row at any width, never wrapping: at most 24 squares, and when they don't all fit, the latest
            // that do (FitAttemptBoxes)
            var attempts = Row("attempts");
            attempts.Add(Lbl("Harness attempts"));
            var boxes = Row("attempt-boxes");
            var squares = new List<VisualElement>();
            var built = Math.Min(run.AttemptLines, MaxAttemptBoxes);
            for (var i = 1; i <= built; ++i)
            {
                // (the last square is the one going, past 24 too)
                var sq = Box("attempt-box" + (i == built ? " current" : ""));
                squares.Add(sq);
                boxes.Add(sq);
            }
            var count = L(run.AttemptLines.ToString(CultureInfo.InvariantCulture), "attempt-count");
            boxes.Add(count);
            boxes.RegisterCallback<GeometryChangedEvent>(_ => FitAttemptBoxes(boxes, squares, count));
            attempts.Add(boxes);
            Label stepCount = null;
            if (run.Steps.Count > 0)
            {
                stepCount = L(PolicySteps(run.Steps.Count), "mono tiny t-muted nowrap step-count");
                attempts.Add(stepCount);
            }
            box.Add(attempts);

            // The feed's last lines, on a plate under its caption
            var feed = Card("plate feed-card");
            var caption = Box("caption");
            caption.Add(L("Feed", "feed-title"));
            feed.Add(caption);
            var lines = Box("col caption-body");
            var last = LastAttemptLine(run.Feed);
            var from = Math.Max(0, run.Feed.Count - 10);
            for (var i = from; i < run.Feed.Count; ++i) lines.Add(FeedRow(run.Feed[i], false, i == last));
            if (run.Feed.Count == 0) lines.Add(L("Nothing yet.", "t-muted small"));
            feed.Add(lines);
            box.Add(feed);

            liveTick = () =>
            {
                elapsed.text = AutopilotText.Clock(run.Elapsed);
                if (stepCount != null) stepCount.text = PolicySteps(run.Steps.Count);
                foreach (var (stage, label) in times)
                    if (stage == run.Stage) label.text = Mmss(run.StageTime(stage));
            };
        }

        /// <summary>The most harness-attempt squares shown.</summary>
        public const int MaxAttemptBoxes = 24;

        /// <summary>The latest squares that fit beside the count (14 px each: 10 and a 4 px gap); the older ones hidden.
        /// The row takes the room left (its width never follows its content), so this settles at once.</summary>
        internal static void FitAttemptBoxes(VisualElement boxes, List<VisualElement> squares, Label count)
        {
            var room = boxes.contentRect.width - count.layout.width - 2;
            if (float.IsNaN(room) || squares.Count == 0) return;
            var fit = Math.Max(1, (int)Math.Floor(room / 14f));  // (each square 10 and its 4 px margin, then the count)
            for (var i = 0; i < squares.Count; ++i)
            {
                var show = i >= squares.Count - fit ? DisplayStyle.Flex : DisplayStyle.None;
                if (squares[i].style.display != show) squares[i].style.display = show;
            }
        }

        static string PolicySteps(int n) => $"{n} policy step{(n == 1 ? "" : "s")}";

        /// <summary>The feed's last "Harness N" line (the attempt going, in the accent; the earlier ones in the text's
        /// colour), -1 when none.</summary>
        static int LastAttemptLine(List<AutopilotFeedLine> feed)
        {
            for (var i = feed.Count - 1; i >= 0; --i)
                if (feed[i].Kind == "attempt")
                    return i;
            return -1;
        }

        /// <summary>A stage cell's least width: its name's longest word (once the font is known: after a layout).</summary>
        internal static void FitStageName(VisualElement cell, Label name)
        {
            var widest = 0f;
            foreach (var word in (name.text ?? "").Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var w = name.MeasureTextSize(word, 0, MeasureMode.Undefined, 0, MeasureMode.Undefined).x;
                if (!float.IsNaN(w)) widest = Math.Max(widest, w);
            }
            if (widest <= 0) return;
            var least = Mathf.Ceil(widest + 2);  // (a little room against rounding)
            var now = cell.style.minWidth;
            if (now.keyword == StyleKeyword.Undefined && Math.Abs(now.value.value - least) < 0.5f) return;
            cell.style.minWidth = least;
        }

        /// <summary>A feed time: m:ss, h:mm:ss from one hour.</summary>
        internal static string Mmss(double seconds)
        {
            var s = (long)Math.Max(0, Math.Floor(double.IsNaN(seconds) ? 0 : seconds));
            return s >= 3600 ? $"{s / 3600}:{s / 60 % 60:00}:{s % 60:00}" : $"{s / 60}:{s % 60:00}";
        }

        static string StageWords(int stage)
        {
            switch (stage)
            {
                case 0: return "Launching the environment";
                case 1: return "Analyzing the project";
                case 2: return "Probing the environment";
                case 3: return "Building the harness";
                case 4: return "Rolling out the harness";
                case 5: return "Processing results";
                case 6: return AutopilotText.SavingMemory;
                default: return "Getting ready";
            }
        }

        /// <summary>A feed line of the Live page (compact: one line each, no folds): its time, its tag or mark, its words
        /// (an activity event: then its result's, quieter).</summary>
        VisualElement FeedRow(AutopilotFeedLine line, bool padded, bool current)
        {
            var row = Box("feed-line");
            if (!padded)
            {
                row.style.paddingLeft = 0;
                row.style.paddingRight = 0;
            }
            FillFeedRow(row, line, current, true);
            return row;
        }

        /// <summary>The Feed tab's items for a run's feed: its lines in order, a heading (Kind "heading": the stage's name,
        /// HeadingCount its lines) before each stretch of lines of one stage (lines before any stage have none). A stage
        /// that comes again (Play, then Results, then Play for the next harness) gets a heading each time.</summary>
        internal static List<AutopilotFeedLine> GroupFeed(List<AutopilotFeedLine> feed)
        {
            var items = new List<AutopilotFeedLine>(feed.Count + 8);
            var current = -1;
            AutopilotFeedLine heading = null;
            foreach (var line in feed)
            {
                if (line == null) continue;
                if (line.Stage >= 0 && line.Stage != current)
                {
                    current = line.Stage;
                    heading = new AutopilotFeedLine
                    {
                        Kind = "heading", Stage = current, At = line.At,
                        Text = current < AutopilotText.StageHeadings.Length ? AutopilotText.StageHeadings[current] : "",
                    };
                    items.Add(heading);
                }
                if (heading != null) ++heading.HeadingCount;
                items.Add(line);
            }
            return items;
        }

        /// <summary>Text for a rich-text label, shown as it is (its own &lt; and &gt; kept).</summary>
        static string NoParse(string text) => "<noparse>" + (text ?? "").Replace("</noparse>", "") + "</noparse>";

        /// <summary>A feed row's content: a stage heading (its name, its count, a rule); an activity event (its time, its
        /// kind's icon, its words with its result's after them in the muted colour, and, unless compact, a fold for each
        /// detail); any other line (its time, its tag, its words, coloured by its kind).</summary>
        void FillFeedRow(VisualElement row, AutopilotFeedLine line, bool current, bool compact)
        {
            row.Clear();
            foreach (var k in new[] { "status", "attempt", "now", "engine", "agent", "stage", "you", "memory", "activity", "heading" })
                row.EnableInClassList(k, k == line.Kind);
            row.EnableInClassList("current", current);
            if (line.IsHeading)
            {
                row.Add(Lbl(line.Text, "feed-heading-name"));
                row.Add(L(line.HeadingCount.ToString(CultureInfo.InvariantCulture), "mono tiny t-muted feed-heading-count"));
                row.Add(Box("feed-heading-rule grow"));
                return;
            }
            row.Add(L(Mmss(line.At), "mono feed-time"));
            if (!line.IsActivity)
            {
                var tag = FeedTag(line);
                if (tag != null) row.Add(tag);
                row.Add(L(line.Text, "mono feed-text"));
                return;
            }
            row.Add(FeedMark(line));
            var body = Box("col grow feed-body");
            // The words, then a short one-line detail the words don't say (a file's path) and the result, quieter, as one
            // wrapping text (rich text: the muted colour on them)
            var words = NoParse(line.Text);
            if (line.Detail.Length > 0 && !AutopilotFeedLine.Folds(line.Detail) && !line.Text.Contains(line.Detail))
                words += "  <color=#8b8f98>" + NoParse(line.Detail) + "</color>";
            if (line.Result.Length > 0) words += "  <color=#8b8f98>" + NoParse(line.Result) + "</color>";
            var text = L(words, "mono feed-text feed-words");
            text.enableRichText = true;
            body.Add(text);
            if (!compact)
            {
                if (line.DetailLabel.Length > 0) body.Add(Fold(line, line.DetailLabel, line.Detail, false));
                if (line.ResultLabel.Length > 0) body.Add(Fold(line, line.ResultLabel, line.ResultDetail, true));
            }
            row.Add(body);
        }

        /// <summary>The editor's own icons that mean the same as the feed's (Unity 6000.6's built-in icons, by the name
        /// AutopilotText.ActivityIcon gives): a magnifying glass, a pencil, a circular arrow, an eye, a gamepad.</summary>
        internal static readonly Dictionary<string, string> EditorIcons = new Dictionary<string, string>
        {
            ["magnifier"] = "Search Icon", ["pencil"] = "editicon.sml", ["circular-arrow"] = "Refresh",
            ["eye"] = "ViewToolOrbit", ["gamepad"] = "UnityEditor.GameView",
        };

        /// <summary>The icon before an activity line's words (12 px, in its kind's colour by USS, its title as the tooltip):
        /// the editor's own icon where it has one of the meaning (EditorIcons), else Lucide's drawn by Glyph (a robot,
        /// binoculars, a flask, a speech bubble, a flag, a megaphone, a notebook; and the others, should an editor icon be missing),
        /// else the kind's letter.</summary>
        static VisualElement FeedMark(AutopilotFeedLine line)
        {
            var icon = line.Icon;
            VisualElement mark = null;
            if (EditorIcons.TryGetValue(icon, out var name))
            {
                var tex = EditorGUIUtility.IconContent(name)?.image;
                if (tex != null) mark = new Image { image = tex, scaleMode = ScaleMode.ScaleToFit };
            }
            if (mark == null && Glyph.Paths.ContainsKey(icon)) mark = new Glyph(icon, 12);
            if (mark != null) mark.AddToClassList("icon");
            else mark = L(line.Glyph, "mono-700");
            mark.AddToClassList("feed-mark");
            mark.AddToClassList("mark-" + line.ActivityKind);
            mark.tooltip = line.Title;
            return mark;
        }

        // The Feed tab's folds open: the line, and whether it is the result's fold (closed again after a script reload)
        readonly HashSet<(AutopilotFeedLine, bool)> openFolds = new HashSet<(AutopilotFeedLine, bool)>();

        /// <summary>A fold under an activity line: "+ inspect the code" (mono, muted; "– hide the code" while open); open,
        /// the detail in a read-only mono field on the well, at most 320 px tall, scrolling past that, its text
        /// selectable.</summary>
        VisualElement Fold(AutopilotFeedLine line, string label, string detail, bool result)
        {
            var key = (line, result);
            var open = openFolds.Contains(key);
            var fold = Box("col feed-fold");
            var toggle = L(AutopilotText.FoldLabel(label, open), "mono tiny t-muted feed-fold-toggle");
            toggle.AddManipulator(new Clickable(() =>
            {
                if (!openFolds.Remove(key)) openFolds.Add(key);
                feedList?.RefreshItems();  // (the row's height changed)
            }));
            fold.Add(toggle);
            if (open)
            {
                var scroll = new ScrollView(ScrollViewMode.Vertical) { horizontalScrollerVisibility = ScrollerVisibility.Hidden };
                scroll.AddToClassList("feed-detail");
                var field = new TextField { multiline = true, isReadOnly = true, value = detail ?? "" };
                field.AddToClassList("mono-field");
                field.AddToClassList("feed-detail-text");
                scroll.Add(field);
                fold.Add(scroll);
            }
            return fold;
        }

        /// <summary>The tag before a feed line's words: an engine finding's severity, the agent's finding, the memory
        /// kept; null for the others.</summary>
        static VisualElement FeedTag(AutopilotFeedLine line)
        {
            VisualElement tag;
            switch (line.Kind)
            {
                case "engine": tag = Tag("Engine", SeverityTone(line.Severity)); break;
                case "agent": tag = Tag("Agent", "red"); break;
                case "memory": tag = Tag("Memory", "lime"); break;
                default: return null;
            }
            tag.style.marginRight = 12;
            return tag;
        }

        static string SeverityTone(string severity)
        {
            switch ((severity ?? "").ToLowerInvariant())
            {
                case "error":
                case "critical":
                case "fatal":
                case "bug":
                    return "red";
                case "warning":
                case "warn":
                    return "amber";
                default:
                    return "info";
            }
        }

        // -- feed ------------------------------------------------------------------------------------------

        // The Feed tab's "Nothing yet." and the plate the lines are on (one shows: ShowFeedPlate)
        Label feedNone;
        VisualElement feedPlate;

        // The Feed tab's items: the run's lines under their stages' headings (GroupFeed), as last built
        List<AutopilotFeedLine> feedItems = new List<AutopilotFeedLine>();

        void BuildFeed(AutopilotRun run)
        {
            // The run as a transcript on a plate (radius 8) under the tabs: a heading per stage, its lines under it, each
            // activity line with its folds; "Nothing yet." in its place until the first line comes
            var page = Box("feed-tab");
            content.Add(page);
            feedNone = L("Nothing yet.", "t-muted small");
            page.Add(feedNone);
            feedPlate = Box("plate feed-plate");
            page.Add(feedPlate);
            feedItems = GroupFeed(run.Feed);
            feedList = new ListView(feedItems, 22, () => Box("feed-line"), (e, i) =>
            {
                if (i < 0 || i >= feedItems.Count) return;
                var line = feedItems[i];
                var last = LastAttemptLine(run.Feed);
                FillFeedRow(e, line, last >= 0 && run.Feed[last] == line, false);
            })
            {
                virtualizationMethod = CollectionVirtualizationMethod.DynamicHeight,
                selectionType = SelectionType.None,
            };
            feedList.AddToClassList("grow");
            feedList.AddToClassList("feed-list");
            feedPlate.Add(feedList);
            ShowFeedPlate(run);
            builtVersion = run.Version;
            if (feedItems.Count > 0) feedList.schedule.Execute(() => feedList.ScrollToItem(feedItems.Count - 1));
        }

        /// <summary>The Feed tab's list told the run's feed changed: its items grouped again, scrolled to the end while the
        /// run goes.</summary>
        void RefreshFeed(AutopilotRun run)
        {
            if (feedList == null) return;
            feedItems = GroupFeed(run.Feed);
            feedList.itemsSource = feedItems;
            feedList.RefreshItems();
            ShowFeedPlate(run);
            if (run.Active && feedItems.Count > 0) feedList.ScrollToItem(feedItems.Count - 1);
        }

        void ShowFeedPlate(AutopilotRun run)
        {
            var any = run.Feed.Count > 0;
            if (feedNone != null) feedNone.style.display = any ? DisplayStyle.None : DisplayStyle.Flex;
            if (feedPlate != null) feedPlate.style.display = any ? DisplayStyle.Flex : DisplayStyle.None;
        }
    }
}
