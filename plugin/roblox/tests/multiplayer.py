"""Two players in the test game (tests/testgame.luau), one brain: Studio's multiplayer test (a server and two clients,
each a Studio process with the plugin), player 1 played as always and player 2 through requests with "player": 2
(the kit's game.player(2)). The basket goes from player 1 to player 2 and onto the cart, which only takes a basket that
was handed over; the planted bug shows on player 2's screen (its HUD never hears it carries the basket). Run it with
Studio open on the test game (see harness.py):

    python plugin/roblox/tests/multiplayer.py
"""

import sys
import time

from harness import BridgeError, brain, check, expect, summary, until

from autopilot.kit.game import Game


def main():
    link = brain()
    req = link.request
    check("hello: feature players", "players" in (link.hello.get("features") or []), str(link.hello.get("features")))
    game = Game(link)
    two = game.player(2)

    started = expect("session start with two players (Studio's multiplayer test)",
                     lambda: req("session", {"action": "restart", "players": 2}, timeout=180),
                     lambda r: r["running"] is True and r["players"] == 2)
    if not started:
        return summary()
    ctx = expect("context: two players, you are 1", lambda: game.context(fresh=True),
                 lambda r: r["you"] == 1 and [p["player"] for p in r["players"]] == [1, 2])
    ctx2 = expect("player 2's context: its own character", lambda: two.context(fresh=True),
                  lambda r: r["you"] == 2 and r["character"] and r["character"] != ctx["character"])
    try:
        req("view", {"max_count": 1, "player": 3})
        check("player 3 is refused (no such player)", False)
    except BridgeError as e:
        check("player 3 is refused (no such player)", e.status == 404 and "No player 3" in e.error, e.error)

    def basket(prop):
        return req("rc", {"verb": "property", "body": {"objectPath": "/Workspace/Grove/Basket", "propertyName": prop}})[prop]

    def cart(prop):
        return req("rc", {"verb": "property", "body": {"objectPath": "/Workspace/Grove/Cart", "propertyName": prop}})[prop]

    # player 1 picks the basket up
    _place(req, 1, (0, 3, 17))
    _place(req, 2, (3, 3, 18))
    req("key", {"key": "E", "mode": "press"})
    expect("player 1 picks the basket up (E)", lambda: until(lambda: basket("Carrier") == ctx["players"][0]["name"], 3),
           lambda r: r)
    # what player 1 holds: the basket, welded to its root part (and no longer a thing it sees)
    v = until(lambda: (x := game.view(max_count=30)) and x.get("held") and x, 3) or {}
    check("player 1's view holds the basket (welded on)", [h["name"] for h in v.get("held", [])] == ["Basket"]
          and v["held"][0].get("attached_at") == "HumanoidRootPart"
          and not any(t["name"] == "Basket" for t in v.get("visible", [])), str(v.get("held")))
    # the cart refuses it from the one who picked it up
    _place(req, 1, (-20, 3, 23))
    time.sleep(0.5)
    req("key", {"key": "E", "mode": "press"})
    expect("the cart refuses a basket that wasn't handed over", lambda: until(lambda: cart("Refused"), 3),
           lambda r: "handed over" in r)
    # player 1 hands it to player 2 (G at them)
    _place(req, 1, (0, 3, 17))
    _place(req, 2, (3, 3, 18))
    req("key", {"key": "G", "mode": "press"})
    name2 = ctx2["players"][1]["name"] if ctx2 else "Player2"
    expect("player 1 hands the basket to player 2 (G)", lambda: until(lambda: basket("Carrier") == name2, 3),
           lambda r: r)
    check("... handed over", basket("HandedOver") is True)
    v2 = until(lambda: (x := two.view(max_count=30)) and x.get("held") and x, 3) or {}
    check("... player 2's view holds it now", [h["name"] for h in v2.get("held", [])] == ["Basket"], str(v2.get("held")))
    expect("player 1's HUD says player 2 carries it", lambda: until(
        lambda: game.screen().get("HUD.BasketLabel") == f"Basket: {name2} carries it", 3), lambda r: r)
    s2 = two.screen()
    check("the planted bug: player 2's HUD still says on the ground", s2.get("HUD.BasketLabel") ==
          "Basket: on the ground", str(s2))
    # player 2 sees player 1 (another player's character)
    v = two.view(max_count=10)
    check("player 2's view: player 1 is another player", any(t.get("player") for t in v.get("visible", [])),
          str([(t.get("name"), t.get("player")) for t in v.get("visible", [])]))
    # player 2 loads the cart (its own keys)
    _place(req, 2, (-20, 3, 23))
    time.sleep(0.5)
    two.press("E")
    expect("player 2 loads the cart (E, player 2's own input)", lambda: until(lambda: cart("Loaded"), 3),
           lambda r: r is True)
    check("... loaded by player 2", cart("LoadedBy") == name2)
    # keys held for player 2 are its own (in the open, away from the cart: its speed while held, and player 1 stands)
    _place(req, 2, (12, 3, 34))
    one_before = game.pose()["at"]
    two.hold("W")
    speed = 0.0
    for _ in range(5):
        time.sleep(0.3)
        game.renew()
        speed = max(speed, two.pose()["velocity"].length2d())
    two.release("W")
    check("W held for player 2 walks player 2", speed > 200, f"{speed:.0f} cm/s")
    check("... and not player 1", game.pose()["at"].dist2d(one_before) < 50)
    time.sleep(0.5)
    warned = [e["finding"]["summary"] for e in link.events
              if e.get("kind") == "finding" and e["finding"].get("detector") == "no_input"]
    check("no warning that a key didn't reach the game (E, G, W for both players)", not warned, str(warned))

    if "media" in (link.hello.get("features") or []):
        expect("capture: a picture (player 1's)", lambda: req("capture", {"max_dim": 480}, timeout=60),
               lambda r: r["jpeg"] and r["width"] > 0)

    expect("back to one player", lambda: req("session", {"action": "restart", "players": 1}, timeout=120),
           lambda r: r["running"] is True and r["players"] == 1)
    expect("session stop", lambda: req("session", {"action": "stop"}, timeout=60), lambda r: r["running"] is False)
    link.server.shutdown()
    return summary()


def _place(req, player, at):
    """Put a player's character at a spot (Roblox's x, y, z): a test's setup, a direct change."""
    req("rc", {"verb": "property", "player": player, "body": {"objectPath": "Character", "propertyName": "position",
               "propertyValue": {"position": {"x": at[0], "y": at[1], "z": at[2]}}, "access": "WRITE_ACCESS"}})
    time.sleep(0.6)


if __name__ == "__main__":
    sys.exit(main())
