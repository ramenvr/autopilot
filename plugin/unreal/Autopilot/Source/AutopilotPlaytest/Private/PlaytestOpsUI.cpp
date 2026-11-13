// `screen`, `ui` and `ui_act` (docs/protocol.md): the texts on screen, and the game's UI elements listed and acted
// on. PlaytestScreen.cpp and PlaytestUI.cpp do the work.

#include "PlaytestOps.h"

namespace PlaytestOps
{
	void Screen(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		TSharedRef<FJsonObject> Texts = MakeShared<FJsonObject>();
		UWorld* World = PlayerWorld(Bridge, Args);  // (the player's own screen: each player's viewport has its own UI)
		for (const PlaytestScreen::FShownText& Shown : PlaytestScreen::Read(World))
		{
			Texts->SetStringField(Shown.Key, Shown.Text);
		}
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetObjectField(TEXT("texts"), Texts);
		bool bUI = false;
		if (Args.IsValid() && Args->TryGetBoolField(TEXT("ui"), bUI) && bUI)
		{
			Result->SetArrayField(TEXT("ui"), PlaytestUI::List(World));
		}
		Bridge.Respond(Id, Obj(Result));
	}

	void UiList(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetArrayField(TEXT("elements"), PlaytestUI::List(PlayerWorld(Bridge, Args)));
		Bridge.Respond(Id, Obj(Result));
	}

	void UiAct(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		const FString Error = PlaytestUI::Act(PlayerWorld(Bridge, Args), Args.IsValid() ? Args : MakeShared<FJsonObject>());
		if (!Error.IsEmpty())
		{
			Bridge.RespondError(Id, Error);
			return;
		}
		Bridge.GetDetectors().NoteAgentInput(StringField(Args, TEXT("id")));
		Bridge.Respond(Id, Obj(MakeShared<FJsonObject>()));
	}
}
