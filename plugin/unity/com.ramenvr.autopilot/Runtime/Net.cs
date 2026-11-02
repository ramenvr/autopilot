// How the game being played is networked (context's `net`, as Unreal's): "standalone", "listen_server" (the player
// hosts it), "dedicated_server" or "client", read from Netcode for GameObjects' NetworkManager or Mirror's
// NetworkServer / NetworkClient through reflection (the package references neither). A client's server runs in another
// process in Unity (Multiplayer Play Mode's players are processes of their own): `server_world` is "" then, and a
// client can't pause the game (`pause` answers why).

using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

namespace Autopilot.Playtest
{
    public static class Net
    {
        const BindingFlags Static = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        static Type Find(string name)
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var t = asm.GetType(name, false);
                    if (t != null) return t;
                }
                catch (Exception)
                {
                    // (an assembly that can't be read)
                }
            }
            return null;
        }

        static readonly Lazy<Type> netcode = new Lazy<Type>(() => Find("Unity.Netcode.NetworkManager"));
        static readonly Lazy<Type> mirrorServer = new Lazy<Type>(() => Find("Mirror.NetworkServer"));
        static readonly Lazy<Type> mirrorClient = new Lazy<Type>(() => Find("Mirror.NetworkClient"));

        static object Get(Type t, object o, string name)
        {
            if (t == null) return null;
            var flags = o == null ? Static : Instance;
            try
            {
                var p = t.GetProperty(name, flags);
                if (p != null && p.GetIndexParameters().Length == 0) return p.GetValue(o);
                return t.GetField(name, flags)?.GetValue(o);
            }
            catch (Exception)
            {
                return null;
            }
        }

        static int Count(object o) => o is ICollection c ? c.Count : 0;

        /// <summary>{"mode", and for a client "server_world" (""), "clients"; for a server "clients"}: the clients
        /// connected to this server, or for a client, itself.</summary>
        public static Dictionary<string, object> Info()
        {
            bool server = false, client = false;
            var clients = 0;
            var manager = Get(netcode.Value, null, "Singleton");
            if (manager != null && Get(netcode.Value, manager, "IsListening") is bool listening && listening)
            {
                server = Get(netcode.Value, manager, "IsServer") is bool s && s;
                client = Get(netcode.Value, manager, "IsClient") is bool c && c;
                if (server) clients = Math.Max(0, Count(Get(netcode.Value, manager, "ConnectedClientsIds")) - (client ? 1 : 0));
            }
            else if (mirrorServer.Value != null || mirrorClient.Value != null)
            {
                server = Get(mirrorServer.Value, null, "active") is bool s && s;
                client = Get(mirrorClient.Value, null, "active") is bool c && c;
                if (server) clients = Math.Max(0, Count(Get(mirrorServer.Value, null, "connections")) - (client ? 1 : 0));
            }
            var mode = server && client ? "listen_server" : server ? "dedicated_server" : client ? "client" : "standalone";
            var info = new Dictionary<string, object> { ["mode"] = mode };
            if (mode == "client")
            {
                info["server_world"] = "";
                info["clients"] = 1;
            }
            else if (server) info["clients"] = clients;
            return info;
        }

        /// <summary>Why the game can't be paused from here (a networked client), or null.</summary>
        public static string CantPause()
        {
            return (string)Info()["mode"] == "client" ? "the game's server runs in another process: it can't be paused from here" : null;
        }
    }
}
