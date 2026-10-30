// How the agent service runs (its `account` frame) and the Aura sign-in the window asks it for (its `aura_sign_in`
// frames), as docs/protocol.md "Account" describes them. The plugin never holds Aura's tokens: the agent service signs
// in on Aura's website and keeps the sign-in for the computer; the window only shows where it stands.

using System;
using System.Collections.Generic;
using System.Globalization;

namespace Autopilot.Playtest
{
    /// <summary>The agent service's `account` frame.</summary>
    public sealed class AccountInfo
    {
        public const string ModeAura = "aura";
        public const string ModeOwnKeys = "own_keys";
        public const string ModeNone = "none";

        /// <summary>"aura", "own_keys" or "none".</summary>
        public string Mode = ModeNone;
        /// <summary>The Aura account's email ("" when not said).</summary>
        public string Email = "";
        public bool SignedIn;
        /// <summary>The last four characters of the keys the agent service has, as it shows them ("••••a91f"), or
        /// null.</summary>
        public string DecisionKey;
        public string LlmKey;
        /// <summary>The frame's `coding_agent` as it said it ("opencode", "claude", "codex"), or null: an agent service
        /// from before it says none.</summary>
        public string CodingAgentSaid;
        /// <summary>The Coding Agent runs with the user's keys use (OpenCode when the frame says none).</summary>
        public string CodingAgent => CodingAgentSaid ?? CodingAgents.OpenCode;
        /// <summary>The frame's `llm_provider` as it said it ("anthropic", "openrouter"...), or null: an agent service
        /// from before it says none.</summary>
        public string LlmProviderSaid;
        /// <summary>The LLM provider runs with the user's keys use (when the frame says none: the Coding Agent's own,
        /// else Anthropic).</summary>
        public string LlmProvider => LlmProviderSaid ?? CodingAgents.ProviderOf(CodingAgent) ?? LlmProviders.Anthropic;
        /// <summary>What Autopilot billed this Aura account in the current period (null: not said, or Aura couldn't
        /// say).</summary>
        public AccountBill Bill;
        /// <summary>Aura's words when the team has no credits left for a run (null while it has some, or not said: an
        /// agent service from before it says none).</summary>
        public AccountCredits OutOfCredits;

        public static AccountInfo From(Dictionary<string, object> m)
        {
            var a = new AccountInfo();
            if (m == null) return a;
            var mode = Json.Str(m, "mode");
            a.Mode = mode == ModeAura || mode == ModeOwnKeys ? mode : ModeNone;
            a.Email = Json.Str(m, "email");
            a.SignedIn = Json.Bool(m, "signed_in", false);
            var keys = Json.Obj(m, "keys");
            a.DecisionKey = Masked(Json.Str(keys, "decision", null));
            a.LlmKey = Masked(Json.Str(keys, "llm", null));
            var agent = Json.Str(m, "coding_agent", null)?.Trim();
            a.CodingAgentSaid = string.IsNullOrEmpty(agent) ? null : agent;
            var provider = Json.Str(m, "llm_provider", null)?.Trim();
            a.LlmProviderSaid = string.IsNullOrEmpty(provider) ? null : provider;
            a.Bill = AccountBill.Read(Json.Obj(m, "bill"));
            a.OutOfCredits = AccountCredits.Read(Json.Obj(m, "out_of_credits"));
            return a;
        }

        // (only an ending the agent service masked: never more than "••••" and four characters)
        static string Masked(string v)
        {
            if (string.IsNullOrEmpty(v)) return null;
            v = v.Trim();
            return v.Length <= 8 ? v : "••••" + v.Substring(v.Length - 4);
        }

        public bool HasKeys => DecisionKey != null || LlmKey != null;

        /// <summary>The saved keys' endings in a line ("Decision provider ••••a91f · Coding Agent: OpenCode (Anthropic)
        /// ••••3c2e"), "" when none.</summary>
        public string KeysLine()
        {
            var parts = new List<string>();
            if (DecisionKey != null) parts.Add("Decision provider " + DecisionKey);
            if (LlmKey != null) parts.Add($"Coding Agent: {CodingAgents.Name(CodingAgent)} ({LlmProviders.Name(LlmProvider)}) " + LlmKey);
            return string.Join(" · ", parts);
        }
    }

    /// <summary>The Coding Agents runs with the user's keys can use (the agent service's AUTOPILOT_AGENT, the account
    /// frame's `coding_agent`), in the first-run setup's order; OpenCode is the default.</summary>
    public static class CodingAgents
    {
        public const string Claude = "claude";
        public const string Codex = "codex";
        public const string OpenCode = "opencode";

        public static readonly string[] All = { Claude, Codex, OpenCode };

        /// <summary>One of All for an id ("Codex " too): OpenCode for none or another.</summary>
        public static string Normalize(string id)
        {
            var a = (id ?? "").Trim().ToLowerInvariant();
            return Array.IndexOf(All, a) >= 0 ? a : OpenCode;
        }

        /// <summary>An agent as shown: "Claude Code", "Codex", "OpenCode", another id as it is (none: OpenCode, the
        /// agent service's default).</summary>
        public static string Name(string id)
        {
            var a = (id ?? "").Trim();
            switch (a.ToLowerInvariant())
            {
                case Claude: return "Claude Code";
                case Codex: return "Codex";
                case "":
                case OpenCode: return "OpenCode";
                default: return a;
            }
        }

        /// <summary>The one LLM provider an agent takes keys for (Claude Code: Anthropic; Codex: OpenAI), or null:
        /// OpenCode takes any of LlmProviders.All.</summary>
        public static string ProviderOf(string agent)
        {
            switch (Normalize(agent))
            {
                case Claude: return LlmProviders.Anthropic;
                case Codex: return LlmProviders.OpenAI;
                default: return null;
            }
        }
    }

    /// <summary>The LLM providers runs with the user's keys can use (OpenCode's AUTOPILOT_OPENCODE_PROVIDER, the account
    /// frame's `llm_provider`), in the first-run setup's order: the first is the default.</summary>
    public static class LlmProviders
    {
        public const string Anthropic = "anthropic";
        public const string OpenAI = "openai";
        public const string OpenCodeZen = "opencode";
        public const string OpenRouter = "openrouter";
        public const string DeepSeek = "deepseek";
        public const string Google = "google";

        public static readonly string[] All = { Anthropic, OpenAI, OpenCodeZen, OpenRouter, DeepSeek, Google };

        /// <summary>One of All for an id ("OpenAI " too): Anthropic for none or another.</summary>
        public static string Normalize(string id)
        {
            var p = (id ?? "").Trim().ToLowerInvariant();
            return Array.IndexOf(All, p) >= 0 ? p : Anthropic;
        }

        /// <summary>A provider as shown: "Anthropic", "OpenAI", "OpenCode Zen", "OpenRouter", "DeepSeek", "Gemini",
        /// another id as it is (none: Anthropic, the agent service's default).</summary>
        public static string Name(string id)
        {
            var p = (id ?? "").Trim();
            switch (p.ToLowerInvariant())
            {
                case "":
                case Anthropic: return "Anthropic";
                case OpenAI: return "OpenAI";
                case OpenCodeZen: return "OpenCode Zen";
                case OpenRouter: return "OpenRouter";
                case DeepSeek: return "DeepSeek";
                case Google: return "Gemini";
                default: return p;
            }
        }
    }

    /// <summary>The account frame's `bill`: what Autopilot billed this Aura account in the current period.</summary>
    public sealed class AccountBill
    {
        public double Usd;
        public int Runs;
        /// <summary>The period's first and last days, "2026-10-01" (as the frame gave them; "" when not said).</summary>
        public string From = "";
        public string To = "";

        /// <summary>The frame's bill, or null (none, null, or no amount in it).</summary>
        public static AccountBill Read(Dictionary<string, object> b)
        {
            if (b == null) return null;
            var usd = Json.Num(b, "usd", double.NaN);
            if (double.IsNaN(usd) || double.IsInfinity(usd) || usd < 0) return null;
            var runs = Json.Num(b, "runs", 0);
            return new AccountBill
            {
                Usd = usd,
                Runs = double.IsNaN(runs) || runs < 0 ? 0 : (int)Math.Min(int.MaxValue, Math.Round(runs)),
                From = Json.Str(b, "from").Trim(),
                To = Json.Str(b, "to").Trim(),
            };
        }

        /// <summary>"Billing period (Oct 1 – Oct 31): $12.34 USD" ("Billing period: $12.34 USD" when the dates aren't
        /// both days).</summary>
        public string Line => (Dates.Length > 0 ? $"Billing period ({Dates})" : "Billing period") + $": {Billing.Money(Usd)} USD";

        /// <summary>"Oct 1 – Oct 31" ("" when the dates aren't both days).</summary>
        public string Dates => Billing.Period(From, To);
    }

    /// <summary>The account frame's `out_of_credits`: Aura's words when the team has no credits left for a run, and
    /// where to add some.</summary>
    public sealed class AccountCredits
    {
        /// <summary>Aura's words, as it said them.</summary>
        public string Message = "";
        /// <summary>The page to add credits on ("" when Aura names none).</summary>
        public string Url = "";

        /// <summary>The frame's out_of_credits, or null (none, null, or no words in it).</summary>
        public static AccountCredits Read(Dictionary<string, object> c)
        {
            var message = Json.Str(c, "message").Trim();
            if (message.Length == 0) return null;
            var url = Json.Str(c, "url").Trim();
            // (a web page only: it is opened in the browser)
            var web = url.StartsWith("https://", StringComparison.OrdinalIgnoreCase) || url.StartsWith("http://", StringComparison.OrdinalIgnoreCase);
            return new AccountCredits { Message = message, Url = web ? url : "" };
        }
    }

    /// <summary>Autopilot's bills as the window shows them.</summary>
    public static class Billing
    {
        /// <summary>"$0.59", "$1,234.50"; "&lt; $0.01" below a cent (but above nothing).</summary>
        public static string Money(double usd)
        {
            if (double.IsNaN(usd) || double.IsInfinity(usd) || usd <= 0) return "$0.00";
            if (usd < 0.01) return "< $0.01";
            return "$" + Math.Round(usd, 2, MidpointRounding.AwayFromZero).ToString("#,0.00", CultureInfo.InvariantCulture);
        }

        /// <summary>A run's bill as its `bill` frame said it, in dollars; null when it wasn't billed (billed false, or no
        /// bill: a run with the user's own keys, or one from before bills).</summary>
        public static double? RunUsd(Dictionary<string, object> bill)
        {
            if (bill == null || !Json.Bool(bill, "billed", false)) return null;
            var usd = Json.Num(bill, "usd", double.NaN);
            if (double.IsNaN(usd) || double.IsInfinity(usd) || usd < 0) return null;
            return usd;
        }

        /// <summary>What a `bill` frame keeps of itself: usd, billed and the reason (not its type).</summary>
        public static Dictionary<string, object> Kept(Dictionary<string, object> frame)
        {
            if (frame == null) return null;
            var kept = new Dictionary<string, object>
            {
                ["usd"] = Json.Num(frame, "usd", 0),
                ["billed"] = Json.Bool(frame, "billed", false),
            };
            var reason = Json.Str(frame, "reason");
            if (!string.IsNullOrEmpty(reason)) kept["reason"] = reason;
            return kept;
        }

        static bool Day(string s, out DateTime day)
        {
            return DateTime.TryParseExact(s ?? "", "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day);
        }

        /// <summary>"Oct 1 – Oct 31"; with the years when they differ ("Dec 1, 2026 – Jan 31, 2027"); "" when either
        /// isn't a day.</summary>
        public static string Period(string from, string to)
        {
            if (!Day(from, out var a) || !Day(to, out var b)) return "";
            string D(DateTime d, bool year) => d.ToString(year ? "MMM d, yyyy" : "MMM d", CultureInfo.InvariantCulture);
            var years = a.Year != b.Year;
            return $"{D(a, years)} – {D(b, years)}";
        }
    }

    /// <summary>The Aura sign-in the window asked the agent service for: its `aura_sign_in` frames.</summary>
    public sealed class SignInState
    {
        public const string Idle = "";
        /// <summary>Asked for; the agent service hasn't said "waiting" yet.</summary>
        public const string Asked = "asked";
        public const string Waiting = "waiting";
        public const string SignedIn = "signed_in";
        public const string Failed = "error";

        public string State = Idle;
        /// <summary>The page the browser was sent to (waiting).</summary>
        public string Url = "";
        public string Email = "";
        /// <summary>The error's reason: denied | timeout | unreachable | refused.</summary>
        public string Reason = "";
        /// <summary>The error in plain words, as the window shows it.</summary>
        public string Message = "";

        public bool Going => State == Asked || State == Waiting;

        public void Ask()
        {
            State = Asked;
            Url = "";
            Email = "";
            Reason = "";
            Message = "";
        }

        public void Reset()
        {
            State = Idle;
            Url = "";
            Email = "";
            Reason = "";
            Message = "";
        }

        /// <summary>Take an `aura_sign_in` frame. A cancelled sign-in (the window's own Cancel) shows nothing: back to
        /// idle.</summary>
        public void Apply(Dictionary<string, object> m)
        {
            switch (Json.Str(m, "state"))
            {
                case "waiting":
                    State = Waiting;
                    Url = Json.Str(m, "url");
                    break;
                case "signed_in":
                    State = SignedIn;
                    Email = Json.Str(m, "email");
                    Url = "";
                    break;
                case "error":
                    var reason = Json.Str(m, "reason");
                    if (reason == "cancelled")
                    {
                        Reset();
                        break;
                    }
                    State = Failed;
                    Reason = reason;
                    Message = Words(reason, Json.Str(m, "message"));
                    Url = "";
                    break;
            }
        }

        /// <summary>The sign-in finished while its connection was gone: the account frame after the next welcome said
        /// who is signed in.</summary>
        public void SignedInAs(string email)
        {
            State = SignedIn;
            Email = email ?? "";
            Url = "";
            Reason = "";
            Message = "";
        }

        /// <summary>An error's one plain line.</summary>
        public static string Words(string reason, string message)
        {
            switch (reason)
            {
                case "denied": return "Sign-in was cancelled in the browser.";
                case "timeout": return "Nothing came back from the browser in 5 minutes.";
                case "unreachable": return "Couldn’t reach Aura. Check your connection and try again.";
                default: return string.IsNullOrWhiteSpace(message) ? "The sign-in didn’t finish. Try again." : message.Trim();
            }
        }
    }
}
