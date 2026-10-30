// The test course's menu (uGUI): options, and Start opens the level.

using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

public class MainMenu : MonoBehaviour
{
    public Toggle hardModeToggle;
    public Slider volumeSlider;
    public Dropdown qualityDropdown;
    public InputField nameField;
    public string levelScene = "Main";
    public bool hardMode;
    public float volume = 0.5f;
    public int quality;
    public string playerName = "";

    void Awake()
    {
        hardModeToggle.onValueChanged.AddListener(on => hardMode = on);
        volumeSlider.onValueChanged.AddListener(v => volume = v);
        qualityDropdown.onValueChanged.AddListener(i => quality = i);
        nameField.onValueChanged.AddListener(text => playerName = text);
    }

    public void StartGame()
    {
        SceneManager.LoadScene(levelScene);
    }
}
