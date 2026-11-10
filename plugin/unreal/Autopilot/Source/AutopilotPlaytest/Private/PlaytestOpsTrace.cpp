// `trace` (docs in autopilot/kit/game.py, Game.trace): the first thing a line, or a ball swept along it, meets between
// two points of the played world, by the world's collision. What a thing's box can't tell: a big mesh's box is many times
// what is solid of it, a ring or an arch is open through the middle of its box.

#include "PlaytestOps.h"

#include "Components/PrimitiveComponent.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"

namespace PlaytestOps
{
	namespace
	{
		const double TraceMaxRadius = 10000.0;

		TSharedRef<FJsonObject> UnitVec(const FVector& V)
		{
			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetNumberField(TEXT("X"), FMath::RoundToDouble(V.X * 1000.0) / 1000.0);
			O->SetNumberField(TEXT("Y"), FMath::RoundToDouble(V.Y * 1000.0) / 1000.0);
			O->SetNumberField(TEXT("Z"), FMath::RoundToDouble(V.Z * 1000.0) / 1000.0);
			return O;
		}
	}

	// {"start", "end": {"X","Y","Z"}, "radius"?: a ball's radius (0: a line), "channel"?: "body" (the default: what stops
	// the player's body, its own collision channel and responses) | "sight" (what stops a line of sight: the visibility
	// channel)} -> {"channel": the one asked of, "hit": null | {"location" (where the line stops: the ball's center when
	// it touches), "point" (where it touches), "normal", "distance" (start to location), "actor" (path, the whole actor
	// for a child actor's part), "name", "label"? (editor), "component", "inside" (start was already in it)}}. The
	// player's own pawn and what it carries are never hit. "player" n: in player n's world, its pawn never hit (the
	// editor's multiplayer Play In Editor gives each player a world of its own).
	void Trace(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		UWorld* World = PlayerWorld(Bridge, Args);
		if (!World)
		{
			Bridge.RespondError(Id, TEXT("No game world"));
			return;
		}
		if (!ObjectField(Args, TEXT("start")).IsValid() || !ObjectField(Args, TEXT("end")).IsValid())
		{
			Bridge.RespondError(Id, TEXT("trace needs a start and an end ({\"X\", \"Y\", \"Z\"})"));
			return;
		}
		const FVector Start = VectorField(Args, TEXT("start"));
		const FVector End = VectorField(Args, TEXT("end"));
		const double Radius = FMath::Clamp(NumberField(Args, TEXT("radius"), 0.0), 0.0, TraceMaxRadius);
		const bool bSight = StringField(Args, TEXT("channel")) == TEXT("sight");

		APlayerController* PC = PlayerController(World);
		APawn* Pawn = PC ? PC->GetPawn() : nullptr;
		FCollisionQueryParams Params(SCENE_QUERY_STAT(PlaytestTrace), false, Pawn);
		FCollisionResponseParams Responses;
		ECollisionChannel Channel = bSight ? ECC_Visibility : ECC_Pawn;
		if (Pawn)
		{
			TArray<AActor*> Carried;
			Pawn->GetAttachedActors(Carried, true, true);
			Params.AddIgnoredActors(Carried);
			UPrimitiveComponent* PlayerBody = Cast<UPrimitiveComponent>(Pawn->GetRootComponent());
			if (!bSight && PlayerBody && PlayerBody->GetCollisionEnabled() != ECollisionEnabled::NoCollision)
			{
				// (as its moves are swept: its object channel, its responses to every channel, what it ignores moving)
				PlayerBody->InitSweepCollisionParams(Params, Responses);
				Channel = PlayerBody->GetCollisionObjectType();
			}
		}

		FHitResult Hit;
		const bool bHit = Radius > 0.0
			? World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, Channel,
				  FCollisionShape::MakeSphere((float)Radius), Params, Responses)
			: World->LineTraceSingleByChannel(Hit, Start, End, Channel, Params, Responses);

		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetStringField(TEXT("channel"), bSight ? TEXT("sight") : TEXT("body"));
		if (!bHit || !Hit.bBlockingHit)
		{
			Out->SetField(TEXT("hit"), MakeShared<FJsonValueNull>());
			Bridge.Respond(Id, Obj(Out));
			return;
		}
		TSharedRef<FJsonObject> H = MakeShared<FJsonObject>();
		H->SetObjectField(TEXT("location"), Vec(Hit.Location));
		H->SetObjectField(TEXT("point"), Vec(Hit.ImpactPoint));
		H->SetObjectField(TEXT("normal"), UnitVec(Hit.ImpactNormal));
		H->SetNumberField(TEXT("distance"), FMath::RoundToDouble(Hit.Distance * 10.0) / 10.0);
		H->SetBoolField(TEXT("inside"), Hit.bStartPenetrating);
		if (AActor* Actor = WholeOf(Hit.GetActor()))
		{
			H->SetStringField(TEXT("actor"), PathOf(Actor));
			H->SetStringField(TEXT("name"), Actor->GetName());
#if WITH_EDITOR
			H->SetStringField(TEXT("label"), Actor->GetActorLabel());
#endif
		}
		if (const UPrimitiveComponent* Component = Hit.GetComponent())
		{
			H->SetStringField(TEXT("component"), Component->GetName());
		}
		Out->SetObjectField(TEXT("hit"), H);
		Bridge.Respond(Id, Obj(Out));
	}
}
