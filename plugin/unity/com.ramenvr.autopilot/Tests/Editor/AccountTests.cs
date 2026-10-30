// The agent service's account and aura_sign_in frames (docs/protocol.md, Account) as the window takes them: the account
// frame parsed (with what Autopilot billed the Aura account this period, Aura's words when the team is out of credits,
// and the BYOK Coding Agent and LLM provider), the
// sign-in's states as its frames arrive, which account corner the Settings page shows, bills' money and dates as shown, and
// the setup's Coding Agent and LLM provider picked before the user picks them.

using System.Collections.Generic;
using System.IO;
using Autopilot.Playtest.Editor;
using NUnit.Framework;

namespace Autopilot.Playtest.Tests
{
    public class AccountTests
    {
        static Dictionary<string, object> Frame(string json) => Json.ParseObject(json);

        [Test]
        public void AnAuraAccountFrame()
        {
            var a = AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"aura\", \"email\": \"dev@studio.test\", \"signed_in\": true, \"keys\": {\"decision\": null, \"llm\": \"••••3c2e\"}}"));
            Assert.AreEqual(AccountInfo.ModeAura, a.Mode);
            Assert.AreEqual("dev@studio.test", a.Email);
            Assert.IsTrue(a.SignedIn);
            Assert.IsNull(a.DecisionKey);
            Assert.AreEqual("••••3c2e", a.LlmKey);
            Assert.IsTrue(a.HasKeys);
            Assert.AreEqual("Coding Agent: OpenCode (Anthropic) ••••3c2e", a.KeysLine());
        }

        [Test]
        public void TheFramesLlmProvider()
        {
            AccountInfo A(string provider) => AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"own_keys\", \"signed_in\": false, " +
                                                                     "\"keys\": {\"decision\": \"••••a91f\", \"llm\": \"••••3c2e\"}" + provider + "}"));
            var anthropic = A(", \"llm_provider\": \"anthropic\"");
            Assert.AreEqual("anthropic", anthropic.LlmProviderSaid);
            Assert.AreEqual(LlmProviders.Anthropic, anthropic.LlmProvider);
            Assert.IsNull(anthropic.CodingAgentSaid);
            Assert.AreEqual(CodingAgents.OpenCode, anthropic.CodingAgent);
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: OpenCode (Anthropic) ••••3c2e", anthropic.KeysLine());

            var openai = A(", \"llm_provider\": \"openai\"");
            Assert.AreEqual(LlmProviders.OpenAI, openai.LlmProvider);
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: OpenCode (OpenAI) ••••3c2e", openai.KeysLine());

            // each Coding Agent, and each of OpenCode's providers, by name
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: Claude Code (Anthropic) ••••3c2e",
                A(", \"coding_agent\": \"claude\", \"llm_provider\": \"anthropic\"").KeysLine());
            var codex = A(", \"coding_agent\": \"codex\", \"llm_provider\": \"openai\"");
            Assert.AreEqual("codex", codex.CodingAgentSaid);
            Assert.AreEqual(CodingAgents.Codex, codex.CodingAgent);
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: Codex (OpenAI) ••••3c2e", codex.KeysLine());
            var shown = new Dictionary<string, string>
            {
                ["anthropic"] = "Anthropic", ["openai"] = "OpenAI", ["opencode"] = "OpenCode Zen", ["openrouter"] = "OpenRouter",
                ["deepseek"] = "DeepSeek", ["google"] = "Gemini",
            };
            foreach (var pair in shown)
                Assert.AreEqual($"Decision provider ••••a91f · Coding Agent: OpenCode ({pair.Value}) ••••3c2e",
                    A($", \"coding_agent\": \"opencode\", \"llm_provider\": \"{pair.Key}\"").KeysLine(), pair.Key);

            // absent (an agent service from before them) or null or empty: OpenCode and Anthropic, its defaults; with
            // only the agent said, the provider that agent takes
            foreach (var none in new[] { "", ", \"llm_provider\": null, \"coding_agent\": null", ", \"llm_provider\": \"  \", \"coding_agent\": \"\"" })
            {
                var a = A(none);
                Assert.IsNull(a.LlmProviderSaid, none);
                Assert.IsNull(a.CodingAgentSaid, none);
                Assert.AreEqual(LlmProviders.Anthropic, a.LlmProvider, none);
                Assert.AreEqual("Decision provider ••••a91f · Coding Agent: OpenCode (Anthropic) ••••3c2e", a.KeysLine(), none);
            }
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: Codex (OpenAI) ••••3c2e", A(", \"coding_agent\": \"codex\"").KeysLine());

            // another one is shown as it is
            var other = A(", \"llm_provider\": \"mistral\"");
            Assert.AreEqual("mistral", other.LlmProvider);
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: OpenCode (mistral) ••••3c2e", other.KeysLine());
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: aider (mistral) ••••3c2e",
                A(", \"coding_agent\": \"aider\", \"llm_provider\": \"mistral\"").KeysLine());

            // no LLM key: no provider in the line
            var decisionOnly = AccountInfo.From(Frame("{\"mode\": \"own_keys\", \"keys\": {\"decision\": \"••••a91f\", \"llm\": null}, \"llm_provider\": \"openai\"}"));
            Assert.AreEqual("Decision provider ••••a91f", decisionOnly.KeysLine());

            Assert.AreEqual("Anthropic", LlmProviders.Name(null));
            Assert.AreEqual("OpenAI", LlmProviders.Name(" OpenAI "));
            Assert.AreEqual(LlmProviders.OpenAI, LlmProviders.Normalize("OpenAI"));
            Assert.AreEqual(LlmProviders.Anthropic, LlmProviders.Normalize("mistral"));
            Assert.AreEqual(LlmProviders.Anthropic, LlmProviders.Normalize(null));
            Assert.AreEqual(LlmProviders.Google, LlmProviders.Normalize(" Google"));
            CollectionAssert.AreEqual(new[] { "anthropic", "openai", "opencode", "openrouter", "deepseek", "google" }, LlmProviders.All);

            Assert.AreEqual("OpenCode", CodingAgents.Name(null));
            Assert.AreEqual("Claude Code", CodingAgents.Name(" Claude "));
            Assert.AreEqual(CodingAgents.Codex, CodingAgents.Normalize("Codex"));
            Assert.AreEqual(CodingAgents.OpenCode, CodingAgents.Normalize("aider"));
            Assert.AreEqual(CodingAgents.OpenCode, CodingAgents.Normalize(null));
            CollectionAssert.AreEqual(new[] { "claude", "codex", "opencode" }, CodingAgents.All);
            Assert.AreEqual(LlmProviders.Anthropic, CodingAgents.ProviderOf("claude"));
            Assert.AreEqual(LlmProviders.OpenAI, CodingAgents.ProviderOf("codex"));
            Assert.IsNull(CodingAgents.ProviderOf("opencode"));
        }

        [Test]
        public void TheSetupsCodingAgentAndLlmProviderToBeginWith()
        {
            var dir = Path.Combine(Path.GetTempPath(), "autopilot-provider-test-" + System.Guid.NewGuid().ToString("N"));
            var env = Path.Combine(dir, AutopilotEnv.FileName);
            try
            {
                AccountInfo A(string json) => AccountInfo.From(Frame(json));
                // the account frame's, when the agent service sent one (autopilot.env's isn't read then)
                Directory.CreateDirectory(dir);
                File.WriteAllText(env, "AUTOPILOT_OPENCODE_PROVIDER=openai\n");
                Assert.AreEqual(LlmProviders.Anthropic, AutopilotView.PreselectedLlmProvider(A("{\"mode\": \"own_keys\", \"llm_provider\": \"anthropic\"}"), env));
                Assert.AreEqual(LlmProviders.OpenAI, AutopilotView.PreselectedLlmProvider(A("{\"mode\": \"own_keys\", \"llm_provider\": \"openai\"}"), env));
                Assert.AreEqual(LlmProviders.Anthropic, AutopilotView.PreselectedLlmProvider(A("{\"mode\": \"own_keys\", \"llm_provider\": \"mistral\"}"), env));
                // else autopilot.env's (no frame, or one from before llm_provider)
                Assert.AreEqual(LlmProviders.OpenAI, AutopilotView.PreselectedLlmProvider(null, env));
                Assert.AreEqual(LlmProviders.OpenAI, AutopilotView.PreselectedLlmProvider(A("{\"mode\": \"none\"}"), env));
                File.WriteAllText(env, "AUTOPILOT_OPENCODE_PROVIDER=\"OpenAI\"\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\n");  // (the last line, as the agent service reads it)
                Assert.AreEqual(LlmProviders.Anthropic, AutopilotView.PreselectedLlmProvider(null, env));
                File.WriteAllText(env, "export AUTOPILOT_OPENCODE_PROVIDER = 'openai'\n");
                Assert.AreEqual(LlmProviders.OpenAI, AutopilotView.PreselectedLlmProvider(null, env));
                File.WriteAllText(env, "AUTOPILOT_OPENCODE_PROVIDER=llama\n");
                Assert.AreEqual(LlmProviders.Anthropic, AutopilotView.PreselectedLlmProvider(null, env));
                File.WriteAllText(env, "AUTOPILOT_OPENCODE_PROVIDER=deepseek\n");
                Assert.AreEqual(LlmProviders.DeepSeek, AutopilotView.PreselectedLlmProvider(null, env));
                // a frame for Claude Code or Codex says their provider, not OpenCode's: autopilot.env's then
                Assert.AreEqual(LlmProviders.DeepSeek, AutopilotView.PreselectedLlmProvider(A("{\"mode\": \"own_keys\", \"coding_agent\": \"codex\", \"llm_provider\": \"openai\"}"), env));
                Assert.AreEqual(LlmProviders.OpenRouter, AutopilotView.PreselectedLlmProvider(A("{\"mode\": \"own_keys\", \"coding_agent\": \"opencode\", \"llm_provider\": \"openrouter\"}"), env));
                // else Anthropic
                File.Delete(env);
                Assert.AreEqual(LlmProviders.Anthropic, AutopilotView.PreselectedLlmProvider(null, env));

                // The Coding Agent: the account frame's, else autopilot.env's AUTOPILOT_AGENT, else OpenCode (unknown ones:
                // OpenCode)
                File.WriteAllText(env, "AUTOPILOT_AGENT=codex\n");
                Assert.AreEqual(CodingAgents.Claude, AutopilotView.PreselectedCodingAgent(A("{\"mode\": \"own_keys\", \"coding_agent\": \"claude\"}"), env));
                Assert.AreEqual(CodingAgents.OpenCode, AutopilotView.PreselectedCodingAgent(A("{\"mode\": \"own_keys\", \"coding_agent\": \"aider\"}"), env));
                Assert.AreEqual(CodingAgents.Codex, AutopilotView.PreselectedCodingAgent(A("{\"mode\": \"own_keys\", \"llm_provider\": \"anthropic\"}"), env));
                Assert.AreEqual(CodingAgents.Codex, AutopilotView.PreselectedCodingAgent(null, env));
                File.WriteAllText(env, "export AUTOPILOT_AGENT = 'Claude'\n");
                Assert.AreEqual(CodingAgents.Claude, AutopilotView.PreselectedCodingAgent(null, env));
                File.WriteAllText(env, "AUTOPILOT_AGENT=aider\n");
                Assert.AreEqual(CodingAgents.OpenCode, AutopilotView.PreselectedCodingAgent(null, env));
                File.Delete(env);
                Assert.AreEqual(CodingAgents.OpenCode, AutopilotView.PreselectedCodingAgent(null, env));
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }

        [Test]
        public void AnAuraAccountFramesBill()
        {
            var a = AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"aura\", \"email\": \"dev@studio.test\", \"signed_in\": true, \"keys\": {}, " +
                                           "\"bill\": {\"usd\": 12.34, \"runs\": 38, \"from\": \"2026-10-01\", \"to\": \"2026-10-31\"}}"));
            Assert.IsNotNull(a.Bill);
            Assert.AreEqual(12.34, a.Bill.Usd, 1e-9);
            Assert.AreEqual(38, a.Bill.Runs);
            Assert.AreEqual("2026-10-01", a.Bill.From);
            Assert.AreEqual("2026-10-31", a.Bill.To);
            Assert.AreEqual("Billing period (Oct 1 – Oct 31): $12.34 USD", a.Bill.Line);
            Assert.AreEqual("Oct 1 – Oct 31", a.Bill.Dates);

            // null (Aura couldn't say), absent (an agent service from before bills), or with no amount: no bill
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"bill\": null}")).Bill);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true}")).Bill);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"bill\": {\"runs\": 3}}")).Bill);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"bill\": {\"usd\": -1}}")).Bill);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"bill\": 5}")).Bill);

            // one run, an odd period, a big bill
            var one = AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"bill\": {\"usd\": 0.004, \"runs\": 1, \"from\": \"soon\"}}")).Bill;
            Assert.AreEqual("Billing period: < $0.01 USD", one.Line);
            Assert.AreEqual("", one.Dates);
            var big = AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"bill\": {\"usd\": 123456.789, \"runs\": 1234567, \"from\": \"2026-12-01\", \"to\": \"2027-01-31\"}}")).Bill;
            Assert.AreEqual("Billing period (Dec 1, 2026 – Jan 31, 2027): $123,456.79 USD", big.Line);
            Assert.AreEqual("Dec 1, 2026 – Jan 31, 2027", big.Dates);
        }

        const string NoCredits = "Your team is out of Aura credits. Add credits at https://aura.example/account/pricing to keep playtesting.";

        [Test]
        public void AnAuraAccountFramesOutOfCredits()
        {
            var a = AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"aura\", \"email\": \"dev@studio.test\", \"signed_in\": true, \"keys\": {}, " +
                                           $"\"out_of_credits\": {{\"message\": \"{NoCredits}\", \"url\": \"https://aura.example/account/pricing\"}}}}"));
            Assert.IsNotNull(a.OutOfCredits);
            Assert.AreEqual(NoCredits, a.OutOfCredits.Message);
            Assert.AreEqual("https://aura.example/account/pricing", a.OutOfCredits.Url);
            Assert.AreSame(a.OutOfCredits, AutopilotView.CreditsNotice(a));

            // Aura named no page: the words, no url
            var nowhere = AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"out_of_credits\": {\"message\": \"Your team is out of Aura credits.\", \"url\": null}}")).OutOfCredits;
            Assert.AreEqual("Your team is out of Aura credits.", nowhere.Message);
            Assert.AreEqual("", nowhere.Url);
            // (a web page only: it is opened in the browser)
            Assert.AreEqual("", AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"out_of_credits\": {\"message\": \"Out.\", \"url\": \"file:///C:/x\"}}")).OutOfCredits.Url);

            // null (the team has credits), absent (an agent service from before it), or with no words: none
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"out_of_credits\": null}")).OutOfCredits);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true}")).OutOfCredits);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"out_of_credits\": {\"message\": \" \", \"url\": \"https://aura.example\"}}")).OutOfCredits);
            Assert.IsNull(AccountInfo.From(Frame("{\"mode\": \"aura\", \"signed_in\": true, \"out_of_credits\": \"none\"}")).OutOfCredits);

            // Shown only for a signed-in Aura account
            Assert.IsNull(AutopilotView.CreditsNotice(AccountInfo.From(Frame($"{{\"mode\": \"aura\", \"signed_in\": false, \"out_of_credits\": {{\"message\": \"{NoCredits}\"}}}}"))));
            Assert.IsNull(AutopilotView.CreditsNotice(AccountInfo.From(Frame($"{{\"mode\": \"own_keys\", \"signed_in\": false, \"out_of_credits\": {{\"message\": \"{NoCredits}\"}}}}"))));
            Assert.IsNull(AutopilotView.CreditsNotice(null));
        }

        [Test]
        public void MoneyAsShown()
        {
            Assert.AreEqual("$0.59", Billing.Money(0.593177));
            Assert.AreEqual("$0.60", Billing.Money(0.5951));
            Assert.AreEqual("$0.01", Billing.Money(0.01));
            Assert.AreEqual("< $0.01", Billing.Money(0.0099));
            Assert.AreEqual("< $0.01", Billing.Money(0.0001));
            Assert.AreEqual("$0.00", Billing.Money(0));
            Assert.AreEqual("$0.00", Billing.Money(double.NaN));
            Assert.AreEqual("$12.34", Billing.Money(12.34));
            Assert.AreEqual("$1,234,567.89", Billing.Money(1234567.891));
            Assert.AreEqual("$3.00", Billing.Money(2.999));

            Assert.AreEqual("Oct 1 – Oct 31", Billing.Period("2026-10-01", "2026-10-31"));
            Assert.AreEqual("Sep 15 – Oct 14", Billing.Period("2026-09-15", "2026-10-14"));
            Assert.AreEqual("", Billing.Period("2026-10-01", null));
            Assert.AreEqual("", Billing.Period("2026-13-01", "2026-10-31"));
        }

        [Test]
        public void AnOwnKeysFrameAndOddOnes()
        {
            var a = AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"own_keys\", \"signed_in\": false, \"keys\": {\"decision\": \"••••a91f\", \"llm\": \"••••3c2e\"}}"));
            Assert.AreEqual(AccountInfo.ModeOwnKeys, a.Mode);
            Assert.AreEqual("", a.Email);
            Assert.IsFalse(a.SignedIn);
            Assert.AreEqual("Decision provider ••••a91f · Coding Agent: OpenCode (Anthropic) ••••3c2e", a.KeysLine());

            var none = AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"none\", \"signed_in\": false, \"keys\": {\"decision\": null, \"llm\": null}}"));
            Assert.AreEqual(AccountInfo.ModeNone, none.Mode);
            Assert.IsFalse(none.HasKeys);
            Assert.AreEqual("", none.KeysLine());

            // an unknown mode is nothing set up; a key sent whole is never shown whole
            var odd = AccountInfo.From(Frame("{\"type\": \"account\", \"mode\": \"later\", \"keys\": {\"decision\": \"sk-0123456789abcdef\"}}"));
            Assert.AreEqual(AccountInfo.ModeNone, odd.Mode);
            Assert.AreEqual("••••cdef", odd.DecisionKey);
            Assert.AreEqual(AccountInfo.ModeNone, AccountInfo.From(null).Mode);
        }

        [Test]
        public void ASignInThatWorks()
        {
            var s = new SignInState();
            Assert.AreEqual(SignInState.Idle, s.State);
            Assert.IsFalse(s.Going);
            s.Ask();
            Assert.AreEqual(SignInState.Asked, s.State);
            Assert.IsTrue(s.Going);
            s.Apply(Frame("{\"type\": \"aura_sign_in\", \"state\": \"waiting\", \"url\": \"https://aura.test/auth/cli?code=AB12\"}"));
            Assert.AreEqual(SignInState.Waiting, s.State);
            Assert.AreEqual("https://aura.test/auth/cli?code=AB12", s.Url);
            Assert.IsTrue(s.Going);
            s.Apply(Frame("{\"type\": \"aura_sign_in\", \"state\": \"signed_in\", \"email\": \"dev@studio.test\"}"));
            Assert.AreEqual(SignInState.SignedIn, s.State);
            Assert.AreEqual("dev@studio.test", s.Email);
            Assert.AreEqual("", s.Url);
            Assert.IsFalse(s.Going);
        }

        [Test]
        public void SignInErrorsInPlainWords()
        {
            var cases = new Dictionary<string, string>
            {
                ["denied"] = "Sign-in was cancelled in the browser.",
                ["timeout"] = "Nothing came back from the browser in 5 minutes.",
                ["unreachable"] = "Couldn’t reach Aura. Check your connection and try again.",
                ["refused"] = "Aura said no: this account has no plan.",
            };
            foreach (var pair in cases)
            {
                var s = new SignInState();
                s.Ask();
                s.Apply(Frame("{\"type\": \"aura_sign_in\", \"state\": \"error\", \"reason\": \"" + pair.Key + "\", \"message\": \"Aura said no: this account has no plan.\"}"));
                Assert.AreEqual(SignInState.Failed, s.State, pair.Key);
                Assert.AreEqual(pair.Key, s.Reason);
                Assert.AreEqual(pair.Value, s.Message, pair.Key);
                Assert.IsFalse(s.Going);
            }
            Assert.AreEqual("The sign-in didn’t finish. Try again.", SignInState.Words("refused", "  "));
        }

        [Test]
        public void ACancelledSignInShowsNothing()
        {
            var s = new SignInState();
            s.Ask();
            s.Apply(Frame("{\"type\": \"aura_sign_in\", \"state\": \"waiting\", \"url\": \"https://aura.test/x\"}"));
            s.Apply(Frame("{\"type\": \"aura_sign_in\", \"state\": \"error\", \"reason\": \"cancelled\", \"message\": \"Sign-in cancelled\"}"));
            Assert.AreEqual(SignInState.Idle, s.State);
            Assert.AreEqual("", s.Message);
            Assert.AreEqual("", s.Url);
        }

        [Test]
        public void WhatTheAgentServiceRemembersIsForgottenWhenTheConnectionCloses()
        {
            // A memory_state is the agent service's on this connection: once it's gone (removed from this computer,
            // say) the Memory line shows nothing, not the runs it had, until the next connection's memory_state
            var client = new PlaytestClient(EditorHost.Instance) { TestWire = _ => { } };
            try
            {
                client.TestConnected(true);
                client.TestReceive("{\"type\": \"memory_state\", \"runs\": 7, \"notes\": true, \"lib\": true}");
                Assert.IsTrue(client.MemoryKnown);
                Assert.AreEqual(7, client.MemoryRuns);
                client.TestConnected(false);
                Assert.IsFalse(client.MemoryKnown);
                Assert.AreEqual(0, client.MemoryRuns);
                Assert.IsFalse(client.MemoryNotes || client.MemoryLib);
                client.TestConnected(true);
                client.TestReceive("{\"type\": \"memory_state\", \"runs\": 0, \"notes\": false, \"lib\": false}");
                Assert.IsTrue(client.MemoryKnown);
                Assert.AreEqual(0, client.MemoryRuns);
            }
            finally
            {
                client.Disconnect();
                client.TestWire = null;
            }
        }

        [Test]
        public void ASignInGoesOnAfterTheConnectionComesBack()
        {
            // The agent service keeps a sign-in going when the connection to it closes: the account frame after the next
            // welcome finishes it (signed in, with the session's email), else it's asked again on the new connection
            var signIns = 0;
            var client = new PlaytestClient(EditorHost.Instance)
            {
                TestWire = s => signIns += Json.Str(Json.ParseObject(s), "type") == "aura_sign_in" ? 1 : 0,
            };
            const string signedOut = "{\"type\": \"account\", \"mode\": \"aura\", \"signed_in\": false, \"keys\": {}}";
            void Connect()
            {
                client.TestConnected(true);
                client.TestReceive("{\"type\": \"welcome\", \"role\": \"editor\", \"session\": \"sign-in-test\", \"features\": [\"account\"]}");
            }
            try
            {
                Connect();
                Assert.IsTrue(client.AskAuraSignIn());
                client.TestReceive("{\"type\": \"aura_sign_in\", \"state\": \"waiting\", \"url\": \"https://aura.test/x\"}");
                client.TestReceive(signedOut);
                Assert.AreEqual(1, signIns, "an account frame on the same connection asks nothing");

                client.TestConnected(false);
                Assert.AreEqual(SignInState.Waiting, client.SignIn.State, "kept while the connection is gone");
                Connect();
                client.TestReceive(signedOut);
                Assert.AreEqual(2, signIns, "asked again on the new connection");
                client.TestReceive(signedOut);
                Assert.AreEqual(2, signIns, "once");

                // signed_in without an email (only the Aura app running): not this sign-in's, asked again
                client.TestConnected(false);
                Connect();
                client.TestReceive("{\"type\": \"account\", \"mode\": \"aura\", \"signed_in\": true, \"keys\": {}}");
                Assert.AreEqual(3, signIns);
                Assert.AreEqual(SignInState.Waiting, client.SignIn.State);

                // Finished while away: signed in, nothing asked
                client.TestConnected(false);
                Connect();
                client.TestReceive("{\"type\": \"account\", \"mode\": \"aura\", \"signed_in\": true, \"email\": \"dev@studio.test\", \"keys\": {}}");
                Assert.AreEqual(3, signIns);
                Assert.AreEqual(SignInState.SignedIn, client.SignIn.State);
                Assert.AreEqual("dev@studio.test", client.SignIn.Email);

                // Nothing going: a new connection's account frame asks nothing
                client.TestConnected(false);
                Connect();
                client.TestReceive(signedOut);
                Assert.AreEqual(3, signIns);
            }
            finally
            {
                client.Disconnect();
                client.TestWire = null;
            }
        }

        [Test]
        public void TheSettingsPagesAccountCorner()
        {
            AccountInfo A(string json) => AccountInfo.From(Frame(json));
            var aura = A("{\"mode\": \"aura\", \"email\": \"dev@studio.test\", \"signed_in\": true, \"keys\": {}}");
            var ended = A("{\"mode\": \"aura\", \"email\": \"dev@studio.test\", \"signed_in\": false, \"keys\": {}}");
            var keys = A("{\"mode\": \"own_keys\", \"signed_in\": false, \"keys\": {\"llm\": \"••••3c2e\"}}");
            var none = A("{\"mode\": \"none\", \"signed_in\": false, \"keys\": {}}");

            Assert.AreEqual("aura", AutopilotView.AccountCornerKind(aura, Preferences.AccessAura, false));
            Assert.AreEqual("signed_out", AutopilotView.AccountCornerKind(ended, Preferences.AccessAura, false));
            Assert.AreEqual("aura_app", AutopilotView.AccountCornerKind(aura, Preferences.AccessAura, true));
            Assert.AreEqual("own_keys", AutopilotView.AccountCornerKind(keys, Preferences.AccessOwnKeys, false));
            Assert.AreEqual("none", AutopilotView.AccountCornerKind(none, "", false));
            // not connected yet: what the setup chose
            Assert.AreEqual("aura", AutopilotView.AccountCornerKind(null, Preferences.AccessAura, false));
            Assert.AreEqual("own_keys", AutopilotView.AccountCornerKind(null, Preferences.AccessOwnKeys, false));
            Assert.AreEqual("none", AutopilotView.AccountCornerKind(null, "", false));
        }
    }
}
