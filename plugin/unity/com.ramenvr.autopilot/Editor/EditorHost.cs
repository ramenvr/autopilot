// The bridge in the editor: one client for the editor's life, in edit mode and play mode alike (play mode is the
// session: the brain's `session start` enters it, as Unreal's Play In Editor). Ticked by the editor's update while
// the game isn't playing (or is paused), by the player loop while it plays.
//
// Entering play mode reloads the scripts (unless the project turned that off): the connection closes with code 1012
// and the client connects again afterwards with hello {"resume": <session>}, so the brain keeps the same run; requests
// waiting on the change (session start) are answered on the new connection.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Autopilot.Playtest.Editor
{
    [InitializeOnLoad]
    public sealed class EditorHost : IPlaytestHost
    {
        const string StateKey = "AutopilotPlaytest.Client";
        const string PendingKey = "AutopilotPlaytest.PendingSessions";
        // Play mode started in a scene of the brain's (playModeStartScene): set back once play mode ends, so the
        // editor's own Play plays the open scene again. And the scene open in the editor while it plays
        const string StartSceneKey = "AutopilotPlaytest.StartSceneSet";
        const string EditSceneKey = "AutopilotPlaytest.EditScene";
        public const string HeadlessKey = "AutopilotPlaytest.Headless";
        // Play mode the agent service entered (session start, restart): kept across the script reload, let go when play
        // mode ends
        internal const string AgentPlayKey = "AutopilotPlaytest.AgentPlay";

        /// <summary>Whether play mode now is the agent service's (it entered it, and is still connected): its game must
        /// go on whatever happens in it (ErrorPause).</summary>
        public static bool AgentPlaying => EditorApplication.isPlayingOrWillChangePlaymode && SessionState.GetBool(AgentPlayKey, false)
                                           && Client != null && (Client.Connected || Client.WantConnected);

        public static readonly EditorHost Instance = new EditorHost();
        public static PlaytestClient Client { get; private set; }

        sealed class SessionWait
        {
            public long Id;
            public string Action;  // start | stop | fresh
            public int Stage;  // (start: -1 while the project index is written before play mode)
            public double Since;
            public string Map;  // (start: the scene asked for, to enter play mode with once the index is written;
                                // fresh: the scene to play again)
            public string Launch;  // (fresh: the session's launch scene: a restart without a map still means that one)
        }

        static readonly List<SessionWait> waits = new List<SessionWait>();
        static int playFrames;

        static EditorHost()
        {
            EditorApplication.update += Update;
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.playModeStateChanged += OnPlayMode;
            EditorApplication.quitting += () =>
            {
                Codex.Shutdown();
                Client?.Shutdown();
            };
            Bootstrap.InstallTick();
            AgentService.Ready += ServiceReady;
            EditorApplication.delayCall += Restore;
            Ops.EditorLevels = Levels;
            Ops.EditorLoading = () => PlayModeLoading;
        }

        // -- the client --------------------------------------------------------------------------------------

        public static PlaytestClient Ensure()
        {
            if (Client != null) return Client;
            Client = new PlaytestClient(Instance);
            Hands.EnsureDevices();
            ClientCreated?.Invoke(Client);
            return Client;
        }

        public static event Action<PlaytestClient> ClientCreated;

        /// <summary>Tests: the client the window uses (one with no socket), or the one before put back.</summary>
        internal static void TestClient(PlaytestClient client) => Client = client;

        public static void Connect()
        {
            if (Uninstall.Holds) return;  // (Autopilot is being removed from this computer, or was: Uninstall.cs)
            SessionState.EraseBool(DisconnectedKey);
            var s = PlaytestSettings.Get();
            // The agent service on this computer: the one running, else started now (AgentService.cs); the client
            // finds it in brain.json once it is up (ServiceReady connects again at once)
            if (AgentService.StartsWithConnect(s)) AgentService.Start();
            Ensure().Connect(s.Url, s.Token, s.UseWithAura);
        }

        static double nextServiceCheck;

        /// <summary>The agent service came up: a client waiting for it connects now rather than at its next retry.</summary>
        static void ServiceReady()
        {
            var client = Client;
            if (client == null || !client.WantConnected || client.Connected || client.Linked || Uninstall.Holds) return;
            var s = PlaytestSettings.Get();
            if (!AgentService.StartsWithConnect(s)) return;
            client.Connect(s.Url, s.Token, s.UseWithAura);
        }

        /// <summary>A client trying to connect with no agent service running (the editor that started it closed, and
        /// it ended with it): start another, as Connect would. Not after a problem (the window shows it; Try again).</summary>
        static void CheckService(PlaytestClient client)
        {
            var now = EditorApplication.timeSinceStartup;
            if (now < nextServiceCheck) return;
            nextServiceCheck = now + 3.0;
            if (!client.WantConnected || client.Connected || client.Linked || AgentService.Current != AgentService.Phase.Idle || Uninstall.Holds) return;
            if (!AgentService.StartsWithConnect(PlaytestSettings.Get())) return;
            AgentService.Start();
        }

        /// <summary>Disconnect, or Stop (the user's: it connects by itself again only in the next editor session,
        /// or with Reconnect).</summary>
        public static void Disconnect() => Disconnect(true);

        /// <summary>Disconnect; <paramref name="byHand"/> false: Autopilot's own (a sign-in that needs another agent
        /// service, Autopilot removed), which leaves connecting by itself as it was.</summary>
        public static void Disconnect(bool byHand)
        {
            if (byHand) SessionState.SetBool(DisconnectedKey, true);
            Client?.Disconnect();
            Hands.RemoveDevices();
            SessionState.EraseString(StateKey);
        }

        const string DisconnectedKey = "AutopilotPlaytest.Disconnected";
        static double nextAutoConnect;

        /// <summary>Whether the user disconnected in this editor session (the connect card's Disconnected state).</summary>
        public static bool DisconnectedByHand => SessionState.GetBool(DisconnectedKey, false);

        /// <summary>Whether the editor connects by itself now: always once the first-run setup is done, unless the user
        /// disconnected in this editor session, Autopilot is removed from this computer, or it is a batch-mode or
        /// command-line run (those connect as their command line says). A refused connection waits for Try again.</summary>
        public static bool ConnectsByItself(bool onboarded, bool disconnected, bool removed, bool batch, PlaytestClient client)
        {
            if (!onboarded || disconnected || removed || batch) return false;
            return client == null || (!client.WantConnected && !client.Connected && string.IsNullOrEmpty(client.Problem));
        }

        static void AutoConnect()
        {
            var now = EditorApplication.timeSinceStartup;
            if (now < nextAutoConnect) return;
            nextAutoConnect = now + 1.0;
            var batch = Application.isBatchMode || !string.IsNullOrEmpty(PlaytestClient.Arg("Playtest"));
            if (ConnectsByItself(Preferences.Current.Onboarded, DisconnectedByHand, Uninstall.Holds, batch, Client)) Connect();
        }

        static void Restore()
        {
            var saved = SessionState.GetString(StateKey, "");
            if (!string.IsNullOrEmpty(saved))
            {
                SessionState.EraseString(StateKey);
                Dictionary<string, object> state = null;
                try
                {
                    state = Json.ParseObject(saved);
                }
                catch
                {
                    // (unreadable: start afresh)
                }
                if (state != null && Json.Bool(state, "want", false))
                {
                    var client = Ensure();
                    client.CommandLineRun = Json.Obj(state, "run");
                    client.CommandLineReportDir = Json.Str(state, "report_dir");
                    client.ExitWhenDone = Json.Bool(state, "exit_when_done", false);
                    var pendingText = SessionState.GetString(PendingKey, "");
                    SessionState.EraseString(PendingKey);
                    if (!string.IsNullOrEmpty(pendingText))
                    {
                        foreach (var o in Json.Parse(pendingText) as List<object> ?? new List<object>())
                        {
                            if (!(o is Dictionary<string, object> w)) continue;
                            waits.Add(new SessionWait
                            {
                                Id = (long)Json.Num(w, "id", 0),
                                Action = Json.Str(w, "action"),
                                Stage = (int)Json.Num(w, "stage", 0),
                                Since = Json.Num(w, "since", EditorApplication.timeSinceStartup),
                                Map = Json.Str(w, "map"),
                                Launch = Json.Str(w, "launch"),
                            });
                        }
                    }
                    var session = Json.Str(state, "session");
                    // (a recording the brain asked for goes on over the resumed connection, once the game runs)
                    if (!string.IsNullOrEmpty(session)) Media.RestoreState(Json.Obj(state, "record"));
                    client.Resume(Json.Str(state, "url"), Json.Str(state, "token"), string.IsNullOrEmpty(session) ? null : session,
                        Json.Bool(state, "run_active", false), Json.Bool(state, "sent_run", false),
                        Json.Num(state, "connect_timeout", 0), Json.Num(state, "connect_since", 0), Json.Bool(state, "welcomed", false),
                        Json.Bool(state, "with_aura", false), Json.Str(state, "linked_by"));
                    // (a script reload while the project index was written before play mode: written again, then play)
                    foreach (var w in waits.Where(w => w.Action == "start" && w.Stage < 0).ToList())
                        EnterPlayMode(client, w);
                    return;
                }
            }
            if (Application.isBatchMode && !string.IsNullOrEmpty(PlaytestClient.Arg("Playtest")) && !SessionState.GetBool(HeadlessKey, false))
                Headless.Start();
            // (otherwise the editor connects by itself: AutoConnect, from its update, once the first-run setup is done)
        }

        static void BeforeReload()
        {
            if (Client == null) return;
            var state = Client.SaveState();
            if (Json.Bool(state, "want", false))
            {
                SessionState.SetString(StateKey, Json.Write(state));
                SessionState.SetString(PendingKey, Json.Write(waits.Select(w => (object)new Dictionary<string, object>
                {
                    ["id"] = w.Id, ["action"] = w.Action, ["stage"] = w.Stage, ["since"] = w.Since, ["map"] = w.Map ?? "",
                    ["launch"] = w.Launch ?? "",
                }).ToList()));
            }
            Client.CloseForReload();
        }

        static void OnPlayMode(PlayModeStateChange change)
        {
            if (change == PlayModeStateChange.EnteredPlayMode || change == PlayModeStateChange.ExitingEditMode) playFrames = 0;
            if (change == PlayModeStateChange.ExitingEditMode) SessionState.SetString(EditSceneKey, OpenScene());
            if (change == PlayModeStateChange.EnteredEditMode)
            {
                SessionState.EraseBool(AgentPlayKey);
                Ops.ResetSession();
                if (SessionState.GetBool(StartSceneKey, false))
                {
                    EditorSceneManager.playModeStartScene = null;
                    SessionState.EraseBool(StartSceneKey);
                }
            }
            // (a project that doesn't reload scripts for play mode: no reload restores the waiting connection)
            if (change == PlayModeStateChange.EnteredPlayMode && Client == null && !string.IsNullOrEmpty(SessionState.GetString(StateKey, "")))
                Restore();
        }

        static void Update()
        {
            if (EditorApplication.isPlaying) ++playFrames;
            AutoConnect();
            var client = Client;
            if (client == null) return;
            if (!EditorApplication.isPlaying || EditorApplication.isPaused)
            {
                try
                {
                    client.Tick();
                }
                catch (Exception e)
                {
                    Debug.LogWarning("[Autopilot] tick failed: " + e);
                }
            }
            TickWaits(client);
            CheckService(client);
        }

        static void TickWaits(PlaytestClient client)
        {
            if (waits.Count == 0) return;
            var now = EditorApplication.timeSinceStartup;
            foreach (var w in waits.ToList())
            {
                var done = false;
                var playing = EditorApplication.isPlaying && !EditorApplication.isCompiling;
                switch (w.Action)
                {
                    case "start":
                        if (w.Stage < 0)
                        {
                            w.Since = now;  // (the project index is being written: the time to enter play mode counts after it)
                            continue;
                        }
                        if (playing && playFrames >= 3 && SceneManager.GetActiveScene().isLoaded)
                        {
                            client.Respond(w.Id, new Dictionary<string, object> { ["running"] = true, ["scene"] = SceneManager.GetActiveScene().path });
                            done = true;
                        }
                        break;
                    case "stop":
                        if (!EditorApplication.isPlayingOrWillChangePlaymode)
                        {
                            client.Respond(w.Id, new Dictionary<string, object> { ["running"] = false });
                            done = true;
                        }
                        break;
                    case "fresh":
                        // (only once out of play mode for good: entering edit mode clears the start scene, so Play
                        // has to come after it, and isPlayingOrWillChangePlaymode turns false before that)
                        if (w.Stage == 0 && !EditorApplication.isPlaying && !EditorApplication.isPlayingOrWillChangePlaymode)
                        {
                            w.Stage = 1;
                            Play(client, w.Map);
                        }
                        else if (w.Stage == 1 && playing && playFrames >= 3 && SceneManager.GetActiveScene().isLoaded)
                        {
                            if (!string.IsNullOrEmpty(w.Launch)) Ops.LaunchScene = w.Launch;
                            client.Respond(w.Id, new Dictionary<string, object> { ["running"] = true, ["scene"] = SceneManager.GetActiveScene().path });
                            done = true;
                        }
                        break;
                }
                if (!done && now - w.Since > 90)
                {
                    client.RespondError(w.Id, $"Timed out: session {w.Action}", 500);
                    done = true;
                }
                if (done) waits.Remove(w);
            }
        }

        // -- IPlaytestHost -------------------------------------------------------------------------------------

        public string HostName => "editor";

        public string ProjectDir => Directory.GetParent(Application.dataPath).FullName;

        public string ProjectId => PlayerSettings.productGUID.ToString();

        public bool SessionRunning => EditorApplication.isPlaying;

        /// <summary>The hello asks for every policy step (`inspect`) for the Policy log tab: not in batch mode, and only
        /// while Show Policy Log is on (PlaytestSettings; from the next connection).</summary>
        public bool WantsInspection => !Application.isBatchMode && PlaytestSettings.Get().ShowPolicyLog;

        public bool Session(PlaytestClient client, long id, string action, Dictionary<string, object> args)
        {
            var map = Json.Str(args, "map");
            if (!string.IsNullOrEmpty(map) && action != "stop" && ScenePath(map) == null)
            {
                client.RespondError(id, $"No scene {map} in the project", 404);  // (before anything changes)
                return true;
            }
            switch (action)
            {
                case "start":
                    if (EditorApplication.isPlaying)
                    {
                        // (a scene asked for while another plays: switched to, as Godot's game does)
                        if (!string.IsNullOrEmpty(map) && !Ops.IsActiveScene(map) && !Ops.IsActiveScene(ScenePath(map)))
                        {
                            StopAndPlay(id, map);
                            return true;
                        }
                        client.Respond(id, new Dictionary<string, object> { ["running"] = true, ["scene"] = SceneManager.GetActiveScene().path });
                        return true;
                    }
                    EnterPlayMode(client, Wait(id, map));
                    return true;
                case "stop":
                    if (!EditorApplication.isPlaying)
                    {
                        client.Respond(id, new Dictionary<string, object> { ["running"] = false });
                        return true;
                    }
                    Hands.ReleaseAll();
                    EditorApplication.ExitPlaymode();
                    waits.Add(new SessionWait { Id = id, Action = "stop", Since = EditorApplication.timeSinceStartup });
                    return true;
                case "restart":
                    if (!EditorApplication.isPlaying)
                    {
                        EnterPlayMode(client, Wait(id, map));
                        return true;
                    }
                    StopAndPlay(id, map);
                    return true;
            }
            return false;
        }

        /// <summary>Start over as the person at the editor would, with Stop and then Play. Only that starts a game
        /// over: a scene loaded inside play mode keeps what the game carries between scenes (DontDestroyOnLoad objects
        /// and what they told static fields), and a game that builds on those may not start a second time. Statics stay
        /// when the project doesn't reload scripts for play mode, as they do for its own Play. Answered once it plays
        /// (TickWaits).</summary>
        static void StopAndPlay(long id, string map)
        {
            Hands.ReleaseAll();
            var launch = Ops.LaunchScene;  // (entering play mode forgets it)
            waits.Add(new SessionWait
            {
                Id = id, Action = "fresh", Since = EditorApplication.timeSinceStartup,
                Map = string.IsNullOrEmpty(map) ? launch : map, Launch = launch,
            });
            SessionState.SetBool(AgentPlayKey, true);
            EditorApplication.ExitPlaymode();
        }

        /// <summary>A session start's wait: answered once play mode plays (TickWaits).</summary>
        static SessionWait Wait(long id, string map)
        {
            var w = new SessionWait { Id = id, Action = "start", Since = EditorApplication.timeSinceStartup, Map = map ?? "" };
            waits.Add(w);
            return w;
        }

        /// <summary>Whether the session is still starting: the project index written before play mode, or play mode
        /// asked for and not playing yet (the Live page's "Waiting for play mode", `session` status's `loading`).</summary>
        public static bool PlayModeLoading
        {
            get
            {
                if (CodexExport.Running != null) return true;
                if (waits.Any(w => w.Action == "start" || w.Action == "fresh")) return true;
                return EditorApplication.isPlayingOrWillChangePlaymode && (!EditorApplication.isPlaying || playFrames < 3);
            }
        }

        /// <summary>Whether play mode is being entered (asked for, its scripts reloading, its scene loading), not the
        /// project index before it: the Live page's "Waiting for play mode".</summary>
        public static bool WaitingForPlayMode => CodexExport.Running == null && PlayModeLoading;

        static void EnterPlayMode(PlaytestClient client, SessionWait wait)
        {
            // The project's export for the codex first, while scenes can still be opened (edit mode): in steps across the
            // editor's updates, the Live page counting its files, Stop ending it (CodexExport.Start); then play mode
            try
            {
                var dir = Path.Combine(Instance.ProjectDir, "Library", "AutopilotCodex");
                if (CodexExport.Needed(Instance.ProjectDir) != null)
                {
                    client.Status("Indexing the project (scenes, prefabs, input)");
                    wait.Stage = -1;
                    CodexExport.Start(dir, job =>
                    {
                        wait.Stage = 0;
                        wait.Since = EditorApplication.timeSinceStartup;
                        if (!waits.Contains(wait)) return;  // (answered meanwhile)
                        // (stopped: the run is ending; play mode is entered all the same, so the session start is
                        // answered as the agent service waits for it, and the stop goes on from there)
                        if (EditorApplication.isPlayingOrWillChangePlaymode) return;
                        Play(client, wait.Map);
                    });
                    return;
                }
            }
            catch (Exception e)
            {
                wait.Stage = 0;
                Debug.LogWarning("[Autopilot] The project index export failed: " + e.Message);
            }
            Play(client, wait.Map);
        }

        static void Play(PlaytestClient client, string map)
        {
            if (!string.IsNullOrEmpty(map)) SetStartScene(map);
            else
            {
                EditorSceneManager.playModeStartScene = null;
                var active = SceneManager.GetActiveScene();
                if (string.IsNullOrEmpty(active.path))
                {
                    // An untitled scene (the editor in batch mode): the build's first scene
                    var first = EditorBuildSettings.scenes.FirstOrDefault(s => s.enabled);
                    if (first != null) EditorSceneManager.OpenScene(first.path, OpenSceneMode.Single);
                }
            }
            client.Status("Entering play mode");
            SessionState.SetBool(AgentPlayKey, true);
            EditorApplication.EnterPlaymode();
        }

        /// <summary>Play mode starts in this scene (a path, or a scene's name in the build list or the project); the
        /// scenes open in the editor stay as they are, and the editor's own Play plays them again once play mode ends.</summary>
        public static bool SetStartScene(string map)
        {
            var path = ScenePath(map);
            var asset = string.IsNullOrEmpty(path) ? null : AssetDatabase.LoadAssetAtPath<SceneAsset>(path);
            EditorSceneManager.playModeStartScene = asset;
            if (asset == null) Debug.LogWarning($"[Autopilot] No scene {map}: play mode starts with the open scene");
            else SessionState.SetBool(StartSceneKey, true);
            return asset != null;
        }

        /// <summary>What a session start without a map plays: the active scene, or the build's first when it is untitled.</summary>
        static string OpenScene()
        {
            var active = SceneManager.GetActiveScene();
            if (!string.IsNullOrEmpty(active.path)) return active.path;
            var first = EditorBuildSettings.scenes.FirstOrDefault(s => s.enabled);
            return first != null ? first.path : "";
        }

        /// <summary>`levels` (docs/protocol.md): the project's scenes (under Assets), the one open in the editor and the
        /// one playing.</summary>
        static Dictionary<string, object> Levels()
        {
            var levels = AssetDatabase.FindAssets("t:Scene", new[] { "Assets" }).Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => p.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)).Distinct().OrderBy(p => p, StringComparer.Ordinal)
                .Cast<object>().ToList();
            var playing = EditorApplication.isPlaying ? SceneManager.GetActiveScene().path : "";
            var open = EditorApplication.isPlaying ? SessionState.GetString(EditSceneKey, "") : OpenScene();
            return new Dictionary<string, object> { ["levels"] = levels, ["open"] = open, ["playing"] = playing ?? "" };
        }

        public static string ScenePath(string map)
        {
            if (string.IsNullOrEmpty(map)) return null;
            if (map.EndsWith(".unity", StringComparison.OrdinalIgnoreCase) && File.Exists(map)) return map.Replace('\\', '/');
            var name = Path.GetFileNameWithoutExtension(map);
            var inBuild = EditorBuildSettings.scenes.FirstOrDefault(s => Path.GetFileNameWithoutExtension(s.path) == name);
            if (inBuild != null) return inBuild.path;
            var guid = AssetDatabase.FindAssets("t:Scene " + name).FirstOrDefault(g => Path.GetFileNameWithoutExtension(AssetDatabase.GUIDToAssetPath(g)) == name);
            return guid != null ? AssetDatabase.GUIDToAssetPath(guid) : null;
        }

        public bool Export(string dir, Action<int?, string> done)
        {
            // (in steps across the editor's updates, as before play mode: counted on the Live page, ended by Stop)
            CodexExport.Start(dir, job =>
            {
                if (job.Error != null) done(1, "the export failed: " + job.Error.Message);
                else if (job.Stopped) done(1, "the export was stopped");
                else done(0, File.Exists(Path.Combine(dir, "summary.txt")) ? null : "the export wrote no summary.txt");
            });
            return true;
        }

        public void Exit(int code)
        {
            EditorApplication.delayCall += () => EditorApplication.Exit(code);
        }

        public string ReportDir()
        {
            return Path.Combine(ProjectDir, "Logs", "Playtest", DateTime.Now.ToString("yyyyMMdd-HHmmss"));
        }
    }
}
