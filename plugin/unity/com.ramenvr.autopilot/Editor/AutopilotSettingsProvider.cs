// Autopilot's settings in Unity's own Preferences window (Edit > Preferences > Autopilot; Unity > Settings on macOS), as
// Unreal's Editor Preferences > Plugins > Autopilot: where the agent service is, how runs go, and whether the window
// shows the policy log. The same settings as the window's (PlaytestSettings: UserSettings/AutopilotPlaytest.json, per
// user and per project), read and saved through it, so a change in either shows in the other.

using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace Autopilot.Playtest.Editor
{
    static class AutopilotSettingsProvider
    {
        public const string Path = "Preferences/Autopilot";

        // The words: Unreal's settings' names and tooltips (PlaytestSettings.h), in Unity's
        internal static readonly GUIContent UrlLabel = new GUIContent("Agent Service URL",
            "The agent service to connect to. Left at the default (ws://127.0.0.1:8765/v1/bridge), the one running on this computer, on whatever port it says it uses; or one you host (wss://<host>/v1/bridge).");
        internal static readonly GUIContent TokenLabel = new GUIContent("Access Token",
            "Optional. Sent as \"Authorization: Bearer <token>\" when connecting, for an agent service that asks for one. Leave empty for one on this computer. The AUTOPILOT_PLAYTEST_TOKEN environment variable is used when this is empty.");
        internal static readonly GUIContent AuraLabel = new GUIContent("Use with Aura",
            "Connect to the agent service the Aura app runs on this computer instead of the one above. Runs then go through Aura, on your Aura plan, and what the agent learns about each game is kept with Aura across your team's computers. Needs Aura installed and running.");
        internal static readonly GUIContent CommandLabel = new GUIContent("Agent service command",
            "The command that runs the agent service, without \"serve\" (e.g. C:\\autopilot\\.venv\\Scripts\\python.exe -m autopilot). Empty: the autopilot command is found by itself. Autopilot connects by itself, and starts the agent service on this computer when none is running (Agent Service URL left at the default, not with Aura).");
        internal static readonly GUIContent ExploreLabel = new GUIContent("Default Explore Minutes",
            "Minutes of play of the first-run setup's Explore run (1 to 600).");
        internal static readonly GUIContent BriefLabel = new GUIContent("Design Brief",
            "How the game is meant to work: the design, the rules players are told (e.g. \"the player has three lives; each coin is worth 10 points\"). Exploration reports behavior that differs from it as a bug, even where the game's own values agree with each other. Optional; sent with every run.");
        internal static readonly GUIContent PolicyLabel = new GUIContent("Show Policy Log",
            "Show the policy log in the Autopilot window: every policy step (the observation, the action probabilities, the action taken and how long it took). Autopilot asks the agent service for them when it connects; an agent service on this computer sends them. Takes effect at the next connect.");

        [SettingsProvider]
        static SettingsProvider Create()
        {
            return new SettingsProvider(Path, SettingsScope.User)
            {
                label = "Autopilot",
                guiHandler = _ => Draw(),
                keywords = new HashSet<string>(new[]
                {
                    "Autopilot", "Agent Service", "URL", "Access Token", "Aura", "Explore", "Design Brief", "Policy Log",
                }),
            };
        }

        static Vector2 briefScroll;

        static void Draw()
        {
            var s = PlaytestSettings.Get();
            var was = EditorGUIUtility.labelWidth;
            EditorGUIUtility.labelWidth = 200;
            EditorGUILayout.Space(4);
            EditorGUI.BeginChangeCheck();

            EditorGUILayout.LabelField("Connection", EditorStyles.boldLabel);
            var url = EditorGUILayout.DelayedTextField(UrlLabel, s.Url);
            var token = EditorGUILayout.PasswordField(TokenLabel, s.Token);
            var aura = EditorGUILayout.Toggle(AuraLabel, s.UseWithAura);
            EditorGUILayout.Space(8);

            EditorGUILayout.LabelField("Agent Service", EditorStyles.boldLabel);
            var command = EditorGUILayout.DelayedTextField(CommandLabel, s.ServiceCommand);
            EditorGUILayout.Space(8);

            EditorGUILayout.LabelField("Runs", EditorStyles.boldLabel);
            var minutes = EditorGUILayout.IntSlider(ExploreLabel, s.ExploreMinutes, PlaytestSettings.MinTimeLimitMinutes, PlaytestSettings.MaxTimeLimitMinutes);
            EditorGUILayout.LabelField(BriefLabel);
            briefScroll = EditorGUILayout.BeginScrollView(briefScroll, GUILayout.MinHeight(64), GUILayout.MaxHeight(160));
            var brief = EditorGUILayout.TextArea(s.DesignBrief, EditorStyles.textArea, GUILayout.ExpandHeight(true));
            EditorGUILayout.EndScrollView();
            EditorGUILayout.Space(8);

            EditorGUILayout.LabelField("Policy Log", EditorStyles.boldLabel);
            var policy = EditorGUILayout.Toggle(PolicyLabel, s.ShowPolicyLog);

            if (EditorGUI.EndChangeCheck())
                Apply(s, url, token, aura, command, minutes, brief, policy);
            EditorGUIUtility.labelWidth = was;
        }

        /// <summary>The values shown saved into the window's settings (the URL and the command trimmed, as the window does);
        /// true when any changed. Show Policy Log reconnects when no run is going, as the window's switch does.</summary>
        internal static bool Apply(PlaytestSettings s, string url, string token, bool aura, string command, int minutes, string brief, bool policy)
        {
            url = (url ?? "").Trim();
            token = (token ?? "").Trim();
            command = (command ?? "").Trim();
            minutes = PlaytestSettings.ClampTimeLimit(minutes);
            brief = brief ?? "";
            var policyChanged = s.ShowPolicyLog != policy;
            var changed = s.Url != url || s.Token != token || s.UseWithAura != aura || s.ServiceCommand != command
                          || s.ExploreMinutes != minutes || s.DesignBrief != brief || policyChanged;
            if (!changed) return false;
            s.Url = url;
            s.Token = token;
            s.UseWithAura = aura;
            s.ServiceCommand = command;
            s.ExploreMinutes = minutes;
            s.DesignBrief = brief;
            s.ShowPolicyLog = policy;
            s.Save();
            if (policyChanged)
            {
                try
                {
                    AutopilotView.Reconnect();
                }
                catch (Exception e)
                {
                    Debug.LogWarning("[Autopilot] Couldn't connect again for the policy log: " + e.Message);
                }
            }
            return true;
        }
    }
}
