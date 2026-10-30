// The Autopilot window's Settings page: how Autopilot runs (the account card: Aura, the Aura app, BYOK, or not set up,
// from the agent service's account frame and preferences.json), this project's runs and, for runs through Aura, what
// Autopilot billed (the project's total and run by run), whether runs' data is shared, how to run Autopilot from
// the user's own Claude Code or Codex, and Remove Autopilot (AutopilotView.Remove.cs; AutopilotView.cs has the rest
// of the window).

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
    public sealed partial class AutopilotView
    {
        /// <summary>The data-sharing disclosure: the same words on the Settings page as in the setup's step 3.</summary>
        public const string ShareLine = TrainLine;

        string SettingsKey()
        {
            var share = Preferences.Current.ShareData;
            var client = EditorHost.Client;
            var prefs = Preferences.Current;
            return $"settings|{Wide}|{AutopilotUsage.Version}|{SelectedId}|{share}|{client != null && client.Connected}|{client != null && client.RunActive}|" +
                   $"{client?.AccountVersion}|{client != null && client.CanSignIn}|{SigningIn}|{signOutConfirm}|{setupProblem}|{prefs.Access}|{PlaytestSettings.Get().UseWithAura}|{removeConfirm}|" +
                   $"{AgentService.Current}|{AgentService.Version}";
        }

        void BuildSettings()
        {
            var old = content.Q<ScrollView>();
            var offset = old != null ? old.scrollOffset : Vector2.zero;
            content.Clear();
            PageHead("Settings", Btn("Back to runs", () => ShowPage("runs"), "quiet small"));
            var box = PageBox("settings-page stack-12");
            var scroll = content.Q<ScrollView>();

            box.Add(AccountCorner());
            box.Add(UsageCard());
            box.Add(SharingCard());
            box.Add(AssistantsCard());
            box.Add(RemoveCard());
            if (offset != Vector2.zero) scroll.schedule.Execute(() => scroll.scrollOffset = offset);
        }

        static string KindName(string kind)
        {
            switch (kind)
            {
                case "verify": return "Verify";
                case "goal": return "Goal";
                case "explore": return "Explore";
                default: return "Run";
            }
        }

        static string UsagePrompt(UsageEntry e)
        {
            var p = (e.Prompt ?? "").Trim();
            return p.Length > 0 ? AutopilotText.Short(p, 80) : FallbackPrompt(e.Kind);
        }

        /// <summary>This project's runs: how many, what Aura billed for them in all, and each run (Time, Prompt,
        /// Billed: "—" for a run Aura didn't bill, BYOK's).</summary>
        VisualElement UsageCard()
        {
            var entries = AutopilotUsage.Entries.Where(e => e.Usage != null || e.Bill != null).ToList();
            if (entries.Count == 0)
            {
                // (no run with usage yet: the plate's name and one line)
                var empty = Card("plate card-sec stack-6 usage");
                empty.Add(Lbl("This project"));
                empty.Add(Para("No usage yet.", "t-sec2"));
                return empty;
            }
            var card = Card("plate flat card-sec usage");
            var head = Box("usage-head");
            head.Add(Lbl($"This project · {entries.Count} run{(entries.Count == 1 ? "" : "s")}"));
            var total = AutopilotUsage.TotalBilled(entries);
            if (total != null)
            {
                var sum = Box("usage-total");
                sum.Add(L("Billed by Aura"));
                sum.Add(L(Billing.Money(total.Value), "mono small"));
                head.Add(sum);
            }
            card.Add(head);
            var body = Box("usage-body");
            card.Add(body);
            var columns = Row("urow head");
            columns.Add(Lbl("Time", "ucell-when"));
            columns.Add(Lbl("Prompt", "ucell-name"));
            columns.Add(Lbl("Billed", "ucell-bill"));
            body.Add(columns);
            // (rows to read, not to pick)
            for (var i = entries.Count - 1; i >= 0; --i)
            {
                var e = entries[i];
                var row = Row("urow");
                row.Add(L(AutopilotText.When(e.Started), "mono small t-sec2 ucell-when nowrap"));
                var prompt = L(UsagePrompt(e), "ucell-name nowrap");
                prompt.tooltip = e.Prompt;
                row.Add(prompt);
                var billed = e.Billed;
                var bill = L(billed == null ? "—" : Billing.Money(billed.Value), "mono small ucell-bill nowrap" + (billed == null ? " t-muted" : ""));
                row.Add(bill);
                body.Add(row);
            }
            return card;
        }

        public const string ShareTitle = "Share runs to improve Autopilot";

        /// <summary>The words beside the sharing switch, as it is now: "Data sharing on" / "Data sharing off".</summary>
        public static string ShareState(bool on) => on ? "Data sharing on" : "Data sharing off";

        /// <summary>A setting's card (Share runs, Autopilot in Claude Code &amp; Codex): its title (mono capitals),
        /// the line under it (as the sharing text: small, t-sec2), then its control (the box returned), the same
        /// gaps in each (.setting in Autopilot.uss).</summary>
        static VisualElement SettingCard(string title, string line, string classes, out VisualElement control)
        {
            var card = Card("plate card-sec setting " + classes);
            card.Add(Lbl(title, "setting-title"));
            card.Add(Para(line, "small t-sec2 setting-line"));
            control = Box("setting-control");
            card.Add(control);
            return card;
        }

        VisualElement SharingCard()
        {
            var card = SettingCard(ShareTitle, ShareLine, "sharing", out var control);
            control.Add(ShareSwitch(Preferences.Current.ShareData, SetShare));
            return card;
        }

        /// <summary>The sharing switch: its words, right of the pill, say how it is now (ShareState; clicking them
        /// toggles it too); <paramref name="changed"/> gets each new value.</summary>
        public static Toggle ShareSwitch(bool on, Action<bool> changed)
        {
            var toggle = Switch(ShareState(on), on);
            toggle.AddToClassList("share-toggle");
            toggle.tooltip = ShareTitle;
            toggle.RegisterValueChangedCallback(e =>
            {
                toggle.text = ShareState(e.newValue);
                changed(e.newValue);
            });
            return toggle;
        }

        public const string AssistantsTitle = "Autopilot in Claude Code & Codex";
        public const string AssistantsLine = "One-time CLI setup to run Autopilot through Claude Code or Codex using your Claude or ChatGPT subscriptions, respectively.";
        /// <summary>What sets up the user's own Claude Code and Codex (autopilot/connect.py), one row each.</summary>
        public static readonly string[] ConnectCommands = { "autopilot connect claude", "autopilot connect codex" };

        /// <summary>Running Autopilot from the user's own Claude Code or Codex: the command that sets each up, with
        /// Copy; a run started there shows in this window.</summary>
        static VisualElement AssistantsCard()
        {
            var card = SettingCard(AssistantsTitle, AssistantsLine, "assistants", out var control);
            foreach (var command in ConnectCommands)
            {
                var row = Row("wrap serve-row connect-command" + (control.childCount > 0 ? " next" : ""));
                row.Add(L(command, "code"));
                var copy = command;
                row.Add(Btn("Copy", () => EditorGUIUtility.systemCopyBuffer = copy, "quiet small"));
                control.Add(row);
            }
            return card;
        }

        static void SetShare(bool share)
        {
            if (Preferences.Current.ShareData == share) return;
            if (!Preferences.Update(new Dictionary<string, object> { ["share_data"] = share }))
                AutopilotStatus.Say(AutopilotStatus.SetupNotSaved(Preferences.DefaultPath, Preferences.LastError));
            Reconnect();
        }

        /// <summary>Connect again (when connected and no run is going) so the agent service gets the new choices.</summary>
        static void Reconnect()
        {
            var client = EditorHost.Client;
            if (client == null || !client.Connected || client.RunActive) return;
            EditorHost.Disconnect();
            EditorHost.Connect();
        }
    }
}
