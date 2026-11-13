// `trace` (as Unreal's PlaytestOpsTrace.cpp): the first thing a line, or a ball swept along it, meets between two points
// of the played world, by the scene's 3D physics. What a thing's box can't tell: a big mesh's box is many times what is
// solid of it, a ring or an arch is open through the middle of its box.

using System;
using System.Collections.Generic;
using UnityEngine;

namespace Autopilot.Playtest
{
    public static class Trace
    {
        const float MaxRadius = 100f;  // metres

        /// <summary>{"start", "end": kit {"X","Y","Z"}, "radius"?: cm (0: a line), "channel"?: "body" (the default: the
        /// layers the player's layer collides with, as its moves are) | "sight" (every layer a raycast takes by default)}
        /// -> {"channel", "hit": null | {"location" (where the line stops: the ball's center when it touches), "point"
        /// (where it touches), "normal", "distance" (cm, start to location), "actor" (the collider's object path), "name",
        /// "component" (the collider's type), "inside" (start was already in it)}}. Triggers never stop it, nor the
        /// player's own colliders and what hangs under it.</summary>
        public static void Handle(PlaytestClient client, long id, Dictionary<string, object> args)
        {
#if AUTOPILOT_PHYSICS
            var s = Json.Obj(args, "start");
            var e = Json.Obj(args, "end");
            if (s == null || e == null)
            {
                client.RespondError(id, "trace needs a start and an end ({\"X\", \"Y\", \"Z\"})");
                return;
            }
            var start = Frame.ToUnity(s);
            var end = Frame.ToUnity(e);
            var radius = Mathf.Clamp((float)(Json.Num(args, "radius", 0.0) / Frame.Scale), 0f, MaxRadius);
            var sight = Json.Str(args, "channel") == "sight";
            var player = Player.Find();
            var mask = Physics.DefaultRaycastLayers;
            if (!sight && player != null)
            {
                mask = 0;
                for (var i = 0; i < 32; ++i)
                    if (!Physics.GetIgnoreLayerCollision(player.layer, i))
                        mask |= 1 << i;
                mask &= ~(1 << 2);  // (never Ignore Raycast)
            }
            var d = end - start;
            var dist = d.magnitude;
            var dir = dist > 1e-6f ? d / dist : Vector3.forward;
            RaycastHit? best = null;
            var inside = false;
            if (radius > 0f && dist <= 1e-6f)
            {
                foreach (var c in Physics.OverlapSphere(start, radius, mask, QueryTriggerInteraction.Ignore))
                {
                    if (Skip(c, player)) continue;
                    Respond(client, id, sight, start, start, -dir, 0f, c, true);
                    return;
                }
            }
            else
            {
                var hits = radius > 0f
                    ? Physics.SphereCastAll(start, radius, dir, dist, mask, QueryTriggerInteraction.Ignore)
                    : Physics.RaycastAll(start, dir, dist, mask, QueryTriggerInteraction.Ignore);
                foreach (var h in hits)
                {
                    if (Skip(h.collider, player)) continue;
                    if (best != null && h.distance >= best.Value.distance) continue;
                    best = h;
                }
                if (best != null)
                {
                    var h = best.Value;
                    // (a sphere cast that starts overlapping answers distance 0 and a zero point; a line never meets
                    // what it starts in)
                    inside = radius > 0f && h.distance <= 0f && h.point == Vector3.zero;
                    var location = start + dir * h.distance;
                    var point = inside ? start : h.point;
                    var normal = inside ? -dir : h.normal;
                    Respond(client, id, sight, location, point, normal, h.distance, h.collider, inside);
                    return;
                }
            }
            client.Respond(id, new Dictionary<string, object> { ["channel"] = sight ? "sight" : "body", ["hit"] = null });
#else
            client.RespondError(id, "trace: this game has no 3D physics module");
#endif
        }

#if AUTOPILOT_PHYSICS
        static bool Skip(Collider c, GameObject player)
        {
            return c == null || (player != null && c.transform.IsChildOf(player.transform));
        }

        static void Respond(PlaytestClient client, long id, bool sight, Vector3 location, Vector3 point, Vector3 normal,
            float distance, Collider c, bool inside)
        {
            var hit = new Dictionary<string, object>
            {
                ["location"] = Frame.Point(location),
                ["point"] = Frame.Point(point),
                ["normal"] = Frame.Point(normal / (float)Frame.Scale, 3),  // (a direction: Point's axes, not its scale)
                ["distance"] = Json.Round(distance * Frame.Scale, 1),
                ["actor"] = Paths.Of(c.gameObject),
                ["name"] = c.gameObject.name,
                ["component"] = c.GetType().Name,
                ["inside"] = inside,
            };
            client.Respond(id, new Dictionary<string, object> { ["channel"] = sight ? "sight" : "body", ["hit"] = hit });
        }
#endif
    }
}
