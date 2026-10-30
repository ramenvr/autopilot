// The user's Autopilot choices, for every engine and project on the machine (docs/protocol.md, "Sharing a run's data"):
// Autopilot/preferences.json in the user settings folder (Aura.Folder, beside brain.json):
//
//     {"version": 1, "onboarded": true, "share_data": true, "access": "aura" | "own_keys"}
//
// and the first-run setup's own model keys, Coding Agent and LLM provider in Autopilot/autopilot.env (KEY=value lines), which `autopilot
// serve` reads (docs/protocol.md, above "Account"). Both
// are written atomically (a temporary file, then a rename) and merged with what is there (other keys and lines stay).
// A missing or unreadable preferences.json is the defaults: not onboarded, sharing on.

using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Autopilot.Playtest
{
    public sealed class Preferences
    {
        public const string FileName = "preferences.json";
        public const string AccessAura = "aura";
        public const string AccessOwnKeys = "own_keys";

        /// <summary>The first-run setup was finished (or skipped).</summary>
        public bool Onboarded;
        /// <summary>Runs' data is sent to the developers of Autopilot (absent: true).</summary>
        public bool ShareData = true;
        /// <summary>"aura", "own_keys" or "" (never chosen).</summary>
        public string Access = "";

        public static string DefaultPath => Path.Combine(Aura.Folder(), FileName);

        /// <summary>The file's object, or an empty one when it is missing or unreadable.</summary>
        public static Dictionary<string, object> ReadRaw(string path = null)
        {
            try
            {
                path = path ?? DefaultPath;
                if (!File.Exists(path)) return new Dictionary<string, object>();
                return Json.Parse(File.ReadAllText(path)) as Dictionary<string, object> ?? new Dictionary<string, object>();
            }
            catch (Exception)
            {
                return new Dictionary<string, object>();  // (corrupt: the defaults)
            }
        }

        public static Preferences From(Dictionary<string, object> o)
        {
            var p = new Preferences();
            if (o == null) return p;
            p.Onboarded = o.TryGetValue("onboarded", out var ob) && ob is bool b && b;
            p.ShareData = !(o.TryGetValue("share_data", out var sd) && sd is bool s) || s;
            p.Access = o.TryGetValue("access", out var a) && a is string text ? text : "";
            return p;
        }

        public static Preferences Load(string path = null) => From(ReadRaw(path));

        /// <summary>Change some keys, keeping the others the file has; version 1. Returns false when it couldn't be
        /// written.</summary>
        /// <summary>The error number of the last write that failed (Update returned false).</summary>
        public static int LastError;

        public static bool Update(Dictionary<string, object> changes, string path = null)
        {
            path = path ?? DefaultPath;
            var o = ReadRaw(path);
            foreach (var pair in changes)
            {
                if (pair.Value == null) o.Remove(pair.Key);
                else o[pair.Key] = pair.Value;
            }
            o["version"] = 1L;
            try
            {
                Files.WriteAtomic(path, Json.Pretty(o) + "\n");
                cached = null;
                UnsavedShare = null;
                return true;
            }
            catch (Exception e)
            {
                UnityEngine.Debug.LogWarning($"[Autopilot] Couldn't write {path}: {e.Message}");
                LastError = e.HResult & 0xFFFF;
                if (changes.TryGetValue("share_data", out var share) && share is bool b) UnsavedShare = b;
                return false;
            }
        }

        /// <summary>The user's sharing choice when preferences.json couldn't be written (this process only).</summary>
        public static bool? UnsavedShare;

        static Preferences cached;
        static DateTime cachedStamp;
        static DateTime checkedAt;

        /// <summary>The preferences now (the file is looked at again at most once a second, and read again when it
        /// changed).</summary>
        public static Preferences Current
        {
            get
            {
                var now = DateTime.UtcNow;
                if (cached != null && (now - checkedAt).TotalSeconds < 1.0) return cached;
                checkedAt = now;
                DateTime stamp;
                try
                {
                    stamp = File.Exists(DefaultPath) ? File.GetLastWriteTimeUtc(DefaultPath) : DateTime.MaxValue;
                }
                catch (Exception)
                {
                    stamp = DateTime.MaxValue;
                }
                if (cached == null || stamp != cachedStamp)
                {
                    cached = Load();
                    cachedStamp = stamp;
                }
                return cached;
            }
        }

        /// <summary>-PlaytestShare=0|1 (also false|true): null when the command line doesn't say.</summary>
        public static bool? CommandLineShare()
        {
            var v = PlaytestClient.Arg("PlaytestShare").Trim().ToLowerInvariant();
            if (v == "0" || v == "false" || v == "no" || v == "off") return false;
            if (v == "1" || v == "true" || v == "yes" || v == "on") return true;
            return null;
        }

        /// <summary>What every hello says as "share": -PlaytestShare when given, else preferences.json's share_data
        /// (true when it doesn't say).</summary>
        public static bool Share()
        {
            return CommandLineShare() ?? UnsavedShare ?? Load().ShareData;
        }
    }

    /// <summary>Autopilot/autopilot.env: the model keys the first-run setup was given and the Coding Agent and LLM provider
    /// picked, for `autopilot serve`.</summary>
    public static class AutopilotEnv
    {
        public const string FileName = "autopilot.env";
        public const string TypeSafeKey = "TYPESAFE_API_KEY";
        public const string AnthropicKey = "ANTHROPIC_API_KEY";
        public const string OpenAIKey = "OPENAI_API_KEY";
        public const string OpenCodeKey = "OPENCODE_API_KEY";
        public const string OpenRouterKey = "OPENROUTER_API_KEY";
        public const string DeepSeekKey = "DEEPSEEK_API_KEY";
        public const string GeminiKey = "GEMINI_API_KEY";
        /// <summary>The Coding Agent runs with the user's keys use (CodingAgents: claude | codex | opencode).</summary>
        public const string AgentKey = "AUTOPILOT_AGENT";
        /// <summary>The LLM provider OpenCode uses (LlmProviders.All).</summary>
        public const string ProviderKey = "AUTOPILOT_OPENCODE_PROVIDER";

        public static string DefaultPath => Path.Combine(Aura.Folder(), FileName);

        /// <summary>The key an LLM provider's API key is saved as (Anthropic's for none or another).</summary>
        public static string LlmKeyOf(string provider)
        {
            switch (LlmProviders.Normalize(provider))
            {
                case LlmProviders.OpenAI: return OpenAIKey;
                case LlmProviders.OpenCodeZen: return OpenCodeKey;
                case LlmProviders.OpenRouter: return OpenRouterKey;
                case LlmProviders.DeepSeek: return DeepSeekKey;
                case LlmProviders.Google: return GeminiKey;
                default: return AnthropicKey;
            }
        }

        /// <summary>What the setup's Use my keys saves: always the Coding Agent; OpenCode's LLM provider when OpenCode is
        /// the agent (for Claude Code and Codex that line is left as it is); the LLM key under the name of the provider
        /// the agent uses (Claude Code: Anthropic's, Codex: OpenAI's); and the decision key. An empty key keeps its
        /// line; other providers' key lines are kept.</summary>
        public static Dictionary<string, string> ByokKeys(string decisionKey, string agent, string provider, string llmKey)
        {
            agent = CodingAgents.Normalize(agent);
            var uses = CodingAgents.ProviderOf(agent) ?? LlmProviders.Normalize(provider);
            var keys = new Dictionary<string, string>
            {
                [TypeSafeKey] = decisionKey ?? "",
                [AgentKey] = agent,
            };
            if (agent == CodingAgents.OpenCode) keys[ProviderKey] = uses;
            keys[LlmKeyOf(uses)] = llmKey ?? "";
            return keys;
        }

        /// <summary>A key's value in the file (its last line, as the agent service reads it), or null: no such line,
        /// an empty value, or no file to read.</summary>
        public static string Value(string key, string path = null)
        {
            try
            {
                path = path ?? DefaultPath;
                if (!File.Exists(path)) return null;
                string value = null;
                foreach (var line in File.ReadAllLines(path))
                {
                    if (KeyOf(line) != key) continue;
                    var v = line.Substring(line.IndexOf('=') + 1).Trim();
                    if (v.Length >= 2 && (v[0] == '"' || v[0] == '\'') && v[v.Length - 1] == v[0]) v = v.Substring(1, v.Length - 2);
                    else if (v.IndexOf(" #", StringComparison.Ordinal) >= 0) v = v.Substring(0, v.IndexOf(" #", StringComparison.Ordinal)).TrimEnd();
                    value = v.Length > 0 ? v : null;
                }
                return value;
            }
            catch (Exception)
            {
                return null;  // (unreadable: as if it said nothing)
            }
        }

        /// <summary>OpenCode's LLM provider the file names (one of LlmProviders.All: Anthropic when it names none or
        /// another).</summary>
        public static string SavedLlmProvider(string path = null) => LlmProviders.Normalize(Value(ProviderKey, path));

        /// <summary>The Coding Agent the file names (one of CodingAgents.All: OpenCode when it names none or
        /// another).</summary>
        public static string SavedCodingAgent(string path = null) => CodingAgents.Normalize(Value(AgentKey, path));

        /// <summary>The KEY of a KEY=value line ("export KEY=value" too), or null for a comment, a blank line or
        /// anything else.</summary>
        public static string KeyOf(string line)
        {
            var t = (line ?? "").Trim();
            if (t.Length == 0 || t.StartsWith("#", StringComparison.Ordinal)) return null;
            if (t.StartsWith("export ", StringComparison.Ordinal)) t = t.Substring(7).TrimStart();
            var eq = t.IndexOf('=');
            if (eq <= 0) return null;
            return t.Substring(0, eq).Trim();
        }

        /// <summary>The file's text with these keys set: a line of the same KEY is replaced (later ones of it dropped),
        /// every other line kept as it was, new keys added at the end. Empty values are left out (the key stays as it
        /// was).</summary>
        public static string Merge(string existing, IDictionary<string, string> keys)
        {
            var set = new Dictionary<string, string>();
            foreach (var pair in keys)
            {
                var k = (pair.Key ?? "").Trim();
                var v = (pair.Value ?? "").Replace("\r", "").Replace("\n", "").Trim();
                if (k.Length == 0 || v.Length == 0) continue;
                set[k] = v;
            }
            var lines = new List<string>();
            if (!string.IsNullOrEmpty(existing))
                lines.AddRange(existing.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n'));
            if (lines.Count > 0 && lines[lines.Count - 1].Length == 0) lines.RemoveAt(lines.Count - 1);
            var done = new HashSet<string>();
            var output = new List<string>();
            foreach (var line in lines)
            {
                var k = KeyOf(line);
                if (k == null || !set.ContainsKey(k))
                {
                    output.Add(line);
                    continue;
                }
                if (done.Add(k)) output.Add(k + "=" + set[k]);
            }
            foreach (var pair in set)
                if (!done.Contains(pair.Key))
                    output.Add(pair.Key + "=" + pair.Value);
            return output.Count == 0 ? "" : string.Join("\n", output) + "\n";
        }

        /// <summary>Set these keys in autopilot.env (made, with its folder, when missing; readable by the user only where
        /// the system allows). Never logs the values. Returns false when it couldn't be written.</summary>
        /// <summary>The error number of the last write that failed (Write returned false).</summary>
        public static int LastError;

        public static bool Write(IDictionary<string, string> keys, string path = null)
        {
            path = path ?? DefaultPath;
            try
            {
                var any = false;
                foreach (var pair in keys) any |= !string.IsNullOrWhiteSpace(pair.Value);
                if (!any) return true;
                var existing = File.Exists(path) ? File.ReadAllText(path) : "";
                Files.WriteAtomic(path, Merge(existing, keys), true);
                return true;
            }
            catch (Exception e)
            {
                UnityEngine.Debug.LogWarning($"[Autopilot] Couldn't write {path}: {e.GetType().Name}");
                LastError = e.HResult & 0xFFFF;
                return false;
            }
        }
    }

    public static class Files
    {
        /// <summary>Write a text file whole or not at all: a temporary file beside it, then renamed over it.
        /// private: readable by the user only (macOS, Linux; on Windows the user settings folder is the user's
        /// own).</summary>
        public static void WriteAtomic(string path, string text, bool @private = false)
        {
            var dir = Path.GetDirectoryName(Path.GetFullPath(path));
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            var tmp = path + "." + Guid.NewGuid().ToString("N").Substring(0, 8) + ".tmp";
            try
            {
                File.WriteAllText(tmp, text, new UTF8Encoding(false));
                if (@private) OwnerOnly(tmp);
                if (File.Exists(path)) File.Replace(tmp, path, null);
                else File.Move(tmp, path);
            }
            finally
            {
                try
                {
                    if (File.Exists(tmp)) File.Delete(tmp);
                }
                catch (Exception)
                {
                    // (left behind)
                }
            }
        }

        [DllImport("libc", EntryPoint = "chmod", SetLastError = true)]
        static extern int chmod(string path, int mode);

        internal static void OwnerOnly(string path)
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;
            try
            {
                chmod(path, 0x180);  // 0600
            }
            catch (Exception)
            {
                // (no libc here: left as the system made it)
            }
        }
    }
}
