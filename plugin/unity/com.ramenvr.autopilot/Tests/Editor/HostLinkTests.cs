// The connection carried by another editor plugin (PlaytestClient.ConnectLink, Editor/HostLink.cs): the hello first and
// the brain's frames answered over the link, the script reload's 1012 and the session resumed over the next link, a new
// hello once the other plugin's connection ended, the link ended by a disconnect, the result passed on just before the
// link closes handled first, and HostLink's members as the other plugin finds them by name.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using Autopilot.Playtest.Editor;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace Autopilot.Playtest.Tests
{
    public class HostLinkTests
    {
        sealed class Host : IPlaytestHost
        {
            public string HostName => "editor";
            public string ProjectDir => "";
            public string ProjectId => "";
            public bool SessionRunning => false;
            public bool WantsInspection => false;
            public bool Session(PlaytestClient client, long id, string action, Dictionary<string, object> args) => false;
            public bool Export(string dir, Action<int?, string> done) => false;
            public void Exit(int code) { }
            public string ReportDir() => System.IO.Path.GetTempPath();
        }

        sealed class Wire
        {
            public readonly List<Dictionary<string, object>> Sent = new List<Dictionary<string, object>>();
            public readonly List<(int code, string why)> Closes = new List<(int, string)>();
            public void Send(string json) => Sent.Add(Json.ParseObject(json));
            public void Close(int code, string why) => Closes.Add((code, why));
            public List<Dictionary<string, object>> OfType(string type) => Sent.Where(f => Json.Str(f, "type") == type).ToList();
        }

        const string Welcome = "{\"type\": \"welcome\", \"role\": \"service\", \"session\": \"s-1\", \"features\": []}";

        static PlaytestClient Linked(Wire wire)
        {
            var client = new PlaytestClient(new Host());
            client.ConnectLink("aura", wire.Send, wire.Close);
            client.Tick();
            return client;
        }

        [Test]
        public void TheHelloGoesFirstAndTheBrainsRequestsAreAnsweredOverTheLink()
        {
            var wire = new Wire();
            var client = Linked(wire);
            try
            {
                Assert.IsTrue(client.Linked);
                Assert.IsTrue(client.Connected);
                Assert.AreEqual("aura", client.LinkedBy);
                Assert.AreEqual("hello", Json.Str(wire.Sent[0], "type"));
                Assert.AreEqual("unity", Json.Str(wire.Sent[0], "engine"));
                Assert.IsFalse(wire.Sent[0].ContainsKey("resume"));
                client.LinkReceive(Welcome);
                client.LinkReceive("{\"type\": \"request\", \"id\": 7, \"op\": \"ping\", \"args\": {}}");
                client.Tick();
                Assert.AreEqual("s-1", client.SessionId);
                var response = wire.OfType("response").Single();
                Assert.AreEqual(7, (int)Json.Num(response, "id", 0));
                Assert.IsTrue(Json.Bool(response, "ok", false));
                Assert.IsEmpty(wire.Closes);
            }
            finally
            {
                client.Shutdown();
            }
        }

        [Test]
        public void AScriptReloadClosesWith1012AndTheNextLinkResumesTheSession()
        {
            var wire = new Wire();
            var client = Linked(wire);
            client.LinkReceive(Welcome);
            client.Tick();
            var state = client.SaveState();
            client.CloseForReload();
            Assert.AreEqual(1012, wire.Closes.Single().code);
            Assert.AreEqual("aura", Json.Str(state, "linked_by"));

            // After the reload: the client waits for the link, and opens no socket of its own meanwhile
            var after = new PlaytestClient(new Host());
            try
            {
                after.Resume(Json.Str(state, "url"), Json.Str(state, "token"), Json.Str(state, "session"),
                    Json.Bool(state, "run_active", false), Json.Bool(state, "sent_run", false),
                    linkedBy: Json.Str(state, "linked_by"));
                after.Tick();
                Assert.IsTrue(after.Linked);
                Assert.IsTrue(after.WantConnected);
                Assert.IsFalse(after.Connected);
                var next = new Wire();
                after.ConnectLink("aura", next.Send, next.Close);
                after.Tick();
                Assert.IsTrue(after.Connected);
                var hello = next.OfType("hello").Single();
                Assert.AreEqual("s-1", Json.Str(hello, "resume"));
            }
            finally
            {
                after.Shutdown();
                client.Shutdown();
            }
        }

        [UnityTest]
        public IEnumerator AHelloGoesAgainAfterTheOtherPluginsConnectionEnded()
        {
            var wire = new Wire();
            var client = Linked(wire);
            try
            {
                client.LinkReceive(Welcome);
                client.Tick();
                client.LinkClosed("the brain went away");
                client.Tick();
                Assert.IsFalse(client.Connected);
                Assert.IsTrue(client.WantConnected);
                Assert.IsEmpty(wire.Closes, "the other plugin's connection ended: nothing to tell it");
                // (frames on a closed link are dropped)
                client.LinkReceive("{\"type\": \"request\", \"id\": 8, \"op\": \"ping\", \"args\": {}}");
                var end = DateTime.UtcNow.AddSeconds(5);
                while (wire.OfType("hello").Count < 2)
                {
                    if (DateTime.UtcNow > end) Assert.Fail("no second hello within 5 s");
                    client.Tick();
                    yield return null;
                }
                Assert.IsTrue(client.Connected);
                Assert.IsEmpty(wire.OfType("response"));
            }
            finally
            {
                client.Shutdown();
            }
        }

        [Test]
        public void ADisconnectEndsTheLink()
        {
            var wire = new Wire();
            var client = Linked(wire);
            try
            {
                client.Disconnect();
                Assert.AreEqual(1000, wire.Closes.Single().code);
                Assert.IsFalse(client.Linked);
                Assert.IsFalse(client.WantConnected);
                var sent = wire.Sent.Count;
                client.LinkReceive(Welcome);
                client.Tick();
                Assert.AreEqual(sent, wire.Sent.Count);
                Assert.IsNull(client.SessionId);
            }
            finally
            {
                client.Shutdown();
            }
        }

        [Test]
        public void TheResultPassedOnJustBeforeTheLinkClosesIsHandled()
        {
            var wire = new Wire();
            var client = Linked(wire);
            try
            {
                client.LinkReceive(Welcome);
                client.LinkReceive("{\"type\": \"run_started\", \"run\": {\"kind\": \"verify\", \"claim\": \"Verify it\", \"by\": \"aura\"}}");
                client.Tick();
                Assert.IsTrue(client.RunActive);
                Dictionary<string, object> result = null;
                client.OnResult += (r, dir) => result = r;
                client.LinkReceive("{\"type\": \"result\", \"result\": {\"verdict\": \"CONFIRMED\", \"reason\": \"it works\"}}");
                // As HostLink.Close, with no tick between: what came is handled, then the link ends
                client.HandleReceived();
                client.Disconnect();
                Assert.AreEqual("CONFIRMED", Json.Str(result, "verdict"));
                Assert.IsFalse(client.RunActive);
            }
            finally
            {
                client.Shutdown();
            }
        }

        [Test]
        public void HostLinkIsFoundByNameWithDotNetTypesOnly()
        {
            // As the other plugin finds it: by name, through reflection, with no reference to this package
            var type = Type.GetType("Autopilot.Playtest.Editor.HostLink, Autopilot.Playtest.Editor");
            Assert.IsNotNull(type);
            Assert.IsNotNull(type.GetMethod("Open", new[] { typeof(string), typeof(Action<string>), typeof(Action<int, string>) }));
            Assert.IsNotNull(type.GetMethod("Receive", new[] { typeof(string) }));
            Assert.IsNotNull(type.GetMethod("Closed", new[] { typeof(string) }));
            Assert.IsNotNull(type.GetMethod("Close", Type.EmptyTypes));
            Assert.AreEqual(typeof(bool), type.GetProperty("IsLinked")?.PropertyType);
            Assert.AreEqual(typeof(bool), type.GetProperty("IsConnected")?.PropertyType);
            Assert.AreEqual(typeof(string), type.GetProperty("PackageVersion")?.PropertyType);
            Assert.AreEqual(1, (int)type.GetField("Version").GetRawConstantValue());
            Assert.AreEqual(typeof(HostLink), type);
        }
    }
}
