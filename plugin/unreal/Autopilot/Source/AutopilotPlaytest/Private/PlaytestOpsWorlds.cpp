// `counterpart` and `read_worlds`: comparing host and client. In Play In Editor every world (the server's, each
// client's) has its own copy of a replicated actor, under its own path (its world's PIE prefix). `counterpart` finds an
// actor's copy in another world; `read_worlds` reads the same fields of every copy inside one game-thread call, so all
// of them come from the same engine frame: a gap between the host's value and a client's is the game's own (lag that
// closes again, or a desync that stays), not two reads a frame apart. The hello's features say "worlds".

#include "PlaytestOps.h"

#include "Engine/Level.h"
#include "Engine/NetDriver.h"
#include "Engine/PackageMapClient.h"
#include "EngineUtils.h"
#include "GameFramework/Controller.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerState.h"
#include "PlaytestJson.h"
#include "UObject/Package.h"

namespace PlaytestOps
{
namespace
{
	/** A world a request names: player n's, or "server" (the world with authority over the game, when it runs in this
	 *  process: a listen server's host world, the editor's dedicated server). */
	struct FAskedWorld
	{
		TSharedPtr<FJsonValue> Key;  // as the answer says it: n or "server"
		UWorld* World = nullptr;
		FString Problem;  // why there is no such world ("" when there is)
	};

	FAskedWorld AskedWorld(UPlaytestSubsystem& Bridge, const TSharedPtr<FJsonValue>& Value)
	{
		FAskedWorld Out;
		if (Value.IsValid() && Value->Type == EJson::String && Value->AsString() == TEXT("server"))
		{
			Out.Key = MakeShared<FJsonValueString>(TEXT("server"));
			Out.World = Bridge.GetServerWorld(Bridge.GetGameWorld());
			if (!Out.World)
			{
				Out.Problem = TEXT("The server's world isn't in this editor (the server runs in another process): only "
					"the players' worlds can be read");
			}
		}
		else if (Value.IsValid() && Value->Type == EJson::Number)
		{
			const int32 Player = FMath::FloorToInt32(Value->AsNumber());
			Out.Key = MakeShared<FJsonValueNumber>(Player);
			Out.World = Bridge.GetPlayerWorld(Player);
			if (!Out.World)
			{
				const int32 Count = Bridge.GetPlayerWorlds().Num();
				Out.Problem = FString::Printf(TEXT("No player %d in the game: %d player%s"), Player, Count,
					Count == 1 ? TEXT("") : TEXT("s"));
			}
		}
		else
		{
			Out.Key = Value.IsValid() ? Value : MakeShared<FJsonValueNull>();
			Out.Problem = TEXT("A world is a player's number (1, 2...) or \"server\"");
		}
		return Out;
	}

	const TCHAR* NetModeName(const UWorld* World)
	{
		switch (World ? World->GetNetMode() : NM_Standalone)
		{
		case NM_Client:
			return TEXT("client");
		case NM_ListenServer:
			return TEXT("listen_server");
		case NM_DedicatedServer:
			return TEXT("dedicated_server");
		default:
			return TEXT("standalone");
		}
	}

	/** The actor a request names, or nullptr and why. */
	AActor* NamedActor(const FString& Path, FString& Problem)
	{
		UObject* Object = PlaytestJson::ResolveObject(Path);
		AActor* Actor = Cast<AActor>(Object);
		if (!Object)
		{
			Problem = Missing(Path);
		}
		else if (!Actor)
		{
			Problem = FString::Printf(TEXT("%s isn't an actor: give the actor's path (its copies are found by actor)"), *Path);
		}
		return Actor;
	}

	/** The player state an actor belongs to: its own (a player state), its controller's, its pawn's; else nullptr. */
	APlayerState* PlayerStateOf(AActor* Actor)
	{
		if (APlayerState* State = Cast<APlayerState>(Actor))
		{
			return State;
		}
		if (AController* Controller = Cast<AController>(Actor))
		{
			return Controller->PlayerState;
		}
		if (APawn* Pawn = Cast<APawn>(Actor))
		{
			return Pawn->GetPlayerState();
		}
		return nullptr;
	}

	/** The same player's actor of the same kind in another world (as ServerController matches a client's controller):
	 *  the player state with the same player id there, and its controller or pawn. A client has only its own player's
	 *  controller, so another player's is nullptr there. */
	AActor* SamePlayers(AActor* Source, const APlayerState* State, UWorld* To)
	{
		for (TActorIterator<APlayerState> It(To); It; ++It)
		{
			APlayerState* Other = *It;
			if (!Other || Other->GetPlayerId() != State->GetPlayerId())
			{
				continue;
			}
			if (Cast<APlayerState>(Source))
			{
				return Other;
			}
			if (Cast<AController>(Source))
			{
				return Other->GetOwningController();
			}
			return Other->GetPawn();
		}
		return nullptr;
	}

	/** A replicated actor by its network GUID: the server gives each replicated object one, and every client's net
	 *  driver maps the same GUID to its own copy. Looked up in the actor's own world's GUID cache, then found in the
	 *  other world's. */
	AActor* SameGuid(AActor* Source, UWorld* To)
	{
		UWorld* From = Source->GetWorld();
		UNetDriver* FromDriver = From ? From->GetNetDriver() : nullptr;
		UNetDriver* ToDriver = To->GetNetDriver();
		if (!FromDriver || !ToDriver || !FromDriver->GuidCache.IsValid() || !ToDriver->GuidCache.IsValid())
		{
			return nullptr;
		}
		const FNetworkGUID Guid = FromDriver->GuidCache->GetNetGUID(Source);
		if (!Guid.IsValid())
		{
			return nullptr;
		}
		AActor* Found = Cast<AActor>(ToDriver->GuidCache->GetObjectFromNetGUID(Guid, true));
		return Found && Found->GetWorld() == To ? Found : nullptr;
	}

	/** An actor loaded with the map (never one spawned in play: another world's actor of that name may be another
	 *  thing): the actor of the same name in the same level of the other world, the levels' PIE prefixes stripped
	 *  (/Game/Maps/UEDPIE_0_Arena and /Game/Maps/UEDPIE_1_Arena are both /Game/Maps/Arena). */
	AActor* SameName(AActor* Source, UWorld* To)
	{
		ULevel* Level = Source->GetLevel();
		if (!Source->IsNetStartupActor() || !Level)
		{
			return nullptr;
		}
		const FString Package = UWorld::RemovePIEPrefix(Level->GetPackage()->GetName());
		for (ULevel* Other : To->GetLevels())
		{
			if (Other && UWorld::RemovePIEPrefix(Other->GetPackage()->GetName()) == Package)
			{
				return FindObject<AActor>(Other, *Source->GetName());
			}
		}
		return nullptr;
	}

	/** Source's copy in the world To, and how it was matched, in this order: the player it belongs to (its player id),
	 *  its network GUID (a replicated actor), its name (an actor loaded with the map); else "none" and why. Source's own
	 *  world: itself ("same"). */
	AActor* CopyIn(AActor* Source, UWorld* To, FString& How, FString& Why)
	{
		if (Source->GetWorld() == To)
		{
			How = TEXT("same");
			return Source;
		}
		if (const APlayerState* State = PlayerStateOf(Source))
		{
			if (AActor* Found = SamePlayers(Source, State, To))
			{
				How = TEXT("player");
				return Found;
			}
		}
		if (AActor* Found = SameGuid(Source, To))
		{
			How = TEXT("guid");
			return Found;
		}
		if (AActor* Found = SameName(Source, To))
		{
			How = TEXT("name");
			return Found;
		}
		How = TEXT("none");
		const FString Name = Source->GetName();
		if (Source->IsActorBeingDestroyed())
		{
			Why = FString::Printf(TEXT("%s is being destroyed"), *Name);
		}
		else if (Cast<AController>(Source) && To->GetNetMode() == NM_Client)
		{
			Why = FString::Printf(TEXT("%s is a player's controller: a client has only its own player's (compare the "
				"player's pawn or player state instead)"), *Name);
		}
		else if (!Source->GetIsReplicated() && !Source->IsNetStartupActor())
		{
			Why = FString::Printf(TEXT("%s isn't replicated and wasn't loaded with the map: each world has its own, "
				"unrelated"), *Name);
		}
		else if (To->GetNetMode() == NM_Client)
		{
			Why = FString::Printf(TEXT("%s hasn't been replicated to that client (not relevant to it, or not yet)"), *Name);
		}
		else
		{
			Why = FString::Printf(TEXT("that world has no copy of %s (spawned in its own world, not replicated from "
				"the server)"), *Name);
		}
		return nullptr;
	}

	/** A position or a velocity: {"X", "Y", "Z"}, unrounded (a gap between worlds can be under a centimetre). */
	TSharedRef<FJsonObject> Exact(const FVector& V)
	{
		TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
		O->SetField(TEXT("X"), PlaytestJson::Number(V.X));
		O->SetField(TEXT("Y"), PlaytestJson::Number(V.Y));
		O->SetField(TEXT("Z"), PlaytestJson::Number(V.Z));
		return O;
	}

	/** One field of an actor: "location", "velocity", "rotation" ({"Pitch", "Yaw", "Roll"}), else its property by name
	 *  (read as `rc` reads one); nullptr and the error (rc's words) for a property it hasn't. */
	TSharedPtr<FJsonValue> FieldOf(AActor* Actor, const FString& Field, FString& Error)
	{
		if (Field.Equals(TEXT("location"), ESearchCase::CaseSensitive))
		{
			return Obj(Exact(Actor->GetActorLocation()));
		}
		if (Field.Equals(TEXT("velocity"), ESearchCase::CaseSensitive))
		{
			return Obj(Exact(Actor->GetVelocity()));
		}
		if (Field.Equals(TEXT("rotation"), ESearchCase::CaseSensitive))
		{
			const FRotator R = Actor->GetActorRotation();
			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetField(TEXT("Pitch"), PlaytestJson::Number(R.Pitch));
			O->SetField(TEXT("Yaw"), PlaytestJson::Number(R.Yaw));
			O->SetField(TEXT("Roll"), PlaytestJson::Number(R.Roll));
			return Obj(O);
		}
		FProperty* Prop = PlaytestJson::FindProperty(Actor->GetClass(), Field);
		if (!Prop || PlaytestJson::IsHidden(Prop))
		{
			Error = FString::Printf(TEXT("Property %s does not exist on object %s"), *Field, *PathOf(Actor));
			return nullptr;
		}
		TSharedPtr<FJsonValue> Value = PlaytestJson::PropertyInContainerToJson(Prop, Actor);
		return Value.IsValid() ? Value : MakeShared<FJsonValueNull>();
	}

	/** One world's entry of read_worlds: {"world", "net_mode", "time" (that world's game time), "path" (the copy
	 *  there), "how", "values": {field: value}}, and "error" when that world couldn't be read (no such world, no copy
	 *  there, a property the actor hasn't): the kit gives that world a GameError. */
	TSharedRef<FJsonObject> OneWorld(AActor* Source, const FAskedWorld& Asked, const TArray<FString>& Fields)
	{
		TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> Values = MakeShared<FJsonObject>();
		Entry->SetField(TEXT("world"), Asked.Key);
		Entry->SetObjectField(TEXT("values"), Values);
		if (!Asked.World)
		{
			Entry->SetStringField(TEXT("path"), FString());
			Entry->SetStringField(TEXT("how"), TEXT("none"));
			Entry->SetStringField(TEXT("error"), Asked.Problem);
			return Entry;
		}
		Entry->SetStringField(TEXT("net_mode"), NetModeName(Asked.World));
		Entry->SetField(TEXT("time"), PlaytestJson::Number(Asked.World->GetTimeSeconds()));
		FString How, Why;
		AActor* Copy = CopyIn(Source, Asked.World, How, Why);
		Entry->SetStringField(TEXT("path"), PathOf(Copy));
		Entry->SetStringField(TEXT("how"), How);
		if (!Copy)
		{
			Entry->SetStringField(TEXT("error"), Why);
			return Entry;
		}
		TArray<FString> Errors;
		for (const FString& Field : Fields)
		{
			FString Error;
			TSharedPtr<FJsonValue> Value = FieldOf(Copy, Field, Error);
			if (Value.IsValid())
			{
				Values->SetField(Field, Value);
			}
			else
			{
				Errors.Add(Error);
			}
		}
		if (Errors.Num() > 0)
		{
			Entry->SetStringField(TEXT("error"), FString::Join(Errors, TEXT("; ")));
		}
		return Entry;
	}
}

	void Counterpart(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		// {path, to: n | "server"} -> {path, how} or {path: "", how: "none", why}
		const FString Path = StringField(Args, TEXT("path"));
		FString Problem;
		AActor* Source = NamedActor(Path, Problem);
		if (!Source)
		{
			Bridge.RespondError(Id, Problem);
			return;
		}
		const FAskedWorld To = AskedWorld(Bridge, Args.IsValid() ? Args->TryGetField(TEXT("to")) : nullptr);
		if (!To.World)
		{
			Bridge.RespondError(Id, To.Problem, 404);
			return;
		}
		FString How, Why;
		AActor* Copy = CopyIn(Source, To.World, How, Why);
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("path"), PathOf(Copy));
		Result->SetStringField(TEXT("how"), How);
		if (!Copy)
		{
			Result->SetStringField(TEXT("why"), Why);
		}
		Bridge.Respond(Id, Obj(Result));
	}

	void ReadWorlds(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		// {path, fields, worlds?} -> {frame, worlds: [...]}. One op, whole on the game thread and answered here (never
		// a pending op that answers in a later frame): every world's values come from this engine frame
		const FString Path = StringField(Args, TEXT("path"));
		FString Problem;
		AActor* Source = NamedActor(Path, Problem);
		if (!Source)
		{
			Bridge.RespondError(Id, Problem);
			return;
		}
		TArray<FString> Fields;
		const TArray<TSharedPtr<FJsonValue>>* FieldValues = nullptr;
		if (Args.IsValid() && Args->TryGetArrayField(TEXT("fields"), FieldValues) && FieldValues)
		{
			for (const TSharedPtr<FJsonValue>& Value : *FieldValues)
			{
				if (Value.IsValid() && Value->Type == EJson::String && !Value->AsString().IsEmpty())
				{
					Fields.Add(Value->AsString());
				}
			}
		}
		if (Fields.Num() == 0)
		{
			Bridge.RespondError(Id, TEXT("read_worlds needs fields: \"location\", \"velocity\", \"rotation\" or "
				"property names"));
			return;
		}
		// The worlds asked for; none: every player's, then the server's when it is in this editor (a listen server's
		// is player 1's own world, read again under "server")
		TArray<TSharedPtr<FJsonValue>> Asked;
		const TArray<TSharedPtr<FJsonValue>>* WorldValues = nullptr;
		if (Args.IsValid() && Args->TryGetArrayField(TEXT("worlds"), WorldValues) && WorldValues && WorldValues->Num() > 0)
		{
			Asked = *WorldValues;
		}
		else
		{
			for (int32 Player = 1; Player <= Bridge.GetPlayerWorlds().Num(); ++Player)
			{
				Asked.Add(MakeShared<FJsonValueNumber>(Player));
			}
			if (Bridge.GetServerWorld(Bridge.GetGameWorld()))
			{
				Asked.Add(MakeShared<FJsonValueString>(TEXT("server")));
			}
		}
		TArray<TSharedPtr<FJsonValue>> Worlds;
		for (const TSharedPtr<FJsonValue>& Value : Asked)
		{
			Worlds.Add(Obj(OneWorld(Source, AskedWorld(Bridge, Value), Fields)));
		}
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetNumberField(TEXT("frame"), GFrameCounter);
		Result->SetArrayField(TEXT("worlds"), Worlds);
		Bridge.Respond(Id, Obj(Result));
	}
}
