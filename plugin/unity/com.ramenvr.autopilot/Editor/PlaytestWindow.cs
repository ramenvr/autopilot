// Window > Autopilot: connect, compose a run (one box: its words say verify, explore or play a goal), watch it live, read its result, the feed
// and the policy log; the Settings page; the first-run setup until it is done. The agent service enters play mode when it needs the game. The content is AutopilotView (UI
// Toolkit); the model, kept across script reloads, is AutopilotSession.

using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Autopilot.Playtest.Editor
{
    public sealed class PlaytestWindow : EditorWindow, IHasCustomMenu
    {
        [SerializeField] string selectedId;
        [SerializeField] string tab = "result";
        [SerializeField] bool composing;
        [SerializeField] string page = "runs";
        [SerializeField] bool setupOpen;
        [SerializeField] int setupStep = 1;
        [SerializeField] string setupAccess = "";
        [SerializeField] string setupChoice = "";
        [SerializeField] string setupEmail = "";
        [SerializeField] bool signingIn;

        AutopilotView view;

        [MenuItem("Window/Autopilot", priority = 2000)]
        public static void Open()
        {
            var w = GetWindow<PlaytestWindow>();
            w.titleContent = new GUIContent(AutopilotText.Name, AutopilotUI.Icon);
            w.minSize = new Vector2(300, 240);  // (the content reflows and scrolls: it never needs more)
            w.Show();
        }

        void OnEnable()
        {
            titleContent = new GUIContent(AutopilotText.Name, AutopilotUI.Icon);
        }

        void OnDisable()
        {
            RecordingPlayer.ReleaseAll();  // (a recording playing lets go of its player with the window)
        }

        void CreateGUI()
        {
            view = new AutopilotView
            {
                SelectedId = selectedId, Tab = string.IsNullOrEmpty(tab) ? "result" : tab, Composing = composing,
                Page = string.IsNullOrEmpty(page) ? "runs" : page,
                SetupOpen = setupOpen, SetupStep = setupStep >= 1 && setupStep <= 4 ? setupStep : 1, SetupAccess = setupAccess ?? "",
                SetupChoice = setupChoice ?? "", SetupEmail = setupEmail ?? "", SigningIn = signingIn,
            };
            view.StateChanged += () =>
            {
                selectedId = view.SelectedId;
                tab = view.Tab;
                composing = view.Composing;
                page = view.Page;
                setupOpen = view.SetupOpen;
                setupStep = view.SetupStep;
                setupAccess = view.SetupAccess;
                setupChoice = view.SetupChoice;
                setupEmail = view.SetupEmail;
                signingIn = view.SigningIn;
            };
            rootVisualElement.Add(view);
            view.CheckServiceGone();  // (set up, but the agent service is gone: the setup's Agent service step)
            view.Refresh(true);
        }

        public void AddItemsToMenu(GenericMenu menu)
        {
            var client = EditorHost.Client;
            var connected = client != null && client.Connected;
            if (client != null && client.WantConnected) menu.AddItem(new GUIContent("Disconnect"), false, EditorHost.Disconnect);
            else menu.AddItem(new GUIContent("Connect"), false, EditorHost.Connect);
            menu.AddItem(new GUIContent("Settings"), view != null && view.Page == "settings", () => view?.ShowPage(view.Page == "settings" ? "runs" : "settings"));
            if (client == null || !client.RunActive) menu.AddItem(new GUIContent(AutopilotView.RerunSetupLabel), false, () => view?.RerunSetup());
            else menu.AddDisabledItem(new GUIContent(AutopilotView.RerunSetupLabel));
            if (connected && !client.RunActive) menu.AddItem(new GUIContent("Reset memory…"), false, () => view?.AskResetMemory());
            else menu.AddDisabledItem(new GUIContent("Reset memory…"));
        }
    }
}
