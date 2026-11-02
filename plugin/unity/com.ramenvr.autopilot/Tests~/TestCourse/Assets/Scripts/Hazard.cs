// The level's misbehaviours for the detectors and watch to catch: numbers out of their range, a player moved across
// the level in one frame, objects piling up, and a lamp, sparks and a beacon going out.

using System.Collections;
using UnityEngine;

public class Hazard : MonoBehaviour
{
    [Range(0, 100)] public float stamina = 50;
    public int health = 100;
    public int maxHealth = 100;
    public Light lamp;
    public ParticleSystem sparks;
    public Renderer beacon;

    /// <summary>A moment later (not the frame it was asked in), the player is moved 17 m in one frame.</summary>
    public void Blink()
    {
        StartCoroutine(BlinkLater());
    }

    IEnumerator BlinkLater()
    {
        yield return new WaitForSeconds(0.3f);
        var player = GameObject.FindWithTag("Player");
        var body = player.GetComponent<CharacterController>();
        body.enabled = false;
        player.transform.position = new Vector3(12f, 1.08f, 12f);
        body.enabled = true;
    }

    /// <summary>Sparks, piling up: `perSecond` empty objects a second for `seconds`.</summary>
    public void Spawn(int perSecond, float seconds)
    {
        StartCoroutine(SpawnFor(perSecond, seconds));
    }

    IEnumerator SpawnFor(int perSecond, float seconds)
    {
        for (var t = 0f; t < seconds; t += 0.1f)
        {
            for (var i = 0; i < Mathf.Max(1, perSecond / 10); ++i) new GameObject("Spark");
            yield return new WaitForSeconds(0.1f);
        }
    }

    /// <summary>A moment later: the lamp off, the sparks stopped, the beacon's mesh hidden.</summary>
    public void GoOut()
    {
        StartCoroutine(GoOutLater());
    }

    IEnumerator GoOutLater()
    {
        yield return new WaitForSeconds(0.5f);
        lamp.enabled = false;
        sparks.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
        beacon.enabled = false;
    }
}
