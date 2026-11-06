// Automation tests of when a held key goes down again (Session Frontend > Automation: AutopilotPlaytest.Keys). A live
// check over the bridge counts the game's presses: Autopilot.Test.WatchPresses and Autopilot.Test.Presses.

#include "Misc/AutomationTest.h"
#include "PlaytestOps.h"

#if WITH_DEV_AUTOMATION_TESTS

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPlaytestHeldKeyTest, "AutopilotPlaytest.Keys.HeldKeyGoesDownAgain",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FPlaytestHeldKeyTest::RunTest(const FString& Parameters)
{
	bool bSeenDown = false;
	int32 UpTicks = 0;
	const auto Tick = [&bSeenDown, &UpTicks](bool bDown) { return PlaytestOps::HeldKeyGoesDownAgain(bDown, bSeenDown, UpTicks); };

	// Just pressed: the player's input reads it a frame later, and that isn't the key going up
	for (int32 i = 0; i < 5; ++i)
	{
		TestFalse(TEXT("a press not read yet isn't pressed again"), Tick(false));
	}
	TestFalse(TEXT("read down: nothing to do"), Tick(true));
	TestFalse(TEXT("still down"), Tick(true));

	// The engine let go of every key: down again once, after a full input pass, and not on and on
	TestFalse(TEXT("the pass with the engine's release in it goes by first"), Tick(false));
	TestTrue(TEXT("then it goes down again"), Tick(false));
	for (int32 i = 0; i < 5; ++i)
	{
		TestFalse(TEXT("that press isn't sent again before it is read"), Tick(false));
	}

	// And the same the next time
	TestFalse(TEXT("read down again"), Tick(true));
	TestFalse(TEXT("let go again: the pass goes by"), Tick(false));
	TestTrue(TEXT("down again, a second time"), Tick(false));

	// Down again before the second tick: the engine's blip is over, nothing to press
	Tick(true);
	TestFalse(TEXT("up for one tick"), Tick(false));
	TestFalse(TEXT("back down by itself"), Tick(true));
	TestFalse(TEXT("the count starts over"), Tick(false));
	return true;
}

#endif
