// tools/net_smoke.py's test game: a training dummy whose state only its server changes, as a dedicated-server game's
// (Omeda's Predecessor, IronMace's tutorial dummies). It walks to and fro (the server moves it: a paused game stops
// it), takes hits only on the server, and dies there; a client's copy shows what the server sent.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "NetDummy.generated.h"

class UStaticMeshComponent;

UCLASS()
class ANetDummy : public AActor
{
	GENERATED_BODY()

public:
	ANetDummy();

	UPROPERTY(Replicated, BlueprintReadOnly, Category = "Dummy")
	float Health = 100.f;

	UPROPERTY(Replicated, BlueprintReadOnly, Category = "Dummy")
	bool bDead = false;

	/** The hits it has taken: the server's count alone (not replicated: a client's copy stays at 0) */
	UPROPERTY(BlueprintReadOnly, Category = "Dummy")
	int32 HitsTaken = 0;

	/** How far it walks each way from where it was spawned */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Dummy")
	float PatrolDistance = 300.f;

	/** A hit: only the server's copy takes it */
	UFUNCTION(BlueprintCallable, Category = "Dummy")
	void TakeHit(float Damage);

	virtual void Tick(float DeltaSeconds) override;
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

protected:
	virtual void BeginPlay() override;

private:
	UPROPERTY(VisibleAnywhere, Category = "Dummy")
	TObjectPtr<UStaticMeshComponent> Body;

	FVector Origin = FVector::ZeroVector;
	double Walked = 0.0;
};
