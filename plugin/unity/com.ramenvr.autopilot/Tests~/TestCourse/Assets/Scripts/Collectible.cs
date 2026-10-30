// An orb on the test course: it spins, and the player picks it up by walking into it.

using UnityEngine;

public class Collectible : MonoBehaviour
{
    public float spinDegreesPerSecond = 90f;
    public int value = 1;

    void Update()
    {
        transform.Rotate(0f, spinDegreesPerSecond * Time.deltaTime, 0f, Space.World);
    }

    void OnTriggerEnter(Collider other)
    {
        if (!other.CompareTag("Player")) return;
        var counter = FindAnyObjectByType<OrbCounter>();
        if (counter != null) counter.Add(value);
        Destroy(gameObject);
    }
}
