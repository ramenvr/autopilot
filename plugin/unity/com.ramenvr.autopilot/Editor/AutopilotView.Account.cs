// The Settings page's account card (at its top): how Autopilot runs on this computer, from the agent service's account
// frame (docs/protocol.md, Account) and preferences.json: through Aura (the email, what Autopilot billed this period,
// Aura's words when the team is out of credits with Add credits, Sign out with an inline confirm), the Aura app, BYOK
// (the saved keys' endings), or not set up; an ended Aura sign-in is signed in again in place (the same browser sign-in
// as the first-run setup's, AutopilotView.Setup.cs).

using UnityEngine.UIElements;
using static Autopilot.Playtest.Editor.AutopilotUI;

namespace Autopilot.Playtest.Editor
{
    public sealed partial class AutopilotView
    {
        bool signOutConfirm;

        static void AccountHead(VisualElement card, string tag, string tone)
        {
            var head = Row("wrap");
            head.Add(Lbl("Account"));
            var t = Tag(tag, tone);
            t.style.marginLeft = 8;
            head.Add(t);
            card.Add(head);
        }

        /// <summary>The card's title (15 / 500) and a line under it, each when there is one.</summary>
        static void AccountBody(VisualElement card, string title, string line, string lineClasses = "t-sec2")
        {
            if (!string.IsNullOrEmpty(title)) card.Add(Para(title, "big breaks"));
            if (!string.IsNullOrEmpty(line)) card.Add(Para(line, lineClasses));
        }

        /// <summary>The card's buttons (small, 6 apart); ruled: under a rule, as on the Account card.</summary>
        static VisualElement AccountButtons(VisualElement card, bool ruled = true)
        {
            var buttons = Box("buttons" + (ruled ? " ruled" : ""));
            if (!ruled) buttons.style.paddingTop = 2;
            card.Add(buttons);
            return buttons;
        }

        /// <summary>"Billing period (Oct 1 – Oct 31): $12.34 USD", the amount in mono (nothing when the account frame
        /// had no bill, or isn't a signed-in Aura account).</summary>
        static void BillLines(VisualElement card, AccountInfo account)
        {
            var bill = account != null && account.Mode == AccountInfo.ModeAura && account.SignedIn ? account.Bill : null;
            if (bill == null) return;
            var row = Box("bill-row bill-line");
            var text = bill.Line ?? "";
            var cut = text.LastIndexOf(": ", System.StringComparison.Ordinal);
            if (cut > 0)
            {
                row.Add(Para(text.Substring(0, cut + 1), "t-sec breaks"));
                row.Add(L(text.Substring(cut + 2), "mono t-sec bill-amount"));
            }
            else row.Add(Para(text, "t-sec breaks"));
            card.Add(row);
        }

        public const string AddCredits = "Add credits";

        /// <summary>Aura's words while the team has no credits left for a run (a signed-in Aura account's
        /// out_of_credits), or null.</summary>
        public static AccountCredits CreditsNotice(AccountInfo account)
        {
            return account != null && account.Mode == AccountInfo.ModeAura && account.SignedIn ? account.OutOfCredits : null;
        }

        /// <summary>Opens the page Aura names to add credits on, in the browser.</summary>
        static VisualElement AddCreditsButton(string url, string classes)
        {
            return Btn(AddCredits, () => UnityEngine.Application.OpenURL(url), classes);
        }

        /// <summary>Aura's out-of-credits words in red, under the bill (nothing while the team has credits).</summary>
        static void CreditsLine(VisualElement card, AccountInfo account)
        {
            var credits = CreditsNotice(account);
            if (credits != null) card.Add(Para(AutopilotText.Breakable(credits.Message), "t-red breaks credits-line"));
        }

        /// <summary>Add credits, first in the card's buttons, when Aura names where (nothing otherwise).</summary>
        static void CreditsButton(VisualElement buttons, AccountInfo account)
        {
            var credits = CreditsNotice(account);
            if (credits != null && credits.Url.Length > 0) buttons.Add(AddCreditsButton(credits.Url, "primary small"));
        }

        static VisualElement SmallSpinner()
        {
            var s = new Spinner();
            s.style.marginRight = 8;
            return s;
        }

        public const string ServiceRunning = "Agent service: running";

        /// <summary>The agent service's state in a line (a square dot, the words, its address or what to do), under
        /// the account in every mode but the Aura app.</summary>
        static VisualElement ServiceLine()
        {
            var row = Box("account-line wrap");
            void Words(string text, string classes = "account-words")
            {
                row.Add(Para(text, classes + " grow breaks"));
            }
            switch (AgentService.Current)
            {
                case AgentService.Phase.Running:
                {
                    row.Add(Dot(Success, 6));
                    row.Add(L(ServiceRunning, "account-words"));
                    row.Add(L(AgentService.Url, "account-url nowrap"));
                    break;
                }
                case AgentService.Phase.Starting:
                    row.Add(SmallSpinner());
                    Words("Starting the agent service…");
                    break;
                case AgentService.Phase.Installing:
                    row.Add(SmallSpinner());
                    Words("Installing the agent service…");
                    break;
                case AgentService.Phase.NotInstalled:
                    row.Add(Dot(Amber, 6));
                    Words("Agent service: not installed", "account-words t-amber");
                    row.Add(Btn("Install", AgentService.Install, "small"));
                    break;
                case AgentService.Phase.NoInstaller:
                    row.Add(Dot(Amber, 6));
                    Words("Agent service: not installed", "account-words t-amber");
                    break;
                case AgentService.Phase.Failed:
                case AgentService.Phase.InstallFailed:
                    row.Add(Dot(Red, 6));
                    Words(ServiceDidntStart, "account-words t-red");
                    row.tooltip = AgentService.Problem ?? "";
                    row.Add(Btn(ViewLogs, AgentService.OpenLog, "small"));
                    break;
                default:
                    row.Add(Dot(Muted, 6));
                    Words("Agent service: starts when Autopilot connects");
                    break;
            }
            return row;
        }

        /// <summary>What the account card shows: aura_app | aura | signed_out | own_keys (BYOK) | none.</summary>
        public static string AccountCornerKind(AccountInfo account, string access, bool useWithAura)
        {
            if (access == Preferences.AccessAura && useWithAura) return "aura_app";
            var mode = account?.Mode ?? (access == Preferences.AccessAura ? AccountInfo.ModeAura
                : access == Preferences.AccessOwnKeys ? AccountInfo.ModeOwnKeys : AccountInfo.ModeNone);
            if (mode == AccountInfo.ModeAura) return account == null || account.SignedIn ? "aura" : "signed_out";
            return mode == AccountInfo.ModeOwnKeys ? "own_keys" : "none";
        }

        /// <summary>How Autopilot runs, at the top of the Settings page.</summary>
        VisualElement AccountCorner()
        {
            var client = EditorHost.Client;
            var account = client?.Account;
            var sign = client?.SignIn;

            // Sign in again, in place: the Account plate kept, what the sign-in is doing in it
            if (SigningIn || (sign != null && sign.Going))
                return SigningInAccount(client);

            var card = Card("plate stack-10 account");
            switch (AccountCornerKind(account, Preferences.Current.Access, PlaytestSettings.Get().UseWithAura))
            {
                case "aura_app":
                    AccountHead(card, "Aura app", "lime");
                    AccountBody(card, "The Aura app’s agent service", null);
                    BillLines(card, account);
                    CreditsLine(card, account);
                    var appButtons = AccountButtons(card);
                    CreditsButton(appButtons, account);
                    appButtons.Add(RerunSetupButton("small"));
                    return card;

                case "aura":
                {
                    if (sign != null && sign.State == SignInState.SignedIn) client.ClearSignIn();
                    var email = account != null ? account.Email : "";
                    if (signOutConfirm && client != null && client.CanSignIn)
                    {
                        card.AddToClassList("confirm");
                        AccountHead(card, "Aura", "lime");
                        AccountBody(card, string.IsNullOrEmpty(email) ? "Sign out of Aura on this computer?" : $"Sign out {email} on this computer?",
                            "Every editor on this computer stops using Aura until you sign in again.");
                        var b = AccountButtons(card, false);
                        b.Add(Btn("Sign out", () =>
                        {
                            signOutConfirm = false;
                            var c = EditorHost.Client;
                            c?.AuraSignOut();
                            c?.AskAccount();
                            Refresh(true);
                        }, "small danger-fill"));
                        b.Add(Btn("Cancel", () =>
                        {
                            signOutConfirm = false;
                            Refresh(true);
                        }, "quiet small"));
                        return card;
                    }
                    signOutConfirm = false;
                    AccountHead(card, "Aura", "lime");
                    AccountBody(card, email, null);
                    BillLines(card, account);
                    CreditsLine(card, account);
                    card.Add(ServiceLine());
                    var buttons = AccountButtons(card);
                    CreditsButton(buttons, account);
                    buttons.Add(RerunSetupButton("small"));
                    var connected = client != null && client.CanSignIn;
                    // (the red text button, as Uninstall)
                    var signOut = Btn("Sign out", () =>
                    {
                        var c = EditorHost.Client;
                        if (c == null || !c.CanSignIn) return;
                        signOutConfirm = true;
                        Refresh(true);
                    }, "small danger");
                    // (not connected: greyed, its tooltip saying why)
                    signOut.EnableInClassList("off", !connected);
                    if (!connected) signOut.tooltip = "Connect to the agent service to sign out";
                    buttons.Add(signOut);
                    return card;
                }

                case "signed_out":
                {
                    // The Aura sign-in ended (signed out here or in another editor, or it expired)
                    AccountHead(card, "Signed out", "red");
                    var failed = sign != null && sign.State == SignInState.Failed;
                    var why = !string.IsNullOrEmpty(setupProblem) ? setupProblem
                        : failed ? sign.Message
                        : "Your Aura sign-in on this computer ended.";
                    AccountBody(card, account?.Email, why, "t-red");
                    card.Add(ServiceLine());
                    var buttons = AccountButtons(card);
                    buttons.Add(Btn(failed ? "Try again" : "Sign in again", () =>
                    {
                        setupProblem = "";
                        BeginSignIn();
                    }, "primary small"));
                    buttons.Add(RerunSetupButton("quiet small"));
                    return card;
                }

                case "own_keys":
                {
                    // (the tag says BYOK; no keys saved: the tag, the title, the agent service's line and the button)
                    AccountHead(card, "BYOK", "muted");
                    AccountBody(card, OwnKeysTitle, null);
                    if (account != null && account.HasKeys) card.Add(Para("Saved on this computer: " + account.KeysLine(), "keys-line breaks"));
                    card.Add(ServiceLine());
                    AccountButtons(card).Add(RerunSetupButton("small"));
                    return card;
                }

                default:
                {
                    AccountHead(card, "Not set up", "amber");
                    AccountBody(card, "Choose how to run Autopilot", null);
                    card.Add(ServiceLine());
                    AccountButtons(card).Add(RerunSetupButton("primary small"));
                    return card;
                }
            }
        }

        /// <summary>The Account plate while an Aura sign-in is signed in again from the Settings page: its tag says
        /// where it is, a line what it does (the browser's link to open again), Cancel; when the agent service can't be
        /// got up, why, with Try again and Rerun setup.</summary>
        VisualElement SigningInAccount(PlaytestClient client)
        {
            var away = SignInAway(client);
            var waiting = !away && client != null && client.SignIn.State == SignInState.Waiting;
            var phase = AgentService.Current;
            var stuck = (SigningIn || away) && (phase == AgentService.Phase.NoInstaller || phase == AgentService.Phase.Failed || phase == AgentService.Phase.InstallFailed);
            var card = Card("plate stack-10 account" + (stuck ? " bad" : " going"));
            AccountHead(card, stuck ? "Signed out" : waiting ? "In your browser" : "Getting ready", stuck ? "red" : "info");
            if (stuck)
            {
                card.Add(Para(phase == AgentService.Phase.NoInstaller ? "No agent service found" : ServiceDidntStart, "t-red"));
                var again = AccountButtons(card);
                again.Add(Btn("Try again", () =>
                {
                    AgentService.TryAgain();
                    BeginSignIn();
                }, "primary small"));
                again.Add(RerunSetupButton("quiet small"));
                return card;
            }
            var words = waiting ? "Finish signing in in your browser…" : PreparingStep(client) == 0 ? "Setting up the agent service…" : "Starting the agent service…";
            card.Add(Progress(words, "body-500"));
            var buttons = AccountButtons(card);
            if (waiting)
            {
                var url = client.SignIn.Url;
                buttons.Add(Btn("Open again", () =>
                {
                    if (!string.IsNullOrEmpty(url)) UnityEngine.Application.OpenURL(url);
                }, "small"));
            }
            buttons.Add(Btn("Cancel", () =>
            {
                CancelSignIn();
                Refresh(true);
            }, "quiet small"));
            return card;
        }

        /// <summary>The BYOK account card's title.</summary>
        public const string OwnKeysTitle = "Your own keys";
    }
}
