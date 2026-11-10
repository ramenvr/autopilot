-- Load under ServerStorage.UnitTest.Cases with the plugin sources in UnitTest.Modules.
-- These cases use native Roblox values and in-memory plugin settings; no network or account access.
return function(t)
	local root = script.Parent.Parent.Modules
	local Values = require(root.Values)
	local Paths = require(root.Paths)
	local Settings = require(root.Settings)
	local Editor = require(root.Editor)
	local Host = require(root.Host)

	-- Serialized orientation survives a read/write round trip and position-only editing.
	t.test("preserves rolled CFrames", function()
		local original = CFrame.new(1, 2, 3) * CFrame.fromEulerAnglesYXZ(0.3, 0.4, 1.1)
		local encoded = Values.toJson(original)
		encoded.position = { x = 10, y = 20, z = 30 }
		local restored = Values.fromJson(encoded, original)
		t.expect.near((restored.UpVector - original.UpVector).Magnitude, 0, 0.01)
		t.expect.near((restored.LookVector - original.LookVector).Magnitude, 0, 0.01)
		t.expect.equal(restored.Position, Vector3.new(10, 20, 30))
	end)

	-- Discovered duplicate paths resolve to their own instances, never ordinal guesses.
	t.test("resolves discovered duplicate sibling paths", function()
		local folder = Instance.new("Folder")
		folder.Name = "AutopilotPathRegression"
		folder.Parent = workspace
		local ok, err = pcall(function()
			local a, b = Instance.new("Part"), Instance.new("Part")
			a.Name, b.Name = "Coin", "Coin"
			a.Parent, b.Parent = folder, folder
			local pa, pb = Paths.of(a), Paths.of(b)
			t.expect.truthy(pa ~= pb and string.find(pa, "Coin@") and string.find(pb, "Coin@"))
			t.expect.equal(Paths.resolve(pa), a)
			t.expect.equal(Paths.resolve(pb), b)
			a:Destroy()
			t.expect.equal(Paths.resolve(pa), nil)
			t.expect.equal(Paths.resolve(pb), b)
		end)
		folder:Destroy()
		assert(ok, err)
	end)

	-- No cached Studio default can enable sharing before the service preference arrives.
	t.test("synchronizes privacy before allowing runs", function()
		local values, sent = {}, {}
		local plugin = { GetSetting = function(_, k) return values[k] end,
			SetSetting = function(_, k, v) values[k] = v end }
		t.expect.equal(Settings.get(plugin, "share"), false)
		local editor = setmetatable({ plugin = plugin, privacyReady = false, listeners = {}, account = {},
			link = { connected = true, role = "service", transport = "test", send = function(_, f)
				table.insert(sent, f)
				return true
			end } }, Editor)
		Settings.set(plugin, "share", true) -- an old Studio cache must not opt in before synchronization
		editor:_hello()
		t.expect.equal(sent[1].share, false)
		table.clear(sent)
		t.expect.equal(editor:startRun({ goal = "test" }), false)
		t.expect.equal(#sent, 0)
		editor:_onBrainFrame({ type = "account", share_data = false })
		t.expect.equal(sent[#sent].share, false)
		t.expect.truthy(editor.privacyReady)
		t.expect.truthy(editor:startRun({ goal = "test" }))
		editor:setSharing(true)
		t.expect.equal(sent[#sent].type, "save_preferences")
		t.expect.equal(Settings.get(plugin, "share"), false) -- wait for durable confirmation
		editor:_onBrainFrame({ type = "account", share_data = true })
		t.expect.equal(Settings.get(plugin, "share"), true)
		t.expect.truthy(editor.runActive) -- settings synchronization must not interrupt the run
		local count = #sent
		editor:_onBrainFrame({ type = "account", share_data = true })
		t.expect.equal(#sent, count) -- no hello/account response loop
	end)

	-- Detach releases held input state and clears queued events even when repeated.
	t.test("detach clears held inputs and events", function()
		local held = { W = true }
		local host = setmetatable({ side = "client", attached = true, events = { { kind = "log" } },
			onDetach = function() table.clear(held) end }, Host)
		host:_onFrame({ to = "client", type = "detach" })
		t.expect.equal(next(held), nil)
		t.expect.equal(#host.events, 0)
		t.expect.equal(host.attached, false)
		host:_detach()
		t.expect.equal(next(held), nil)
	end)
end
