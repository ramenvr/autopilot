// -fps N on the command line caps the frame rate (to test as a slow machine runs the game).

using System;
using System.Globalization;
using UnityEngine;

public static class FrameCap
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Apply()
    {
        var args = Environment.GetCommandLineArgs();
        for (var i = 0; i + 1 < args.Length; ++i)
        {
            if (args[i] != "-fps" || !int.TryParse(args[i + 1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var fps) || fps <= 0) continue;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = fps;
        }
    }
}
