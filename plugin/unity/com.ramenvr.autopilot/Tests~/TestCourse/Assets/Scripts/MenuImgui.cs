// The menu's OnGUI part (IMGUI): a box, a label counting the clicks, a Play button and a Quit one, a Music toggle and a
// name field, with GUILayout and with GUI at a rect.

using UnityEngine;

public class MenuImgui : MonoBehaviour
{
    public int plays;
    public int quits;
    public bool music = true;
    public string playerName = "";
    public int drawn;  // (OnGUI passes so far)

    void OnGUI()
    {
        ++drawn;
        GUI.Box(new Rect(10, 10, 220, 180), "Options");
        GUILayout.BeginArea(new Rect(20, 40, 200, 120));
        GUILayout.Label("Plays: " + plays);
        if (GUILayout.Button("Play")) plays++;
        music = GUILayout.Toggle(music, "Music");
        playerName = GUILayout.TextField(playerName);
        GUILayout.EndArea();
        if (GUI.Button(new Rect(20, 160, 80, 24), "Quit")) quits++;
    }
}
