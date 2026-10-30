// Autopilot driven by another editor plugin: Aura's, so that the runs Aura's chat asks for play in this editor. That
// plugin carries Autopilot's connection to the brain in place of the window's WebSocket, frame for frame (the frames
// are the protocol's, docs/protocol.md), over a connection of its own; the window shows those runs as its own. While
// it does, the editor neither connects by itself nor starts an agent service (EditorHost), and the first-run setup
// isn't shown.
//
// The other plugin can't reference this package (the package may not be installed, and Aura's plugin is compiled
// before any project has it), so it finds this class by name, Autopilot.Playtest.Editor.HostLink in the assembly
// Autopilot.Playtest.Editor, and calls it through reflection. What it takes and returns are .NET's own types only,
// and they stay as they are: a later version only adds (Version counts the additions).
//
// Script reloads: the editor reloads both plugins' code (entering play mode, say). The link is closed with 1012 first,
// and the brain keeps the session; once both are back, the other plugin opens the link again (Open, with the same
// name) and the hello on it resumes the session (docs/protocol.md, "Reconnecting after a script reload").

using System;

namespace Autopilot.Playtest.Editor
{
    public static class HostLink
    {
        /// <summary>What this class offers: 1 for Open, Receive, Closed, Close, IsLinked, IsConnected and
        /// PackageVersion.</summary>
        public const int Version = 1;

        /// <summary>The package's version (what the hello says as plugin_version).</summary>
        public static string PackageVersion => PlaytestClient.PluginVersion;

        /// <summary>
        /// Carry Autopilot's connection. <paramref name="by"/> names the plugin ("aura"). <paramref name="send"/> gets
        /// every frame Autopilot sends, as JSON text, on the main thread: a hello begins a new connection to the brain,
        /// everything else goes on the one going. <paramref name="close"/> (code, reason) ends the connection to the
        /// brain: 1012 when a script reload follows (the brain keeps the session for the next hello to resume), 1000
        /// otherwise. Call on the main thread. Opening the link again after a script reload resumes the session;
        /// opening it while it is open begins a new connection. Returns false (and links nothing) while Autopilot is
        /// being removed from this computer.
        /// </summary>
        public static bool Open(string by, Action<string> send, Action<int, string> close)
        {
            if (Uninstall.Holds) return false;
            EditorHost.Ensure().ConnectLink(by, send, close);
            return true;
        }

        /// <summary>A frame from the brain, as JSON text (any thread).</summary>
        public static void Receive(string json) => EditorHost.Client?.LinkReceive(json);

        /// <summary>The connection to the brain ended: the brain closed it, or it couldn't be made (any thread).
        /// Autopilot sends a new hello on the link after a moment, as it connects again after a closed socket.</summary>
        public static void Closed(string reason) => EditorHost.Client?.LinkClosed(reason);

        /// <summary>Stop carrying the connection (main thread): Autopilot disconnects, and the editor connects by itself
        /// again as it would without the other plugin. The frames given to Receive before it are handled first (the run's
        /// result, which the other plugin passes on just before closing: its report is written).</summary>
        public static void Close()
        {
            var client = EditorHost.Client;
            if (client == null || !client.Linked) return;
            client.HandleReceived();
            EditorHost.Disconnect(false);
        }

        /// <summary>Whether the other plugin carries the connection (or Autopilot waits for it to, after a script
        /// reload).</summary>
        public static bool IsLinked => EditorHost.Client?.Linked == true;

        /// <summary>Whether the link is open: Autopilot has sent its hello on it, and hasn't been told it closed.</summary>
        public static bool IsConnected => EditorHost.Client is PlaytestClient c && c.Linked && c.Connected;

        /// <summary>The plugin's name as the window says it ("aura": Aura).</summary>
        internal static string DisplayName(string by)
        {
            if (string.IsNullOrEmpty(by)) return "the plugin that links Autopilot";
            return by == "aura" ? "Aura" : by;
        }
    }
}
