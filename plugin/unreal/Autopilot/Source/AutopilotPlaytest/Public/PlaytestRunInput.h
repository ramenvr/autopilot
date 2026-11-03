#pragma once

#include "CoreMinimal.h"

struct FKeyEvent;

/**
 * A run's input is the run's alone (as Unity's: Hands.cs, IsolateDevices). While a run plays, the keyboard, mouse and
 * gamepads of the person at the computer don't reach the game, and the game's view doesn't take their mouse: see
 * PlaytestRunInput.cpp. The editor stays theirs.
 */
namespace PlaytestRunInput
{
	/** Every frame (the bridge's tick): whether a run plays now (connected, a run going, a game in play). On: the
	 *  person's input is held off the game and the game's view lets the mouse go; off: what that changed is put back. */
	AUTOPILOTPLAYTEST_API void Update(bool bRunPlaying);

	/** The bridge is going away: off, and the input pre-processor unregistered. */
	AUTOPILOTPLAYTEST_API void Shutdown();

	/** Whether the person's input is held off the game now. */
	AUTOPILOTPLAYTEST_API bool IsIsolating();

	/** Tests: on or off whatever the bridge says (unset: as it says again). Takes effect at once. */
	AUTOPILOTPLAYTEST_API void SetForTests(TOptional<bool> bOn);

	/** The editor's own keys while a game plays (its Stop shortcut, Escape by default): set by the editor module.
	 *  During a run such a key does what the editor does with it (stops play) and returns true; the game doesn't get it
	 *  either way. */
	AUTOPILOTPLAYTEST_API TFunction<bool(const FKeyEvent&)>& EditorKey();

	/** Events the run itself sends through Slate (the pointer's clicks and drags on the game's UI) while one of these is
	 *  in scope: they reach the game as before. */
	struct AUTOPILOTPLAYTEST_API FOwnEvents
	{
		FOwnEvents();
		~FOwnEvents();
		FOwnEvents(const FOwnEvents&) = delete;
		FOwnEvents& operator=(const FOwnEvents&) = delete;
	};
}
