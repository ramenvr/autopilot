// preferences.json (read, defaults, merged writes) and autopilot.env (merging keys into what is there, the BYOK Coding
// Agent, LLM provider and key, reading a key's value back).

using System.Collections.Generic;
using System.IO;
using NUnit.Framework;

namespace Autopilot.Playtest.Tests
{
    public class PreferencesTests
    {
        string dir;

        [SetUp]
        public void MakeFolder()
        {
            dir = Path.Combine(Path.GetTempPath(), "autopilot-prefs-test-" + System.Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void RemoveFolder()
        {
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
        }

        string PrefsPath => Path.Combine(dir, "Autopilot", Preferences.FileName);

        [Test]
        public void MissingOrCorruptFileIsTheDefaults()
        {
            var p = Preferences.Load(PrefsPath);
            Assert.IsFalse(p.Onboarded);
            Assert.IsTrue(p.ShareData);
            Assert.AreEqual("", p.Access);

            Directory.CreateDirectory(Path.GetDirectoryName(PrefsPath));
            File.WriteAllText(PrefsPath, "{\"onboarded\": tru");
            p = Preferences.Load(PrefsPath);
            Assert.IsFalse(p.Onboarded);
            Assert.IsTrue(p.ShareData);

            File.WriteAllText(PrefsPath, "[1, 2]");
            Assert.IsTrue(Preferences.Load(PrefsPath).ShareData);

            File.WriteAllText(PrefsPath, "{\"share_data\": \"no\"}");  // (not a bool: the default)
            Assert.IsTrue(Preferences.Load(PrefsPath).ShareData);
        }

        [Test]
        public void ReadsWhatTheFileSays()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(PrefsPath));
            File.WriteAllText(PrefsPath, "{\"version\": 1, \"onboarded\": true, \"share_data\": false, \"access\": \"own_keys\"}");
            var p = Preferences.Load(PrefsPath);
            Assert.IsTrue(p.Onboarded);
            Assert.IsFalse(p.ShareData);
            Assert.AreEqual(Preferences.AccessOwnKeys, p.Access);
        }

        [Test]
        public void UpdateMakesTheFolderAndMergesKeys()
        {
            Assert.IsTrue(Preferences.Update(new Dictionary<string, object> { ["share_data"] = false }, PrefsPath));
            var raw = Preferences.ReadRaw(PrefsPath);
            Assert.AreEqual(1L, raw["version"]);
            Assert.AreEqual(false, raw["share_data"]);
            Assert.IsFalse(raw.ContainsKey("onboarded"));

            // Another plugin's key stays; ours are set; null removes
            File.WriteAllText(PrefsPath, "{\"version\": 1, \"share_data\": false, \"other\": \"kept\", \"access\": \"aura\"}");
            Assert.IsTrue(Preferences.Update(new Dictionary<string, object> { ["onboarded"] = true, ["share_data"] = true, ["access"] = null }, PrefsPath));
            raw = Preferences.ReadRaw(PrefsPath);
            Assert.AreEqual("kept", raw["other"]);
            Assert.AreEqual(true, raw["onboarded"]);
            Assert.AreEqual(true, raw["share_data"]);
            Assert.IsFalse(raw.ContainsKey("access"));
            var p = Preferences.Load(PrefsPath);
            Assert.IsTrue(p.Onboarded && p.ShareData);

            // A corrupt file is replaced by a good one (no temporary file left behind)
            File.WriteAllText(PrefsPath, "garbage");
            Assert.IsTrue(Preferences.Update(new Dictionary<string, object> { ["onboarded"] = true }, PrefsPath));
            Assert.IsTrue(Preferences.Load(PrefsPath).Onboarded);
            Assert.AreEqual(1, Directory.GetFiles(Path.GetDirectoryName(PrefsPath)).Length);
        }

        [Test]
        public void EnvMergeAddsReplacesAndKeeps()
        {
            var keys = new Dictionary<string, string> { [AutopilotEnv.TypeSafeKey] = "ts-new", [AutopilotEnv.AnthropicKey] = "" };
            Assert.AreEqual("TYPESAFE_API_KEY=ts-new\n", AutopilotEnv.Merge("", keys));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-new\n", AutopilotEnv.Merge(null, keys));

            var existing = "# my keys\r\nOPENAI_API_KEY=keep-me\r\nexport TYPESAFE_API_KEY=old\r\n\r\nANTHROPIC_API_KEY=ant-old\r\nTYPESAFE_API_KEY=older\r\n";
            var merged = AutopilotEnv.Merge(existing, new Dictionary<string, string>
            {
                [AutopilotEnv.TypeSafeKey] = " ts-new\n",
                [AutopilotEnv.AnthropicKey] = "   ",
                ["NEW_KEY"] = "v",
            });
            Assert.AreEqual("# my keys\nOPENAI_API_KEY=keep-me\nTYPESAFE_API_KEY=ts-new\n\nANTHROPIC_API_KEY=ant-old\nNEW_KEY=v\n", merged);

            Assert.AreEqual("A=1\n", AutopilotEnv.Merge("A=1", new Dictionary<string, string>()));
            Assert.AreEqual("A=2\n", AutopilotEnv.Merge("A=1", new Dictionary<string, string> { ["A"] = "2" }));
            Assert.IsNull(AutopilotEnv.KeyOf("# A=1"));
            Assert.IsNull(AutopilotEnv.KeyOf("=x"));
            Assert.AreEqual("A", AutopilotEnv.KeyOf("  export A = 1"));
        }

        [Test]
        public void EnvWriteMakesTheFileAndMerges()
        {
            var path = Path.Combine(dir, "Autopilot", AutopilotEnv.FileName);
            Assert.IsTrue(AutopilotEnv.Write(new Dictionary<string, string> { [AutopilotEnv.TypeSafeKey] = "", [AutopilotEnv.AnthropicKey] = "" }, path));
            Assert.IsFalse(File.Exists(path));  // (nothing to write: no file made)

            Assert.IsTrue(AutopilotEnv.Write(new Dictionary<string, string> { [AutopilotEnv.AnthropicKey] = "ant-1" }, path));
            Assert.AreEqual("ANTHROPIC_API_KEY=ant-1\n", File.ReadAllText(path));
            File.AppendAllText(path, "OTHER=x\n");
            Assert.IsTrue(AutopilotEnv.Write(new Dictionary<string, string> { [AutopilotEnv.AnthropicKey] = "ant-2", [AutopilotEnv.TypeSafeKey] = "ts-1" }, path));
            Assert.AreEqual("ANTHROPIC_API_KEY=ant-2\nOTHER=x\nTYPESAFE_API_KEY=ts-1\n", File.ReadAllText(path));
            Assert.AreEqual(1, Directory.GetFiles(Path.GetDirectoryName(path)).Length);
        }

        [Test]
        public void EnvWriteSavesTheCodingAgentLlmProviderAndKey()
        {
            var path = Path.Combine(dir, "Autopilot", AutopilotEnv.FileName);

            // OpenCode with Anthropic: the agent and its provider, the key under the provider's own name
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys("ts-1", CodingAgents.OpenCode, LlmProviders.Anthropic, "ant-1"), path));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-1\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\nANTHROPIC_API_KEY=ant-1\n", File.ReadAllText(path));
            Assert.AreEqual(LlmProviders.Anthropic, AutopilotEnv.SavedLlmProvider(path));
            Assert.AreEqual(CodingAgents.OpenCode, AutopilotEnv.SavedCodingAgent(path));

            // OpenCode with OpenRouter: the provider line replaced, Anthropic's key line kept, an empty decision key keeps
            // its line
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys("", CodingAgents.OpenCode, LlmProviders.OpenRouter, "or-1"), path));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-1\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\nANTHROPIC_API_KEY=ant-1\nOPENROUTER_API_KEY=or-1\n", File.ReadAllText(path));
            Assert.AreEqual(LlmProviders.OpenRouter, AutopilotEnv.SavedLlmProvider(path));
            Assert.AreEqual("or-1", AutopilotEnv.Value(AutopilotEnv.OpenRouterKey, path));

            // Codex: the agent, the key as OpenAI's whatever OpenCode's provider was; OpenCode's provider line left as it is
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys("", CodingAgents.Codex, LlmProviders.DeepSeek, "oai-1"), path));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-1\nAUTOPILOT_AGENT=codex\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\nANTHROPIC_API_KEY=ant-1\nOPENROUTER_API_KEY=or-1\nOPENAI_API_KEY=oai-1\n", File.ReadAllText(path));
            Assert.AreEqual(CodingAgents.Codex, AutopilotEnv.SavedCodingAgent(path));
            // Claude Code: Anthropic's key
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys("", CodingAgents.Claude, LlmProviders.OpenAI, "ant-2"), path));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-1\nAUTOPILOT_AGENT=claude\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\nANTHROPIC_API_KEY=ant-2\nOPENROUTER_API_KEY=or-1\nOPENAI_API_KEY=oai-1\n", File.ReadAllText(path));

            // An empty LLM key keeps that provider's line (only the agent and provider change); the others are kept too
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys("", CodingAgents.OpenCode, LlmProviders.Google, "  "), path));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-1\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=google\nANTHROPIC_API_KEY=ant-2\nOPENROUTER_API_KEY=or-1\nOPENAI_API_KEY=oai-1\n", File.ReadAllText(path));
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys(null, CodingAgents.Codex, null, null), path));
            Assert.AreEqual("TYPESAFE_API_KEY=ts-1\nAUTOPILOT_AGENT=codex\nAUTOPILOT_OPENCODE_PROVIDER=google\nANTHROPIC_API_KEY=ant-2\nOPENROUTER_API_KEY=or-1\nOPENAI_API_KEY=oai-1\n", File.ReadAllText(path));

            // A new key replaces only its own provider's line; other lines stay where they are
            File.WriteAllText(path, "# mine\nOTHER=x\nOPENAI_API_KEY=oai-old\nGEMINI_API_KEY=gem-old\n");
            Assert.IsTrue(AutopilotEnv.Write(AutopilotEnv.ByokKeys("", CodingAgents.OpenCode, LlmProviders.Google, "gem-new"), path));
            Assert.AreEqual("# mine\nOTHER=x\nOPENAI_API_KEY=oai-old\nGEMINI_API_KEY=gem-new\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=google\n", File.ReadAllText(path));

            // Each provider's key under its own name
            var names = new Dictionary<string, string>
            {
                [LlmProviders.Anthropic] = "ANTHROPIC_API_KEY", [LlmProviders.OpenAI] = "OPENAI_API_KEY", [LlmProviders.OpenCodeZen] = "OPENCODE_API_KEY",
                [LlmProviders.OpenRouter] = "OPENROUTER_API_KEY", [LlmProviders.DeepSeek] = "DEEPSEEK_API_KEY", [LlmProviders.Google] = "GEMINI_API_KEY",
            };
            foreach (var pair in names)
            {
                Assert.AreEqual(pair.Value, AutopilotEnv.LlmKeyOf(pair.Key));
                var opencode = AutopilotEnv.ByokKeys("", CodingAgents.OpenCode, pair.Key, "k");
                Assert.AreEqual(pair.Key, opencode[AutopilotEnv.ProviderKey]);
                Assert.AreEqual("k", opencode[pair.Value]);
            }

            // A provider that isn't one of the six is saved as Anthropic; an agent that isn't one of the three as OpenCode
            var keys = AutopilotEnv.ByokKeys("", "aider", "mistral", "k");
            Assert.AreEqual(CodingAgents.OpenCode, keys[AutopilotEnv.AgentKey]);
            Assert.AreEqual(LlmProviders.Anthropic, keys[AutopilotEnv.ProviderKey]);
            Assert.AreEqual("k", keys[AutopilotEnv.AnthropicKey]);
            Assert.IsFalse(keys.ContainsKey(AutopilotEnv.OpenAIKey));
            Assert.IsFalse(AutopilotEnv.ByokKeys("", CodingAgents.Claude, LlmProviders.Google, "k").ContainsKey(AutopilotEnv.ProviderKey));
            Assert.AreEqual(AutopilotEnv.OpenAIKey, AutopilotEnv.LlmKeyOf("OpenAI"));
            Assert.AreEqual(AutopilotEnv.AnthropicKey, AutopilotEnv.LlmKeyOf(null));
            Assert.AreEqual(1, Directory.GetFiles(Path.GetDirectoryName(path)).Length);
        }

        [Test]
        public void EnvValueReadsTheLastLine()
        {
            var path = Path.Combine(dir, AutopilotEnv.FileName);
            Assert.IsNull(AutopilotEnv.Value(AutopilotEnv.ProviderKey, path));  // (no file)
            Assert.AreEqual(LlmProviders.Anthropic, AutopilotEnv.SavedLlmProvider(path));
            Directory.CreateDirectory(dir);
            File.WriteAllText(path, "# AUTOPILOT_OPENCODE_PROVIDER=openai\nA=1\n");
            Assert.IsNull(AutopilotEnv.Value(AutopilotEnv.ProviderKey, path));
            File.WriteAllText(path, "AUTOPILOT_OPENCODE_PROVIDER=anthropic\r\nexport AUTOPILOT_OPENCODE_PROVIDER=openai # picked\r\n");
            Assert.AreEqual("openai", AutopilotEnv.Value(AutopilotEnv.ProviderKey, path));
            File.WriteAllText(path, "AUTOPILOT_OPENCODE_PROVIDER=openai\nAUTOPILOT_OPENCODE_PROVIDER=\n");
            Assert.IsNull(AutopilotEnv.Value(AutopilotEnv.ProviderKey, path));
            Assert.AreEqual(CodingAgents.OpenCode, AutopilotEnv.SavedCodingAgent(path));
            File.WriteAllText(path, "AUTOPILOT_AGENT=claude\nAUTOPILOT_AGENT=Codex\n");
            Assert.AreEqual(CodingAgents.Codex, AutopilotEnv.SavedCodingAgent(path));
            File.WriteAllText(path, "B=\"a #b\"\n");  // (quoted: all of it)
            Assert.AreEqual("a #b", AutopilotEnv.Value("B", path));
        }
    }
}
