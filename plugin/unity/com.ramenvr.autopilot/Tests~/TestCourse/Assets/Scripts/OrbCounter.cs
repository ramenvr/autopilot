// The HUD's orb counter: "Orbs: collected/totalOrbs".

using UnityEngine;
using UnityEngine.UI;

public class OrbCounter : MonoBehaviour
{
    public Text label;
    public int totalOrbs = 4;
    public int collected;

    void Start()
    {
        Show();
    }

    public void Add(int count)
    {
        collected += count;
        Show();
    }

    void Show()
    {
        if (label != null) label.text = $"Orbs: {collected}/{totalOrbs}";
    }
}
