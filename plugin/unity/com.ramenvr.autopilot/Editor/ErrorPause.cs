// While a run plays the game in this editor (or the agent service plays it: play mode it entered), a script error
// doesn't stop it: the Console's Error Pause (which pauses
// play mode at the first error logged) is turned off as a run starts and put back as it was when the run is over, as
// Godot's plugin tells its debugger to continue after a script error's break. A run reports the errors itself (the
// log_error detector), and the agent can't play a paused game. What it was is kept in EditorPrefs for this project, so
// an editor that crashed or closed mid-run puts it back the next time it opens (and as this package is removed).

using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEngine;

namespace Autopilot.Playtest.Editor
{
    [InitializeOnLoad]
    static class ErrorPause
    {
        const double LetGoAfter = 10.0;  // seconds with no run before it is put back (a reconnect is not the end)
        const string PackageName = "com.ramenvr.autopilot";

        static double idleSince = -1;
        static double nextCheck;

        /// <summary>Tests: whether a run is going (null: the real client's and window's).</summary>
        internal static Func<bool> RunningOverride;

        static ErrorPause()
        {
            EditorApplication.update += Update;
            EditorApplication.quitting += PutBack;
            Events.registeringPackages += args =>
            {
                if (args.removed.Any(p => p.name == PackageName)) PutBack();
            };
        }

        /// <summary>The EditorPrefs key that holds what Error Pause was while a run turned it off (this project's: two
        /// projects' editors keep their own), absent when nothing is held.</summary>
        internal static string HeldKey => "AutopilotPlaytest.ErrorPauseWas." + Hash128.Compute(Application.dataPath);

        static bool Running
        {
            get
            {
                if (RunningOverride != null) return RunningOverride();
                var client = EditorHost.Client;
                return client != null && client.RunActive || AutopilotSession.Current != null || EditorHost.AgentPlaying;
            }
        }

        static void Update()
        {
            var now = EditorApplication.timeSinceStartup;
            if (now < nextCheck) return;
            nextCheck = now + 0.25;
            Check(now);
        }

        /// <summary>One check: a run going holds Error Pause off (again, should it be turned on meanwhile); none for
        /// LetGoAfter seconds puts it back.</summary>
        internal static void Check(double now)
        {
            if (Running)
            {
                idleSince = -1;
                Hold();
                return;
            }
            if (!EditorPrefs.HasKey(HeldKey)) return;
            if (idleSince < 0) idleSince = now;
            if (now - idleSince >= LetGoAfter) PutBack();
        }

        internal static void Hold()
        {
            var on = Get();
            if (on == null) return;  // (this Unity's Console can't be read: left alone)
            if (!EditorPrefs.HasKey(HeldKey)) EditorPrefs.SetInt(HeldKey, on.Value ? 1 : 0);
            if (!on.Value) return;
            Set(false);
            Debug.Log("[Autopilot] The Console's Error Pause is off while the run plays (a script error doesn't stop the game: the run reports it); it goes back on when the run ends.");
        }

        internal static void PutBack()
        {
            idleSince = -1;
            if (!EditorPrefs.HasKey(HeldKey)) return;
            var was = EditorPrefs.GetInt(HeldKey, 0) == 1;
            EditorPrefs.DeleteKey(HeldKey);
            if (was && Get() == false) Set(true);
        }

        // -- the Console's flag (UnityEditor.LogEntries, internal: through reflection) ---------------------------

        static PropertyInfo flagsProperty;
        static MethodInfo setFlag;
        static int bit = -1;

        static bool Find()
        {
            if (bit >= 0) return flagsProperty != null && setFlag != null;
            var editor = typeof(EditorWindow).Assembly;
            var entries = editor.GetType("UnityEditor.LogEntries");
            const BindingFlags any = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
            flagsProperty = entries?.GetProperty("consoleFlags", any);
            setFlag = entries?.GetMethod("SetConsoleFlag", any, null, new[] { typeof(int), typeof(bool) }, null);
            bit = 1 << 2;  // (ConsoleWindow.ConsoleFlags.ErrorPause, as Unity 6 has it)
            var flags = editor.GetType("UnityEditor.ConsoleWindow+ConsoleFlags");
            if (flags != null && flags.IsEnum)
            {
                try
                {
                    bit = Convert.ToInt32(Enum.Parse(flags, "ErrorPause"));
                }
                catch (Exception)
                {
                    // (named otherwise: the bit Unity 6 uses)
                }
            }
            return flagsProperty != null && setFlag != null;
        }

        /// <summary>Whether the Console's Error Pause is on; null when this Unity's Console can't be read.</summary>
        internal static bool? Get()
        {
            try
            {
                if (!Find()) return null;
                return ((int)flagsProperty.GetValue(null) & bit) != 0;
            }
            catch (Exception)
            {
                return null;
            }
        }

        internal static void Set(bool on)
        {
            try
            {
                if (Find()) setFlag.Invoke(null, new object[] { bit, on });
            }
            catch (Exception e)
            {
                Debug.LogWarning("[Autopilot] Couldn't change the Console's Error Pause: " + e.Message);
            }
        }
    }
}
