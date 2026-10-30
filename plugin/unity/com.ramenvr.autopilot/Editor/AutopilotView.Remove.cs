// Uninstalling Autopilot from this computer (Editor/Uninstall.cs): the Settings page's last card, Uninstall Autopilot
// (Uninstall, confirmed in place, with whether to delete what Autopilot learned), and the page that shows, in
// place of everything else, how the removal went until Set up again. And the other way round: set up, but the agent
// service is gone from this computer (uninstalled by hand), the window opens the first-run setup's Agent service step.

using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;
using static Autopilot.Playtest.Editor.AutopilotUI;

namespace Autopilot.Playtest.Editor
{
    public sealed partial class AutopilotView
    {
        public const string RemoveTitle = "Uninstall Autopilot";
        public const string RemoveLine = "Removes the agent service, your keys, your Aura sign-in and your setup choices, for every project.";
        public const string RemoveButton = "Uninstall";
        public const string RemoveQuestion = "Uninstall Autopilot from this computer?";
        public const string DeleteMemoryLabel = "[Optional] Delete Autopilot’s memories for all projects in this machine across all editors. I understand that this action is destructive and deletes Autopilot’s memories for my entire team.";
        public const string RemovingLine = "Uninstalling Autopilot from this computer…";
        public const string RemovedTitle = "Uninstallation successful";
        public const string RemoveFailedTitle = "Couldn’t uninstall Autopilot from this computer.";
        public const string RemovedNext = "To finish, remove the Autopilot package from each project in Window > Package Manager.";

        bool removeConfirm;
        bool removeDelete;  // (the confirmation's Delete Autopilot's memories: unticked each time it asks)

        // -- the Settings page's card ---------------------------------------------------------------------------

        /// <summary>Uninstall Autopilot: Uninstall (greyed while a run goes, saying why), confirmed in
        /// place with what it removes and whether to delete what Autopilot learned (unticked: kept).</summary>
        VisualElement RemoveCard()
        {
            var card = Box("plate card-sec remove-card");
            var free = CanChangeAccount(EditorHost.Client);
            if (removeConfirm && free)
            {
                card.AddToClassList("confirm");
                card.AddToClassList("stack-8");
                card.Add(Lbl(RemoveTitle));
                card.Add(Para(RemoveQuestion, "big breaks"));
                card.Add(Para(RemoveLine, "small t-sec2"));
                // (its words beside the box, wrapping in a narrow window)
                var delete = new Toggle { text = DeleteMemoryLabel, value = removeDelete };
                delete.AddToClassList("wrap-toggle");
                delete.RegisterValueChangedCallback(e => removeDelete = e.newValue);
                card.Add(delete);
                var buttons = Box("buttons");
                buttons.style.paddingTop = 2;
                buttons.Add(Btn("Uninstall", () =>
                {
                    // (never while tests run: Uninstall.RealRunsBlocked)
                    if (!Uninstall.AllowedNow(AgentService.FindHere())) return;
                    removeConfirm = false;
                    if (removeDelete)
                    {
                        // (the runs forgotten here; their files go with the uninstall, and here too in case it can't)
                        AutopilotSession.Forget();
                        Uninstall.ForgetProject(EditorHost.Instance.ProjectDir);
                    }
                    Uninstall.Start(!removeDelete);
                    Refresh(true);
                }, "small danger-fill uninstall-confirm"));
                buttons.Add(Btn("Cancel", () =>
                {
                    removeConfirm = false;
                    Refresh(true);
                }, "quiet small"));
                card.Add(buttons);
                return card;
            }
            removeConfirm = false;
            card.AddToClassList("plate-row");
            card.Add(Lbl(RemoveTitle, "grow"));
            // (greyed by a class rather than disabled, so the tooltip still shows)
            var remove = Btn(RemoveButton, () =>
            {
                if (!CanChangeAccount(EditorHost.Client)) return;
                removeConfirm = true;
                removeDelete = false;
                Refresh(true);
            }, "small danger uninstall-ask" + (free ? "" : " off"));
            remove.tooltip = free ? "" : AccountBusyTip;
            card.Add(remove);
            return card;
        }

        // -- how the removal went --------------------------------------------------------------------------------

        static string RemovedKey() => $"removed|{Uninstall.Current}|{Uninstall.Version}";

        void BuildRemoved()
        {
            content.Clear();
            var page = PageBox("connect-page");
            var card = Card("plate stack-10 removed");
            page.Add(card);
            var head = Row();
            head.Add(new Logo(20));
            var name = Lbl("Autopilot", "t-sec");
            name.style.marginLeft = 10;
            head.Add(name);
            card.Add(head);
            var buttons = Box("buttons ruled");
            void Words(string title, string classes = "big breaks") => card.Add(Para(title, classes));
            // (what is left to remove by hand: the package, in each project)
            void Next() => card.Add(Para(RemovedNext, "small t-sec2 breaks"));
            switch (Uninstall.Current)
            {
                case Uninstall.Phase.Running:
                    card.Add(Progress(RemovingLine, "body-500"));
                    return;
                case Uninstall.Phase.Removed:
                    Words(RemovedTitle);
                    Next();
                    buttons.Add(Btn("Set up again", SetUpAgain, "primary"));
                    buttons.Add(Btn(ViewLogs, Uninstall.OpenLog, "quiet"));
                    break;
                case Uninstall.Phase.RemovedButSome:
                    Words(RemovedTitle);
                    Next();
                    buttons.Add(Btn(ViewLogs, Uninstall.OpenLog, "primary"));
                    buttons.Add(Btn("Set up again", SetUpAgain, "quiet"));
                    break;
                default:
                    Words(RemoveFailedTitle, "big breaks t-red");
                    buttons.Add(Btn(ViewLogs, Uninstall.OpenLog));
                    buttons.Add(Btn("Try again", () =>
                    {
                        Uninstall.TryAgain();
                        Refresh(true);
                    }, "primary"));
                    buttons.Add(Btn("Cancel", () =>
                    {
                        Uninstall.Leave();
                        Refresh(true);
                    }, "quiet"));
                    break;
            }
            card.Add(buttons);
        }

        /// <summary>Set up again: the window as usual, and the first-run setup from its first step.</summary>
        public void SetUpAgain()
        {
            Uninstall.Leave();
            if (Uninstall.Holds) return;  // (still going)
            OpenSetup();
        }

        // -- set up, but the agent service is gone ----------------------------------------------------------------

        const string ServiceCheckedKey = "Autopilot.ServiceGoneChecked";  // (once per editor session)

        /// <summary>Whether the window opens the first-run setup at its Agent service step: the setup was done, the window
        /// starts the agent service (StartsWithConnect: automatic start on, not Use with Aura, the default URL), none is
        /// running, and the `autopilot` command isn't on this computer; never in batch mode or a command-line run, nor
        /// while the window is busy (removed from this computer, the setup open, connected).</summary>
        public static bool ServiceGone(bool onboarded, bool startsWithConnect, bool running, bool found, bool batch, bool busy)
        {
            return onboarded && startsWithConnect && !running && !found && !batch && !busy;
        }

        /// <summary>Once per editor session, as the window opens: set up, but the agent service is gone (ServiceGone),
        /// the setup's Agent service step, where Install (or, in a repository checkout, what to do) shows.</summary>
        public void CheckServiceGone()
        {
            if (SessionState.GetBool(ServiceCheckedKey, false)) return;
            SessionState.SetBool(ServiceCheckedKey, true);
            var client = EditorHost.Client;
            var busy = Uninstall.Holds || SetupOpen || (client != null && (client.Connected || client.RunActive));
            var batch = Application.isBatchMode || !string.IsNullOrEmpty(PlaytestClient.Arg("Playtest"));
            if (!ServiceGone(Preferences.Current.Onboarded, AgentService.StartsWithConnect(PlaytestSettings.Get()), AgentService.RunningUrl() != null,
                    AgentService.FindHere() != null, batch, busy))
                return;
            var access = Preferences.Current.Access;
            SetupOpen = true;
            SetupAccess = access;
            SetupChoice = access == Preferences.AccessOwnKeys ? ChoiceKeys : access == Preferences.AccessAura ? ChoiceAura : "";
            GoToStep(2);
        }

        // -- tests ------------------------------------------------------------------------------------------------

        /// <summary>Tests: the Settings page's Remove Autopilot confirming.</summary>
        internal void TestConfirmRemove()
        {
            removeConfirm = true;
        }
    }
}
