// Builds the test course in batch mode (Tests~/testgame.py runs these):
//
//   Prepare: Input System as the active input handling (-inputHandling 2: Both), the product name. (Run first, with
//            -quit: the input handling takes effect when the editor next opens the project.)
//   Build:   the two scenes (Menu, Main) and the build list; with -buildPlayer <path> a Development player; then
//            the project index (Library/AutopilotCodex). Exits 0, or 1 on an error.
//
// The course: a menu (uGUI Start button, toggle, slider, dropdown and name field; a UI Toolkit label and button) and a
// level: a 40 m floor, a wall behind the start, a block, four spinning orbs to pick up, a gate whose panel swings open
// on Interact (E), a first-person player, a HUD counting the orbs, and LegacyProbe (off) reading UnityEngine.Input.

using System;
using System.IO;
using System.Linq;
using TMPro;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.Events;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;
using Button = UnityEngine.UI.Button;
using PanelSettings = UnityEngine.UIElements.PanelSettings;
using Slider = UnityEngine.UI.Slider;
using Toggle = UnityEngine.UI.Toggle;
using UIDocument = UnityEngine.UIElements.UIDocument;

public static class TestCourseBuilder
{
    const string MenuScene = "Assets/Scenes/Menu.unity";
    const string MainScene = "Assets/Scenes/Main.unity";
    const string PanelAsset = "Assets/UI/MenuPanel.asset";

    public static void Prepare()
    {
        var settings = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset").FirstOrDefault();
        if (settings != null)
        {
            var so = new SerializedObject(settings);
            var handler = so.FindProperty("activeInputHandler");
            // (the Input System package only; -inputHandling 0: the legacy Input Manager only, 2: both)
            var handling = Arg("-inputHandling");
            if (handler != null) handler.intValue = string.IsNullOrEmpty(handling) ? 1 : int.Parse(handling);
            so.ApplyModifiedPropertiesWithoutUndo();
        }
        PlayerSettings.productName = "OrbCourse";
        PlayerSettings.companyName = "AutopilotTests";
        AssetDatabase.SaveAssets();
        Debug.Log("[TestCourse] prepared");
    }

    public static void Build()
    {
        try
        {
            BuildScenes();
            var player = Arg("-buildPlayer");
            if (!string.IsNullOrEmpty(player)) BuildPlayer(player);
            ExportIndex();
            Debug.Log("[TestCourse] built");
            EditorApplication.Exit(0);
        }
        catch (Exception e)
        {
            Debug.LogError("[TestCourse] build failed: " + e);
            EditorApplication.Exit(1);
        }
    }

    static string Arg(string name)
    {
        var args = Environment.GetCommandLineArgs();
        for (var i = 0; i + 1 < args.Length; ++i)
            if (args[i] == name)
                return args[i + 1];
        return null;
    }

    // -- scenes ---------------------------------------------------------------------------------------------

    static void BuildScenes()
    {
        Directory.CreateDirectory("Assets/Scenes");
        Directory.CreateDirectory("Assets/UI");
        AssetDatabase.Refresh();  // (CreateAsset needs the folders known)
        if (AssetDatabase.LoadAssetAtPath<PanelSettings>(PanelAsset) == null)
        {
            AssetDatabase.CreateAsset(ScriptableObject.CreateInstance<PanelSettings>(), PanelAsset);
            AssetDatabase.SaveAssets();
        }
        BuildMenu();
        BuildMain();
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(MenuScene, true), new EditorBuildSettingsScene(MainScene, true) };
        AssetDatabase.SaveAssets();
    }

    static void BuildMenu()
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        // (loaded after NewScene, which unloads assets nothing in a scene uses)
        var panel = AssetDatabase.LoadAssetAtPath<PanelSettings>(PanelAsset);
        if (panel == null) throw new Exception(PanelAsset + " wasn't made");
        new GameObject("MenuCamera", typeof(Camera));
        var res = new DefaultControls.Resources();
        var canvas = NewCanvas("MenuCanvas");
        var menu = canvas.AddComponent<MainMenu>();
        Place(DefaultControls.CreateText(res), canvas, "TitleText", 160).GetComponent<Text>().text = "Orb Course";
        var start = Place(DefaultControls.CreateButton(res), canvas, "StartButton", 90);
        start.GetComponentInChildren<Text>().text = "Start";
        var toggle = Place(DefaultControls.CreateToggle(res), canvas, "HardModeToggle", 40);
        toggle.GetComponentInChildren<Text>().text = "Hard mode";
        toggle.GetComponent<Toggle>().isOn = false;
        var slider = Place(DefaultControls.CreateSlider(res), canvas, "VolumeSlider", 0);
        slider.GetComponent<Slider>().value = 0.5f;
        var dropdown = Place(DefaultControls.CreateDropdown(res), canvas, "QualityDropdown", -40).GetComponent<Dropdown>();
        dropdown.ClearOptions();
        dropdown.AddOptions(new[] { "Low", "Medium", "High" }.ToList());
        var field = Place(DefaultControls.CreateInputField(res), canvas, "NameField", -80);
        menu.hardModeToggle = toggle.GetComponent<Toggle>();
        menu.volumeSlider = slider.GetComponent<Slider>();
        menu.qualityDropdown = dropdown;
        menu.nameField = field.GetComponent<InputField>();
        UnityEventTools.AddPersistentListener(start.GetComponent<Button>().onClick, menu.StartGame);
        UseBuiltinFont(canvas);

        var doc = new GameObject("MenuDoc");
        doc.AddComponent<UIDocument>().panelSettings = panel;
        doc.AddComponent<MenuToolkit>();

        var events = new GameObject("EventSystem", typeof(EventSystem));
        events.AddComponent<InputSystemUIInputModule>().AssignDefaultActions();
        EditorSceneManager.SaveScene(scene, MenuScene);
    }

    static void BuildMain()
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var sun = new GameObject("Sun", typeof(Light));
        sun.GetComponent<Light>().type = LightType.Directional;
        sun.transform.rotation = Quaternion.Euler(50f, -30f, 0f);

        var level = new GameObject("Level");
        Prim(PrimitiveType.Cube, "Ground", level, new Vector3(0f, -0.5f, 0f), new Vector3(40f, 1f, 40f));
        Prim(PrimitiveType.Cube, "Wall", level, new Vector3(0f, 1.5f, -8f), new Vector3(10f, 3f, 0.5f));
        Prim(PrimitiveType.Cube, "Block", level, new Vector3(4f, 0.5f, 2f), Vector3.one);

        var orbs = new GameObject("Orbs");
        foreach (var at in new[] { new Vector3(0f, 1f, 5f), new Vector3(-1.5f, 1f, 6.5f), new Vector3(1.5f, 1f, 6.5f), new Vector3(-6f, 1f, -3f) })
        {
            var orb = Prim(PrimitiveType.Sphere, "Orb", orbs, at, Vector3.one * 0.5f);
            orb.GetComponent<SphereCollider>().isTrigger = true;
            orb.AddComponent<Collectible>();
        }

        var gate = new GameObject("Gate");
        gate.transform.position = new Vector3(6f, 0f, 0f);
        gate.AddComponent<Gate>().panel = Prim(PrimitiveType.Cube, "Panel", gate, new Vector3(0f, 1.25f, 0.6f), new Vector3(0.2f, 2.5f, 1.2f)).transform;

        var player = new GameObject("Player") { tag = "Player" };
        player.transform.position = new Vector3(0f, 1.08f, 0f);
        var body = player.AddComponent<CharacterController>();
        body.height = 2f;
        body.radius = 0.4f;
        body.center = Vector3.zero;
        body.stepOffset = 0.3f;
        body.slopeLimit = 45f;
        body.skinWidth = 0.08f;
        var pivot = new GameObject("CameraPivot") { tag = "MainCamera" };
        pivot.transform.SetParent(player.transform, false);
        pivot.transform.localPosition = new Vector3(0f, 0.6f, 0f);
        pivot.AddComponent<Camera>().nearClipPlane = 0.05f;
        pivot.AddComponent<AudioListener>();
        var controller = player.AddComponent<PlayerController>();
        controller.cameraPivot = pivot.transform;
        controller.controls = AssetDatabase.LoadAssetAtPath<InputActionAsset>("Assets/Input/Controls.inputactions");
        if (controller.controls == null) throw new Exception("Assets/Input/Controls.inputactions didn't import");

        var hud = NewCanvas("HUD");
        var res = new DefaultControls.Resources();
        var counter = hud.AddComponent<OrbCounter>();
        var count = Place(DefaultControls.CreateText(res), hud, "OrbsText", 200).GetComponent<Text>();
        count.text = "Orbs: 0/4";
        counter.label = count;
        var hint = new GameObject("HintText", typeof(RectTransform));
        hint.transform.SetParent(hud.transform, false);
        ((RectTransform)hint.transform).anchoredPosition = new Vector2(0f, 160f);
        hint.AddComponent<TextMeshProUGUI>().text = "<b>Find</b> the orbs";
        UseBuiltinFont(hud);

        new GameObject("LegacyProbe").AddComponent<LegacyProbe>().enabled = false;
        EditorSceneManager.SaveScene(scene, MainScene);
    }

    static GameObject NewCanvas(string name)
    {
        var go = new GameObject(name, typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
        go.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
        return go;
    }

    static GameObject Place(GameObject go, GameObject parent, string name, float y)
    {
        go.name = name;
        go.transform.SetParent(parent.transform, false);
        ((RectTransform)go.transform).anchoredPosition = new Vector2(0f, y);
        return go;
    }

    static void UseBuiltinFont(GameObject root)
    {
        var font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        foreach (var text in root.GetComponentsInChildren<Text>(true)) text.font = font;
    }

    static GameObject Prim(PrimitiveType type, string name, GameObject parent, Vector3 localPosition, Vector3 scale)
    {
        var go = GameObject.CreatePrimitive(type);
        go.name = name;
        go.transform.SetParent(parent.transform, false);
        go.transform.localPosition = localPosition;
        go.transform.localScale = scale;
        return go;
    }

    // -- the player build and the project index ---------------------------------------------------------------

    static void BuildPlayer(string path)
    {
        var target = Application.platform == RuntimePlatform.OSXEditor ? BuildTarget.StandaloneOSX
            : Application.platform == RuntimePlatform.LinuxEditor ? BuildTarget.StandaloneLinux64 : BuildTarget.StandaloneWindows64;
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { MenuScene, MainScene },
            locationPathName = path,
            target = target,
            options = BuildOptions.Development,
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception($"the player build {report.summary.result} ({report.summary.totalErrors} errors)");
    }

    static void ExportIndex()
    {
        // (the package's editor assembly isn't referenced by the project's own: called by name)
        var export = Type.GetType("Autopilot.Playtest.Editor.CodexExport, Autopilot.Playtest.Editor")?.GetMethod("Export", new[] { typeof(string) });
        if (export == null) throw new Exception("Autopilot.Playtest.Editor.CodexExport.Export(string) not found");
        export.Invoke(null, new object[] { Path.GetFullPath("Library/AutopilotCodex") });
    }
}
