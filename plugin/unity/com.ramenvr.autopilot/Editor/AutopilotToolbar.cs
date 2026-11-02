// The main toolbar's Autopilot button: the Autopilot logo; it opens the window. Unity 6.3 and later have a toolbar API
// for it (MainToolbarElement). Before 6.3 (6000.0 to 6000.2: the package's minimum is 6000.0) the button is put into
// the toolbar's own UI Toolkit tree, found through reflection (UnityEditor.Toolbar's root element, its right-hand
// zone), as editor extensions commonly do; anything not found as expected means no button, never an error.

using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;
#if UNITY_6000_3_OR_NEWER
using UnityEditor.Toolbars;
#endif

namespace Autopilot.Playtest.Editor
{
    static class AutopilotToolbar
    {
        public const string Tooltip = "Open Autopilot";

#if UNITY_6000_3_OR_NEWER
        [MainToolbarElement("Autopilot/Status", defaultDockPosition = MainToolbarDockPosition.Right)]
        static MainToolbarElement Create()
        {
            return new MainToolbarButton(new MainToolbarContent(AutopilotUI.Icon, Tooltip), PlaytestWindow.Open);
        }
#else
        [InitializeOnLoadMethod]
        static void Install()
        {
            if (Application.isBatchMode) return;
            EditorApplication.update += TryInsert;
        }

        static int tries;

        static void TryInsert()
        {
            // (the toolbar is made a moment after the editor starts: tried for a while until it is there)
            if (++tries > 600 || Insert() != null) EditorApplication.update -= TryInsert;
        }
#endif

        public const string ButtonName = "autopilot-toolbar-button";

        /// <summary>The button put into the main toolbar's right-hand zone, or the one there already; null when this
        /// editor's toolbar isn't found as expected (no button then). Compiled on every Unity (used before 6.3).</summary>
        internal static VisualElement Insert()
        {
            try
            {
                var toolbarType = typeof(EditorWindow).Assembly.GetType("UnityEditor.Toolbar");
                if (toolbarType == null) return null;
                var toolbars = Resources.FindObjectsOfTypeAll(toolbarType);
                if (toolbars == null || toolbars.Length == 0) return null;
                var rootField = toolbarType.GetField("m_Root", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                var root = rootField?.GetValue(toolbars[0]) as VisualElement;
                return root == null ? null : Insert(root);
            }
            catch (Exception)
            {
                return null;  // (another toolbar than expected: no button)
            }
        }

        /// <summary>The button put into a toolbar root's right-hand zone ("ToolbarZoneRightAlign"), first in it; null
        /// without that zone.</summary>
        internal static VisualElement Insert(VisualElement root)
        {
            var zone = root.Q("ToolbarZoneRightAlign");
            if (zone == null) return null;
            var there = zone.Q(ButtonName);
            if (there != null) return there;
            var button = new Button(PlaytestWindow.Open) { name = ButtonName, tooltip = Tooltip };
            button.style.width = 30;
            button.style.marginLeft = 2;
            button.style.marginRight = 2;
            button.style.paddingLeft = 4;
            button.style.paddingRight = 4;
            button.style.alignItems = Align.Center;
            button.style.justifyContent = Justify.Center;
            var icon = new Image { image = AutopilotUI.Icon, scaleMode = ScaleMode.ScaleToFit, pickingMode = PickingMode.Ignore };
            icon.style.width = 16;
            icon.style.height = 16;
            button.Add(icon);
            zone.Insert(0, button);
            return button;
        }
    }
}
