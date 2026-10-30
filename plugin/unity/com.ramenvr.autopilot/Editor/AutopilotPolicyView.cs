// The Policy log tab: every policy step of a run (newest at the bottom, following live until a row is selected),
// filtered by attempt and action tag; a replay strip of what ran over game time; the selected step's action
// probabilities π(a | s) and its Observation, readable or as raw JSON (our instructions taken out).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;
using static Autopilot.Playtest.Editor.AutopilotUI;

namespace Autopilot.Playtest.Editor
{
    public sealed class AutopilotPolicyView : VisualElement
    {
        static readonly string[] Tags = { "SWITCH", "START", "KEEP", "HELD", "DROPPED", "FAILED" };
        public const string PickStepLine = "Pick a step to see what the policy observed and chose.";
        public const string NoStepsLine = "No policy steps yet.";
        public const string FindPlaceholder = "Find an action…";
        public const string PreviousStepName = "Previous step";
        public const string NextStepName = "Next step";

        /// <summary>Pick the step <paramref name="by"/> rows from <paramref name="from"/> (◀ ▶): paused on it.</summary>
        internal void StepBy(AutopilotStep from, int by)
        {
            var i = rows.IndexOf(from) + by;
            if (i < 0 || i >= rows.Count) return;
            selected = rows[i];
            Update();
            list.ScrollToItem(i);
        }

        /// <summary>Tests: the step shown in the detail (the one picked, else the last).</summary>
        internal AutopilotStep Shown => selected ?? (run.Steps.Count > 0 ? run.Steps[run.Steps.Count - 1] : null);
        internal int RowCount => rows.Count;
        internal void TestFind(string words) => search.value = words;
        const int MaxRawChars = 16000;

        AutopilotRun run;
        bool wide;
        bool built;
        int attemptFilter;  // 0: all
        readonly HashSet<string> hiddenTags = new HashSet<string>();
        AutopilotStep selected;
        bool raw;
        int seen = -1;
        AutopilotStep detailFor;

        readonly List<AutopilotStep> rows = new List<AutopilotStep>();
        readonly VisualElement filters, chips, follow, strip, body, table, detail;
        // Find an action…: the rows whose action (chosen or current) has these words
        readonly TextField search;
        string find = "";
        ListView list;
        Label foot;
        Lane lane;
        Ticks ticks;
        Label laneName, stripStart, stripEnd;
        VisualElement legend;

        public AutopilotPolicyView()
        {
            AddToClassList("grow");
            style.flexDirection = FlexDirection.Column;
            // (the chips and the following / paused part are built again; the search field stays, keeping its focus)
            filters = Row("filters");
            chips = Row("filter-chips wrap");
            filters.Add(chips);
            filters.Add(Box("grow filter-gap"));
            search = Field("", false, v =>
            {
                find = (v ?? "").Trim();
                Update();
            }, FindPlaceholder);
            search.AddToClassList("mono-field");
            search.AddToClassList("find-field");
            filters.Add(search);
            follow = Row("filter-follow");
            filters.Add(follow);
            Add(filters);
            strip = Box("strip");
            Add(strip);
            body = Box("grow");
            Add(body);
            table = Box("policy-table col grow");
            detail = new ScrollView(ScrollViewMode.Vertical);
            detail.AddToClassList("detail");
        }

        public void Refresh(AutopilotRun r, bool isWide)
        {
            if (!built || r != run || isWide != wide)
            {
                if (r != run)
                {
                    selected = null;
                    attemptFilter = 0;
                    hiddenTags.Clear();
                }
                run = r;
                wide = isWide;
                Build();
                return;
            }
            if (r.Steps.Count != seen) Update();
        }

        int Attempts => run.Steps.Count == 0 ? 0 : run.Steps[run.Steps.Count - 1].Attempt;

        /// <summary>The attempt the strip and footer are about: the selected step's, the filter's, else the last.</summary>
        int FocusAttempt => selected != null ? selected.Attempt : attemptFilter > 0 ? attemptFilter : Attempts;

        void Build()
        {
            built = true;
            body.Clear();
            body.style.flexDirection = wide ? FlexDirection.Row : FlexDirection.Column;
            table.Clear();
            var head = Row("prow head");
            foreach (var (cls, text) in new[] { ("c-wall", "Wall clock"), ("c-t", "Game t"), ("c-n", "Step"), ("c-cur", "Current action"), ("c-dec", "Decision"), ("c-bar", "Action probabilities"), ("c-ms", "ms") })
            {
                head.Add(Lbl(text, cls));
            }
            table.Add(head);
            list = new ListView(rows, 28, MakeRow, BindRow)
            {
                selectionType = SelectionType.Single,
                virtualizationMethod = CollectionVirtualizationMethod.FixedHeight,
            };
            list.AddToClassList("policy-list");
            list.selectionChanged += items =>
            {
                var s = items?.OfType<AutopilotStep>().FirstOrDefault();
                if (s == null) return;
                selected = s;
                ShowDetail();
                BuildFilters();
                UpdateStrip();
            };
            table.Add(list);
            foot = L("", "mono tiny t-muted policy-foot");
            table.Add(foot);
            body.Add(table);
            body.Add(detail);
            BuildStrip();
            seen = -1;
            Update();
        }

        void Update()
        {
            seen = run.Steps.Count;
            Filter();
            BuildFilters();
            UpdateStrip();
            UpdateFoot();
            ShowDetail();
        }

        void Filter()
        {
            rows.Clear();
            foreach (var s in run.Steps)
            {
                if (attemptFilter > 0 && s.Attempt != attemptFilter) continue;
                if (hiddenTags.Contains(s.Tag)) continue;
                if (find.Length > 0 && (s.Chosen ?? "").IndexOf(find, StringComparison.OrdinalIgnoreCase) < 0
                    && (s.Current ?? "").IndexOf(find, StringComparison.OrdinalIgnoreCase) < 0) continue;
                rows.Add(s);
            }
            list.itemsSource = rows;
            list.RefreshItems();
            var i = selected == null ? -1 : rows.IndexOf(selected);
            if (i >= 0) list.SetSelectionWithoutNotify(new[] { i });
            else
            {
                list.ClearSelection();
                if (rows.Count > 0) list.schedule.Execute(() => list.ScrollToItem(rows.Count - 1));
            }
        }

        void Follow()
        {
            selected = null;
            Update();
        }

        // -- filters ------------------------------------------------------------------------------------------

        void BuildFilters()
        {
            chips.Clear();
            follow.Clear();
            chips.Add(Lbl("Policy"));
            chips.Add(Chip($"All · {run.Steps.Count}", attemptFilter == 0, () => SetAttempt(0)));
            for (var a = 1; a <= Attempts; ++a)
            {
                var n = run.Steps.Count(s => s.Attempt == a);
                var attempt = a;
                chips.Add(Chip($"H{a} · {n}", attemptFilter == a, () => SetAttempt(attempt)));
            }
            chips.Add(Box("filter-sep"));
            chips.Add(Lbl("Show"));
            foreach (var tag in Tags)
            {
                var n = run.Steps.Count(s => s.Tag == tag && (attemptFilter == 0 || s.Attempt == attemptFilter));
                if (tag == "FAILED" && n == 0) continue;  // (FAILED only when a step failed)
                var t = tag;
                chips.Add(Chip($"{tag} {n}", !hiddenTags.Contains(tag), () =>
                {
                    if (!hiddenTags.Remove(t)) hiddenTags.Add(t);
                    Update();
                }));
            }
            if (selected == null) follow.Add(DotLabel("FOLLOWING", Accent));
            else
            {
                // (a step picked: paused on it, and back to following)
                follow.Add(L($"PAUSED ON H{selected.Attempt} #{selected.Call}", "paused-on"));
                follow.Add(Btn("Follow live", Follow, "small follow-live"));
            }
        }

        void SetAttempt(int a)
        {
            attemptFilter = a;
            if (selected != null && a > 0 && selected.Attempt != a) selected = null;
            Update();
        }

        // -- the table ---------------------------------------------------------------------------------------

        static VisualElement MakeRow()
        {
            var row = Box("prow mono");
            row.Add(L("", "mono c-wall"));
            row.Add(L("", "mono c-t"));
            row.Add(L("", "mono c-n"));
            row.Add(L("", "mono c-cur nowrap"));
            var dec = Box("c-dec");
            dec.Add(L("", "act"));
            dec.Add(L("", "mono nowrap grow"));
            row.Add(dec);
            var bar = new Bar();
            bar.AddToClassList("c-bar");
            row.Add(bar);
            row.Add(L("", "mono c-ms"));
            return row;
        }

        void BindRow(VisualElement e, int i)
        {
            if (i < 0 || i >= rows.Count) return;
            var s = rows[i];
            ((Label)e[0]).text = s.Wall > 0 ? AutopilotText.Local(s.Wall).ToString("HH:mm:ss.fff", CultureInfo.InvariantCulture) : "—";
            ((Label)e[1]).text = AutopilotText.Num(s.T, "0.00") + "s";
            ((Label)e[2]).text = s.Label;
            var cur = (Label)e[3];
            cur.text = string.IsNullOrEmpty(s.CurrentShown) ? "—" : s.CurrentShown;
            cur.style.color = string.IsNullOrEmpty(s.Current) ? Muted : ActionColor(run.ColorOf(s.Current));
            var dec = e[4];
            var act = (Label)dec[0];
            foreach (var t in Tags) act.EnableInClassList(t, t == s.Tag);
            act.text = TagWord(s.Tag);
            var chosen = (Label)dec[1];
            // (a failed call: its error in the decision's place, in red)
            var failed = s.Error.Length > 0;
            chosen.text = failed ? AutopilotText.Scrub(s.Error) : string.IsNullOrEmpty(s.Chosen) ? "—" : s.Chosen;
            chosen.style.color = failed ? Red : ActionColor(run.ColorOf(s.Chosen));
            chosen.EnableInClassList("mono-700", s.Tag == "SWITCH");
            ((Bar)e[5]).Set(Segments(s, 2));
            ((Label)e[6]).text = s.Ms > 0 ? s.Ms.ToString("0", CultureInfo.InvariantCulture) : "—";
        }

        static string TagWord(string tag)
        {
            switch (tag)
            {
                case "START": return "START";
                case "KEEP": return "KEEP";
                case "SWITCH": return "SWITCH";
                case "HELD": return "HELD";
                case "DROPPED": return "DROPPED";
                default: return string.IsNullOrEmpty(tag) ? "—" : tag;
            }
        }

        static string TagLong(string tag)
        {
            switch (tag)
            {
                case "START": return "Started";
                case "KEEP": return "Kept";
                case "SWITCH": return "Switched";
                case "HELD": return "Held";
                case "DROPPED": return "Dropped";
                case "FAILED": return "Failed";
                default: return string.IsNullOrEmpty(tag) ? "Step" : tag;
            }
        }

        IEnumerable<(Color, float)> Segments(AutopilotStep s, int top)
        {
            var rest = 0.0;
            for (var i = 0; i < s.Pick.Count; ++i)
            {
                if (i < top) yield return (ActionColor(run.ColorOf(s.Pick[i].Key)), (float)s.Pick[i].Value);
                else rest += s.Pick[i].Value;
            }
            if (rest > 0) yield return (Hex("#3a3e47"), (float)rest);
        }

        /// <summary>The footer: the attempt picked (a step's, the filter's) as "H2: …"; with All and no step picked,
        /// every harness's as "All harnesses: …".</summary>
        void UpdateFoot()
        {
            var all = selected == null && attemptFilter == 0;
            var a = FocusAttempt;
            var steps = all ? run.Steps.ToList() : run.Steps.Where(s => s.Attempt == a).ToList();
            if (steps.Count == 0)
            {
                foot.text = "";
                return;
            }
            // (the steps in scope, not the rows the filters leave; the latency over the steps that had one)
            var ms = steps.Where(s => s.Ms > 0).Select(s => s.Ms).OrderBy(x => x).ToList();
            int C(string tag) => steps.Count(s => s.Tag == tag);
            string N(int n, string one, string many) => $"{n} {(n == 1 ? one : many)}";
            var text = $"{(all ? "All harnesses" : "H" + a)}: {N(steps.Count, "step", "steps")}";
            if (ms.Count > 0)
                text += $" · median {ms[ms.Count / 2].ToString("0", CultureInfo.InvariantCulture)} ms · max {ms[ms.Count - 1].ToString("0", CultureInfo.InvariantCulture)} ms";
            text += $" · {N(C("SWITCH"), "switch", "switches")} · {N(C("START"), "start", "starts")} · {C("HELD")} held · {C("DROPPED")} dropped";
            if (C("FAILED") > 0) text += $" · {C("FAILED")} failed";
            var gone = run.StepsSeen - run.Steps.Count;
            if (gone > 0) text += $" · the oldest {gone} no longer kept";
            foot.text = text;
        }

        // -- the replay strip ---------------------------------------------------------------------------------

        void BuildStrip()
        {
            strip.Clear();
            var r1 = Row();
            laneName = Lbl("", "strip-name");
            r1.Add(laneName);
            lane = new Lane(this);
            lane.AddToClassList("strip-lane");
            r1.Add(lane);
            strip.Add(r1);
            var r2 = Row("strip-ticks-row");
            r2.Add(Lbl("Policy steps", "strip-name"));
            ticks = new Ticks(this);
            ticks.AddToClassList("strip-ticks");
            r2.Add(ticks);
            strip.Add(r2);
            var r3 = Row();
            r3.Add(Box("strip-name"));
            stripStart = L("", "mono tiny t-muted");
            r3.Add(stripStart);
            r3.Add(Spacer());
            stripEnd = L("", "mono tiny t-muted");
            r3.Add(stripEnd);
            strip.Add(r3);
            legend = Row("wrap strip-legend");
            strip.Add(legend);
        }

        List<AutopilotStep> StripSteps()
        {
            var a = FocusAttempt;
            return run.Steps.Where(s => s.Attempt == a).ToList();
        }

        void UpdateStrip()
        {
            var steps = StripSteps();
            strip.style.display = steps.Count > 1 ? DisplayStyle.Flex : DisplayStyle.None;
            if (steps.Count <= 1) return;
            laneName.text = $"H{FocusAttempt} · RUNNING";
            stripStart.text = AutopilotText.Num(steps[0].T, "0.0") + "s";
            stripEnd.text = AutopilotText.Num(steps[steps.Count - 1].T, "0.0") + "s";
            legend.Clear();
            var names = steps.Select(Running).Where(n => !string.IsNullOrEmpty(n)).Distinct().Take(8);
            foreach (var n in names)
            {
                var item = Box("legend-item");
                var sw = Box("swatch");
                sw.style.backgroundColor = ActionColor(run.ColorOf(n));
                item.Add(sw);
                item.Add(L(n, "mono tiny t-sec2"));
                legend.Add(item);
            }
            var held = Box("legend-item");
            var hs = Box("swatch");
            hs.style.backgroundColor = Amber;
            held.Add(hs);
            held.Add(L("held", "mono tiny t-muted"));
            legend.Add(held);
            var sws = Box("legend-item");
            var ss = Box("swatch");
            ss.style.backgroundColor = Accent;
            ss.style.rotate = new Rotate(Angle.Degrees(45));
            sws.Add(ss);
            sws.Add(L("switch", "mono tiny t-muted"));
            legend.Add(sws);
            lane.MarkDirtyRepaint();
            ticks.MarkDirtyRepaint();
        }

        /// <summary>The action running after a step.</summary>
        static string Running(AutopilotStep s)
        {
            if (s.Tag == "HELD" || s.Tag == "DROPPED") return string.IsNullOrEmpty(s.Current) ? s.Chosen : s.Current;
            return string.IsNullOrEmpty(s.Chosen) ? s.Current : s.Chosen;
        }

        void SelectNearest(float fraction)
        {
            var steps = StripSteps();
            if (steps.Count == 0) return;
            var t0 = steps[0].T;
            var t1 = steps[steps.Count - 1].T;
            var t = t0 + fraction * Math.Max(1e-6, t1 - t0);
            var best = steps.OrderBy(s => Math.Abs(s.T - t)).First();
            selected = best;
            if (hiddenTags.Contains(best.Tag) || (attemptFilter > 0 && attemptFilter != best.Attempt))
            {
                hiddenTags.Remove(best.Tag);
                attemptFilter = 0;
            }
            Update();
            var i = rows.IndexOf(best);
            if (i >= 0) list.ScrollToItem(i);
        }

        sealed class Lane : VisualElement
        {
            readonly AutopilotPolicyView view;

            public Lane(AutopilotPolicyView v)
            {
                view = v;
                generateVisualContent += Draw;
                RegisterCallback<PointerDownEvent>(e =>
                {
                    if (contentRect.width > 0) view.SelectNearest(e.localPosition.x / contentRect.width);
                });
            }

            void Draw(MeshGenerationContext mgc)
            {
                var steps = view.StripSteps();
                var r = contentRect;
                if (steps.Count < 2 || r.width <= 0) return;
                var p = mgc.painter2D;
                FillRect(p, 0, 0, r.width, r.height, Hover);
                var t0 = steps[0].T;
                var span = Math.Max(1e-6, steps[steps.Count - 1].T - t0);
                for (var i = 0; i < steps.Count - 1; ++i)
                {
                    var x0 = (float)((steps[i].T - t0) / span) * r.width;
                    var x1 = (float)((steps[i + 1].T - t0) / span) * r.width;
                    var name = Running(steps[i]);
                    if (string.IsNullOrEmpty(name) || x1 <= x0) continue;
                    FillRect(p, x0, 0, x1 - x0, r.height, ActionColor(view.run.ColorOf(name)));
                }
                if (view.selected != null && view.selected.Attempt == steps[0].Attempt)
                {
                    var x = (float)((view.selected.T - t0) / span) * r.width;
                    FillRect(p, Mathf.Clamp(x - 1, 0, r.width - 2), -3, 2, r.height + 6, Text);
                }
            }
        }

        sealed class Ticks : VisualElement
        {
            readonly AutopilotPolicyView view;

            public Ticks(AutopilotPolicyView v)
            {
                view = v;
                generateVisualContent += Draw;
            }

            void Draw(MeshGenerationContext mgc)
            {
                var steps = view.StripSteps();
                var r = contentRect;
                if (steps.Count < 2 || r.width <= 0) return;
                var p = mgc.painter2D;
                var t0 = steps[0].T;
                var span = Math.Max(1e-6, steps[steps.Count - 1].T - t0);
                foreach (var s in steps)
                {
                    var x = (float)((s.T - t0) / span) * (r.width - 2);
                    if (s.Tag == "SWITCH") Diamond(p, new Vector2(x + 1, r.height / 2f), 4.5f, Accent, Color.clear, 0);
                    else if (s.Tag == "HELD") FillRect(p, x - 2, 1, 6, 6, Amber);
                    else FillRect(p, x, 0, 1.5f, r.height, Hex("#3a3e47"));
                }
            }
        }

        // -- the selected step -------------------------------------------------------------------------------

        void ShowDetail()
        {
            var s = selected ?? (run.Steps.Count > 0 ? run.Steps[run.Steps.Count - 1] : null);
            if (s == detailFor && s != null && detail.childCount > 0) return;
            detailFor = s;
            detail.Clear();
            var box = Box("detail-box");
            detail.Add(box);
            if (s == null)
            {
                // (nothing to show: no steps yet, or none picked)
                box.Add(L(run.Steps.Count == 0 ? NoStepsLine : PickStepLine, "small t-muted"));
                return;
            }

            var meta = $"H{s.Attempt} · #{s.Call} · t={AutopilotText.Num(s.T, "0.0")}s" +
                       (s.Wall > 0 ? " · " + AutopilotText.Local(s.Wall).ToString("HH:mm:ss.fff", CultureInfo.InvariantCulture) : "") +
                       (s.Ms > 0 ? $" · {s.Ms:0} ms" : "");
            box.Add(L(meta, "mono tiny t-muted"));
            if (s.Error.Length > 0) box.Add(L("This step failed: " + AutopilotText.Scrub(s.Error), "small t-red step-failed"));
            var head = Row("wrap detail-head");
            var tag = L(TagLong(s.Tag).ToUpperInvariant(), "act wide");
            foreach (var t in Tags) tag.EnableInClassList(t, t == s.Tag);
            head.Add(tag);
            if (!string.IsNullOrEmpty(s.Current))
            {
                var cur = L(s.Current, "mono detail-action");
                cur.style.color = ActionColor(run.ColorOf(s.Current));
                head.Add(cur);
                head.Add(L("→", "mono t-muted detail-arrow"));
            }
            var chosen = L(s.Chosen, "mono-700 detail-action");
            chosen.style.color = ActionColor(run.ColorOf(s.Chosen));
            head.Add(chosen);
            box.Add(head);
            var state = AutopilotStep.State(s.Frame);
            var now = Json.Obj(state, "now_doing");
            if (now != null)
            {
                var label = Criteria(s, Json.Str(now, "option"));
                // Action: "Walk to the gate along the left fence" (4.2 s)
                var what = string.IsNullOrEmpty(label) ? Json.Str(now, "option") : AutopilotText.Scrub(label);
                box.Add(L($"Action: \"{what}\" ({AutopilotText.Num(Json.Num(now, "for_seconds", 0), "0.0")} s)", "small t-sec2 detail-then"));
            }

            var probs = Box("col detail-probs");
            probs.Add(Lbl("Policy output · π(a | s)", "detail-probs-head"));
            foreach (var pair in s.Pick)
            {
                var row = Box("pbar-row");
                row.Add(ActionName(pair.Key, pair.Key == s.Current, pair.Key == s.Chosen, false, "pbar-name"));
                var bar = new Bar();
                bar.AddToClassList("pbar");
                bar.Set(new[] { (ActionColor(run.ColorOf(pair.Key)), (float)pair.Value) });
                row.Add(bar);
                row.Add(L(AutopilotText.Prob(pair.Value), "mono pbar-value"));
                probs.Add(row);
            }
            box.Add(probs);

            var toggles = Row("detail-toggles");
            var obs = Lbl($"Observation · #{s.Call}", "t-accent grow");
            toggles.Add(obs);
            toggles.Add(Chip("Readable", !raw, () =>
            {
                raw = false;
                detailFor = null;
                ShowDetail();
            }));
            toggles.Add(Chip("Raw JSON", raw, () =>
            {
                raw = true;
                detailFor = null;
                ShowDetail();
            }));
            toggles.Add(Btn("Copy step JSON", () =>
            {
                EditorGUIUtility.systemCopyBuffer = Json.Pretty(s.Redacted());
                AutopilotStatus.Say(AutopilotStatus.Copied);
            }, "small"));
            // ◀ ▶: the step before and after this one among the rows shown
            var at = rows.IndexOf(s);
            var prev = IconBtn(Glyph.Shape.Prev, PreviousStepName, () => StepBy(s, -1), "small step-nav");
            prev.SetEnabled(at > 0);
            toggles.Add(prev);
            var next = IconBtn(Glyph.Shape.Next, NextStepName, () => StepBy(s, 1), "small step-nav");
            next.SetEnabled(at >= 0 && at < rows.Count - 1);
            toggles.Add(next);
            box.Add(toggles);

            if (raw)
            {
                var text = Json.Pretty(s.Redacted());
                if (text.Length > MaxRawChars) text = text.Substring(0, MaxRawChars) + "\n… (cut here: Copy step JSON has all of it)";
                var l = new Label(text) { enableRichText = false };
                l.AddToClassList("raw");
                l.selection.isSelectable = true;
                box.Add(l);
                return;
            }
            Readable(box, s, state);
        }

        /// <summary>An action's name (mono; the chosen one in its colour and 500, or every one in its colour when
        /// <paramref name="colored"/>), then " · current" muted when it was the one running.</summary>
        VisualElement ActionName(string option, bool current, bool chosen, bool colored, string classes)
        {
            var row = Row("action-name " + classes);
            var name = L(option, (chosen ? "mono-700" : "mono") + " nowrap");
            name.style.color = chosen || colored ? ActionColor(run.ColorOf(option)) : AutopilotUI.Text2;
            row.Add(name);
            if (current) row.Add(L(" · current", "mono t-muted nowrap action-current"));
            return row;
        }

        static string Criteria(AutopilotStep s, string option)
        {
            var pick = Json.Obj(Json.Obj(Json.Obj(s.Frame, "input"), "questions"), "pick");
            return Json.Str(Json.Obj(pick, "criteria"), option);
        }

        AutopilotStep Previous(AutopilotStep s)
        {
            var i = run.Steps.IndexOf(s);
            return i > 0 && run.Steps[i - 1].Attempt == s.Attempt ? run.Steps[i - 1] : null;
        }

        void Readable(VisualElement box, AutopilotStep s, Dictionary<string, object> state)
        {
            var goal = Json.Str(state, "goal", Json.Str(s.Frame, "goal"));
            if (!string.IsNullOrEmpty(goal))
            {
                var task = Box("sec card-sec row");
                task.style.alignItems = Align.FlexStart;
                var tl = Lbl("Task");
                tl.style.width = 50;
                tl.style.paddingTop = 2;
                tl.style.flexShrink = 0;
                task.Add(tl);
                task.Add(L(AutopilotText.Scrub(goal), "t-sec grow"));
                box.Add(Spaced(task));
            }

            var situation = Json.Obj(state, "situation");
            if (situation != null)
            {
                var prev = Previous(s);
                var before = prev == null ? null : Json.Obj(AutopilotStep.State(prev.Frame), "situation");
                var sec = Box("sec card-sec");
                var head = Row();
                head.style.marginBottom = 7;
                head.Add(Lbl("Observation · environment state"));
                head.Add(Spacer());
                var changed = 0;
                var kv = Box("col");
                foreach (var pair in situation)
                {
                    var value = Compact(pair.Value, true);
                    var isNew = before != null && (!before.TryGetValue(pair.Key, out var old) || Json.Write(old) != Json.Write(pair.Value));
                    if (isNew) ++changed;
                    kv.Add(KeyValue(pair.Key, value, isNew));
                }
                if (prev != null && changed > 0) head.Add(DotLabel(wide ? $"{changed} changed since #{prev.Call}" : $"{changed} changed", Accent, "close"));
                sec.Add(head);
                sec.Add(kv);
                box.Add(Spaced(sec));
            }

            var now = Json.Obj(state, "now_doing");
            var cur = Box("sec card-sec");
            cur.Add(Spaced(Lbl("Current action"), 7));
            if (now == null) cur.Add(L("none", "mono small t-sec2"));
            else
            {
                var option = Json.Str(now, "option");
                var line = L($"{option} for {AutopilotText.Num(Json.Num(now, "for_seconds", 0), "0.0")} s", "mono small");
                line.style.color = ActionColor(run.ColorOf(option));
                cur.Add(line);
            }
            box.Add(Spaced(cur));

            var recent = Json.List(state, "recent");
            if (recent != null && recent.Count > 0)
            {
                var sec = Box("sec card-sec");
                sec.Add(Spaced(Lbl("Recent actions · outcomes"), 7));
                foreach (var o in recent)
                {
                    if (!(o is Dictionary<string, object> r)) continue;
                    var row = Row();
                    row.style.alignItems = Align.FlexStart;
                    row.style.marginBottom = 2;
                    var option = Json.Str(r, "option");
                    var name = L(option, "mono recent-name nowrap");
                    name.style.color = ActionColor(run.ColorOf(option));
                    row.Add(name);
                    row.Add(L(AutopilotText.Num(Json.Num(r, "seconds", 0), "0.0") + " s", "mono recent-secs"));
                    var worked = r.TryGetValue("worked", out var w) ? w : null;
                    row.Add(L(worked is bool b ? (b ? "success" : "failure") : "unknown", "mono recent-outcome " + (worked is bool b2 ? (b2 ? "t-lime" : "t-red") : "t-muted")));
                    row.Add(L(AutopilotText.Scrub(Json.Str(r, "how")), "small t-sec2 grow"));
                    sec.Add(row);
                }
                box.Add(Spaced(sec));
            }

            var options = Json.Obj(state, "options");
            if (options != null)
            {
                var sec = Box("sec card-sec");
                var head = Row();
                head.style.marginBottom = 7;
                head.Add(Lbl($"Action space · {options.Count}"));
                head.Add(Spacer());
                head.Add(Lbl("π(a | s)"));
                sec.Add(head);
                foreach (var pair in options.OrderByDescending(p => s.P(p.Key)))
                {
                    var chosen = pair.Key == s.Chosen;
                    var card = Row("option" + (chosen ? " chosen" : ""));
                    card.style.alignItems = Align.FlexStart;
                    var col = Box("col grow");
                    col.Add(ActionName(pair.Key, pair.Key == s.Current, chosen, true, "option-name"));
                    var label = Criteria(s, pair.Key);
                    if (!string.IsNullOrEmpty(label)) col.Add(L(AutopilotText.Scrub(label), "small t-sec2"));
                    var facts = Facts(pair.Value);
                    if (!string.IsNullOrEmpty(facts)) col.Add(L(facts, "mono tiny t-muted"));
                    card.Add(col);
                    card.Add(L(AutopilotText.Prob(s.P(pair.Key)), (chosen ? "mono-700 t-accent" : "mono t-sec2") + " option-p"));
                    sec.Add(card);
                }
                box.Add(Spaced(sec));
            }

            var answers = Json.Obj(Json.Obj(s.Frame, "output"), "answers");
            if (answers != null && answers.Count > 1)
            {
                var sec = Box("sec card-sec");
                sec.Add(Spaced(Lbl("Other outputs"), 7));
                foreach (var pair in answers)
                    if (pair.Key != "pick")
                        sec.Add(KeyValue(pair.Key, Compact(pair.Value, true), false));
                box.Add(Spaced(sec));
            }
        }

        static VisualElement Spaced(VisualElement e, float bottom = 10)
        {
            e.style.marginBottom = bottom;
            return e;
        }

        VisualElement KeyValue(string key, string value, bool changed)
        {
            var row = Box("kv-row");
            var k = L(key, "mono kv-key");
            if (!wide) k.style.width = 110;
            row.Add(k);
            if (changed) row.Add(RoundDot(Accent));
            row.Add(L(value, "mono kv-value" + (changed ? " changed" : "")));
            return row;
        }

        static string Compact(object v, bool quoteStrings)
        {
            string text;
            if (v == null) text = "null";
            else if (v is string str) text = quoteStrings ? "\"" + str + "\"" : str;
            else if (v is bool b) text = b ? "true" : "false";
            else if (v is double d) text = AutopilotText.Num(d, "0.###");
            else text = Json.Write(v);
            return AutopilotText.Short(AutopilotText.Scrub(text), 240);
        }

        static string Facts(object facts)
        {
            if (facts is Dictionary<string, object> d)
                return AutopilotText.Short(string.Join(" · ", d.Select(p => $"{p.Key}: {Compact(p.Value, false)}")), 300);
            return facts == null ? "" : Compact(facts, false);
        }
    }
}
