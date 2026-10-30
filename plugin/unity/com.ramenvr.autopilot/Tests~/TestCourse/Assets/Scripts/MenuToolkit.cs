// The menu's UI Toolkit part: a version label and a Credits button.

using UnityEngine;
using UnityEngine.UIElements;

[RequireComponent(typeof(UIDocument))]
public class MenuToolkit : MonoBehaviour
{
    public int creditsClicks;

    void Start()
    {
        var root = GetComponent<UIDocument>().rootVisualElement;
        root.Clear();
        root.Add(new Label("Version 1.0") { name = "VersionLabel" });
        root.Add(new Button(() => creditsClicks++) { name = "CreditsButton", text = "Credits" });
    }
}
