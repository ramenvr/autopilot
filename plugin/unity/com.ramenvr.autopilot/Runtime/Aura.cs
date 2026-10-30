// Autopilot with Aura (the window's "Use with Aura"): instead of an agent service of your own, the one the Aura app
// runs on this machine, so runs go through Aura (on your Aura plan) and what the agent learns about each game is kept
// with Aura across your team's machines. Off by default: Autopilot on its own never connects to Aura.
//
// A running agent service says where it listens in Autopilot/<file> in the user settings folder, as the Unreal plugin
// and autopilot/bridge.py name it (%LOCALAPPDATA% on Windows, ~/Library/Application Support on macOS, $XDG_CONFIG_HOME or
// ~/.config elsewhere): brain.json for an Autopilot of your own, aura-brain.json for Aura's.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

namespace Autopilot.Playtest
{
    public static class Aura
    {
        public const string DownloadUrl = "https://www.tryaura.dev/";
        public const string OwnBrainFile = "brain.json";
        public const string AuraBrainFile = "aura-brain.json";

        /// <summary>The user settings folder (as autopilot/bridge.py's brain_file): %LOCALAPPDATA% on Windows,
        /// ~/Library/Application Support on macOS, $XDG_CONFIG_HOME or ~/.config elsewhere.</summary>
        public static string SettingsFolder()
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                var local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
                return string.IsNullOrEmpty(local) ? Path.Combine(home, "AppData", "Local") : local;
            }
            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) return Path.Combine(home, "Library", "Application Support");
            var xdg = Environment.GetEnvironmentVariable("XDG_CONFIG_HOME");
            return string.IsNullOrEmpty(xdg) ? Path.Combine(home, ".config") : xdg;
        }

        /// <summary>Autopilot's folder in the user settings folder: brain.json, aura-brain.json, preferences.json,
        /// autopilot.env.</summary>
        public static string Folder()
        {
            if (FolderOverride != null) return FolderOverride;
            return Path.Combine(SettingsFolder(), FolderName);
        }

        /// <summary>Tests only: another folder in place of the user's Autopilot folder (null: the user's).</summary>
        public static string FolderOverride;

        public const string FolderName = "Autopilot";

        /// <summary>
        /// The address a running agent service wrote to Autopilot/<paramref name="file"/>; null when none did, the one
        /// that did has ended, or it names anything but this machine.
        /// </summary>
        public static string AnnouncedUrl(string file) => Announced(file, out _);

        /// <summary>As AnnouncedUrl, with the process id the file names (0 when there is no url).</summary>
        public static string Announced(string file, out int pid)
        {
            pid = 0;
            try
            {
                var path = Path.Combine(Folder(), file);
                if (!File.Exists(path)) return null;
                var url = ParseAnnouncement(File.ReadAllText(path), out var said);
                if (url == null || !Running(said)) return null;
                pid = said;
                return url;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>A brain.json's url and pid ({"url", "pid", "started"}); null when it names no url on this machine
        /// or no process.</summary>
        public static string ParseAnnouncement(string text, out int pid)
        {
            pid = 0;
            try
            {
                if (!(Json.Parse(text ?? "") is Dictionary<string, object> said)) return null;
                var url = Json.Str(said, "url");
                var p = (int)Json.Num(said, "pid", 0);
                if (string.IsNullOrEmpty(url) || p <= 0) return null;
                if (!url.StartsWith("ws://127.0.0.1:") && !url.StartsWith("ws://localhost:")) return null;
                pid = p;
                return url;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Whether the process <paramref name="pid"/> runs.</summary>
        public static bool Running(int pid)
        {
            try
            {
                using (var p = Process.GetProcessById(pid)) return !p.HasExited;
            }
            catch (Exception)
            {
                return false;  // (no such process)
            }
        }

        /// <summary>The agent service the Aura app runs; null when it isn't running.</summary>
        public static string ServiceUrl() => AnnouncedUrl(AuraBrainFile);

        /// <summary>The Aura app, where its installer puts it; null when it isn't there.</summary>
        public static string AppPath()
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                var local = Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Path.Combine(home, "AppData", "Local");
                var exe = Path.Combine(local, "Programs", "aura-client", "Aura.exe");
                return File.Exists(exe) ? exe : null;
            }
            foreach (var app in new[] { "/Applications/Aura.app", Path.Combine(home, "Applications", "Aura.app") })
                if (Directory.Exists(app)) return app;
            return null;
        }

        /// <summary>Whether Aura is on this machine (its app is installed).</summary>
        public static bool IsInstalled() => InstalledOverride ?? AppPath() != null;

        /// <summary>Tests: whether the Aura app counts as installed (null: as it is).</summary>
        public static bool? InstalledOverride;

        /// <summary>Start the Aura app (it starts its agent service). Returns whether it was started.</summary>
        public static bool Launch()
        {
            var app = AppPath();
            if (app == null) return false;
            try
            {
                var info = app.EndsWith(".app")
                    ? new ProcessStartInfo("/usr/bin/open", "\"" + app + "\"")
                    : new ProcessStartInfo(app);
                info.UseShellExecute = false;
                using (Process.Start(info)) return true;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
