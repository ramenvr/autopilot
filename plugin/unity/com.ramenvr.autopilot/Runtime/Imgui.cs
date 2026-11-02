// IMGUI (OnGUI) for screen, ui and ui_act. IMGUI draws without objects to read, so the package's IL post-processor
// (CodeGen/LegacyInputRewriter.cs) points the game's calls to GUI's and GUILayout's Label, Box, Button, RepeatButton,
// Toggle, TextField and TextArea at ImguiGUI and ImguiGUILayout below, which call Unity's own (the game draws and
// behaves as it always did) and note each control as it is drawn: its kind, its words, where it is on screen, the
// script whose code drew it (the call's own class, which the rewriter passes as a last argument). A click (or a value)
// ui_act asks for is given by that control at its next drawing that isn't a layout pass: the button returns true, the
// toggle the other value, the text field the text, as a player's mouse and keys would have made them.
//
// ids: `<script>.<label>` for a button or a toggle (its words; its image's name in brackets without words), and
// `<script>.TextField` for a text field (`<script>.TextArea`); screen's texts `<script>.Label`, `<script>.Box` (the
// labels' and boxes' words), and the buttons' and toggles' words under their ids. A second one of the same id in a
// pass is `#2`, then `#3`, as ui's other ids.

#if AUTOPILOT_IMGUI
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace Autopilot.Playtest
{
    public static class Imgui
    {
        public sealed class Control
        {
            public string Id, Kind, Owner, Label, Text;
            public bool? On;
            public Rect Screen;
            public bool Enabled;
            public int Frame;
            public float Time;
        }

        static readonly Dictionary<string, Control> controls = new Dictionary<string, Control>();
        static readonly Dictionary<string, object> pending = new Dictionary<string, object>();
        static readonly Dictionary<string, int> counts = new Dictionary<string, int>();
        static int passFrame = -1;
        static EventType passType;
        static Event passEvent;

        /// <summary>Calls routed here so far (any game assembly's OnGUI drawing through the bridge).</summary>
        public static int Drawn { get; private set; }

        static void NewPass(Event e)
        {
            if (passFrame == UnityEngine.Time.frameCount && passType == e.type && ReferenceEquals(passEvent, e)) return;
            passFrame = UnityEngine.Time.frameCount;
            passType = e.type;
            passEvent = e;
            counts.Clear();
        }

        static string Unique(string id)
        {
            counts.TryGetValue(id, out var n);
            counts[id] = n + 1;
            return n == 0 ? id : id + "#" + (n + 1);
        }

        /// <summary>A control's words: its text, else its tooltip, else its image's name in brackets.</summary>
        internal static string Words(GUIContent c)
        {
            if (c == null) return "";
            if (!string.IsNullOrWhiteSpace(c.text)) return c.text.Trim();
            if (!string.IsNullOrWhiteSpace(c.tooltip)) return c.tooltip.Trim();
            return c.image != null ? "[" + c.image.name + "]" : "";
        }

        internal static GUIContent C(string text) => new GUIContent(text ?? "");
        internal static GUIContent C(Texture image) => new GUIContent(image);

        /// <summary>A control drawn: noted (when the pass is the one that paints, where it is), its id returned.</summary>
        static string Note(string kind, string owner, string label, string text, bool? on, Rect rect, bool layout)
        {
            ++Drawn;
            var e = Event.current;
            if (e == null) return null;
            NewPass(e);
            owner = string.IsNullOrEmpty(owner) ? "OnGUI" : owner;
            var id = Unique(owner + "." + label);
            if (e.type != EventType.Repaint) return id;
            Rect r;
            try
            {
                r = layout ? GUILayoutUtility.GetLastRect() : rect;
                var a = GUIUtility.GUIToScreenPoint(new Vector2(r.xMin, r.yMin));
                var b = GUIUtility.GUIToScreenPoint(new Vector2(r.xMax, r.yMax));
                r = Rect.MinMaxRect(Mathf.Min(a.x, b.x), Mathf.Min(a.y, b.y), Mathf.Max(a.x, b.x), Mathf.Max(a.y, b.y));
            }
            catch (Exception)
            {
                r = rect;
            }
            controls[id] = new Control
            {
                Id = id, Kind = kind, Owner = owner, Label = label, Text = text, On = on, Screen = r, Enabled = GUI.enabled,
                Frame = UnityEngine.Time.frameCount, Time = UnityEngine.Time.unscaledTime,
            };
            return id;
        }

        /// <summary>What ui_act asked of this control, taken at a drawing that isn't a layout pass (as a real click comes
        /// in the event after its layout).</summary>
        static bool Take(string id, out object value)
        {
            value = null;
            var e = Event.current;
            if (id == null || e == null || e.type == EventType.Layout || !pending.TryGetValue(id, out value)) return false;
            pending.Remove(id);
            return true;
        }

        internal static void Text(string kind, string owner, GUIContent content, Rect rect, bool layout)
        {
            var words = Words(content);
            Note(kind, owner, kind == "label" ? "Label" : "Box", words, null, rect, layout);
        }

        internal static bool Button(string owner, GUIContent content, Rect rect, bool layout, bool result)
        {
            var words = Words(content);
            var id = Note("button", owner, words.Length > 0 ? words : "Button", words, null, rect, layout);
            return Take(id, out _) || result;
        }

        internal static bool Toggle(string owner, GUIContent content, Rect rect, bool layout, bool result)
        {
            var words = Words(content);
            var id = Note("checkbox", owner, words.Length > 0 ? words : "Toggle", words, result, rect, layout);
            if (!Take(id, out var asked)) return result;
            var now = asked is bool b ? b : !result;
            if (controls.TryGetValue(id, out var c)) c.On = now;  // (as it is drawn from now on)
            return now;
        }

        internal static string Field(string kind, string owner, Rect rect, bool layout, string result)
        {
            var id = Note("text_input", owner, kind, result ?? "", null, rect, layout);
            if (!Take(id, out var asked) || !(asked is string s)) return result;
            if (controls.TryGetValue(id, out var c)) c.Text = s;
            return s;
        }

        /// <summary>The controls drawn in the last moment (the last few frames, or a quarter of a second).</summary>
        public static List<Control> Shown()
        {
            var frame = UnityEngine.Time.frameCount;
            var now = UnityEngine.Time.unscaledTime;
            foreach (var gone in controls.Values.Where(c => frame - c.Frame > 3 && now - c.Time > 0.25f).Select(c => c.Id).ToList())
                controls.Remove(gone);
            return controls.Values.OrderBy(c => c.Screen.yMin).ThenBy(c => c.Screen.xMin).ToList();
        }

        /// <summary>screen's IMGUI texts: labels and boxes by `<script>.Label` / `.Box`, buttons and toggles by their ids.</summary>
        public static void AddTexts(Dictionary<string, string> texts)
        {
            foreach (var c in Shown())
            {
                if (string.IsNullOrWhiteSpace(c.Text) || c.Kind == "text_input") continue;
                if (!texts.ContainsKey(c.Id)) texts[c.Id] = c.Text.Trim();
            }
        }

        /// <summary>ui's IMGUI controls: buttons, toggles and text fields, with where they are on screen (-1 to 1 from the
        /// middle, y up, as view's screen_x/screen_y).</summary>
        public static List<Dictionary<string, object>> Items()
        {
            var list = new List<Dictionary<string, object>>();
            foreach (var c in Shown())
            {
                if (c.Kind != "button" && c.Kind != "checkbox" && c.Kind != "text_input") continue;
                var item = new Dictionary<string, object>
                {
                    ["id"] = c.Id, ["kind"] = c.Kind, ["name"] = c.Label, ["owner"] = c.Owner, ["label"] = c.Kind == "text_input" ? "" : c.Text,
                    ["enabled"] = c.Enabled, ["imgui"] = true,
                };
                if (c.On != null) item["checked"] = c.On.Value;
                if (c.Kind == "text_input") item["text"] = c.Text;
                var w = Mathf.Max(1, UnityEngine.Screen.width);
                var h = Mathf.Max(1, UnityEngine.Screen.height);
                item["screen_x"] = Json.Round((c.Screen.center.x / w - 0.5) * 2, 3);
                item["screen_y"] = Json.Round((0.5 - c.Screen.center.y / h) * 2, 3);
                list.Add(item);
            }
            return list;
        }

        /// <summary>ui_act on an IMGUI control: "click" (a button pressed, a toggle flipped), "set" (a toggle's value, a
        /// text field's text). Given at its next drawing. Returns "" or what went wrong; null when no IMGUI control has
        /// that id.</summary>
        public static string Act(string id, string action, Dictionary<string, object> args)
        {
            var c = Shown().FirstOrDefault(x => x.Id == id);
            if (c == null) return null;
            if (!c.Enabled) return $"{id} is disabled";
            switch (action)
            {
                case "click":
                case "double_click":
                    if (c.Kind == "text_input") return "";  // (focusing a text field changes nothing a player sees in its value)
                    pending[id] = "click";
                    return "";
                case "set":
                    if (c.Kind == "checkbox")
                    {
                        pending[id] = Json.Bool(args, "value", !(c.On ?? false));
                        return "";
                    }
                    if (c.Kind == "text_input")
                    {
                        pending[id] = Json.Str(args, "text", Json.Str(args, "value"));
                        return "";
                    }
                    return $"{id} takes no value";
            }
            return $"unknown action {action} for an OnGUI control";
        }

        /// <summary>Tests and a new session: nothing noted, nothing pending.</summary>
        public static void Clear()
        {
            controls.Clear();
            pending.Clear();
            counts.Clear();
        }
    }

    /// <summary>UnityEngine.GUI's controls as the game calls them (the rewriter's targets: each overload with the game's
    /// class name last).</summary>
    public static class ImguiGUI
    {
        public static void Label(Rect position, string text, string owner) { GUI.Label(position, text); Imgui.Text("label", owner, Imgui.C(text), position, false); }
        public static void Label(Rect position, Texture image, string owner) { GUI.Label(position, image); Imgui.Text("label", owner, Imgui.C(image), position, false); }
        public static void Label(Rect position, GUIContent content, string owner) { GUI.Label(position, content); Imgui.Text("label", owner, content, position, false); }
        public static void Label(Rect position, string text, GUIStyle style, string owner) { GUI.Label(position, text, style); Imgui.Text("label", owner, Imgui.C(text), position, false); }
        public static void Label(Rect position, Texture image, GUIStyle style, string owner) { GUI.Label(position, image, style); Imgui.Text("label", owner, Imgui.C(image), position, false); }
        public static void Label(Rect position, GUIContent content, GUIStyle style, string owner) { GUI.Label(position, content, style); Imgui.Text("label", owner, content, position, false); }

        public static void Box(Rect position, string text, string owner) { GUI.Box(position, text); Imgui.Text("box", owner, Imgui.C(text), position, false); }
        public static void Box(Rect position, Texture image, string owner) { GUI.Box(position, image); Imgui.Text("box", owner, Imgui.C(image), position, false); }
        public static void Box(Rect position, GUIContent content, string owner) { GUI.Box(position, content); Imgui.Text("box", owner, content, position, false); }
        public static void Box(Rect position, string text, GUIStyle style, string owner) { GUI.Box(position, text, style); Imgui.Text("box", owner, Imgui.C(text), position, false); }
        public static void Box(Rect position, Texture image, GUIStyle style, string owner) { GUI.Box(position, image, style); Imgui.Text("box", owner, Imgui.C(image), position, false); }
        public static void Box(Rect position, GUIContent content, GUIStyle style, string owner) { GUI.Box(position, content, style); Imgui.Text("box", owner, content, position, false); }

        public static bool Button(Rect position, string text, string owner) => Imgui.Button(owner, Imgui.C(text), position, false, GUI.Button(position, text));
        public static bool Button(Rect position, Texture image, string owner) => Imgui.Button(owner, Imgui.C(image), position, false, GUI.Button(position, image));
        public static bool Button(Rect position, GUIContent content, string owner) => Imgui.Button(owner, content, position, false, GUI.Button(position, content));
        public static bool Button(Rect position, string text, GUIStyle style, string owner) => Imgui.Button(owner, Imgui.C(text), position, false, GUI.Button(position, text, style));
        public static bool Button(Rect position, Texture image, GUIStyle style, string owner) => Imgui.Button(owner, Imgui.C(image), position, false, GUI.Button(position, image, style));
        public static bool Button(Rect position, GUIContent content, GUIStyle style, string owner) => Imgui.Button(owner, content, position, false, GUI.Button(position, content, style));

        public static bool RepeatButton(Rect position, string text, string owner) => Imgui.Button(owner, Imgui.C(text), position, false, GUI.RepeatButton(position, text));
        public static bool RepeatButton(Rect position, Texture image, string owner) => Imgui.Button(owner, Imgui.C(image), position, false, GUI.RepeatButton(position, image));
        public static bool RepeatButton(Rect position, GUIContent content, string owner) => Imgui.Button(owner, content, position, false, GUI.RepeatButton(position, content));
        public static bool RepeatButton(Rect position, string text, GUIStyle style, string owner) => Imgui.Button(owner, Imgui.C(text), position, false, GUI.RepeatButton(position, text, style));
        public static bool RepeatButton(Rect position, Texture image, GUIStyle style, string owner) => Imgui.Button(owner, Imgui.C(image), position, false, GUI.RepeatButton(position, image, style));
        public static bool RepeatButton(Rect position, GUIContent content, GUIStyle style, string owner) => Imgui.Button(owner, content, position, false, GUI.RepeatButton(position, content, style));

        public static bool Toggle(Rect position, bool value, string text, string owner) => Imgui.Toggle(owner, Imgui.C(text), position, false, GUI.Toggle(position, value, text));
        public static bool Toggle(Rect position, bool value, Texture image, string owner) => Imgui.Toggle(owner, Imgui.C(image), position, false, GUI.Toggle(position, value, image));
        public static bool Toggle(Rect position, bool value, GUIContent content, string owner) => Imgui.Toggle(owner, content, position, false, GUI.Toggle(position, value, content));
        public static bool Toggle(Rect position, bool value, string text, GUIStyle style, string owner) => Imgui.Toggle(owner, Imgui.C(text), position, false, GUI.Toggle(position, value, text, style));
        public static bool Toggle(Rect position, bool value, Texture image, GUIStyle style, string owner) => Imgui.Toggle(owner, Imgui.C(image), position, false, GUI.Toggle(position, value, image, style));
        public static bool Toggle(Rect position, bool value, GUIContent content, GUIStyle style, string owner) => Imgui.Toggle(owner, content, position, false, GUI.Toggle(position, value, content, style));

        public static string TextField(Rect position, string text, string owner) => Imgui.Field("TextField", owner, position, false, GUI.TextField(position, text));
        public static string TextField(Rect position, string text, int maxLength, string owner) => Imgui.Field("TextField", owner, position, false, GUI.TextField(position, text, maxLength));
        public static string TextField(Rect position, string text, GUIStyle style, string owner) => Imgui.Field("TextField", owner, position, false, GUI.TextField(position, text, style));
        public static string TextField(Rect position, string text, int maxLength, GUIStyle style, string owner) => Imgui.Field("TextField", owner, position, false, GUI.TextField(position, text, maxLength, style));

        public static string TextArea(Rect position, string text, string owner) => Imgui.Field("TextArea", owner, position, false, GUI.TextArea(position, text));
        public static string TextArea(Rect position, string text, int maxLength, string owner) => Imgui.Field("TextArea", owner, position, false, GUI.TextArea(position, text, maxLength));
        public static string TextArea(Rect position, string text, GUIStyle style, string owner) => Imgui.Field("TextArea", owner, position, false, GUI.TextArea(position, text, style));
        public static string TextArea(Rect position, string text, int maxLength, GUIStyle style, string owner) => Imgui.Field("TextArea", owner, position, false, GUI.TextArea(position, text, maxLength, style));
    }

    /// <summary>UnityEngine.GUILayout's controls as the game calls them (the rewriter's targets: each overload with the
    /// game's class name last).</summary>
    public static class ImguiGUILayout
    {
        static readonly Rect None = default;

        public static void Label(Texture image, GUILayoutOption[] options, string owner) { GUILayout.Label(image, options); Imgui.Text("label", owner, Imgui.C(image), None, true); }
        public static void Label(string text, GUILayoutOption[] options, string owner) { GUILayout.Label(text, options); Imgui.Text("label", owner, Imgui.C(text), None, true); }
        public static void Label(GUIContent content, GUILayoutOption[] options, string owner) { GUILayout.Label(content, options); Imgui.Text("label", owner, content, None, true); }
        public static void Label(Texture image, GUIStyle style, GUILayoutOption[] options, string owner) { GUILayout.Label(image, style, options); Imgui.Text("label", owner, Imgui.C(image), None, true); }
        public static void Label(string text, GUIStyle style, GUILayoutOption[] options, string owner) { GUILayout.Label(text, style, options); Imgui.Text("label", owner, Imgui.C(text), None, true); }
        public static void Label(GUIContent content, GUIStyle style, GUILayoutOption[] options, string owner) { GUILayout.Label(content, style, options); Imgui.Text("label", owner, content, None, true); }

        public static void Box(Texture image, GUILayoutOption[] options, string owner) { GUILayout.Box(image, options); Imgui.Text("box", owner, Imgui.C(image), None, true); }
        public static void Box(string text, GUILayoutOption[] options, string owner) { GUILayout.Box(text, options); Imgui.Text("box", owner, Imgui.C(text), None, true); }
        public static void Box(GUIContent content, GUILayoutOption[] options, string owner) { GUILayout.Box(content, options); Imgui.Text("box", owner, content, None, true); }
        public static void Box(Texture image, GUIStyle style, GUILayoutOption[] options, string owner) { GUILayout.Box(image, style, options); Imgui.Text("box", owner, Imgui.C(image), None, true); }
        public static void Box(string text, GUIStyle style, GUILayoutOption[] options, string owner) { GUILayout.Box(text, style, options); Imgui.Text("box", owner, Imgui.C(text), None, true); }
        public static void Box(GUIContent content, GUIStyle style, GUILayoutOption[] options, string owner) { GUILayout.Box(content, style, options); Imgui.Text("box", owner, content, None, true); }

        public static bool Button(Texture image, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(image), None, true, GUILayout.Button(image, options));
        public static bool Button(string text, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(text), None, true, GUILayout.Button(text, options));
        public static bool Button(GUIContent content, GUILayoutOption[] options, string owner) => Imgui.Button(owner, content, None, true, GUILayout.Button(content, options));
        public static bool Button(Texture image, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(image), None, true, GUILayout.Button(image, style, options));
        public static bool Button(string text, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(text), None, true, GUILayout.Button(text, style, options));
        public static bool Button(GUIContent content, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Button(owner, content, None, true, GUILayout.Button(content, style, options));

        public static bool RepeatButton(Texture image, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(image), None, true, GUILayout.RepeatButton(image, options));
        public static bool RepeatButton(string text, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(text), None, true, GUILayout.RepeatButton(text, options));
        public static bool RepeatButton(GUIContent content, GUILayoutOption[] options, string owner) => Imgui.Button(owner, content, None, true, GUILayout.RepeatButton(content, options));
        public static bool RepeatButton(Texture image, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(image), None, true, GUILayout.RepeatButton(image, style, options));
        public static bool RepeatButton(string text, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Button(owner, Imgui.C(text), None, true, GUILayout.RepeatButton(text, style, options));
        public static bool RepeatButton(GUIContent content, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Button(owner, content, None, true, GUILayout.RepeatButton(content, style, options));

        public static bool Toggle(bool value, Texture image, GUILayoutOption[] options, string owner) => Imgui.Toggle(owner, Imgui.C(image), None, true, GUILayout.Toggle(value, image, options));
        public static bool Toggle(bool value, string text, GUILayoutOption[] options, string owner) => Imgui.Toggle(owner, Imgui.C(text), None, true, GUILayout.Toggle(value, text, options));
        public static bool Toggle(bool value, GUIContent content, GUILayoutOption[] options, string owner) => Imgui.Toggle(owner, content, None, true, GUILayout.Toggle(value, content, options));
        public static bool Toggle(bool value, Texture image, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Toggle(owner, Imgui.C(image), None, true, GUILayout.Toggle(value, image, style, options));
        public static bool Toggle(bool value, string text, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Toggle(owner, Imgui.C(text), None, true, GUILayout.Toggle(value, text, style, options));
        public static bool Toggle(bool value, GUIContent content, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Toggle(owner, content, None, true, GUILayout.Toggle(value, content, style, options));

        public static string TextField(string text, GUILayoutOption[] options, string owner) => Imgui.Field("TextField", owner, None, true, GUILayout.TextField(text, options));
        public static string TextField(string text, int maxLength, GUILayoutOption[] options, string owner) => Imgui.Field("TextField", owner, None, true, GUILayout.TextField(text, maxLength, options));
        public static string TextField(string text, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Field("TextField", owner, None, true, GUILayout.TextField(text, style, options));
        public static string TextField(string text, int maxLength, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Field("TextField", owner, None, true, GUILayout.TextField(text, maxLength, style, options));

        public static string TextArea(string text, GUILayoutOption[] options, string owner) => Imgui.Field("TextArea", owner, None, true, GUILayout.TextArea(text, options));
        public static string TextArea(string text, int maxLength, GUILayoutOption[] options, string owner) => Imgui.Field("TextArea", owner, None, true, GUILayout.TextArea(text, maxLength, options));
        public static string TextArea(string text, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Field("TextArea", owner, None, true, GUILayout.TextArea(text, style, options));
        public static string TextArea(string text, int maxLength, GUIStyle style, GUILayoutOption[] options, string owner) => Imgui.Field("TextArea", owner, None, true, GUILayout.TextArea(text, maxLength, style, options));
    }
}
#endif
