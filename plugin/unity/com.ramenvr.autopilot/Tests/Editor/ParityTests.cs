// What Unity's plugin does as Unreal's (and Godot's) do: OnGUI's controls read and clicked (ImguiTests: drawn in an
// IMGUI pass of the test's own, as a batch-mode player calls no OnGUI; the rewriter's routes matching the shims'
// overloads; the test game's MenuImgui routed when the project has it), the Console's Error Pause held off while a run
// plays and put back (also after an editor that closed mid-run), the Design Brief sent with runs, Show Policy Log, the
// Preferences page's settings saved where the window keeps them, the project index written in steps with its count
// and stopped by Stop, the Live page's "Waiting for play mode" and "Indexing files (n/total)", and the bundled fonts'
// notice, the recording's frames played as a flipbook (a project without the Video module), and the toolbar button
// put into an older Unity's toolbar.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Autopilot.Playtest.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace Autopilot.Playtest.Tests
{
    public class ImguiTests
    {
        static readonly GUILayoutOption[] None = new GUILayoutOption[0];

        [SetUp]
        public void SetUp() => Imgui.Clear();

        [TearDown]
        public void TearDown() => Imgui.Clear();

        static Type Find(string name)
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                var t = asm.GetType(name);
                if (t != null) return t;
            }
            return null;
        }

        [Test]
        public void EveryRouteHasItsShim()
        {
            var rewriter = Type.GetType("Autopilot.Playtest.CodeGen.LegacyInputRewriter, Unity.Autopilot.Playtest.CodeGen");
            Assert.IsNotNull(rewriter, "the IL post-processor");
            var routes = (HashSet<string>)rewriter.GetField("GuiRouted", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.AreEqual(76, routes.Count, "Label, Box, Button, RepeatButton (6 each), Toggle (6), TextField, TextArea (4 each), for GUI and GUILayout");
            foreach (var route in routes)
            {
                var declaring = route.Substring(0, route.IndexOf("::", StringComparison.Ordinal));
                var name = route.Substring(declaring.Length + 2, route.IndexOf('(') - declaring.Length - 2);
                var types = route.Substring(route.IndexOf('(') + 1).TrimEnd(')').Split(',').Select(Find).ToArray();
                Assert.IsTrue(types.All(t => t != null), route);
                var real = (declaring == "UnityEngine.GUI" ? typeof(GUI) : typeof(GUILayout)).GetMethod(name, BindingFlags.Static | BindingFlags.Public, null, types, null);
                Assert.IsNotNull(real, "Unity has " + route);
                var shim = (declaring == "UnityEngine.GUI" ? typeof(ImguiGUI) : typeof(ImguiGUILayout)).GetMethod(name, BindingFlags.Static | BindingFlags.Public, null,
                    types.Concat(new[] { typeof(string) }).ToArray(), null);
                Assert.IsNotNull(shim, "the shim has " + route + " with the class's name last");
                Assert.AreEqual(real.ReturnType, shim.ReturnType, route);
            }
        }

        [Test]
        public void TheGamesOnGuiIsRouted()
        {
            // (the test game's MenuImgui, when this project has it: its OnGUI calls the shims, the class's name pushed)
            var menu = Type.GetType("MenuImgui, Assembly-CSharp");
            if (menu == null) Assert.Inconclusive("This project has no MenuImgui (Tests~/TestCourse/Assets/Scripts/MenuImgui.cs)");
            var onGui = menu.GetMethod("OnGUI", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
            var il = onGui.GetMethodBody().GetILAsByteArray();
            var called = new List<MethodBase>();
            for (var i = 0; i + 4 < il.Length; ++i)
            {
                if (il[i] != 0x28) continue;  // (call)
                try
                {
                    called.Add(menu.Module.ResolveMethod(BitConverter.ToInt32(il, i + 1)));
                }
                catch (Exception)
                {
                    // (not a call's token)
                }
            }
            var shims = called.Where(m => m.DeclaringType == typeof(ImguiGUI) || m.DeclaringType == typeof(ImguiGUILayout)).ToList();
            Assert.AreEqual(6, shims.Count, string.Join(", ", called.Select(m => m.DeclaringType?.Name + "." + m.Name)));
            Assert.IsFalse(called.Any(m => (m.DeclaringType == typeof(GUI) || m.DeclaringType == typeof(GUILayout)) && new[] { "Button", "Label", "Toggle", "TextField", "Box" }.Contains(m.Name)),
                "no control is drawn past the bridge");
            Assert.GreaterOrEqual(LegacyInput.ImguiCalls, 6, "context's imgui calls");
        }

        int plays, quits;
        bool music = true;
        string playerName = "";

        void Menu()
        {
            ImguiGUI.Box(new Rect(10, 10, 220, 180), "Options", "MenuImgui");
            GUILayout.BeginArea(new Rect(20, 40, 200, 120));
            ImguiGUILayout.Label("Plays: " + plays, None, "MenuImgui");
            if (ImguiGUILayout.Button("Play", None, "MenuImgui")) plays++;
            music = ImguiGUILayout.Toggle(music, "Music", None, "MenuImgui");
            playerName = ImguiGUILayout.TextField(playerName, None, "MenuImgui");
            GUILayout.EndArea();
            if (ImguiGUI.Button(new Rect(20, 160, 80, 24), "Quit", "MenuImgui")) quits++;
            ImguiGUI.Button(new Rect(110, 160, 80, 24), "Quit", "MenuImgui");  // (a second of the same words)
        }

        /// <summary>One OnGUI frame, as Unity draws one: a layout pass, then a repaint, through an IMGUIContainer.</summary>
        void Frame(IMGUIContainer container, MethodInfo handle)
        {
            foreach (var type in new[] { EventType.Layout, EventType.Repaint })
                handle.Invoke(container, new object[] { new Event { type = type }, (Action)Menu, false });
        }

        [Test]
        public void OnGuiControlsAreReadAndClicked()
        {
            // (the container in an editor panel of its own, as LayoutTests lay pages out)
            var owner = ScriptableObject.CreateInstance<StyleSheet>();
            owner.hideFlags = HideFlags.HideAndDontSave;
            var create = typeof(VisualElement).Assembly.GetType("UnityEngine.UIElements.Panel")?.GetMethod("CreateEditorPanel", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            if (create == null) Assert.Inconclusive("This Unity has no Panel.CreateEditorPanel");
            var panel = (IPanel)create.Invoke(null, new object[] { owner });
            var container = new IMGUIContainer();
            container.style.width = 400;
            container.style.height = 300;
            panel.visualTree.Add(container);
            try
            {
                Draw(container);
            }
            finally
            {
                (panel as IDisposable)?.Dispose();
                UnityEngine.Object.DestroyImmediate(owner);
            }
        }

        void Draw(IMGUIContainer container)
        {
            var handle = typeof(IMGUIContainer).GetMethod("HandleIMGUIEvent", BindingFlags.Instance | BindingFlags.NonPublic, null,
                new[] { typeof(Event), typeof(Action), typeof(bool) }, null);
            if (handle == null) Assert.Inconclusive("This Unity's IMGUIContainer has no HandleIMGUIEvent(Event, Action, bool)");
            Frame(container, handle);

            var texts = ScreenText.Texts();
            Assert.AreEqual("Options", texts["MenuImgui.Box"]);
            Assert.AreEqual("Plays: 0", texts["MenuImgui.Label"]);
            Assert.AreEqual("Play", texts["MenuImgui.Play"]);
            Assert.AreEqual("Quit", texts["MenuImgui.Quit#2"], "a second of the same id is #2");
            var items = Imgui.Items().ToDictionary(i => (string)i["id"]);
            CollectionAssert.IsSupersetOf(items.Keys, new[] { "MenuImgui.Play", "MenuImgui.Music", "MenuImgui.TextField", "MenuImgui.Quit", "MenuImgui.Quit#2" });
            Assert.AreEqual("button", items["MenuImgui.Play"]["kind"]);
            Assert.AreEqual("checkbox", items["MenuImgui.Music"]["kind"]);
            Assert.AreEqual(true, items["MenuImgui.Music"]["checked"]);
            Assert.AreEqual("text_input", items["MenuImgui.TextField"]["kind"]);
            Assert.AreEqual(true, items["MenuImgui.Play"]["imgui"]);
            Assert.IsFalse(items.ContainsKey("MenuImgui.Label"), "a label isn't a control");

            Assert.AreEqual("", Imgui.Act("MenuImgui.Play", "click", new Dictionary<string, object>()));
            Frame(container, handle);
            Assert.AreEqual(1, plays, "the button returned true at its next drawing");
            Frame(container, handle);
            Assert.AreEqual(1, plays, "once");
            Assert.AreEqual("Plays: 1", ScreenText.Texts()["MenuImgui.Label"]);

            Imgui.Act("MenuImgui.Music", "click", new Dictionary<string, object>());
            Frame(container, handle);
            Assert.IsFalse(music, "the toggle flipped");
            Imgui.Act("MenuImgui.Music", "set", new Dictionary<string, object> { ["value"] = true });
            Frame(container, handle);
            Assert.IsTrue(music, "the toggle set");
            Assert.AreEqual("", Imgui.Act("MenuImgui.TextField", "set", new Dictionary<string, object> { ["text"] = "Ada" }));
            Frame(container, handle);
            Assert.AreEqual("Ada", playerName);
            Assert.AreEqual("Ada", Imgui.Items().First(i => (string)i["id"] == "MenuImgui.TextField")["text"]);
            Imgui.Act("MenuImgui.Quit", "click", new Dictionary<string, object>());
            Frame(container, handle);
            Assert.AreEqual(1, quits, "GUI.Button at a rect");
            Assert.IsNull(Imgui.Act("MenuImgui.Nope", "click", new Dictionary<string, object>()), "not an OnGUI control: ui_act's own error");
            Assert.AreEqual("MenuImgui.Play takes no value", Imgui.Act("MenuImgui.Play", "set", new Dictionary<string, object>()));
        }
    }

    public class ErrorPauseTests
    {
        bool? was;

        [SetUp]
        public void SetUp()
        {
            was = ErrorPause.Get();
            if (was == null) Assert.Inconclusive("This Unity's Console flags can't be read");
            EditorPrefs.DeleteKey(ErrorPause.HeldKey);
        }

        [TearDown]
        public void TearDown()
        {
            ErrorPause.RunningOverride = null;
            EditorPrefs.DeleteKey(ErrorPause.HeldKey);
            if (was != null) ErrorPause.Set(was.Value);
        }

        [Test]
        public void ARunTurnsErrorPauseOffAndPutsItBack()
        {
            ErrorPause.Set(true);
            ErrorPause.RunningOverride = () => true;
            ErrorPause.Check(0);
            Assert.AreEqual(false, ErrorPause.Get(), "off while a run plays");
            Assert.AreEqual(1, EditorPrefs.GetInt(ErrorPause.HeldKey, -1), "what it was, kept past a crash");
            ErrorPause.Set(true);  // (turned on again mid-run: off again)
            ErrorPause.Check(1);
            Assert.AreEqual(false, ErrorPause.Get());
            ErrorPause.RunningOverride = () => false;
            ErrorPause.Check(2);
            Assert.AreEqual(false, ErrorPause.Get(), "a moment without a run (a reconnect) isn't the end");
            ErrorPause.Check(13);
            Assert.AreEqual(true, ErrorPause.Get(), "put back");
            Assert.IsFalse(EditorPrefs.HasKey(ErrorPause.HeldKey));
        }

        [Test]
        public void OffStaysOff()
        {
            ErrorPause.Set(false);
            ErrorPause.RunningOverride = () => true;
            ErrorPause.Check(0);
            ErrorPause.RunningOverride = () => false;
            ErrorPause.Check(1);
            ErrorPause.Check(20);
            Assert.AreEqual(false, ErrorPause.Get());
        }

        [Test]
        public void AnEditorThatClosedMidRunPutsItBack()
        {
            // (the editor crashed with it held off: the next one, with no run, puts it back)
            ErrorPause.Set(false);
            EditorPrefs.SetInt(ErrorPause.HeldKey, 1);
            ErrorPause.RunningOverride = () => false;
            ErrorPause.Check(100);
            ErrorPause.Check(111);
            Assert.AreEqual(true, ErrorPause.Get());
        }
    }

    public class SettingsParityTests
    {
        string path;
        PlaytestSettings saved;

        [SetUp]
        public void SetUp()
        {
            var s = PlaytestSettings.Get();
            saved = new PlaytestSettings
            {
                Url = s.Url, Token = s.Token, UseWithAura = s.UseWithAura, ServiceCommand = s.ServiceCommand, ExploreMinutes = s.ExploreMinutes,
                DesignBrief = s.DesignBrief, ShowPolicyLog = s.ShowPolicyLog,
            };
            path = PlaytestSettings.FilePath;
        }

        [TearDown]
        public void TearDown()
        {
            var s = PlaytestSettings.Get();
            s.Url = saved.Url;
            s.Token = saved.Token;
            s.UseWithAura = saved.UseWithAura;
            s.ServiceCommand = saved.ServiceCommand;
            s.ExploreMinutes = saved.ExploreMinutes;
            s.DesignBrief = saved.DesignBrief;
            s.ShowPolicyLog = saved.ShowPolicyLog;
            s.Save();
        }

        [Test]
        public void TheDesignBriefGoesWithRuns()
        {
            var frame = AutopilotView.ComposerFrame("Reach the gate", 20, "  The player has three lives. ", out _);
            Assert.AreEqual("The player has three lives.", frame["design_brief"]);
            Assert.IsFalse(AutopilotView.ComposerFrame("Reach the gate", 20, "  ", out _).ContainsKey("design_brief"), "none when empty");
            var explore = AutopilotView.ExploreFrame(10, "Coins are worth 10 points");
            CollectionAssert.AreEquivalent(new Dictionary<string, object> { ["kind"] = "explore", ["minutes"] = 10, ["brief"] = "Coins are worth 10 points" }, explore);
            Assert.IsFalse(AutopilotView.ExploreFrame(10, "").ContainsKey("brief"));
            var s = PlaytestSettings.Get();
            s.Prompt = "Reach the gate";
            s.DesignBrief = "Doors open with E";
            Assert.AreEqual("Doors open with E", AutopilotView.ComposerFrame(s, out _)["design_brief"]);
        }

        [Test]
        public void ThePreferencesPageSavesWhereTheWindowReads()
        {
            var s = PlaytestSettings.Get();
            Assert.IsTrue(AutopilotSettingsProvider.Apply(s, " ws://127.0.0.1:9999/v1/bridge ", "tok", false, @" C:\a\python.exe -m autopilot ", 900,
                "Three lives", s.ShowPolicyLog));
            Assert.IsFalse(AutopilotSettingsProvider.Apply(s, "ws://127.0.0.1:9999/v1/bridge", "tok", false, @"C:\a\python.exe -m autopilot", 600, "Three lives", s.ShowPolicyLog),
                "nothing changed");
            var read = PlaytestSettings.Load(path);
            Assert.AreEqual("ws://127.0.0.1:9999/v1/bridge", read.Url);
            Assert.AreEqual("tok", read.Token);
            Assert.AreEqual(@"C:\a\python.exe -m autopilot", read.ServiceCommand);
            Assert.AreEqual(600, read.ExploreMinutes, "Default Explore Minutes kept to 1-600");
            Assert.AreEqual("Three lives", read.DesignBrief);
            var provider = typeof(AutopilotSettingsProvider).GetMethod("Create", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null) as SettingsProvider;
            Assert.AreEqual("Preferences/Autopilot", provider.settingsPath);
            Assert.AreEqual(SettingsScope.User, provider.scope);
        }

        [Test]
        public void ShowPolicyLogDecidesTheHellosInspect()
        {
            var s = PlaytestSettings.Get();
            s.ShowPolicyLog = false;
            Assert.IsFalse(EditorHost.Instance.WantsInspection);
            s.ShowPolicyLog = true;
            Assert.AreEqual(!Application.isBatchMode, EditorHost.Instance.WantsInspection, "on, and never in batch mode");
            s.Save();
            Assert.IsTrue(PlaytestSettings.Load(path).ShowPolicyLog);
            s.ShowPolicyLog = false;
            s.Save();
            Assert.IsFalse(PlaytestSettings.Load(path).ShowPolicyLog, "kept as show_policy_log");
        }
    }

    public class IndexingTests
    {
        string dir;

        [SetUp]
        public void SetUp() => dir = Path.Combine(Path.GetTempPath(), "autopilot-index-" + Guid.NewGuid().ToString("N"));

        [TearDown]
        public void TearDown()
        {
            CodexExport.Stop();
            try
            {
                Directory.Delete(dir, true);
            }
            catch (Exception)
            {
                // (left in the temp folder)
            }
        }

        [UnityTest]
        public IEnumerator TheIndexIsWrittenInStepsWithItsCount()
        {
            CodexExport.Job ended = null;
            var counts = new List<(int, int)>();
            var job = CodexExport.Start(dir, j => ended = j);
            Assert.AreSame(job, CodexExport.Running);
            for (var i = 0; i < 2000 && ended == null; ++i)
            {
                if (CodexExport.Progress(out var done, out var total)) counts.Add((done, total));
                yield return null;
            }
            Assert.IsNotNull(ended, "it ended");
            Assert.IsFalse(ended.Stopped);
            Assert.IsNull(ended.Error);
            Assert.IsTrue(File.Exists(Path.Combine(dir, "summary.txt")));
            Assert.AreEqual(ended.Total, ended.Done, "every file counted");
            Assert.Greater(ended.Total, 2);
            Assert.IsTrue(counts.Count > 0 && counts.All(c => c.Item1 <= c.Item2 || c.Item2 == 0), string.Join(" ", counts));
            Assert.IsNull(CodexExport.Running);
            Assert.IsFalse(CodexExport.Progress(out _, out _));
        }

        [UnityTest]
        public IEnumerator StopEndsIt()
        {
            CodexExport.Job ended = null;
            CodexExport.Start(dir, j => ended = j);
            CodexExport.Stop();
            for (var i = 0; i < 100 && ended == null; ++i) yield return null;
            Assert.IsNotNull(ended);
            Assert.IsTrue(ended.Stopped);
            Assert.IsFalse(File.Exists(Path.Combine(dir, "summary.txt")), "no summary: written again next time");
        }

        [Test]
        public void TheLivePagesNowLine()
        {
            var run = new AutopilotRun { Id = "now", Stage = 1, Started = 1 };
            Assert.AreEqual("Waiting for play mode", AutopilotView.NowWords(run, true, false, 0, 0));
            Assert.AreEqual("Indexing files (12/40)", AutopilotView.NowWords(run, false, true, 12, 40));
            Assert.AreEqual("Indexing files", AutopilotView.NowWords(run, false, true, 0, 0));
            run.Stage = 0;
            Assert.AreEqual("Indexing files (3/9)", AutopilotView.NowWords(run, false, true, 3, 9), "before play mode, from the launch on");
            run.Stage = 2;
            Assert.AreNotEqual("Waiting for play mode", AutopilotView.NowWords(run, true, false, 0, 0), "past the analysis, the stage's words");
            run.Stage = 1;
            run.Now = "Walking to the gate";
            Assert.AreEqual("Walking to the gate", AutopilotView.NowWords(run, true, true, 1, 2), "what the agent says it does comes first");
            run.Now = "";
            run.Ended = 5;
            Assert.AreNotEqual("Waiting for play mode", AutopilotView.NowWords(run, true, false, 0, 0), "a run that ended");
        }

        [Test]
        public void TheBundledFontsAreFound()
        {
            Assert.IsTrue(AutopilotUI.FontsFound(), "the package's fonts");
            Assert.IsFalse(AutopilotUI.FontsFound("Packages/com.ramenvr.autopilot/Editor/NoSuchFolder/"), "a font that can't be read");
            Assert.AreEqual("A bundled font couldn’t be read: using the editor’s font", AutopilotStatus.FontMissing);
        }
    }

    public class FlipbookTests
    {
        string dir;

        [SetUp]
        public void SetUp()
        {
            dir = Path.Combine(Path.GetTempPath(), "autopilot-frames-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                Directory.Delete(dir, true);
            }
            catch (Exception)
            {
                // (left in the temp folder)
            }
        }

#if AUTOPILOT_IMAGECONVERSION
        [Test]
        public void TheFramesPlayAsAFlipbook()
        {
            Assert.IsNull(RecordingPlayer.Flipbook.Open(dir, 2), "no frames");
            for (var i = 0; i < 3; ++i)
            {
                var tex = new Texture2D(16, 8, TextureFormat.RGB24, false);
                File.WriteAllBytes(Path.Combine(dir, $"frame_{i:00000}.jpg"), tex.EncodeToJPG());
                UnityEngine.Object.DestroyImmediate(tex);
            }
            using (var flip = RecordingPlayer.Flipbook.Open(dir, 2))
            {
                Assert.AreEqual(3, flip.Frames.Count);
                Assert.AreEqual(1.5, flip.Length, 1e-9, "3 frames at 2 a second");
                Assert.AreEqual(0, flip.IndexAt(0.2));
                Assert.AreEqual(1, flip.IndexAt(0.5));
                Assert.AreEqual(2, flip.IndexAt(9));
                var shown = flip.Show(0.6);
                Assert.IsNotNull(shown);
                Assert.AreEqual(16, shown.width);
                Assert.AreEqual(1, flip.Shown);
            }
            // (where the Video module is missing, the player plays them: the MP4 gone isn't the recording gone)
            var player = new RecordingPlayer(Path.Combine(dir, "recording.mp4"), 0, dir, 2);
            Assert.AreEqual(RecordingPlayer.PlaysFrames, player.Q<Label>(className: "rec-problem").style.display == DisplayStyle.None);
            player.Release();
        }
#endif

        [Test]
        public void TheToolbarButtonGoesIntoAnOlderToolbar()
        {
            var root = new VisualElement();
            var zone = new VisualElement { name = "ToolbarZoneRightAlign" };
            zone.Add(new Button { name = "Account" });
            root.Add(zone);
            var button = AutopilotToolbar.Insert(root);
            Assert.IsNotNull(button);
            Assert.AreSame(button, zone[0], "first in the right-hand zone");
            Assert.AreEqual(AutopilotToolbar.Tooltip, button.tooltip);
            Assert.AreSame(button, AutopilotToolbar.Insert(root), "once");
            Assert.IsNull(AutopilotToolbar.Insert(new VisualElement()), "no zone: no button");
            Assert.DoesNotThrow(() => AutopilotToolbar.Insert(), "this editor's toolbar: a button or none, never an error");
        }
    }
}
