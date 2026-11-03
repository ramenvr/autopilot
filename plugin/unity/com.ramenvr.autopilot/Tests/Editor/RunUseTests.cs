// The detectors and the log events work only while a run uses the game (PlaytestClient.RunUsingGame): the user's own
// play sessions, connected with no run, don't pay for them.

using System;
using System.Collections.Generic;
using NUnit.Framework;

namespace Autopilot.Playtest.Tests
{
    public class RunUseTests
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

        static PlaytestClient Client()
        {
            var client = new PlaytestClient(new Host()) { TestWire = _ => { } };
            client.TestConnected(true);
            return client;
        }

        [Test]
        public void NotWhileConnectedWithNoRun()
        {
            var client = new PlaytestClient(new Host()) { TestWire = _ => { } };
            Assert.IsFalse(client.RunUsingGame, "not connected");
            client.TestConnected(true);
            Assert.IsFalse(client.RunUsingGame, "connected: the user's own play session");
            client.TestReceive("{\"type\": \"welcome\", \"role\": \"service\", \"session\": \"s\", \"features\": []}");
            Assert.IsFalse(client.RunUsingGame, "welcomed by an agent service with no run");
        }

        [Test]
        public void WhileARunGoesAndUntilItsResult()
        {
            var client = Client();
            client.TestReceive("{\"type\": \"run_started\", \"run\": {\"kind\": \"verify\"}}");
            Assert.IsTrue(client.RunUsingGame, "a run started elsewhere");
            client.TestRequestAge(600);
            Assert.IsTrue(client.RunUsingGame, "nothing asked for a long time: the agent thinking");
            client.TestReceive("{\"type\": \"result\", \"result\": {\"kind\": \"verify\"}}");
            Assert.IsFalse(client.RunUsingGame, "its result");
        }

        [Test]
        public void ACommandLineBrainIsARun()
        {
            var client = Client();
            client.TestReceive("{\"type\": \"welcome\", \"role\": \"cli\", \"session\": \"s\", \"features\": []}");
            Assert.IsTrue(client.RunUsingGame);
        }

        [Test]
        public void ARequestCountsForHalfAMinute()
        {
            var client = Client();
            client.TestReceive("{\"type\": \"request\", \"id\": 1, \"op\": \"ping\", \"args\": {}}");
            Assert.IsTrue(client.RunUsingGame, "a run this side didn't hear start");
            client.TestRequestAge(29);
            Assert.IsTrue(client.RunUsingGame);
            client.TestRequestAge(31);
            Assert.IsFalse(client.RunUsingGame);
        }

        [Test]
        public void NotOnceTheConnectionIsGone()
        {
            var client = Client();
            client.TestReceive("{\"type\": \"request\", \"id\": 1, \"op\": \"ping\", \"args\": {}}");
            client.TestConnected(false);
            Assert.IsFalse(client.RunUsingGame);
        }
    }
}
