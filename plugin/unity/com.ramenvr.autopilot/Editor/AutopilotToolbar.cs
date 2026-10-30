// The main toolbar's Autopilot button (Unity 6.3 and later): the Autopilot logo; it opens the window.

#if UNITY_6000_3_OR_NEWER
using UnityEditor.Toolbars;

namespace Autopilot.Playtest.Editor
{
    static class AutopilotToolbar
    {
        [MainToolbarElement("Autopilot/Status", defaultDockPosition = MainToolbarDockPosition.Right)]
        static MainToolbarElement Create()
        {
            return new MainToolbarButton(new MainToolbarContent(AutopilotUI.Icon, "Open Autopilot"), PlaytestWindow.Open);
        }
    }
}
#endif
