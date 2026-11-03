// A run's input is the run's alone (as Unity's: Hands.cs, "a run's input is the bridge's alone"). While a run plays, the
// keyboard, mouse and gamepads of the person at the computer don't reach the game, so what they do in the editor (or a
// controller in their hands) doesn't mix with the run's input: a Slate input pre-processor, first in line, takes every
// event Slate would give the game before anything else sees it. A key, a stick or a motion goes to the game when the game
// has that user's focus; the mouse when it is over the game's view or held by it (a drag the editor holds stays the
// editor's). In a game of its own (-game) all of it is the game's. The run's own input isn't the person's: keys, sticks
// and the mouse's movement go into the player controller (PlaytestOpsInput.cpp), past Slate, and the pointer's clicks on
// the game's UI go through Slate marked as the run's (FOwnEvents), so all of it reaches the game as before.
//
// The editor stays the person's: a key goes to the editor's own widgets as before, a click anywhere but the game's view
// too, and the editor's Stop shortcut in play (Escape by default) stops play instead of reaching the game (EditorKey).
// A key or button that went down before the run comes up in the game as it would; one whose press was held off has its
// release held off too.
//
// Nor does the game's view take the mouse: a game that holds it (SetInputMode GameOnly, a view that captures it
// permanently, "Game Gets Mouse Control") would lock the desktop's cursor inside the editor and hide it, wherever the
// person was working, as soon as the editor came to the front. During a run the view's capture and lock are off and any
// capture it took is let go; the game's own settings come back when the run ends (the next click in the view takes the
// mouse again, as it would). The game still turns with the run's mouse movement, which never needed the capture.

#include "PlaytestRunInput.h"

#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Framework/Application/IInputProcessor.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Application/SlateUser.h"
#include "Input/Events.h"
#include "Input/Reply.h"
#include "Layout/WidgetPath.h"
#include "PlaytestInternal.h"
#include "Widgets/SViewport.h"
#include "Widgets/SWindow.h"

namespace PlaytestRunInput
{
namespace
{
	int32 OwnDepth = 0;
	bool bIsolating = false;
	TOptional<bool> Forced;  // (tests)

	/** A game view's own mouse settings, while the run has them off. */
	struct FGameMouse
	{
		EMouseCaptureMode Capture = EMouseCaptureMode::CapturePermanently;
		EMouseLockMode Lock = EMouseLockMode::LockOnCapture;
		bool bHide = true;
	};
	TMap<TWeakObjectPtr<UGameViewportClient>, FGameMouse> GameMouse;

	/** Presses held off the game: their releases are held off too (a key per user, a mouse button). */
	TSet<TPair<int32, FKey>> HeldKeys;
	TSet<FKey> HeldButtons;

	/** The views of the games being played (each play-in-editor world's, or this game's own). */
	TArray<TSharedRef<SViewport>> GameViews()
	{
		TArray<TSharedRef<SViewport>> Views;
		if (!GEngine)
		{
			return Views;
		}
		for (const FWorldContext& Context : GEngine->GetWorldContexts())
		{
			UWorld* World = Context.World();
			if (!World || !World->IsGameWorld() || !Context.GameViewport)
			{
				continue;
			}
			if (TSharedPtr<SViewport> View = Context.GameViewport->GetGameViewportWidget())
			{
				Views.Add(View.ToSharedRef());
			}
		}
		return Views;
	}

	bool InGameView(TSharedPtr<SWidget> Widget, const TArray<TSharedRef<SViewport>>& Views)
	{
		for (; Widget.IsValid(); Widget = Widget->GetParentWidget())
		{
			for (const TSharedRef<SViewport>& View : Views)
			{
				if (Widget.Get() == &View.Get())
				{
					return true;
				}
			}
		}
		return false;
	}

	/** Whether Slate would give a key, stick or motion of this user's to the game. */
	bool KeyToGame(FSlateApplication& Slate, int32 UserIndex)
	{
		const TArray<TSharedRef<SViewport>> Views = GameViews();
		if (!Views.Num())
		{
			return false;
		}
		if (!GIsEditor)
		{
			return true;  // (a game of its own: every key is the game's)
		}
		return UserIndex >= 0 && InGameView(Slate.GetUserFocusedWidget(UserIndex), Views);
	}

	/** Whether Slate would give this mouse event to the game: over its view, or the view (or its UI) holds the mouse. */
	bool MouseToGame(FSlateApplication& Slate, const FPointerEvent& Event)
	{
		const TArray<TSharedRef<SViewport>> Views = GameViews();
		if (!Views.Num())
		{
			return false;
		}
		if (!GIsEditor)
		{
			return true;
		}
		if (TSharedPtr<FSlateUser> User = Slate.GetUser(Event.GetUserIndex()))
		{
			if (TSharedPtr<SWidget> Captor = User->GetPointerCaptor(Event.GetPointerIndex()))
			{
				return InGameView(Captor, Views);  // (a drag the editor holds stays the editor's, wherever it goes)
			}
		}
		const FWidgetPath Path = Slate.LocateWindowUnderMouse(Event.GetScreenSpacePosition(), Slate.GetInteractiveTopLevelWindows(),
			false, Event.GetUserIndex());
		for (int32 i = Path.Widgets.Num() - 1; i >= 0; --i)
		{
			for (const TSharedRef<SViewport>& View : Views)
			{
				if (&Path.Widgets[i].Widget.Get() == &View.Get())
				{
					return true;
				}
			}
		}
		return false;
	}

	bool Takes()
	{
		return bIsolating && OwnDepth == 0;
	}

	class FProcessor : public IInputProcessor
	{
	public:
		virtual void Tick(const float DeltaTime, FSlateApplication& SlateApp, TSharedRef<ICursor> Cursor) override {}

		virtual bool HandleKeyDownEvent(FSlateApplication& Slate, const FKeyEvent& Event) override
		{
			if (!Takes() || !KeyToGame(Slate, Event.GetUserIndex()))
			{
				return false;
			}
			if (!Event.IsRepeat() && EditorKey())
			{
				EditorKey()(Event);  // (the editor's Stop: play stops; the game doesn't get the key either way)
			}
			HeldKeys.Add(TPair<int32, FKey>(Event.GetUserIndex(), Event.GetKey()));
			return true;
		}

		virtual bool HandleKeyUpEvent(FSlateApplication& Slate, const FKeyEvent& Event) override
		{
			return OwnDepth == 0 && HeldKeys.Remove(TPair<int32, FKey>(Event.GetUserIndex(), Event.GetKey())) > 0;
		}

		virtual bool HandleAnalogInputEvent(FSlateApplication& Slate, const FAnalogInputEvent& Event) override
		{
			return Takes() && KeyToGame(Slate, Event.GetUserIndex());
		}

		virtual bool HandleMotionDetectedEvent(FSlateApplication& Slate, const FMotionEvent& Event) override
		{
			return Takes() && KeyToGame(Slate, Event.GetUserIndex());
		}

		virtual bool HandleMouseMoveEvent(FSlateApplication& Slate, const FPointerEvent& Event) override
		{
			return Takes() && MouseToGame(Slate, Event);
		}

		virtual bool HandleMouseButtonDownEvent(FSlateApplication& Slate, const FPointerEvent& Event) override
		{
			return Press(Slate, Event);
		}

		virtual bool HandleMouseButtonDoubleClickEvent(FSlateApplication& Slate, const FPointerEvent& Event) override
		{
			return Press(Slate, Event);
		}

		virtual bool HandleMouseButtonUpEvent(FSlateApplication& Slate, const FPointerEvent& Event) override
		{
			return OwnDepth == 0 && HeldButtons.Remove(Event.GetEffectingButton()) > 0;
		}

		virtual bool HandleMouseWheelOrGestureEvent(FSlateApplication& Slate, const FPointerEvent& Event, const FPointerEvent* Gesture) override
		{
			return Takes() && MouseToGame(Slate, Event);
		}

		virtual const TCHAR* GetDebugName() const override { return TEXT("AutopilotRunInput"); }

	private:
		static bool Press(FSlateApplication& Slate, const FPointerEvent& Event)
		{
			if (!Takes() || !MouseToGame(Slate, Event))
			{
				return false;
			}
			HeldButtons.Add(Event.GetEffectingButton());
			return true;
		}
	};
	TSharedPtr<FProcessor> Processor;

	/** The game's views don't take the mouse during the run: their capture and lock off (each one's own settings kept
	 *  for after, again whenever the game sets its own), and a capture or lock they hold let go, as Slate lets go of it. */
	void LetGoOfMouse(FSlateApplication& Slate)
	{
		if (!GEngine)
		{
			return;
		}
		for (const FWorldContext& Context : GEngine->GetWorldContexts())
		{
			UGameViewportClient* Client = Context.GameViewport;
			UWorld* World = Context.World();
			if (!Client || !World || !World->IsGameWorld())
			{
				continue;
			}
			bool bLetGo = false;
			const EMouseCaptureMode Capture = Client->GetMouseCaptureMode();
			const EMouseLockMode Lock = Client->GetMouseLockMode();
			const bool bHide = Client->HideCursorDuringCapture();
			if (Capture != EMouseCaptureMode::NoCapture || Lock != EMouseLockMode::DoNotLock || bHide)
			{
				GameMouse.Add(Client, FGameMouse{Capture, Lock, bHide});  // (the game's, set again: kept for after)
				Client->SetMouseCaptureMode(EMouseCaptureMode::NoCapture);
				Client->SetMouseLockMode(EMouseLockMode::DoNotLock);
				Client->SetHideCursorDuringCapture(false);
				bLetGo = true;
			}
			TSharedPtr<SViewport> View = Client->GetGameViewportWidget();
			if (!View.IsValid())
			{
				continue;
			}
			if (!bLetGo)
			{
				const TArray<TSharedRef<SViewport>> Views = {View.ToSharedRef()};
				TSharedPtr<FSlateUser> User = Slate.GetCursorUser();
				bLetGo = User.IsValid() && InGameView(User->GetCursorCaptor(), Views);
			}
			if (!bLetGo)
			{
				continue;
			}
			FWidgetPath Path;
			if (Slate.FindPathToWidget(View.ToSharedRef(), Path))
			{
				// (as a view's own reply lets go: the capture, the raw mouse input it turned on, and the cursor's lock)
				Slate.ProcessReply(Path, FReply::Handled().ReleaseMouseCapture().ReleaseMouseLock(), nullptr, nullptr,
					Slate.GetUserIndexForMouse());
				continue;
			}
			// No path to the view (its window minimized or not drawn): a reply needs one, so the same, by hand
			Slate.ReleaseAllPointerCapture(Slate.GetUserIndexForMouse());
			if (TSharedPtr<GenericApplication> Platform = Slate.GetPlatformApplication())
			{
				if (Platform->IsUsingHighPrecisionMouseMode())
				{
					Platform->SetHighPrecisionMouseMode(false, nullptr);
					Platform->SetCapture(nullptr);
				}
			}
			if (TSharedPtr<ICursor> Cursor = Slate.GetPlatformCursor())
			{
				Cursor->Lock(nullptr);
			}
		}
	}

	/** The run is over: each game view gets its own mouse settings back. */
	void GiveBackMouse()
	{
		for (const TPair<TWeakObjectPtr<UGameViewportClient>, FGameMouse>& Pair : GameMouse)
		{
			if (UGameViewportClient* Client = Pair.Key.Get())
			{
				Client->SetMouseCaptureMode(Pair.Value.Capture);
				Client->SetMouseLockMode(Pair.Value.Lock);
				Client->SetHideCursorDuringCapture(Pair.Value.bHide);
			}
		}
		GameMouse.Reset();
	}
}

void Update(bool bRunPlaying)
{
	if (Forced.IsSet())
	{
		bRunPlaying = Forced.GetValue();
	}
	if (!FSlateApplication::IsInitialized())
	{
		return;
	}
	FSlateApplication& Slate = FSlateApplication::Get();
	if (bRunPlaying && !Processor.IsValid())
	{
		Processor = MakeShared<FProcessor>();
		Slate.RegisterInputPreProcessor(Processor, 0);  // (first: no other pre-processor sees the person's input either)
	}
	if (bRunPlaying != bIsolating)
	{
		if (bRunPlaying)
		{
			UE_LOG(LogPlaytest, Display, TEXT("[Autopilot] While the run plays, your keyboard, mouse and gamepads don't reach the game and it doesn't take your mouse; the editor stays yours (its Stop shortcut stops play)."));
		}
		else
		{
			UE_LOG(LogPlaytest, Display, TEXT("[Autopilot] The run stopped playing: your keyboard, mouse and gamepads reach the game again."));
			GiveBackMouse();
		}
	}
	bIsolating = bRunPlaying;
	if (bIsolating)
	{
		LetGoOfMouse(Slate);
	}
}

void Shutdown()
{
	if (bIsolating)
	{
		GiveBackMouse();
	}
	bIsolating = false;
	HeldKeys.Reset();
	HeldButtons.Reset();
	if (Processor.IsValid() && FSlateApplication::IsInitialized())
	{
		FSlateApplication::Get().UnregisterInputPreProcessor(Processor);
	}
	Processor.Reset();
}

bool IsIsolating()
{
	return bIsolating;
}

void SetForTests(TOptional<bool> bOn)
{
	Forced = bOn;
	if (bOn.IsSet())
	{
		Update(bOn.GetValue());
	}
}

TFunction<bool(const FKeyEvent&)>& EditorKey()
{
	static TFunction<bool(const FKeyEvent&)> Handler;
	return Handler;
}

FOwnEvents::FOwnEvents()
{
	++OwnDepth;
}

FOwnEvents::~FOwnEvents()
{
	--OwnDepth;
}
}
