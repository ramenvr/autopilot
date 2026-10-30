// The account and Aura sign-in frames over a real connection, against Tests~/account_standin.py (a stand-in agent
// service): the welcome's "account" feature, the account frame, a sign-in that works, signing out, a cancelled
// sign-in. Ignored unless RG_STANDIN_URL names the stand-in (account_standin.py sets it for the editor it starts).

using System;
using System.Collections;
using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace Autopilot.Playtest.Tests
{
    public class AccountLiveTests
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

        static IEnumerator Until(PlaytestClient c, Func<bool> done, string what, double seconds = 10)
        {
            var end = DateTime.UtcNow.AddSeconds(seconds);
            while (!done())
            {
                if (DateTime.UtcNow > end) Assert.Fail("Timed out waiting for " + what);
                c.Tick();
                yield return null;
            }
        }

        [UnityTest]
        public IEnumerator SignInSignOutAndCancel()
        {
            var url = Environment.GetEnvironmentVariable("RG_STANDIN_URL");
            if (string.IsNullOrEmpty(url)) Assert.Ignore("RG_STANDIN_URL isn't set: run Tests~/account_standin.py");
            var c = new PlaytestClient(new Host());
            try
            {
                c.Connect(url);
                yield return Until(c, () => c.CanSignIn && c.Account != null, "the welcome and the account frame");
                Assert.AreEqual(AccountInfo.ModeNone, c.Account.Mode);
                Assert.AreEqual("••••3c2e", c.Account.LlmKey);
                Assert.AreEqual(CodingAgents.Codex, c.Account.CodingAgent);
                Assert.AreEqual(LlmProviders.OpenAI, c.Account.LlmProvider);

                Assert.IsTrue(c.AskAuraSignIn());
                Assert.AreEqual(SignInState.Asked, c.SignIn.State);
                yield return Until(c, () => c.SignIn.State == SignInState.Waiting, "waiting");
                Assert.AreEqual("https://aura.test/auth/cli?code=AB12", c.SignIn.Url);
                yield return Until(c, () => c.SignIn.State == SignInState.SignedIn, "signed_in");
                Assert.AreEqual("dev@studio.test", c.SignIn.Email);
                yield return Until(c, () => c.Account.Mode == AccountInfo.ModeAura && c.Account.SignedIn, "the account after the sign-in");
                Assert.AreEqual("dev@studio.test", c.Account.Email);

                Assert.IsTrue(c.AuraSignOut());
                yield return Until(c, () => !c.Account.SignedIn, "the account after signing out");
                Assert.AreEqual(AccountInfo.ModeAura, c.Account.Mode);

                Assert.IsTrue(c.AskAuraSignIn());
                yield return Until(c, () => c.SignIn.State == SignInState.Waiting, "waiting again");
                c.CancelAuraSignIn();
                Assert.AreEqual(SignInState.Idle, c.SignIn.State);
                var end = DateTime.UtcNow.AddSeconds(2);
                while (DateTime.UtcNow < end)
                {
                    c.Tick();
                    yield return null;
                }
                // (the agent service's "cancelled" error shows nothing)
                Assert.AreEqual(SignInState.Idle, c.SignIn.State);
                c.AskAccount();
                var version = c.AccountVersion;
                yield return Until(c, () => c.AccountVersion != version, "the account asked for");
            }
            finally
            {
                c.Shutdown();
            }
        }
    }
}
