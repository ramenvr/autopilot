// A gate: its panel swings open (a quarter turn) or shut when toggled.

using UnityEngine;

public class Gate : MonoBehaviour
{
    public Transform panel;
    public float openAngle = 90f;
    public float swingDegreesPerSecond = 180f;
    public bool isOpen;

    public void Toggle()
    {
        isOpen = !isOpen;
    }

    void Update()
    {
        var target = Quaternion.Euler(0f, isOpen ? openAngle : 0f, 0f);
        panel.localRotation = Quaternion.RotateTowards(panel.localRotation, target, swingDegreesPerSecond * Time.deltaTime);
    }
}
