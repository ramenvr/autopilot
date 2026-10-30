// The test course's player: first person on a CharacterController. Move walks (keys or a stick), Look turns the
// body (yaw) and the camera pivot (pitch), Jump jumps, Interact toggles the nearest gate in reach.

using UnityEngine;
using UnityEngine.InputSystem;

[RequireComponent(typeof(CharacterController))]
public class PlayerController : MonoBehaviour
{
    public InputActionAsset controls;
    public Transform cameraPivot;
    public float moveSpeed = 4f;
    public float jumpHeight = 1.2f;
    public float gravity = 20f;
    public float lookDegreesPerCount = 0.1f;
    public float interactReach = 3f;
    public int jumps;
    public int interactions;

    CharacterController controller;
    float verticalSpeed;
    float pitch;
    Vector3 spawn;
    InputActionMap map;
    InputAction move, look, jump, interact;

    void Awake()
    {
        controller = GetComponent<CharacterController>();
        spawn = transform.position;
        map = controls.FindActionMap("Player", true);
        move = map.FindAction("Move", true);
        look = map.FindAction("Look", true);
        jump = map.FindAction("Jump", true);
        interact = map.FindAction("Interact", true);
    }

    void OnEnable()
    {
        map.Enable();
    }

    void OnDisable()
    {
        map.Disable();
    }

    void Update()
    {
        var turn = look.ReadValue<Vector2>();
        transform.Rotate(0f, turn.x * lookDegreesPerCount, 0f, Space.World);
        pitch = Mathf.Clamp(pitch - turn.y * lookDegreesPerCount, -85f, 85f);
        cameraPivot.localEulerAngles = new Vector3(pitch, 0f, 0f);

        if (interact.WasPressedThisFrame()) Interact();
        if (!controller.enabled) return;
        var input = move.ReadValue<Vector2>();
        var direction = transform.right * input.x + transform.forward * input.y;
        if (direction.sqrMagnitude > 1f) direction.Normalize();
        if (controller.isGrounded && verticalSpeed < 0f) verticalSpeed = -2f;
        if (jump.WasPressedThisFrame() && controller.isGrounded)
        {
            verticalSpeed = Mathf.Sqrt(2f * gravity * jumpHeight);
            jumps++;
        }
        verticalSpeed -= gravity * Time.deltaTime;
        controller.Move((direction * moveSpeed + Vector3.up * verticalSpeed) * Time.deltaTime);
    }

    public void Interact()
    {
        interactions++;
        Gate nearest = null;
        var best = interactReach;
        foreach (var hit in Physics.OverlapSphere(transform.position, interactReach))
        {
            var gate = hit.GetComponentInParent<Gate>();
            if (gate == null) continue;
            var d = Vector3.Distance(transform.position, gate.transform.position);
            if (d > best) continue;
            best = d;
            nearest = gate;
        }
        if (nearest != null) nearest.Toggle();
    }

    public void Respawn()
    {
        controller.enabled = false;
        transform.position = spawn;
        controller.enabled = true;
        verticalSpeed = 0f;
    }
}
