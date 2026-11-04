// A run's input is the run's alone (PlaytestRunInput): in play, the person's keys, gamepad and mouse go in the way the
// operating system's do (FSlateApplication's message handler: OnKeyDown, OnControllerAnalog, OnMouseDown) and must not
// reach the game while a run plays, but must between runs; the game's view doesn't take the mouse during the run and
// gets its own mouse settings back after; the run's own Slate events (FOwnEvents) still reach the game's UI.
// Plays the level open in the editor (PIE) when nothing plays yet, and stops it after.

#include "Editor.h"
#include "Editor/EditorEngine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Framework/Application/SlateApplication.h"
#include "GameFramework/PlayerController.h"
#include "GenericPlatform/GenericPlatformInputDeviceMapper.h"
#include "Misc/AutomationTest.h"
#include "PlayInEditorDataTypes.h"
#include "PlaytestRunInput.h"
#include "Slate/SceneViewport.h"
#include "Tests/AutomationCommon.h"
#include "Widgets/SViewport.h"
#include "Widgets/SWindow.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	APlayerController* PlayController()
	{
		UWorld* World = GEditor ? GEditor->PlayWorld.Get() : nullptr;
		return World ? World->GetFirstPlayerController() : nullptr;
	}

	UGameViewportClient* PlayView()
	{
		UWorld* World = GEditor ? GEditor->PlayWorld.Get() : nullptr;
		return World ? World->GetGameViewport() : nullptr;
	}

	/** Whether a key got to the game's view (FSceneViewport keeps it before anything else; a view of no size, a game
	 *  that doesn't render, then doesn't pass it on to the player controller) or to the player controller. */
	bool ReachedGame(const FKey& Key)
	{
		UGameViewportClient* View = PlayView();
		FSceneViewport* Scene = View ? View->GetGameViewport() : nullptr;
		APlayerController* PC = PlayController();
		return (Scene && Scene->KeyState(Key)) || (PC && PC->IsInputKeyDown(Key));
	}

	/** The person presses and lets go of W, as the keyboard would: whether the game had it down in between. */
	bool PersonKeyReaches(FAutomationTestBase& Test)
	{
		FSlateApplication& Slate = FSlateApplication::Get();
		Slate.SetAllUserFocusToGameViewport();  // (as a click into the view would: the keys go to the game)
		Slate.OnKeyDown('W', 'W', false);
		const bool bDown = ReachedGame(EKeys::W);
		Slate.OnKeyUp('W', 'W', false);
		Test.TestFalse(TEXT("W is up again"), ReachedGame(EKeys::W));
		return bDown;
	}

	/** The person's gamepad stick, pushed and let go. Each check takes its own stick: the view keeps an axis it has seen
	 *  as down from then on. */
	bool PersonStickReaches(FGamepadKeyNames::Type Name, const FKey& Key)
	{
		FSlateApplication& Slate = FSlateApplication::Get();
		IPlatformInputDeviceMapper& Mapper = IPlatformInputDeviceMapper::Get();
		const FPlatformUserId User = Mapper.GetPrimaryPlatformUser();
		const FInputDeviceId Device = Mapper.GetDefaultInputDevice();
		Slate.SetAllUserFocusToGameViewport();
		Slate.OnControllerAnalog(Name, User, Device, 0.9f);
		const bool bReached = ReachedGame(Key);
		Slate.OnControllerAnalog(Name, User, Device, 0.f);
		return bReached;
	}
}

// What the engine does to the keys when the game's view loses the focus, on demand (a live check of a run's held keys)
static FAutoConsoleCommand GAutopilotTestFlushKeys(TEXT("Autopilot.Test.FlushKeys"),
	TEXT("Lets go of every key of the game being played, as the engine does when its view loses the focus."),
	FConsoleCommandDelegate::CreateLambda([]()
	{
		if (APlayerController* PC = PlayController())
		{
			PC->FlushPressedKeys();
		}
	}));

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAutopilotRunInputTest, "AutopilotPlaytest.Editor.RunInput",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAutopilotRunInputTest::RunTest(const FString& Parameters)
{
	if (!FSlateApplication::IsInitialized() || !GEditor)
	{
		AddError(TEXT("needs the editor"));
		return false;
	}
	const bool bStarted = GEditor->PlayWorld == nullptr;
	if (bStarted)
	{
		GEditor->RequestPlaySession(FRequestPlaySessionParams());
	}
	const double Deadline = FPlatformTime::Seconds() + 60.0;
	const double PawnDeadline = FPlatformTime::Seconds() + 20.0;
	ADD_LATENT_AUTOMATION_COMMAND(FFunctionLatentCommand([this, Deadline, PawnDeadline]()
	{
		APlayerController* PC = PlayController();
		if (PC && PlayView() && PlayView()->GetGameViewportWidget().IsValid()
			&& (PC->GetPawn() || FPlatformTime::Seconds() > PawnDeadline))  // (a level with no player, a menu, is tested as it is)
		{
			return true;
		}
		if (FPlatformTime::Seconds() > Deadline)
		{
			AddError(TEXT("play didn't start in 60 s"));
			return true;
		}
		return false;
	}));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitLatentCommand(12.0));  // (a game's loading screen: its own input mode and mouse capture come after)
	ADD_LATENT_AUTOMATION_COMMAND(FFunctionLatentCommand([this]()
	{
		UGameViewportClient* View = PlayView();
		if (!View || !PlayController())
		{
			return true;
		}
		FSlateApplication& Slate = FSlateApplication::Get();
		PlaytestRunInput::SetForTests(false);  // (between runs; a run going now has the game's mouse settings off: back first)
		const EMouseCaptureMode GameCapture = View->GetMouseCaptureMode();
		const EMouseLockMode GameLock = View->GetMouseLockMode();
		AddInfo(FString::Printf(TEXT("the game's own mouse: capture %d, lock %d, hidden during capture %d"),
			(int32)GameCapture, (int32)GameLock, View->HideCursorDuringCapture() ? 1 : 0));
		TSharedPtr<SViewport> ViewWidget = View->GetGameViewportWidget();
		Slate.SetAllUserFocusToGameViewport();
		const TSharedPtr<SWidget> Focused = Slate.GetUserFocusedWidget(0);
		AddInfo(FString::Printf(TEXT("focus on %s (the view: %d), view ignores input %d, view holds the mouse %d, raw mouse input %d"),
			Focused.IsValid() ? *Focused->GetTypeAsString() : TEXT("nothing"), Focused == ViewWidget ? 1 : 0, View->IgnoreInput() ? 1 : 0,
			ViewWidget.IsValid() && ViewWidget->HasMouseCapture() ? 1 : 0, Slate.IsUsingHighPrecisionMouseMovment() ? 1 : 0));

		// Between runs: the person's input reaches the game
		TestTrue(TEXT("between runs the person's key reaches the game"), PersonKeyReaches(*this));
		TestTrue(TEXT("between runs the person's stick reaches the game"),
			PersonStickReaches(FGamepadKeyNames::LeftAnalogX, EKeys::Gamepad_LeftX));

		// A run plays: none of it does, and the view lets the mouse go
		PlaytestRunInput::SetForTests(true);
		TestTrue(TEXT("isolating"), PlaytestRunInput::IsIsolating());
		TestFalse(TEXT("in a run the person's key doesn't reach the game"), PersonKeyReaches(*this));
		TestFalse(TEXT("in a run the person's stick doesn't reach the game"),
			PersonStickReaches(FGamepadKeyNames::RightAnalogX, EKeys::Gamepad_RightX));
		TestEqual(TEXT("in a run the view doesn't capture the mouse"), (int32)View->GetMouseCaptureMode(), (int32)EMouseCaptureMode::NoCapture);
		TestEqual(TEXT("in a run the view doesn't lock the mouse"), (int32)View->GetMouseLockMode(), (int32)EMouseLockMode::DoNotLock);
		TSharedPtr<SViewport> Widget = View->GetGameViewportWidget();
		TSharedPtr<SWindow> Window = Widget.IsValid() ? Slate.FindWidgetWindow(Widget.ToSharedRef()) : nullptr;
		const FGeometry& Geometry = Widget.IsValid() ? Widget->GetTickSpaceGeometry() : FGeometry();
		if (Window.IsValid() && Window->GetNativeWindow().IsValid() && Geometry.GetLocalSize().X >= 1.f)
		{
			// The person clicks in the middle of the game's view: the game gets no button, and no capture follows
			const FVector2D Middle(Geometry.LocalToAbsolute(Geometry.GetLocalSize() * 0.5f));
			Slate.OnMouseDown(Window->GetNativeWindow(), EMouseButtons::Left, Middle);
			APlayerController* PC = PlayController();
			FSceneViewport* Scene = View->GetGameViewport();
			TestFalse(TEXT("in a run the person's click doesn't reach the game's view"), Scene && Scene->KeyState(EKeys::LeftMouseButton));
			TestFalse(TEXT("in a run the person's click doesn't reach the game"), PC && PC->IsInputKeyDown(EKeys::LeftMouseButton));
			TestFalse(TEXT("in a run the click doesn't capture the mouse"), Widget->HasMouseCapture());
			TestFalse(TEXT("in a run the click doesn't turn on raw mouse input"), Slate.IsUsingHighPrecisionMouseMovment());
			Slate.OnMouseUp(EMouseButtons::Left, Middle);
			TestFalse(TEXT("the click's release was held off too"), PC && PC->IsInputKeyDown(EKeys::LeftMouseButton));

			// The run's own click (the pointer's, on the game's UI) still goes through Slate to the view
			{
				PlaytestRunInput::FOwnEvents Own;
				const FPointerEvent Down(IPlatformInputDeviceMapper::Get().GetDefaultInputDevice(), FSlateApplication::CursorPointerIndex,
					Middle, Middle, TSet<FKey>({EKeys::LeftMouseButton}), EKeys::LeftMouseButton, 0.f, FModifierKeysState());
				const FPointerEvent Up(IPlatformInputDeviceMapper::Get().GetDefaultInputDevice(), FSlateApplication::CursorPointerIndex,
					Middle, Middle, TSet<FKey>(), EKeys::LeftMouseButton, 0.f, FModifierKeysState());
				Slate.ProcessMouseButtonDownEvent(nullptr, Down);
				TestTrue(TEXT("the run's own click reaches the game's view"), Scene && Scene->KeyState(EKeys::LeftMouseButton));
				Slate.ProcessMouseButtonUpEvent(Up);
			}
			PlaytestRunInput::SetForTests(true);  // (lets go of anything that click took)
			TestFalse(TEXT("nothing holds the mouse after the run's click"), Widget->HasMouseCapture());
		}
		else
		{
			AddInfo(TEXT("the game's view isn't laid out (no picture): the mouse checks are skipped"));
		}

		// The run is over: the game's own mouse settings come back, and the person's input reaches the game again
		PlaytestRunInput::SetForTests(false);
		TestFalse(TEXT("not isolating after the run"), PlaytestRunInput::IsIsolating());
		TestEqual(TEXT("the game's capture mode is back"), (int32)View->GetMouseCaptureMode(), (int32)GameCapture);
		TestEqual(TEXT("the game's lock mode is back"), (int32)View->GetMouseLockMode(), (int32)GameLock);
		TestTrue(TEXT("after the run the person's key reaches the game"), PersonKeyReaches(*this));
		TestTrue(TEXT("after the run the person's stick reaches the game"),
			PersonStickReaches(FGamepadKeyNames::RightAnalogY, EKeys::Gamepad_RightY));
		PlaytestRunInput::SetForTests(true);
		TSharedPtr<SViewport> After = View->GetGameViewportWidget();
		TestFalse(TEXT("a run that starts with the view holding the mouse lets it go"), After.IsValid() && After->HasMouseCapture());
		TestFalse(TEXT("... and turns raw mouse input off"), Slate.IsUsingHighPrecisionMouseMovment());
		PlaytestRunInput::SetForTests(false);
		PlaytestRunInput::SetForTests(TOptional<bool>());
		return true;
	}));
	if (bStarted)
	{
		ADD_LATENT_AUTOMATION_COMMAND(FFunctionLatentCommand([]()
		{
			if (GEditor && GEditor->PlayWorld)
			{
				GEditor->RequestEndPlayMap();
			}
			return true;
		}));
	}
	return true;
}

#endif
