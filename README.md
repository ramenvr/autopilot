# Aura Autopilot

> By the developers of Aura, [www.tryaura.dev](https://www.tryaura.dev/)

![Aura by Ramen](./assets/aura-logo.png)

Autopilot is a continually learning real-time general playtesting system. Autopilot learns your game, explores your levels, and improves its gameplay run after run.

Docs: https://www.tryaura.dev/documentation/autopilot

Official Discord: https://discord.gg/tryaura

## Things to Know

An Autopilot prompt is one of 4 types:

- **Exploration**, e.g. "Explore the crafting system in Lvl_Main."
- **Testing**, e.g. "Go through the currently open level and tell me everything that looks wrong."
- **Verification**, e.g. "Verify that the tree shakes when you hit it with an axe."
- **Goal**, "Kill the boss while keeping your health above 20% of its maximum value."

For every prompt, Autopilot will automatically launch a PIE window and start reading your project's source code, probe PIE, and start playing according to your prompt. Autopilot plays by using an LLM coding agent to craft harnesses for a decision model according to what it knows about your game, and requests decisions (action choices) from the decision model during gameplay. Once Autopilot is done, the PIE window will close automatically, and Autopilot generates a report for you.

Your first few runs allow Autopilot to learn about your game as it gradually populates its memory. Expect some suboptimal behavior for the first few harness attempts. **Autopilot visibly improves the more it plays your game**, and later harness attempts and runs both take a shorter time to complete a given task, as well as exhibit more intelligent behavior (e.g. faster dodging, equipping better weapons, playing smarter strategies, better aiming).

## Contributing

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up a development environment, run the tests, and add support for a new engine. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[Apache License 2.0](LICENSE). The plugins bundle fonts under the SIL Open Font License; see each font's `OFL` file.
