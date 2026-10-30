// OpenCode, embedded, for autopilot/agents/opencode.py: one session on one game folder.
//
// stdin:  the first line is the run's config (JSON): {directory, provider, model, variant?, fast?, system,
//         permissions, keep_tools, tools: [{name, description, input}], tools_url, base_url?}. Every later line is a
//         command:
//         {"type": "prompt", "text"} (a user message: the request, or what the user typed to steer),
//         {"type": "interrupt"} (end the turn running now), {"type": "close"} (end; also on stdin's end).
// stdout: one JSON event a line: ready {session, model, variant}, text {text}, tool {name, input},
//         tool_failed {name, error}, step {cost, tokens, finish}, usage {cost, tokens}, retry {...},
//         idle {outcome, cost, tokens, error?} (the session waits for a prompt), error {message, fatal?}.
//
// The game's tools are this plugin's tools: each POSTs its input to <tools_url>/<name>, where Autopilot runs it, and
// returns the text. OpenCode's own tools are cut to keep_tools (Autopilot passes reading, searching and editing), and the
// agent's permissions (made by Autopilot) hold them to the game's folder. The environment Autopilot starts this in points
// OpenCode's config, data, cache and home at a temporary folder, so the user's own OpenCode setup is never loaded.

import { OpenCode } from "@opencode/sdk"
import { Plugin } from "@opencode/plugin"
import { createInterface } from "node:readline"

type Tool = { name: string; description: string; input: Record<string, unknown> }
type Config = {
  directory: string
  provider: string
  model: string
  variant?: string
  fast?: boolean
  system: string
  permissions: { action: string; resource: string; effect: string }[]
  keep_tools: string[]
  tools: Tool[]
  tools_url: string
  base_url?: string | null // the provider's API root, when it isn't the provider's own (e.g. a proxy)
}

const out = (event: Record<string, unknown>) => process.stdout.write(JSON.stringify(event) + "\n")
const short = (value: unknown, n = 600) => {
  const text = typeof value === "string" ? value : JSON.stringify(value) ?? ""
  return text.length <= n ? text : text.slice(0, n) + "..."
}
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })[Symbol.asyncIterator]()
const first = await lines.next()
if (first.done) process.exit(0)
const cfg: Config = JSON.parse(first.value)
const location = { directory: cfg.directory }

// A game tool: Autopilot runs it (it may take minutes: an episode of play). Ended early when OpenCode aborts the call
async function callGame(name: string, input: unknown, signal: AbortSignal) {
  try {
    const response = await fetch(`${cfg.tools_url}/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input ?? {}),
      signal,
      timeout: false, // (Bun: no time limit of its own; Autopilot's budget ends the run)
    } as RequestInit)
    const text = await response.text()
    return { content: response.ok ? text : `Tool failed: HTTP ${response.status}: ${text}` }
  } catch (error) {
    return { content: `Tool failed: ${error}` }
  }
}

const plugin = Plugin.define({
  id: "autopilot",
  async setup(ctx) {
    await ctx.tool.transform((editor) => {
      for (const tool of editor.list()) if (!cfg.keep_tools.includes(tool.id)) editor.remove(tool.id)
      for (const tool of cfg.tools)
        editor.add({
          name: tool.name,
          description: tool.description,
          input: tool.input,
          options: { codemode: false }, // (called directly, not through Code Mode's JavaScript)
          execute: (input: unknown, context: { signal: AbortSignal }) => callGame(tool.name, input, context.signal),
        } as any)
    })
    await ctx.agent.transform((editor) => {
      editor.remove("title") // (no session title: a model call on the whole request for nothing)
      editor.update("build", (agent) => {
        agent.system = cfg.system
        agent.permissions = cfg.permissions as any // (replaces the defaults: what isn't allowed is denied)
      })
    })
  },
})

const opencode: any = await OpenCode.create({
  plugins: [plugin],
  config: {
    project: false, // (no opencode.json, AGENTS.md or .claude/ from the game's folder or its parents)
    content: JSON.stringify({
      snapshots: false, // (no undo snapshots: a git copy of the checkout the game is in)
      ...(cfg.base_url ? { provider: { [cfg.provider]: { options: { baseURL: cfg.base_url } } } } : {}),
    }),
  },
  log: { level: "warn", emit: (entry) => process.stderr.write(`[${entry.level}] ${entry.message}\n`) },
} as any)

// The provider's models appear once its catalog is loaded (a second or two after the start)
async function resolveModel() {
  let offered: string[] = []
  for (let i = 0; i < 60; i++) {
    const models = (await opencode.model.list({ location })).data.filter((m: any) => m.providerID === cfg.provider)
    offered = models.map((m: any) => m.id)
    const base = models.find((m: any) => m.id === cfg.model)
    if (base) {
      const fast = cfg.fast ? models.find((m: any) => m.id === `${cfg.model}-fast`) : undefined
      const model = fast ?? base
      const variant = (model.variants ?? []).some((v: any) => v.id === cfg.variant) ? cfg.variant : undefined
      return { providerID: cfg.provider, id: model.id, ...(variant ? { variant } : {}) }
    }
    await sleep(500)
  }
  throw new Error(`no model ${cfg.provider}/${cfg.model} (is the provider's key set?); offered: ${offered.join(", ")}`)
}

const events = new AbortController()
let sessionID = ""

async function follow() {
  const names = new Map<string, string>() // tool call id -> tool name
  for await (const ev of opencode.events.subscribe({ signal: events.signal })) {
    const d = ev.data ?? {}
    if (process.env.AUTOPILOT_HOST_DEBUG && !ev.type.endsWith(".delta")) process.stderr.write(`${short(ev, 2000)}\n`)
    if (d.sessionID !== sessionID) continue
    switch (ev.type) {
      case "session.tool.input.started":
        names.set(d.id, d.name)
        break
      case "session.tool.called":
        out({ type: "tool", name: names.get(d.id) ?? "?", input: short(d.input) })
        break
      case "session.tool.failed":
        out({ type: "tool_failed", name: names.get(d.id) ?? "?", error: short(d.error ?? d, 1000) })
        break
      case "session.text.ended":
        if (d.text?.trim()) out({ type: "text", text: d.text })
        break
      case "session.step.ended":
        out({ type: "step", cost: d.cost ?? 0, tokens: d.tokens ?? {}, finish: d.finish })
        break
      case "session.step.failed":
        out({ type: "error", message: short(d.error ?? d, 2000) })
        break
      case "session.usage.updated":
        out({ type: "usage", cost: d.cost ?? 0, tokens: d.tokens ?? {} })
        break
      case "session.retry.scheduled":
        out({ type: "retry", info: short(d, 1000) })
        break
    }
  }
}

// After a prompt: wait until the session is idle again, then say how it ended
let waiting = false
async function waitIdle() {
  if (waiting) return
  waiting = true
  try {
    await opencode.session.wait({ sessionID })
    const info = await opencode.sessions.get({ sessionID })
    const idle: Record<string, unknown> = { type: "idle", outcome: info.outcome, cost: info.cost, tokens: info.tokens }
    if (info.outcome !== "succeeded") {
      const messages = (await opencode.message.list({ sessionID })).data ?? []
      const failed = messages.find((m: any) => m.type === "assistant" && m.error)
      if (failed) idle.error = short(failed.error, 2000)
    }
    out(idle)
  } catch (error) {
    out({ type: "error", message: String(error) })
  } finally {
    waiting = false
  }
}

async function close(code = 0) {
  events.abort()
  try {
    await opencode.close()
  } catch {}
  process.exit(code)
}

try {
  const model = await resolveModel()
  const session = await opencode.sessions.create({ location, model })
  sessionID = session.id
  follow().catch(() => {}) // (ends with an error once the host closes)
  out({ type: "ready", session: sessionID, model: model.id, variant: model.variant ?? null })
  for (let line = await lines.next(); !line.done; line = await lines.next()) {
    if (!line.value.trim()) continue
    const command = JSON.parse(line.value)
    if (command.type === "prompt") {
      await opencode.sessions.prompt({ sessionID, text: command.text })
      void waitIdle()
    } else if (command.type === "interrupt") {
      await opencode.session.interrupt({ sessionID }).catch((e: unknown) => out({ type: "error", message: String(e) }))
    } else if (command.type === "close") {
      break
    }
  }
  await close(0)
} catch (error) {
  out({ type: "error", fatal: true, message: String((error as any)?.stack ?? error) })
  await close(1)
}
