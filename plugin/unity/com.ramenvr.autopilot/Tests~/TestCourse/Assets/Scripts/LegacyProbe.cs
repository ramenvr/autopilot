// Reads the legacy Input Manager (UnityEngine.Input) every frame and keeps what it saw, for the smoke test's legacy
// input checks (the bridge's keys reach these reads through the package's IL post-processor). Off until the test turns
// it on: with the Input Manager off (Active Input Handling: Input System), these reads throw outside a run.

using System;
using UnityEngine;

public class LegacyProbe : MonoBehaviour
{
    public int wDowns, wUps, jumpDowns, fireDowns, leftClicks, rightClicks, padADowns, errors, frames;
    public bool wHeld, wHeldByName, leftHeld, anyKey;
    public float horizontal, horizontalRaw, lastMouseX, lastWheel, lastScrollY, padX;
    public Vector3 mouse;
    public string typed = "";

    void Update()
    {
        ++frames;
        try
        {
            if (Input.GetKeyDown(KeyCode.W)) ++wDowns;
            if (Input.GetKeyUp(KeyCode.W)) ++wUps;
            wHeld = Input.GetKey(KeyCode.W);
            wHeldByName = Input.GetKey("w");
            if (Input.GetButtonDown("Jump")) ++jumpDowns;
            if (Input.GetButtonDown("Fire1")) ++fireDowns;
            if (Input.GetMouseButtonDown(0)) ++leftClicks;
            if (Input.GetMouseButtonDown(1)) ++rightClicks;
            leftHeld = Input.GetMouseButton(0);
            if (Input.GetKeyDown(KeyCode.JoystickButton0)) ++padADowns;
            horizontal = Input.GetAxis("Horizontal");
            horizontalRaw = Input.GetAxisRaw("Horizontal");
            var mx = Input.GetAxis("Mouse X");
            if (mx != 0) lastMouseX = mx;
            var wheel = Input.GetAxis("Mouse ScrollWheel");
            if (wheel != 0) lastWheel = wheel;
            var scroll = Input.mouseScrollDelta.y;
            if (scroll != 0) lastScrollY = scroll;
            mouse = Input.mousePosition;
            typed += Input.inputString;
            anyKey = Input.anyKey;
        }
        catch (InvalidOperationException)
        {
            ++errors;
        }
    }
}
