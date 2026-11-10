# Roblox test place

A tiny place for trying Autopilot's Roblox engine (`autopilot/engines/roblox_connector.py`). It is a
[Rojo](https://rojo.space) project, so the whole place is in text:

- **An obby**: three checkpoint platforms (tag `Checkpoint`, attribute `Stage` 1-3), each higher than the last, with a
  lava strip (tag `Hazard`) between stages 2 and 3. Touching a checkpoint raises `leaderstats.Stage`, and the
  character respawns on its last checkpoint.
- **A coin** (tag `Coin`): touching it adds 1 to `leaderstats.Coins`, and the coin is gone.
- **A lever** (tag `Lever`, a ProximityPrompt: hold E for 0.5 s within 10 studs) that toggles **the door** (tag
  `Door`, attribute `Open`).
- **A HUD** (`StarterGui/Hud`): `Coins: N`, `Stage: N`, and a message the server sends with the `StageReached`
  RemoteEvent.
- **Debug helpers** (`ServerScriptService/Debug`, a ModuleScript): `GiveCoin(name, amount)`, `SetPosition(name, x, y,
  z)`, `SetStage(name, stage)`, `Reset(name)`.

## Open it in Studio

```bash
rojo build examples/roblox_testplace -o TestPlace.rbxlx   # or `rojo serve` with the Rojo Studio plugin
```

Open `TestPlace.rbxlx` in Roblox Studio and turn on Studio's MCP server (see [docs/roblox-mcp.md](../../docs/roblox-mcp.md)).

## Run the bench suite

```bash
AUTOPILOT_ENGINE=roblox-mcp python bench/run_autopilot.py --project examples/roblox_testplace --suite bench/suites/roblox_testplace.json
```

The suite's prompts have not been run against a live Studio yet: their expected verdicts are what the place's code
says.
